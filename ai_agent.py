"""
ai_agent.py — All Gemini API calls. No Streamlit, no DB writes.
Receives plain Python data, returns plain Python data.

Models:
  - gemini-2.0-flash  : calorie/macro estimation (fast, frequent)
  - gemini-2.5-pro    : weekly summary, crisis intervention (slower, rare)

API key: st.secrets["GEMINI_API_KEY"]
"""

import json
import re
import streamlit as st
from google import genai
from google.genai import types

# ── Auth ──────────────────────────────────────────────────────────────────────

def _client():
    return genai.Client(api_key=st.secrets["GEMINI_API_KEY"])


FLASH = "gemini-2.0-flash"
PRO   = "gemini-2.5-pro-preview-06-05"

# ── Helpers ───────────────────────────────────────────────────────────────────

def _call(model: str, system: str, user: str, json_mode: bool = False) -> str:
    """Single-turn call. Returns raw text response."""
    client = _client()
    config = types.GenerateContentConfig(
        system_instruction=system,
        response_mime_type="application/json" if json_mode else "text/plain",
    )
    response = client.models.generate_content(
        model=model,
        contents=user,
        config=config,
    )
    return response.text.strip()


def _parse_json(text: str) -> dict:
    """Safely parse JSON from model response, stripping markdown fences if present."""
    cleaned = re.sub(r"```(?:json)?|```", "", text).strip()
    return json.loads(cleaned)


# ── 1. Ingredient parsing + per-unit estimation ───────────────────────────────

PARSE_SYSTEM = """Sen bir diyetisyen asistanısın. Türkçe yemek listesini malzeme malzeme ayır, BİRİM BAŞINA besin değerlerini ver.

KURALLAR:
- Her malzeme ayrı JSON objesi
- calories_per_unit = SADECE 1 birim için kalori, çarpma YAPMA
- "biraz" = makul küçük porsiyon varsay
- Asla 0 verme
- Sadece JSON array yaz, başka hiçbir şey ekleme, markdown kullanma

ÖRNEK ÇIKTI:
[{"ingredient":"yumurta","quantity":2,"unit":"adet","calories_per_unit":78,"protein_per_unit":6.3,"carbs_per_unit":0.6,"fat_per_unit":5.3,"confidence":"high"},{"ingredient":"beyaz peynir","quantity":1,"unit":"dilim","calories_per_unit":80,"protein_per_unit":4.2,"carbs_per_unit":1.2,"fat_per_unit":6.3,"confidence":"medium"}]"""

SINGLE_ESTIMATE_SYSTEM = """Sen bir diyetisyen asistanısın. Verilen tek malzeme için BİRİM BAŞINA besin değeri ver.

Sadece tek bir JSON objesi döndür, markdown veya açıklama ekleme:
{"calories_per_unit":78,"protein_per_unit":6.3,"carbs_per_unit":0.6,"fat_per_unit":5.3,"confidence":"high"}"""


def _robust_parse_array(text: str) -> list | None:
    """
    Tries multiple strategies to extract a JSON array from model output.
    Returns list or None if all strategies fail.
    """
    # Strategy 1: strip fences, direct parse
    cleaned = re.sub(r"```(?:json)?|```", "", text).strip()
    try:
        data = json.loads(cleaned)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("items", "ingredients", "malzemeler", "foods", "data"):
                if isinstance(data.get(key), list):
                    return data[key]
            return [data]
    except Exception:
        pass

    # Strategy 2: extract first [...] block from text
    m = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, list):
                return data
        except Exception:
            pass

    return None


def parse_ingredients(description: str) -> list[dict]:
    """
    Parses free-text meal into components. Each has per-unit values only.
    App does all multiplication. Never returns empty list.
    """
    try:
        # Use text/plain — more reliable than json_mode for arrays
        raw  = _call(FLASH, PARSE_SYSTEM, description, json_mode=False)
        data = _robust_parse_array(raw)

        if not data:
            return _fallback_single(description, error=f"Parse failed: {raw[:120]}")

        validated = []
        for item in data:
            if not isinstance(item, dict):
                continue
            cal = float(item.get("calories_per_unit") or 0)
            if cal == 0:
                continue   # skip empty rows — don't add zero-calorie noise
            validated.append({
                "ingredient":        str(item.get("ingredient", "bilinmeyen")),
                "quantity":          float(item.get("quantity") or 1),
                "unit":              str(item.get("unit", "porsiyon")),
                "calories_per_unit": cal,
                "protein_per_unit":  float(item.get("protein_per_unit")  or 0),
                "carbs_per_unit":    float(item.get("carbs_per_unit")    or 0),
                "fat_per_unit":      float(item.get("fat_per_unit")      or 0),
                "confidence":        str(item.get("confidence", "medium")),
            })
        return validated if validated else _fallback_single(description, error="All items were zero")

    except Exception as e:
        return _fallback_single(description, error=str(e))


def estimate_single(ingredient: str, quantity: float, unit: str) -> dict:
    """
    Estimates nutrition for ONE ingredient only.
    Used by food_db.lookup_single() as the AI tier — no re-parsing.
    Returns per-unit values; caller multiplies by quantity.
    """
    prompt = f"{quantity} {unit} {ingredient}"
    try:
        raw     = _call(FLASH, SINGLE_ESTIMATE_SYSTEM, prompt, json_mode=False)
        cleaned = re.sub(r"```(?:json)?|```", "", raw).strip()
        # Extract first {...} block
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if m:
            data = json.loads(m.group(0))
            return {
                "calories_per_unit": float(data.get("calories_per_unit") or 0),
                "protein_per_unit":  float(data.get("protein_per_unit")  or 0),
                "carbs_per_unit":    float(data.get("carbs_per_unit")    or 0),
                "fat_per_unit":      float(data.get("fat_per_unit")      or 0),
                "confidence":        str(data.get("confidence", "medium")),
                "error":             False,
            }
    except Exception:
        pass
    return {
        "calories_per_unit": 0.0,
        "protein_per_unit":  0.0,
        "carbs_per_unit":    0.0,
        "fat_per_unit":      0.0,
        "confidence":        "low",
        "error":             True,
    }


def _fallback_single(description: str, error: str = "") -> list[dict]:
    """Single zero-value fallback so the app never crashes."""
    return [{
        "ingredient":        description,
        "quantity":          1,
        "unit":              "porsiyon",
        "calories_per_unit": 0.0,
        "protein_per_unit":  0.0,
        "carbs_per_unit":    0.0,
        "fat_per_unit":      0.0,
        "confidence":        "low",
        "error":             error or True,
    }]


def estimate_nutrition(description: str) -> dict:
    """Kept for compatibility. Sums parse_ingredients — math in Python."""
    components = parse_ingredients(description)
    calories   = sum(c["calories_per_unit"]  * c["quantity"] for c in components)
    protein_g  = sum(c["protein_per_unit"]   * c["quantity"] for c in components)
    carbs_g    = sum(c["carbs_per_unit"]     * c["quantity"] for c in components)
    fat_g      = sum(c["fat_per_unit"]       * c["quantity"] for c in components)
    has_error  = any(c.get("error") for c in components)
    confidence = (
        "low"  if has_error else
        "high" if all(c["confidence"] == "high" for c in components) else
        "medium"
    )
    return {
        "calories":   round(calories,  1),
        "protein_g":  round(protein_g, 1),
        "carbs_g":    round(carbs_g,   1),
        "fat_g":      round(fat_g,     1),
        "confidence": confidence,
        "note":       "",
        "error":      has_error,
        "components": components,
    }


# ── 2. Weekly insight summary ─────────────────────────────────────────────────

WEEKLY_SYSTEM = """
Sen samimi, dürüst bir diyetisyen koçsun. Kullanıcı 20 yıldır kilo vermeye çalışıyor
ama sürekli başlıyor ve bırakıyor. Görevin: haftalık verileri analiz edip gerçekçi,
düşündürücü bir özet sunmak.

KURALLAR:
- Yalan söyleme. Kötü bir hafta kötüydü — bunu yumuşatma.
- Ama yargılama da. Soru sor, yargı bildirme.
- Örüntüleri isimlendir: "Bu hafta hep akşam 21:00 sonrası aşım var" gibi.
- Türkçe yaz. Samimi, kısa cümleler. Maksimum 5 cümle.
- Bir soru ile bitir — kullanıcıyı düşündürecek.
"""

def weekly_summary(
    name: str,
    goal_calories: float,
    daily_data: list[dict],      # [{date, total_calories}, ...]
    meal_stats: list[dict],      # [{meal_type, days_logged, avg_log_hour}, ...]
    hourly_data: list[dict],     # [{hour, entry_count, total_calories}, ...]
    streak: int,
) -> str:
    """
    Returns a 3–5 sentence Turkish weekly narrative from Gemini Pro.
    Falls back to a plain string on error.
    """
    exceeded = [d for d in daily_data if d["total_calories"] > goal_calories and d["total_calories"] > 0]
    empty    = [d for d in daily_data if d["total_calories"] == 0]
    avg_over = (
        sum(d["total_calories"] - goal_calories for d in exceeded) / len(exceeded)
        if exceeded else 0
    )
    late_hrs = [h for h in hourly_data if h["hour"] >= 21 and h["entry_count"] >= 2]

    meal_map = {r["meal_type"]: r for r in meal_stats}
    breakfast_days = meal_map.get("breakfast", {}).get("days_logged", 0)
    dinner_avg_h   = meal_map.get("dinner",    {}).get("avg_log_hour", None)

    prompt = f"""
Kullanıcı adı: {name}
Günlük kalori hedefi: {goal_calories:.0f} kcal
Ardışık hedef içi gün serisi: {streak}

Son 7 günlük özet:
- Toplam gün: 7
- Hedef aşılan gün: {len(exceeded)} (ortalama {avg_over:.0f} kcal fazla)
- Hiç kayıt girilmeyen gün: {len(empty)}
- Hedef dahilinde geçen gün: {7 - len(exceeded) - len(empty)}

Öğün düzeni (7 günde kaç gün girildi):
- Kahvaltı: {breakfast_days}/7 gün
- Öğle: {meal_map.get('lunch', {}).get('days_logged', 0)}/7 gün
- Akşam: {meal_map.get('dinner', {}).get('days_logged', 0)}/7 gün (ortalama giriş saati: {f'{dinner_avg_h:.0f}:00' if dinner_avg_h else 'bilinmiyor'})
- Ara öğün: {meal_map.get('snack', {}).get('days_logged', 0)}/7 gün

Gece yeme (21:00+): {'Var — ' + str(sum(h['entry_count'] for h in late_hrs)) + ' giriş' if late_hrs else 'Yok'}

Bu verilere dayanarak haftalık özet yaz.
""".strip()

    try:
        return _call(PRO, WEEKLY_SYSTEM, prompt)
    except Exception as e:
        return f"Haftalık özet yüklenemedi: {str(e)}"


# ── 3. Crisis / late-night intervention ───────────────────────────────────────

CRISIS_SYSTEM = """
Sen gece geç saatte acıkan, diyet yapan birine yardım eden diyetisyen koçsun.
Kullanıcı seni gece geç saatte çağırdı — açken bir şeyler yemek üzere ya da
diyetini bozmak üzere.

KURALLAR:
- Yargılama, anlayış göster — ama yalan da söyleme.
- Bugünkü kalori durumunu biliyorsun, bunu kullan.
- Eğer hedef dolmuşsa: yemek yerine ne yapabileceğini somut öner (su, yürüyüş, çay).
- Eğer hedefte alan varsa: sağlıklı düşük kalorili seçenekler öner.
- Kısa ve pratik yaz. Maksimum 4 cümle. Türkçe.
"""

def crisis_intervention(
    name: str,
    user_message: str,
    goal_calories: float,
    today_calories: float,
    today_logs: list[dict],   # [{meal_type, description, calories}, ...]
    hour: int,
) -> str:
    """
    Returns a short, honest Turkish response for late-night hunger or diet crisis.
    Falls back gracefully on error.
    """
    remaining    = goal_calories - today_calories
    log_summary  = "\n".join(
        f"  - {e['meal_type']}: {e['description']} ({e['calories']:.0f} kcal)"
        for e in today_logs
    ) or "  (bugün hiç kayıt yok)"

    prompt = f"""
Kullanıcı adı: {name}
Saat: {hour:02d}:00
Günlük hedef: {goal_calories:.0f} kcal
Bugün alınan: {today_calories:.0f} kcal
Kalan: {remaining:.0f} kcal ({'fazla' if remaining < 0 else 'kalan'})

Bugün yenenler:
{log_summary}

Kullanıcının mesajı: "{user_message}"

Yanıtla.
""".strip()

    try:
        return _call(PRO, CRISIS_SYSTEM, prompt)
    except Exception as e:
        return f"Şu an yanıt veremiyorum: {str(e)}"