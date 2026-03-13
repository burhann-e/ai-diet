"""
food_db.py — Per-ingredient lookup with persistent Supabase cache.

Flow for a single ingredient:
  Tier 1: Supabase food_cache   → instant, free after first hit
  Tier 2: Open Food Facts       → free, real data, writes to cache
  Tier 3: ai_agent.parse_ingredients → last resort, writes to cache

lookup_multi(description)  — main entry point for multi-ingredient meals
lookup_single(ingredient)  — looks up one ingredient through all 3 tiers

Each result item shape:
{
    "ingredient":   str,
    "quantity":     float,
    "unit":         str,
    "calories":     float,   # quantity × per_unit  (Python math, not LLM)
    "protein_g":    float,
    "carbs_g":      float,
    "fat_g":        float,
    "calories_per_unit": float,
    "protein_per_unit":  float,
    "carbs_per_unit":    float,
    "fat_per_unit":      float,
    "source":       str,     # "cache" | "openfoodfacts" | "ai"
    "confidence":   str,
}
"""

import re
import requests
import streamlit as st


# ── Normalisation helpers ─────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    text = text.lower().strip()
    for tr, en in {"ç":"c","ğ":"g","ı":"i","ö":"o","ş":"s","ü":"u"}.items():
        text = text.replace(tr, en)
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_quantity(text: str) -> tuple[float, str]:
    """Returns (quantity, remainder_without_number)."""
    m = re.match(r"^\s*(\d+(?:[.,]\d+)?)\s*(.*)", text)
    if m:
        return float(m.group(1).replace(",", ".")), m.group(2).strip()
    return 1.0, text.strip()


# ── Supabase client (reused from database.py) ─────────────────────────────────

def _db():
    from database import _db as db_client
    return db_client()


# ── Cache read / write ────────────────────────────────────────────────────────

def _cache_get(key: str) -> dict | None:
    res = (
        _db().table("food_cache")
        .select("*")
        .eq("query_key", key)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def _cache_set(key, name, cal, pro, carb, fat, portion_g, portion_label, source):
    try:
        _db().table("food_cache").upsert({
            "query_key":     key,
            "name":          name,
            "calories_100g": cal,
            "protein_100g":  pro,
            "carbs_100g":    carb,
            "fat_100g":      fat,
            "portion_grams": portion_g,
            "portion_label": portion_label,
            "source":        source,
        }).execute()
    except Exception:
        pass   # cache miss should never crash the app


def _row_to_per_unit(row: dict, quantity: float) -> dict:
    """Scale cached per-100g values → per-unit, then multiply by quantity in Python."""
    portion_g = row.get("portion_grams") or 100.0
    # per-unit values (1 adet / 1 kase / 1 dilim)
    factor_unit = portion_g / 100.0
    cal_u  = round(row["calories_100g"] * factor_unit, 1)
    pro_u  = round(row["protein_100g"]  * factor_unit, 1)
    carb_u = round(row["carbs_100g"]    * factor_unit, 1)
    fat_u  = round(row["fat_100g"]      * factor_unit, 1)
    return {
        "calories_per_unit": cal_u,
        "protein_per_unit":  pro_u,
        "carbs_per_unit":    carb_u,
        "fat_per_unit":      fat_u,
        # totals — Python math only
        "calories":  round(cal_u  * quantity, 1),
        "protein_g": round(pro_u  * quantity, 1),
        "carbs_g":   round(carb_u * quantity, 1),
        "fat_g":     round(fat_u  * quantity, 1),
    }


# ── Tier 2: Open Food Facts ───────────────────────────────────────────────────

OFF_URL = "https://world.openfoodfacts.org/cgi/search.pl"

def _off_lookup(query: str, quantity: float, cache_key: str) -> dict | None:
    try:
        params = {
            "search_terms":  query,
            "search_simple": 1,
            "action":        "process",
            "json":          1,
            "page_size":     5,
            "lc":            "tr",
            "fields":        "product_name,nutriments,serving_size",
        }
        resp = requests.get(OFF_URL, params=params, timeout=6)
        resp.raise_for_status()

        for product in resp.json().get("products", []):
            n   = product.get("nutriments", {})
            cal = n.get("energy-kcal_100g") or n.get("energy_100g")
            if not cal:
                continue

            cal_100g  = float(cal)
            pro_100g  = float(n.get("proteins_100g")      or 0)
            carb_100g = float(n.get("carbohydrates_100g") or 0)
            fat_100g  = float(n.get("fat_100g")            or 0)

            portion_g = 100.0
            m = re.search(r"(\d+(?:[.,]\d+)?)\s*g",
                          product.get("serving_size", ""))
            if m:
                portion_g = float(m.group(1).replace(",", "."))

            name = product.get("product_name") or query
            _cache_set(cache_key, name, cal_100g, pro_100g, carb_100g,
                       fat_100g, portion_g, "porsiyon", "openfoodfacts")

            factor_unit = portion_g / 100.0
            cal_u  = round(cal_100g  * factor_unit, 1)
            pro_u  = round(pro_100g  * factor_unit, 1)
            carb_u = round(carb_100g * factor_unit, 1)
            fat_u  = round(fat_100g  * factor_unit, 1)
            return {
                "calories_per_unit": cal_u,
                "protein_per_unit":  pro_u,
                "carbs_per_unit":    carb_u,
                "fat_per_unit":      fat_u,
                "calories":  round(cal_u  * quantity, 1),
                "protein_g": round(pro_u  * quantity, 1),
                "carbs_g":   round(carb_u * quantity, 1),
                "fat_g":     round(fat_u  * quantity, 1),
                "name":      name,
                "source":    "openfoodfacts",
                "confidence":"medium",
            }
    except Exception:
        pass
    return None


# ── Single-ingredient lookup ──────────────────────────────────────────────────

def lookup_single(ingredient: str, quantity: float, unit: str) -> dict:
    """
    Looks up one ingredient through cache → OFF → AI.
    Always returns a result dict (never None) — caller decides what to do
    if confidence is 'low' and error is True.
    """
    cache_key = _normalize(ingredient)

    # Tier 1: cache
    row = _cache_get(cache_key)
    if row:
        vals = _row_to_per_unit(row, quantity)
        return {
            "ingredient": ingredient,
            "quantity":   quantity,
            "unit":       unit,
            "source":     "cache",
            "confidence": "high",
            **vals,
        }

    # Tier 2: Open Food Facts
    off = _off_lookup(ingredient, quantity, cache_key)
    if off:
        return {"ingredient": ingredient, "quantity": quantity, "unit": unit, **off}

    # Tier 3: AI — single-item estimate, no re-parsing
    try:
        import ai_agent as ai
        est = ai.estimate_single(ingredient, quantity, unit)
        if not est.get("error"):
            cal_u  = est["calories_per_unit"]
            pro_u  = est["protein_per_unit"]
            carb_u = est["carbs_per_unit"]
            fat_u  = est["fat_per_unit"]

            _cache_set(cache_key, ingredient,
                       cal_u, pro_u, carb_u, fat_u,
                       100.0, unit, "ai")

            return {
                "ingredient":        ingredient,
                "quantity":          quantity,
                "unit":              unit,
                "calories_per_unit": cal_u,
                "protein_per_unit":  pro_u,
                "carbs_per_unit":    carb_u,
                "fat_per_unit":      fat_u,
                "calories":  round(cal_u  * quantity, 1),
                "protein_g": round(pro_u  * quantity, 1),
                "carbs_g":   round(carb_u * quantity, 1),
                "fat_g":     round(fat_u  * quantity, 1),
                "source":    "ai",
                "confidence":est.get("confidence", "medium"),
            }
    except Exception:
        pass

    # All tiers failed
    return {
        "ingredient":        ingredient,
        "quantity":          quantity,
        "unit":              unit,
        "calories_per_unit": 0.0,
        "protein_per_unit":  0.0,
        "carbs_per_unit":    0.0,
        "fat_per_unit":      0.0,
        "calories":  0.0,
        "protein_g": 0.0,
        "carbs_g":   0.0,
        "fat_g":     0.0,
        "source":    "unknown",
        "confidence":"low",
        "error":     True,
    }


# ── Multi-ingredient entry point ──────────────────────────────────────────────

def lookup_multi(description: str) -> list[dict]:
    """
    Main entry point.

    1. AI parses the free-text description into components (ingredient + quantity + unit)
    2. Each component is looked up individually via lookup_single()
    3. Returns list of per-ingredient result dicts
    4. Caller (app.py) sums totals in Python — no LLM arithmetic

    If user has manually edited the ingredient list (passed as list[str]),
    each string is parsed for a leading number, then looked up.
    """
    import ai_agent as ai

    # Step 1: parse into components
    components = ai.parse_ingredients(description)

    # Step 2: look up each component individually
    results = []
    for c in components:
        result = lookup_single(
            ingredient = c["ingredient"],
            quantity   = c["quantity"],
            unit       = c["unit"],
        )
        results.append(result)

    return results


def lookup_multi_from_list(ingredient_lines: list[str]) -> list[dict]:
    """
    Used when the user has manually split/edited the ingredient list.
    Each line is a free-text ingredient string like "2 yumurta" or "biraz peynir".
    Parses quantity from leading number, looks up the rest.
    """
    import ai_agent as ai

    results = []
    for line in ingredient_lines:
        line = line.strip()
        if not line:
            continue
        quantity, remainder = _parse_quantity(line)
        # Use AI to get unit for ambiguous items, or just use "porsiyon"
        components = ai.parse_ingredients(line)
        if components and not components[0].get("error"):
            c = components[0]
            result = lookup_single(c["ingredient"], c["quantity"], c["unit"])
        else:
            result = lookup_single(remainder or line, quantity, "porsiyon")
        results.append(result)

    return results


# ── App-level math (called by app.py, never by LLM) ──────────────────────────

def sum_components(components: list[dict]) -> dict:
    """
    Pure Python sum of a components list.
    This is the ONLY place totals are calculated.
    """
    return {
        "calories":  round(sum(c["calories"]  for c in components), 1),
        "protein_g": round(sum(c["protein_g"] for c in components), 1),
        "carbs_g":   round(sum(c["carbs_g"]   for c in components), 1),
        "fat_g":     round(sum(c["fat_g"]      for c in components), 1),
    }