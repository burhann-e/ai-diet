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


FLASH = "gemini-2.5-flash"
PRO   = "gemini-2.5-flash"

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


# ── 1. Calorie & macro estimation ─────────────────────────────────────────────

NUTRITION_SYSTEM = """
Sen bir diyetisyen asistanısın. Kullanıcı Türkçe yemek tarifi veya yediklerini yazar.
Görüün: porsiyon büyüklüğü dahil kalori ve makro besin değerlerini tahmin et.

KURALLAR:
- Türk mutfağını iyi biliyorsun (köfte, pilav, börek, çorba, vs.)
- Eğer miktar belirsizse (örn. "biraz", "bir tabak") makul bir porsiyon varsay
- Asla 0 döndürme — her zaman makul bir tahmin ver
- Sadece JSON dön, başka hiçbir şey yazma

ÇIKTI FORMATI (kesinlikle bu yapıda):
{
  "calories": 450,
  "protein_g": 28,
  "carbs_g": 42,
  "fat_g": 14,
  "confidence": "high" | "medium" | "low",
  "note": "Porsiyon belirsizdi, orta boy tabak varsayıldı."
}
"""

def estimate_nutrition(description: str) -> dict:
    """
    Estimates calories and macros from a free-text food description.
    Returns dict with keys: calories, protein_g, carbs_g, fat_g, confidence, note
    Falls back to safe defaults on any error so the app never breaks.
    """
    try:
        raw  = _call(FLASH, NUTRITION_SYSTEM, description, json_mode=True)
        data = _parse_json(raw)

        # Validate required fields exist and are numeric
        for key in ("calories", "protein_g", "carbs_g", "fat_g"):
            data[key] = float(data.get(key) or 0)

        data.setdefault("confidence", "medium")
        data.setdefault("note", "")
        return data

    except Exception as e:
        return {
            "calories":   0.0,
            "protein_g":  0.0,
            "carbs_g":    0.0,
            "fat_g":      0.0,
            "confidence": "low",
            "note":       f"Tahmin yapılamadı: {str(e)}",
            "error":      True,
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
