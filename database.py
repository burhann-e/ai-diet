from datetime import date, datetime, timedelta
import streamlit as st
from supabase import create_client, Client
 
 
# ── Client (cached so we reuse one connection per session) ────────────────────
 
@st.cache_resource
def _db() -> Client:
    return create_client(
        st.secrets["SUPABASE_URL"],
        st.secrets["SUPABASE_KEY"],
    )
 
 
def init_db():
    """No-op for Supabase — tables are created via SQL editor. Kept for compatibility."""
    pass
 
 
# ── user_profile ──────────────────────────────────────────────────────────────
 
def get_profile() -> dict | None:
    res = _db().table("user_profile").select("*").eq("id", 1).execute()
    return res.data[0] if res.data else None
 
 
def save_profile(name, birth_year, height_cm, goal_calories, goal_weight_kg):
    _db().table("user_profile").upsert({
        "id":             1,
        "name":           name,
        "birth_year":     birth_year,
        "height_cm":      height_cm,
        "goal_calories":  goal_calories,
        "goal_weight_kg": goal_weight_kg,
    }).execute()
 
 
# ── weight_logs ───────────────────────────────────────────────────────────────
 
def log_weight(weight_kg, note=None, log_date=None):
    log_date = log_date or date.today().isoformat()
    _db().table("weight_logs").insert({
        "date":      log_date,
        "weight_kg": weight_kg,
        "note":      note,
    }).execute()
 
 
def get_weight_logs(limit=30) -> list[dict]:
    res = (
        _db().table("weight_logs")
        .select("*")
        .order("date", desc=True)
        .limit(limit)
        .execute()
    )
    return res.data or []
 
 
def delete_weight_log(log_id):
    _db().table("weight_logs").delete().eq("id", log_id).execute()
 
 
# ── food_logs ─────────────────────────────────────────────────────────────────
 
def log_food(meal_type, description, calories=None,
             protein_g=None, carbs_g=None, fat_g=None, log_date=None) -> dict:
    log_date = log_date or date.today().isoformat()
    res = _db().table("food_logs").insert({
        "date":        log_date,
        "meal_type":   meal_type,
        "description": description,
        "calories":    calories,
        "protein_g":   protein_g,
        "carbs_g":     carbs_g,
        "fat_g":       fat_g,
    }).execute()
    return res.data[0] if res.data else {}
 
 
def update_food_macros(log_id, calories, protein_g, carbs_g, fat_g):
    """Called after AI estimates nutrition for an entry."""
    _db().table("food_logs").update({
        "calories":  calories,
        "protein_g": protein_g,
        "carbs_g":   carbs_g,
        "fat_g":     fat_g,
    }).eq("id", log_id).execute()
 
 
def update_food_calories(log_id, calories):
    """User manually overrides calorie estimate."""
    _db().table("food_logs").update({"calories": calories}).eq("id", log_id).execute()
 
 
def get_food_logs_by_date(log_date=None) -> list[dict]:
    log_date = log_date or date.today().isoformat()
    res = (
        _db().table("food_logs")
        .select("*")
        .eq("date", log_date)
        .order("created_at", desc=False)
        .execute()
    )
    return res.data or []
 
 
def get_total_calories_by_date(log_date=None) -> float:
    log_date = log_date or date.today().isoformat()
    res = (
        _db().table("food_logs")
        .select("calories")
        .eq("date", log_date)
        .execute()
    )
    return sum(r["calories"] or 0 for r in (res.data or []))
 
 
def delete_food_log(log_id):
    _db().table("food_logs").delete().eq("id", log_id).execute()
 
 
# ── Weekly insights ───────────────────────────────────────────────────────────
 
def get_daily_calories_last_n_days(n=7) -> list[dict]:
    """Returns {date, total_calories} for last n days. Missing days filled with 0."""
    since = (date.today() - timedelta(days=n - 1)).isoformat()
    res = (
        _db().table("food_logs")
        .select("date, calories")
        .gte("date", since)
        .execute()
    )
 
    totals: dict[str, float] = {}
    for row in (res.data or []):
        totals[row["date"]] = totals.get(row["date"], 0.0) + (row["calories"] or 0)
 
    filled = []
    for i in range(n - 1, -1, -1):
        d = (date.today() - timedelta(days=i)).isoformat()
        filled.append({"date": d, "total_calories": totals.get(d, 0.0)})
    return filled
 
 
def get_hourly_eating_pattern(n_days=14) -> list[dict]:
    """Returns {hour, entry_count, total_calories} grouped by hour across last n_days."""
    since = (date.today() - timedelta(days=n_days - 1)).isoformat()
    res = (
        _db().table("food_logs")
        .select("created_at, calories")
        .gte("date", since)
        .execute()
    )
 
    buckets: dict[int, dict] = {}
    for row in (res.data or []):
        if not row.get("created_at"):
            continue
        try:
            # Supabase returns timestamptz as ISO string
            hour = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00")).hour
        except Exception:
            continue
        if hour not in buckets:
            buckets[hour] = {"hour": hour, "entry_count": 0, "total_calories": 0.0}
        buckets[hour]["entry_count"]    += 1
        buckets[hour]["total_calories"] += row["calories"] or 0
 
    return sorted(buckets.values(), key=lambda x: x["hour"])
 
 
def get_meal_type_stats(n_days=7) -> list[dict]:
    """Returns days_logged and avg_log_hour per meal_type over last n_days."""
    since = (date.today() - timedelta(days=n_days - 1)).isoformat()
    res = (
        _db().table("food_logs")
        .select("meal_type, date, created_at")
        .gte("date", since)
        .execute()
    )
 
    stats: dict[str, dict] = {}
    for row in (res.data or []):
        mt = row["meal_type"]
        if mt not in stats:
            stats[mt] = {"meal_type": mt, "dates": set(), "hours": []}
        stats[mt]["dates"].add(row["date"])
        if row.get("created_at"):
            try:
                hour = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00")).hour
                stats[mt]["hours"].append(hour)
            except Exception:
                pass
 
    result = []
    for mt, s in stats.items():
        avg_hour = sum(s["hours"]) / len(s["hours"]) if s["hours"] else None
        result.append({
            "meal_type":    mt,
            "days_logged":  len(s["dates"]),
            "avg_log_hour": avg_hour,
        })
    return result
 
 
def get_consecutive_goal_days(goal_calories: float) -> int:
    """Streak of consecutive days within goal, counting back from yesterday."""
    since     = (date.today() - timedelta(days=30)).isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    res = (
        _db().table("food_logs")
        .select("date, calories")
        .gte("date", since)
        .lte("date", yesterday)
        .execute()
    )
 
    totals: dict[str, float] = {}
    for row in (res.data or []):
        totals[row["date"]] = totals.get(row["date"], 0.0) + (row["calories"] or 0)
 
    streak   = 0
    expected = date.today() - timedelta(days=1)
    for _ in range(30):
        d = expected.isoformat()
        if d not in totals:
            break
        if totals[d] > goal_calories:
            break
        streak  += 1
        expected -= timedelta(days=1)
    return streak
