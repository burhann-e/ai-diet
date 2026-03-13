import streamlit as st
from datetime import date, datetime
import database as db
import ai_agent as ai

# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Sağlık Takipçim",
    page_icon="🥗",
    layout="centered",
    initial_sidebar_state="expanded",
)

# ── Custom CSS (mobile-friendly, warm palette) ────────────────────────────────

st.markdown("""
<style>
    /* Warm, friendly palette */
    :root {
        --green:   #4CAF7D;
        --red:     #E05C5C;
        --orange:  #F4A261;
        --bg-card: #FAFAF8;
        --text:    #2D2D2D;
        --muted:   #888;
    }

    /* Larger tap targets on mobile */
    .stButton > button {
        width: 100%;
        border-radius: 12px;
        padding: 0.6rem 1rem;
        font-size: 1rem;
        font-weight: 600;
    }

    /* Metric cards */
    .metric-card {
        background: var(--bg-card);
        border-radius: 16px;
        padding: 1.2rem 1.4rem;
        margin-bottom: 0.8rem;
        border: 1px solid #ECECEC;
        box-shadow: 0 1px 4px rgba(0,0,0,0.05);
    }
    .metric-label {
        font-size: 0.78rem;
        color: var(--muted);
        text-transform: uppercase;
        letter-spacing: 0.06em;
        margin-bottom: 0.2rem;
    }
    .metric-value {
        font-size: 2rem;
        font-weight: 700;
        color: var(--text);
        line-height: 1.1;
    }
    .metric-sub {
        font-size: 0.85rem;
        color: var(--muted);
        margin-top: 0.2rem;
    }

    /* Progress bar */
    .progress-wrap {
        background: #ECECEC;
        border-radius: 99px;
        height: 14px;
        margin: 0.6rem 0;
        overflow: hidden;
    }
    .progress-fill {
        height: 100%;
        border-radius: 99px;
        transition: width 0.4s ease;
    }

    /* Food entry row */
    .food-row {
        background: var(--bg-card);
        border-radius: 12px;
        padding: 0.8rem 1rem;
        margin-bottom: 0.5rem;
        border: 1px solid #ECECEC;
        display: flex;
        justify-content: space-between;
        align-items: center;
    }
    .food-desc  { font-size: 0.95rem; font-weight: 500; }
    .food-meta  { font-size: 0.78rem; color: var(--muted); margin-top: 2px; }
    .food-kcal  { font-size: 1.1rem; font-weight: 700; color: var(--orange); white-space: nowrap; }

    /* Coach message box */
    .coach-box {
        background: linear-gradient(135deg, #E8F5EE 0%, #FFF8F0 100%);
        border-radius: 16px;
        padding: 1.1rem 1.3rem;
        border-left: 4px solid var(--green);
        margin-bottom: 1rem;
        font-size: 0.95rem;
        line-height: 1.6;
        color: var(--text);
    }
    .coach-icon { font-size: 1.4rem; margin-bottom: 0.4rem; }

    /* Section headers */
    .section-title {
        font-size: 0.72rem;
        font-weight: 700;
        letter-spacing: 0.1em;
        text-transform: uppercase;
        color: var(--muted);
        margin: 1.4rem 0 0.6rem;
    }

    /* Hide Streamlit branding */
    #MainMenu { visibility: hidden; }
    footer     { visibility: hidden; }
</style>
""", unsafe_allow_html=True)

# ── Bootstrap DB ──────────────────────────────────────────────────────────────

db.init_db()

# ── Helpers ───────────────────────────────────────────────────────────────────

MEAL_TYPES = {
    "🌅 Kahvaltı":  "breakfast",
    "☀️ Öğle":      "lunch",
    "🌙 Akşam":     "dinner",
    "🍎 Ara Öğün":  "snack",
}

def calorie_color(pct: float) -> str:
    if pct < 0.75:
        return "#4CAF7D"   # green — doing well
    elif pct < 1.0:
        return "#F4A261"   # orange — getting close
    else:
        return "#E05C5C"   # red — over goal

def coach_message(total_kcal: float, goal: float, name: str, logs: list) -> tuple[str, str]:
    """Returns (message, border_color). Proportional, reflective — not just cheerful."""
    pct   = total_kcal / goal if goal else 0
    over  = total_kcal - goal
    first = name.split()[0] if name else "Canım"
    hour  = datetime.now().hour

    # Late-night bulk logging signal: all entries created after 21:00
    bulk_logged_late = False
    if logs:
        late_count = sum(
            1 for e in logs
            if e.get("created_at") and e["created_at"][11:13].isdigit()
            and int(e["created_at"][11:13]) >= 21
        )
        bulk_logged_late = (len(logs) >= 3 and late_count == len(logs))

    GREEN  = "#4CAF7D"
    ORANGE = "#F4A261"
    RED    = "#E05C5C"

    # ── Not yet logged ─────────────────────────────────────────────────────────
    if total_kcal == 0:
        if hour < 11:
            return (f"Günaydın {first}! Bugüne nasıl başladın? Kahvaltını girdikten sonra günü birlikte takip edelim.", GREEN)
        elif hour < 15:
            return (f"Merhaba {first}. Öğle oldu ama bugün henüz hiçbir şey girmedin. Sabahtan beri ne yedin?", ORANGE)
        else:
            return (f"{first}, akşam oldu ve bugün hiç kayıt yok. Gün boyunca gerçekten hiç yemedin mi — yoksa girmeyi mi unuttun?", ORANGE)

    # ── Late-night bulk logging ────────────────────────────────────────────────
    if bulk_logged_late:
        return (f"{first}, bugünkü tüm girişler gece 21:00'den sonra yapılmış. Yemekleri o an mı yedin, yoksa günün sonunda mı girdin? Anlık girmek sana çok daha doğru bir tablo verir.", ORANGE)

    # ── Within goal ───────────────────────────────────────────────────────────
    if pct < 0.75:
        return (f"Günün {int(pct*100)}%'indesin {first}. İyi gidiyor — geri kalanı da dengeli tut.", GREEN)
    elif pct < 1.0:
        remaining = goal - total_kcal
        return (f"Hedefe {remaining:.0f} kcal kaldı {first}. Akşam yemeğinde buna dikkat edersen günü başarıyla kapatırsın.", GREEN)

    # ── Over goal — proportional ───────────────────────────────────────────────
    elif over <= 150:
        # Small overage — barely worth mentioning
        return (f"Bugün hedefini {over:.0f} kcal aştın {first}. Küçük bir fark — yarın bunu telafi etmek zor olmaz.", ORANGE)

    elif over <= 500:
        # Meaningful overage — name it, ask a question
        return (f"Bugün hedefinin {over:.0f} kcal üzerine çıktın {first}. Bu hafta bu kaçıncı kez oldu? Bir örüntü var mı sence?", ORANGE)

    elif over <= 1000:
        # Significant — ask what happened, no comfort
        return (f"Bugün {over:.0f} kcal fazla girdin {first}. Bu rastlantı mı, yoksa bu saatlerde genellikle böyle mi oluyor? Ne tetikledi?", RED)

    else:
        # Large overage — honest, no softening, reflective question
        return (f"{first}, bugün {over:.0f} kcal fazla girdin — bu hedefinin çok üzerinde. Bir an dur: bugün ne oldu? Bunu anlamak, yarın aynı şeyi yaşamamak için önemli.", RED)

VAGUE_WORDS = [
    "bir şeyler", "biraz", "az", "birkaç", "falan", "filan",
    "vs", "vs.", "şeyler", "atıştırma", "bir şey", "hafif",
    "küçük", "ufak", "bir parça", "bir lokma",
]

def check_suspicious_entry(description: str, calories: float, meal_type: str) -> str | None:
    """
    Returns a gentle clarifying question if the entry looks suspicious,
    or None if it looks fine. Does NOT block saving — just surfaces after.
    """
    desc_lower = description.lower().strip()
    word_count = len(desc_lower.split())

    # 1. Too vague — single word or known vague phrases
    if word_count <= 2 or any(v in desc_lower for v in VAGUE_WORDS):
        return f"'{description}' biraz genel kaldı. Biraz daha detay verebilir misin? Örneğin porsiyon büyüklüğü veya yanındaki yiyecekler."

    # 2. Suspiciously round and low calories for a main meal
    if meal_type in ("lunch", "dinner") and calories < 200:
        return f"Öğle/akşam yemeği için {calories:.0f} kcal oldukça düşük görünüyor. Gerçekten bu kadardı — yoksa bir şeyleri atladın mı?"

    # 3. Round number that looks estimated (exactly 100, 200, 300…)
    if calories > 0 and calories % 100 == 0 and calories <= 500:
        return f"{calories:.0f} kcal tam yuvarlak bir sayı. Bunu nasıl hesapladın? Daha yakın bir tahmin girebilirsen takip daha doğru olur."

    return None


def bmi_label(bmi: float) -> str:
    if bmi < 18.5: return "Zayıf"
    elif bmi < 25: return "Normal"
    elif bmi < 30: return "Fazla kilolu"
    else:          return "Obez"

# ── Sidebar navigation ────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## 🥗 Sağlık Takipçim")
    st.markdown("---")
    page = st.radio(
        "Menü",
        ["🏠 Ana Sayfa", "🍽️ Yemek Ekle", "⚖️ Kilo Ekle", "📊 Geçmiş", "📈 Haftalık", "👤 Profil"],
        label_visibility="collapsed",
    )
    st.markdown("---")
    today_str = date.today().strftime("%d %B %Y")
    st.caption(f"📅 {today_str}")

# ═══════════════════════════════════════════════════════════════════════════════
# PAGE: Ana Sayfa (Home)
# ═══════════════════════════════════════════════════════════════════════════════

if page == "🏠 Ana Sayfa":
    profile = db.get_profile()
    total   = db.get_total_calories_by_date()
    goal    = profile["goal_calories"] if profile else 2000.0
    pct     = min(total / goal, 1.2) if goal else 0
    color   = calorie_color(total / goal if goal else 0)
    name    = profile["name"] if profile else "Canım"

    today_logs = db.get_food_logs_by_date()

    # Coach message
    msg, border_color = coach_message(total, goal, name, today_logs)
    st.markdown(f"""
    <div class="coach-box" style="border-left-color:{border_color}">
        {msg}
    </div>
    """, unsafe_allow_html=True)

    # Calorie progress card
    st.markdown('<div class="section-title">Bugünkü Kalori / Today\'s Calories</div>', unsafe_allow_html=True)
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">Alınan / Hedef</div>
        <div class="metric-value" style="color:{color}">{total:.0f} <span style="font-size:1rem;font-weight:400;color:#888">/ {goal:.0f} kcal</span></div>
        <div class="progress-wrap">
            <div class="progress-fill" style="width:{pct*100:.1f}%; background:{color};"></div>
        </div>
        <div class="metric-sub">{total/goal*100:.0f}% kullanıldı &nbsp;·&nbsp; {max(goal-total,0):.0f} kcal kaldı</div>
    </div>
    """, unsafe_allow_html=True)

    # Latest weight
    weight_logs = db.get_weight_logs(limit=2)
    if weight_logs:
        latest = weight_logs[0]
        st.markdown('<div class="section-title">Son Kilo / Latest Weight</div>', unsafe_allow_html=True)

        delta_str = ""
        if len(weight_logs) == 2:
            diff = latest["weight_kg"] - weight_logs[1]["weight_kg"]
            arrow = "▲" if diff > 0 else "▼"
            delta_str = f"&nbsp;·&nbsp; {arrow} {abs(diff):.1f} kg önceki ölçüme göre"

        # BMI if profile exists
        bmi_str = ""
        if profile and profile.get("height_cm"):
            h = profile["height_cm"] / 100
            bmi = latest["weight_kg"] / (h * h)
            bmi_str = f"&nbsp;·&nbsp; BMI {bmi:.1f} ({bmi_label(bmi)})"

        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-label">Kilo</div>
            <div class="metric-value">{latest['weight_kg']} <span style="font-size:1rem;font-weight:400;color:#888">kg</span></div>
            <div class="metric-sub">{latest['date']}{delta_str}{bmi_str}</div>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.info("Henüz kilo girişi yok. ⚖️ Kilo Ekle sayfasından başlayabilirsin!")

    # ── Crisis / late-night chat ───────────────────────────────────────────────
    st.markdown('<div class="section-title">🆘 Koça Sor / Ask the Coach</div>', unsafe_allow_html=True)
    st.caption("Gece acıktın mı? Diyet bunalımında mısın? Yaz, yanıt verelim.")

    crisis_input = st.text_input(
        "",
        placeholder="Canım tatlı istiyor ne yapabilirim?",
        label_visibility="collapsed",
        key="crisis_input",
    )
    if st.button("💬 Sor / Ask", use_container_width=True) and crisis_input.strip():
        with st.spinner("Koç düşünüyor..."):
            reply = ai.crisis_intervention(
                name           = name,
                user_message   = crisis_input.strip(),
                goal_calories  = goal,
                today_calories = total,
                today_logs     = today_logs,
                hour           = datetime.now().hour,
            )
        st.markdown(f"""
        <div class="coach-box" style="border-left-color:#7C6AF7; margin-top:0.6rem">
            🤖 {reply}
        </div>""", unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════════════════
# PAGE: Yemek Ekle (Log Food)
# ═══════════════════════════════════════════════════════════════════════════════

elif page == "🍽️ Yemek Ekle":
    st.markdown("## 🍽️ Yemek Ekle")
    st.caption("Ne yedin? Hızlıca yaz, kaydet — bitti!")

    with st.form("food_form", clear_on_submit=True):
        meal_label = st.selectbox("Öğün / Meal", list(MEAL_TYPES.keys()))
        description = st.text_area(
            "Ne yedin? / What did you eat?",
            placeholder="Mesela 2 köfte, pilav, bir bardak ayran ve bir küçük kase salata",
            height=90,
        )
        calories = st.number_input(
            "Kalori (isteğe bağlı — AI tahmin edecek / optional — AI will estimate)",
            min_value=0,
            max_value=5000,
            step=10,
            value=0,
            help="Boş bırakabilirsin, AI hesaplar. Biliyorsan gir, AI düzeltir.",
        )
        submitted = st.form_submit_button("✅ Kaydet / Save", use_container_width=True)

    if submitted:
        if not description.strip():
            st.warning("Lütfen ne yediğini yaz! / Please describe what you ate.")
        else:
            meal_type = MEAL_TYPES[meal_label]

            # Save immediately with user-entered calories (may be 0)
            db.log_food(
                meal_type=meal_type,
                description=description.strip(),
                calories=float(calories) if calories else None,
            )

            # Get the ID of the row we just inserted
            today_entries = db.get_food_logs_by_date()
            last_entry    = today_entries[-1] if today_entries else None

            # AI macro estimation — spinner while it runs
            with st.spinner("🤖 Besin değerleri hesaplanıyor..."):
                nutrition = ai.estimate_nutrition(description.strip())

            if last_entry and not nutrition.get("error"):
                db.update_food_macros(
                    log_id    = last_entry["id"],
                    calories  = nutrition["calories"],
                    protein_g = nutrition["protein_g"],
                    carbs_g   = nutrition["carbs_g"],
                    fat_g     = nutrition["fat_g"],
                )
                conf_icon = {"high": "✅", "medium": "🟡", "low": "⚠️"}.get(nutrition["confidence"], "🟡")
                st.success(f"Kaydedildi — {nutrition['calories']:.0f} kcal {conf_icon}")
                if nutrition.get("note"):
                    st.caption(f"💬 {nutrition['note']}")

                # Show macro breakdown
                p, c, f = nutrition["protein_g"], nutrition["carbs_g"], nutrition["fat_g"]
                st.markdown(f"""
                <div class="metric-card" style="padding:0.6rem 1rem">
                    <div style="display:flex; gap:1.5rem; font-size:0.88rem">
                        <span>🥩 Protein <strong>{p:.0f}g</strong></span>
                        <span>🍞 Karb <strong>{c:.0f}g</strong></span>
                        <span>🧈 Yağ <strong>{f:.0f}g</strong></span>
                    </div>
                </div>""", unsafe_allow_html=True)
            elif nutrition.get("error"):
                saved_cal = float(calories) if calories else 0
                st.success(f"Kaydedildi — {saved_cal:.0f} kcal (AI tahmin başarısız)")

            # Suspicion check — runs after save regardless
            final_cal = nutrition.get("calories") or float(calories) or 0
            suspicion = check_suspicious_entry(description.strip(), final_cal, meal_type)
            if suspicion:
                st.info(f"💬 {suspicion}")

    # Show today's entries below the form
    today_logs = db.get_food_logs_by_date()
    if today_logs:
        st.markdown('<div class="section-title">Bugün Girilenler / Today\'s Entries</div>', unsafe_allow_html=True)
        total = db.get_total_calories_by_date()

        for entry in today_logs:
            meal_emoji = {"breakfast":"🌅","lunch":"☀️","dinner":"🌙","snack":"🍎"}.get(entry["meal_type"], "🍴")
            col1, col2 = st.columns([5, 1])
            with col1:
                st.markdown(f"""
                <div class="food-row">
                    <div>
                        <div class="food-desc">{meal_emoji} {entry['description']}</div>
                        <div class="food-meta">{entry['meal_type'].capitalize()} · girildi: {entry['created_at'][11:16]}</div>
                    </div>
                    <div class="food-kcal">{entry['calories']:.0f} kcal</div>
                </div>
                """, unsafe_allow_html=True)
            with col2:
                if st.button("🗑️", key=f"del_food_{entry['id']}", help="Sil"):
                    db.delete_food_log(entry["id"])
                    st.rerun()

        st.markdown(f"**Toplam bugün / Total today: {total:.0f} kcal**")

# ═══════════════════════════════════════════════════════════════════════════════
# PAGE: Kilo Ekle (Log Weight)
# ═══════════════════════════════════════════════════════════════════════════════

elif page == "⚖️ Kilo Ekle":
    st.markdown("## ⚖️ Kilo Ekle")
    st.caption("Her gün aynı saatte ölçmek en doğru sonucu verir.")

    with st.form("weight_form", clear_on_submit=True):
        weight = st.number_input(
            "Kilo / Weight (kg)",
            min_value=30.0,
            max_value=300.0,
            step=0.1,
            format="%.1f",
            value=70.0,
        )
        note = st.text_input(
            "Not / Note (isteğe bağlı / optional)",
            placeholder="örn: sabah aç karnına, spor sonrası",
        )
        submitted = st.form_submit_button("✅ Kaydet / Save", use_container_width=True)

    if submitted:
        db.log_weight(weight_kg=weight, note=note.strip() or None)
        st.success(f"{weight} kg kaydedildi! 💪")

    # Recent weight log
    logs = db.get_weight_logs(limit=10)
    if logs:
        st.markdown('<div class="section-title">Son Ölçümler / Recent Logs</div>', unsafe_allow_html=True)
        for entry in logs:
            col1, col2 = st.columns([5, 1])
            with col1:
                note_str = f" · {entry['note']}" if entry.get("note") else ""
                st.markdown(f"""
                <div class="metric-card" style="padding: 0.7rem 1rem; margin-bottom:0.4rem;">
                    <span style="font-weight:700;font-size:1.1rem">{entry['weight_kg']} kg</span>
                    <span style="color:#888;font-size:0.85rem"> &nbsp;·&nbsp; {entry['date']}{note_str}</span>
                </div>
                """, unsafe_allow_html=True)
            with col2:
                if st.button("🗑️", key=f"del_w_{entry['id']}", help="Sil"):
                    db.delete_weight_log(entry["id"])
                    st.rerun()

# ═══════════════════════════════════════════════════════════════════════════════
# PAGE: Geçmiş (History)
# ═══════════════════════════════════════════════════════════════════════════════

elif page == "📊 Geçmiş":
    st.markdown("## 📊 Geçmiş / History")

    tab1, tab2 = st.tabs(["⚖️ Kilo Grafiği", "🍽️ Yemek Geçmişi"])

    with tab1:
        logs = db.get_weight_logs(limit=30)
        if len(logs) >= 2:
            import pandas as pd
            df = pd.DataFrame(logs)[["date","weight_kg"]].sort_values("date")
            df.columns = ["Tarih", "Kilo (kg)"]
            st.line_chart(df.set_index("Tarih"))
        elif len(logs) == 1:
            st.info("Grafik için en az 2 ölçüm gerekli. Yarın tekrar ölç! 📈")
        else:
            st.info("Henüz kilo kaydı yok.")

    with tab2:
        selected_date = st.date_input("Tarih seç / Select date", value=date.today())
        food_logs = db.get_food_logs_by_date(selected_date.isoformat())
        if food_logs:
            total = sum(e["calories"] or 0 for e in food_logs)
            for entry in food_logs:
                meal_emoji = {"breakfast":"🌅","lunch":"☀️","dinner":"🌙","snack":"🍎"}.get(entry["meal_type"], "🍴")
                st.markdown(f"""
                <div class="food-row">
                    <div>
                        <div class="food-desc">{meal_emoji} {entry['description']}</div>
                        <div class="food-meta">{entry['meal_type'].capitalize()} · {entry['created_at'][:16]}</div>
                    </div>
                    <div class="food-kcal">{entry['calories']:.0f} kcal</div>
                </div>
                """, unsafe_allow_html=True)
            st.markdown(f"**Toplam / Total: {total:.0f} kcal**")
        else:
            st.info("Bu tarihte kayıt yok.")

# ═══════════════════════════════════════════════════════════════════════════════
# PAGE: Haftalık (Weekly Insights)
# ═══════════════════════════════════════════════════════════════════════════════

elif page == "📈 Haftalık":
    import pandas as pd

    st.markdown("## 📈 Haftalık Özet / Weekly Insights")
    st.caption("Saatler yemeği girdiğin zamana göre — anlık girdikçe daha doğru olur.")

    profile  = db.get_profile()
    goal     = profile["goal_calories"] if profile else 2000.0
    name     = profile["name"] if profile else "Canım"
    first    = name.split()[0] if name else "Canım"

    # ── 1. Streak ─────────────────────────────────────────────────────────────
    streak = db.get_consecutive_goal_days(goal)
    daily  = db.get_daily_calories_last_n_days(7)

    exceeded_days = [d for d in daily if d["total_calories"] > goal and d["total_calories"] > 0]
    perfect_days  = [d for d in daily if 0 < d["total_calories"] <= goal]
    empty_days    = [d for d in daily if d["total_calories"] == 0]
    avg_overage   = (
        sum(d["total_calories"] - goal for d in exceeded_days) / len(exceeded_days)
        if exceeded_days else 0
    )

    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown(f"""
        <div class="metric-card" style="text-align:center">
            <div class="metric-label">Seri / Streak</div>
            <div class="metric-value" style="color:#4CAF7D">{streak}</div>
            <div class="metric-sub">ardışık gün ✓</div>
        </div>""", unsafe_allow_html=True)
    with col2:
        color = "#E05C5C" if len(exceeded_days) >= 4 else "#F4A261" if len(exceeded_days) >= 2 else "#4CAF7D"
        st.markdown(f"""
        <div class="metric-card" style="text-align:center">
            <div class="metric-label">Aşılan Gün / 7</div>
            <div class="metric-value" style="color:{color}">{len(exceeded_days)}</div>
            <div class="metric-sub">ortalama +{avg_overage:.0f} kcal</div>
        </div>""", unsafe_allow_html=True)
    with col3:
        color = "#E05C5C" if len(empty_days) >= 3 else "#F4A261" if len(empty_days) >= 1 else "#4CAF7D"
        st.markdown(f"""
        <div class="metric-card" style="text-align:center">
            <div class="metric-label">Kayıtsız Gün</div>
            <div class="metric-value" style="color:{color}">{len(empty_days)}</div>
            <div class="metric-sub">bu hafta</div>
        </div>""", unsafe_allow_html=True)

    # ── 2. Daily calorie bar chart ─────────────────────────────────────────────
    st.markdown('<div class="section-title">Günlük Kalori / Daily Calories (7 gün)</div>', unsafe_allow_html=True)

    if any(d["total_calories"] > 0 for d in daily):
        df_daily = pd.DataFrame(daily)
        df_daily["Tarih"] = df_daily["date"].apply(lambda d: d[5:])  # MM-DD
        df_daily = df_daily.rename(columns={"total_calories": "Kalori (kcal)"})
        st.bar_chart(df_daily.set_index("Tarih")["Kalori (kcal)"])
        # Draw goal line as reference caption — Streamlit bar_chart can't overlay lines
        st.caption(f"Günlük hedefiniz: {goal:.0f} kcal")
    else:
        st.info("Bu hafta henüz yeterli veri yok.")

    # ── 3. Honest weekly narrative ─────────────────────────────────────────────
    st.markdown('<div class="section-title">Genel Değerlendirme / Weekly Read</div>', unsafe_allow_html=True)

    if len(exceeded_days) == 0 and len(empty_days) == 0:
        msg = f"Mükemmel bir hafta {first}! 7 günün hepsinde hedef dahilindeydin. Bu çok nadir — gerçekten takdire değer."
        border = "#4CAF7D"
    elif len(empty_days) >= 4:
        msg = f"{first}, bu hafta {len(empty_days)} gün hiç kayıt girilmedi. Girmeyen günlerde ne yendiğini bilmiyoruz — ama muhtemelen bir şeyler yenildi. Bu boşluklar resmi bozuyor."
        border = "#E05C5C"
    elif len(exceeded_days) >= 4:
        msg = f"Bu hafta {len(exceeded_days)} günde hedef aşıldı, ortalama {avg_overage:.0f} kcal fazla. Bu rastlantı değil — bir örüntü var. Hangi günler zor geçti? Saat bilgilerine bak, tekrar eden bir zaman var mı?"
        border = "#E05C5C"
    elif len(exceeded_days) >= 2:
        msg = f"{len(exceeded_days)} gün hedef aşıldı {first}. Ortalama {avg_overage:.0f} kcal fazla. Kötü değil ama {len(perfect_days)} iyi günü {len(exceeded_days)} zorlu gün dengeliyor — bu hafta denge kurmak işe yaradı mı?"
        border = "#F4A261"
    else:
        msg = f"Genel olarak iyi bir hafta {first}. {len(perfect_days)} gün hedef dahilinde, {len(exceeded_days)} gün aşıldı. Devam et."
        border = "#4CAF7D"

    st.markdown(f"""
    <div class="coach-box" style="border-left-color:{border}">
        {msg}
    </div>""", unsafe_allow_html=True)

    # ── 4. Hourly heatmap ──────────────────────────────────────────────────────
    st.markdown('<div class="section-title">Saate Göre Yeme Alışkanlığı / Eating by Hour (14 gün)</div>', unsafe_allow_html=True)

    hourly = db.get_hourly_eating_pattern(14)
    if hourly:
        # Build a full 0–23 hour grid
        hour_map = {r["hour"]: r for r in hourly}
        all_hours = []
        for h in range(24):
            row = hour_map.get(h, {"hour": h, "entry_count": 0, "total_calories": 0})
            all_hours.append({
                "Saat": f"{h:02d}:00",
                "Giriş Sayısı": row["entry_count"],
                "Toplam kcal": row["total_calories"] or 0,
            })

        df_hourly = pd.DataFrame(all_hours).set_index("Saat")
        st.bar_chart(df_hourly["Giriş Sayısı"])

        # Flag risky hours
        risky = [r for r in hourly if r["hour"] >= 21 and r["entry_count"] >= 2]
        if risky:
            total_late = sum(r["total_calories"] or 0 for r in risky)
            st.markdown(f"""
            <div class="coach-box" style="border-left-color:#E05C5C; margin-top:0.6rem">
                Gece 21:00 ve sonrasında son 2 haftada <strong>{sum(r['entry_count'] for r in risky)}</strong> giriş yapılmış
                — toplam <strong>{total_late:.0f} kcal</strong>. Bu saatlerdeki yemekler hedefi en çok zorlayan girişler arasında.
                Bu saatte gerçekten aç mıydın, yoksa başka bir şey miydi?
            </div>""", unsafe_allow_html=True)
    else:
        st.info("Saatlik analiz için daha fazla veriye ihtiyaç var.")

    # ── 5. Meal type consistency ───────────────────────────────────────────────
    st.markdown('<div class="section-title">Öğün Düzeni / Meal Consistency (7 gün)</div>', unsafe_allow_html=True)

    meal_stats = db.get_meal_type_stats(7)
    if meal_stats:
        meal_map   = {r["meal_type"]: r for r in meal_stats}
        meal_labels = {
            "breakfast": ("🌅 Kahvaltı", 7),
            "lunch":     ("☀️ Öğle",     7),
            "dinner":    ("🌙 Akşam",    7),
            "snack":     ("🍎 Ara Öğün", 7),
        }
        for mtype, (label, max_days) in meal_labels.items():
            stat = meal_map.get(mtype)
            days = stat["days_logged"] if stat else 0
            avg_h = stat["avg_log_hour"] if stat else None
            pct   = days / max_days
            color = "#4CAF7D" if pct >= 0.7 else "#F4A261" if pct >= 0.4 else "#E05C5C"

            # Specific flags
            flag = ""
            if mtype == "breakfast" and days <= 2:
                flag = " · Kahvaltı atlanıyor — bu açlık krizlerine yol açabilir."
            elif mtype == "dinner" and avg_h and avg_h >= 20.5:
                flag = f" · Akşam yemeği ortalama {avg_h:.0f}:00'de — geç saatte yemek sindirimi zorlaştırır."
            elif mtype == "snack" and days >= 6:
                flag = " · Neredeyse her gün ara öğün var — içerik önemli."

            avg_str = f"ort. {avg_h:.0f}:00'de girildi" if avg_h is not None else ""
            st.markdown(f"""
            <div class="metric-card" style="padding:0.7rem 1rem; margin-bottom:0.4rem">
                <div style="display:flex; justify-content:space-between; align-items:center">
                    <span style="font-weight:600">{label}</span>
                    <span style="font-weight:700; color:{color}">{days}/7 gün</span>
                </div>
                <div class="progress-wrap" style="margin:0.4rem 0">
                    <div class="progress-fill" style="width:{pct*100:.0f}%; background:{color}"></div>
                </div>
                <div class="metric-sub">{avg_str}{flag}</div>
            </div>""", unsafe_allow_html=True)
    else:
        st.info("Öğün analizi için daha fazla veriye ihtiyaç var.")

    # ── 6. AI weekly narrative ─────────────────────────────────────────────────
    st.markdown('<div class="section-title">🤖 AI Koç Yorumu / AI Weekly Read</div>', unsafe_allow_html=True)
    st.caption("Gemini Pro haftanı analiz eder — veriler yeterince dolunca daha isabetli olur.")

    if st.button("✨ Haftalık AI Yorumu Al", use_container_width=True):
        with st.spinner("Gemini haftan analiz ediyor..."):
            ai_summary = ai.weekly_summary(
                name          = name,
                goal_calories = goal,
                daily_data    = daily,
                meal_stats    = db.get_meal_type_stats(7),
                hourly_data   = db.get_hourly_eating_pattern(14),
                streak        = streak,
            )
        st.markdown(f"""
        <div class="coach-box" style="border-left-color:#7C6AF7">
            🤖 {ai_summary}
        </div>""", unsafe_allow_html=True)



elif page == "👤 Profil":
    st.markdown("## 👤 Profil / Profile")
    st.caption("Bu bilgiler AI önerilerini kişiselleştirmek için kullanılacak.")

    profile = db.get_profile()

    with st.form("profile_form"):
        name        = st.text_input("İsim / Name",                 value=profile["name"]           if profile else "")
        birth_year  = st.number_input("Doğum yılı / Birth year",   value=profile["birth_year"]     if profile else 1970, min_value=1930, max_value=2005, step=1)
        height_cm   = st.number_input("Boy / Height (cm)",         value=profile["height_cm"]      if profile else 165.0, min_value=100.0, max_value=250.0, step=0.5, format="%.1f")
        goal_cal    = st.number_input("Günlük kalori hedefi / Daily calorie goal (kcal)", value=profile["goal_calories"]  if profile else 1600.0, min_value=800.0, max_value=4000.0, step=50.0)
        goal_weight = st.number_input("Hedef kilo / Goal weight (kg)", value=profile["goal_weight_kg"] if profile else 65.0, min_value=30.0, max_value=200.0, step=0.5, format="%.1f")
        saved = st.form_submit_button("💾 Kaydet / Save", use_container_width=True)

    if saved:
        if not name.strip():
            st.warning("İsim boş bırakılamaz!")
        else:
            db.save_profile(
                name=name.strip(),
                birth_year=int(birth_year),
                height_cm=float(height_cm),
                goal_calories=float(goal_cal),
                goal_weight_kg=float(goal_weight),
            )
            st.success("Profil kaydedildi! ✅")

    # Show BMI if we have enough data
    if profile:
        weight_logs = db.get_weight_logs(limit=1)
        if weight_logs and profile.get("height_cm"):
            h = profile["height_cm"] / 100
            bmi = weight_logs[0]["weight_kg"] / (h * h)
            st.markdown('<div class="section-title">Mevcut Durum / Current Status</div>', unsafe_allow_html=True)
            st.markdown(f"""
            <div class="metric-card">
                <div class="metric-label">Vücut Kitle İndeksi / BMI</div>
                <div class="metric-value">{bmi:.1f}</div>
                <div class="metric-sub">{bmi_label(bmi)} &nbsp;·&nbsp; {weight_logs[0]['weight_kg']} kg / {profile['height_cm']} cm</div>
            </div>
            """, unsafe_allow_html=True)