"""
food_db.py — 3-tier food lookup with persistent Supabase cache.

Tier 1: Supabase food_cache table   — instant, free after first lookup
Tier 2: Open Food Facts API         — free, no key, real nutritional data
Tier 3: Gemini Flash via ai_agent   — last resort, costs tokens

Tiers 2 and 3 both write to food_cache on success,
so the same food is never looked up twice.

All tiers return the same shape:
{
    "name":          str,
    "calories":      float,   # for the resolved portion
    "protein_g":     float,
    "carbs_g":       float,
    "fat_g":         float,
    "portion_label": str,     # e.g. "2 adet", "1 kase"
    "source":        str,     # "cache" | "openfoodfacts" | "ai"
    "confidence":    str,     # "high" | "medium" | "low"
}
Returns None only if all 3 tiers fail.
"""

import re
import requests
import streamlit as st


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """
    Lowercase, strip Turkish chars, remove punctuation, collapse spaces.
    Used as the cache key so 'Yumurta', 'yumurta', 'yumurtalı' all map cleanly.
    """
    text = text.lower().strip()
    for tr, en in {"ç":"c","ğ":"g","ı":"i","ö":"o","ş":"s","ü":"u"}.items():
        text = text.replace(tr, en)
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_quantity(description: str) -> float:
    """Extract leading number. '2 yumurta' → 2.0. Defaults to 1.0."""
    m = re.match(r"^\s*(\d+(?:[.,]\d+)?)", description)
    return float(m.group(1).replace(",", ".")) if m else 1.0


def _strip_quantity(description: str) -> str:
    """Remove leading number for cleaner search queries."""
    return re.sub(r"^\s*\d+[.,]?\d*\s*", "", description).strip() or description


def _scale(row: dict, quantity: float) -> dict:
    """
    Scale per-100g cache values by (quantity × portion_grams / 100).
    Returns the full result dict.
    """
    factor = (quantity * (row["portion_grams"] or 100)) / 100.0
    return {
        "name":          row["name"],
        "calories":      round(row["calories_100g"]  * factor, 1),
        "protein_g":     round(row["protein_100g"]   * factor, 1),
        "carbs_g":       round(row["carbs_100g"]     * factor, 1),
        "fat_g":         round(row["fat_100g"]       * factor, 1),
        "portion_label": f"{quantity:.0f} {row['portion_label'] or 'porsiyon'}",
        "source":        "cache",
        "confidence":    "high",
    }


def _db():
    """Reuse the Supabase client from database.py."""
    from database import _db as db_client
    return db_client()


# ── Cache read/write ──────────────────────────────────────────────────────────

def _cache_get(query_key: str) -> dict | None:
    res = (
        _db().table("food_cache")
        .select("*")
        .eq("query_key", query_key)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def _cache_set(query_key: str, name: str, cal: float, pro: float,
               carb: float, fat: float, portion_g: float,
               portion_label: str, source: str):
    try:
        _db().table("food_cache").upsert({
            "query_key":     query_key,
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
        pass   # cache write failure should never crash the app


# ── Tier 1: Supabase cache ────────────────────────────────────────────────────

def lookup_cache(description: str) -> dict | None:
    quantity  = _parse_quantity(description)
    query_key = _normalize(_strip_quantity(description))
    row       = _cache_get(query_key)
    if row:
        return _scale(row, quantity)
    return None


# ── Tier 2: Open Food Facts ───────────────────────────────────────────────────

OFF_URL = "https://world.openfoodfacts.org/cgi/search.pl"

def lookup_openfoodfacts(description: str) -> dict | None:
    quantity  = _parse_quantity(description)
    query     = _strip_quantity(description)
    query_key = _normalize(query)

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
        data = resp.json()

        for product in data.get("products", []):
            n   = product.get("nutriments", {})
            cal = n.get("energy-kcal_100g") or n.get("energy_100g")
            if not cal:
                continue

            cal_100g  = float(cal)
            pro_100g  = float(n.get("proteins_100g")      or 0)
            carb_100g = float(n.get("carbohydrates_100g") or 0)
            fat_100g  = float(n.get("fat_100g")            or 0)

            # Serving size from product, fallback to 100g
            portion_g = 100.0
            m = re.search(r"(\d+(?:[.,]\d+)?)\s*g",
                          product.get("serving_size", ""))
            if m:
                portion_g = float(m.group(1).replace(",", "."))

            name = product.get("product_name") or query

            # Write to cache
            _cache_set(query_key, name, cal_100g, pro_100g, carb_100g,
                       fat_100g, portion_g, "porsiyon", "openfoodfacts")

            factor = (quantity * portion_g) / 100.0
            return {
                "name":          name,
                "calories":      round(cal_100g  * factor, 1),
                "protein_g":     round(pro_100g  * factor, 1),
                "carbs_g":       round(carb_100g * factor, 1),
                "fat_g":         round(fat_100g  * factor, 1),
                "portion_label": f"{quantity:.0f} × {portion_g:.0f}g",
                "source":        "openfoodfacts",
                "confidence":    "medium",
            }

    except Exception:
        pass

    return None


# ── Tier 3: AI fallback ───────────────────────────────────────────────────────

def lookup_ai(description: str) -> dict | None:
    quantity  = _parse_quantity(description)
    query     = _strip_quantity(description)
    query_key = _normalize(query)

    try:
        import ai_agent as ai
        result = ai.estimate_nutrition(description)
        if result.get("error"):
            return None

        # Back-calculate per-100g values from the total estimate
        # AI returns values for the full description (including quantity)
        # We store per-100g so future lookups with different quantities work
        portion_g  = 100.0
        divisor    = quantity if quantity > 0 else 1.0
        cal_100g   = result["calories"]  / divisor
        pro_100g   = result["protein_g"] / divisor
        carb_100g  = result["carbs_g"]   / divisor
        fat_100g   = result["fat_g"]     / divisor

        _cache_set(query_key, query, cal_100g, pro_100g, carb_100g,
                   fat_100g, portion_g, "porsiyon", "ai")

        return {
            "name":          query,
            "calories":      result["calories"],
            "protein_g":     result["protein_g"],
            "carbs_g":       result["carbs_g"],
            "fat_g":         result["fat_g"],
            "portion_label": description,
            "source":        "ai",
            "confidence":    result.get("confidence", "medium"),
        }
    except Exception:
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def lookup(description: str) -> dict | None:
    """
    Main entry point. Tries all 3 tiers silently.
    Returns None only if all 3 fail.
    """
    return (
        lookup_cache(description)
        or lookup_openfoodfacts(description)
        or lookup_ai(description)
    )