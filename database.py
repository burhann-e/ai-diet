import sqlite3
from datetime import date

DB_PATH = "health_tracker.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    c = conn.cursor()

    c.executescript("""
        CREATE TABLE IF NOT EXISTS user_profile (
            id              INTEGER PRIMARY KEY,
            name            TEXT,
            birth_year      INTEGER,
            height_cm       REAL,
            goal_calories   REAL,
            goal_weight_kg  REAL
        );

        CREATE TABLE IF NOT EXISTS weight_logs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            date        TEXT NOT NULL,
            weight_kg   REAL NOT NULL,
            note        TEXT,
            created_at  TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS food_logs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            date        TEXT NOT NULL,
            meal_type   TEXT NOT NULL,
            description TEXT NOT NULL,
            calories    REAL,
            protein_g   REAL,
            carbs_g     REAL,
            fat_g       REAL,
            created_at  TEXT DEFAULT (datetime('now'))
        );
    """)

    conn.commit()
    conn.close()


# ── user_profile ──────────────────────────────────────────────────────────────

def get_profile():
    conn = get_connection()
    row = conn.execute("SELECT * FROM user_profile WHERE id = 1").fetchone()
    conn.close()
    return dict(row) if row else None


def save_profile(name, birth_year, height_cm, goal_calories, goal_weight_kg):
    conn = get_connection()
    conn.execute("""
        INSERT INTO user_profile (id, name, birth_year, height_cm, goal_calories, goal_weight_kg)
        VALUES (1, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name           = excluded.name,
            birth_year     = excluded.birth_year,
            height_cm      = excluded.height_cm,
            goal_calories  = excluded.goal_calories,
            goal_weight_kg = excluded.goal_weight_kg
    """, (name, birth_year, height_cm, goal_calories, goal_weight_kg))
    conn.commit()
    conn.close()


# ── weight_logs ───────────────────────────────────────────────────────────────

def log_weight(weight_kg, note=None, log_date=None):
    log_date = log_date or date.today().isoformat()
    conn = get_connection()
    conn.execute(
        "INSERT INTO weight_logs (date, weight_kg, note) VALUES (?, ?, ?)",
        (log_date, weight_kg, note)
    )
    conn.commit()
    conn.close()


def get_weight_logs(limit=30):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM weight_logs ORDER BY date DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_weight_log(log_id):
    conn = get_connection()
    conn.execute("DELETE FROM weight_logs WHERE id = ?", (log_id,))
    conn.commit()
    conn.close()


# ── food_logs ─────────────────────────────────────────────────────────────────

def log_food(meal_type, description, calories=None,
             protein_g=None, carbs_g=None, fat_g=None, log_date=None):
    log_date = log_date or date.today().isoformat()
    conn = get_connection()
    conn.execute("""
        INSERT INTO food_logs (date, meal_type, description, calories, protein_g, carbs_g, fat_g)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (log_date, meal_type, description, calories, protein_g, carbs_g, fat_g))
    conn.commit()
    conn.close()


def update_food_macros(log_id, calories, protein_g, carbs_g, fat_g):
    """Called after AI estimates nutrition for an entry."""
    conn = get_connection()
    conn.execute("""
        UPDATE food_logs
        SET calories = ?, protein_g = ?, carbs_g = ?, fat_g = ?
        WHERE id = ?
    """, (calories, protein_g, carbs_g, fat_g, log_id))
    conn.commit()
    conn.close()


def update_food_calories(log_id, calories):
    """User manually overrides calorie estimate."""
    conn = get_connection()
    conn.execute("UPDATE food_logs SET calories = ? WHERE id = ?", (calories, log_id))
    conn.commit()
    conn.close()


def get_food_logs_by_date(log_date=None):
    log_date = log_date or date.today().isoformat()
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM food_logs WHERE date = ? ORDER BY created_at ASC", (log_date,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_total_calories_by_date(log_date=None):
    log_date = log_date or date.today().isoformat()
    conn = get_connection()
    row = conn.execute(
        "SELECT SUM(calories) as total FROM food_logs WHERE date = ?", (log_date,)
    ).fetchone()
    conn.close()
    return row["total"] or 0.0


def delete_food_log(log_id):
    conn = get_connection()
    conn.execute("DELETE FROM food_logs WHERE id = ?", (log_id,))
    conn.commit()
    conn.close()


# ── Weekly insights ───────────────────────────────────────────────────────────

def get_daily_calories_last_n_days(n=7):
    """Returns {date, total_calories} for last n days. Missing days filled with 0."""
    from datetime import timedelta
    conn = get_connection()
    rows = conn.execute("""
        SELECT date, SUM(calories) as total_calories
        FROM food_logs
        WHERE date >= date('now', ?)
        GROUP BY date
        ORDER BY date ASC
    """, (f"-{n-1} days",)).fetchall()
    conn.close()

    result_map = {r["date"]: r["total_calories"] or 0.0 for r in rows}
    today = date.today()
    filled = []
    for i in range(n - 1, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        filled.append({"date": d, "total_calories": result_map.get(d, 0.0)})
    return filled


def get_hourly_eating_pattern(n_days=14):
    """Returns {hour, entry_count, total_calories} grouped by hour across last n_days."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT
            CAST(strftime('%H', created_at) AS INTEGER) as hour,
            COUNT(*) as entry_count,
            SUM(calories) as total_calories
        FROM food_logs
        WHERE date >= date('now', ?)
          AND created_at IS NOT NULL
        GROUP BY hour
        ORDER BY hour ASC
    """, (f"-{n_days-1} days",)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_meal_type_stats(n_days=7):
    """Returns days_logged and avg_log_hour per meal_type over last n_days."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT
            meal_type,
            COUNT(DISTINCT date) as days_logged,
            AVG(CAST(strftime('%H', created_at) AS INTEGER)) as avg_log_hour
        FROM food_logs
        WHERE date >= date('now', ?)
        GROUP BY meal_type
    """, (f"-{n_days-1} days",)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_consecutive_goal_days(goal_calories: float) -> int:
    """Streak of consecutive days within goal, counting back from yesterday."""
    from datetime import timedelta
    conn = get_connection()
    rows = conn.execute("""
        SELECT date, SUM(calories) as total
        FROM food_logs
        WHERE date < date('now')
        GROUP BY date
        ORDER BY date DESC
        LIMIT 30
    """).fetchall()
    conn.close()

    streak = 0
    expected = date.today() - timedelta(days=1)
    for row in rows:
        row_date = date.fromisoformat(row["date"])
        if row_date != expected:
            break
        if (row["total"] or 0) > goal_calories:
            break
        streak += 1
        expected -= timedelta(days=1)
    return streak


if __name__ == "__main__":
    init_db()
    print("Database initialised successfully.")