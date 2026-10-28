import streamlit as st
import pandas as pd
from datetime import date, datetime, timedelta

import db
import solver
from solver import t2m, m2t

st.set_page_config(page_title="SEVENS Roster", page_icon="🍳", layout="wide")
db.init_db()

DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@st.cache_data(ttl=10)
def load_staff():
    return db.query("SELECT * FROM staff WHERE active = 1 ORDER BY priority, name")


@st.cache_data(ttl=10)
def load_templates():
    return db.query("SELECT * FROM shift_templates WHERE active = 1 ORDER BY start, code")


@st.cache_data(ttl=10)
def load_blocks():
    return db.query("SELECT * FROM time_blocks ORDER BY start")


@st.cache_data(ttl=10)
def load_targets():
    return db.query("""SELECT bt.*, tb.name AS block, tb.start, tb.end, tb.hard
                       FROM block_targets bt JOIN time_blocks tb ON tb.id = bt.block_id
                       ORDER BY tb.start, bt.weekday""")


@st.cache_data(ttl=10)
def load_settings():
    return db.get_settings()


def refresh():
    st.cache_data.clear()


def targets_for(wd):
    return [t for t in load_targets() if t["weekday"] == wd]


def closed_weekdays(settings):
    return {int(x) for x in settings["closed_weekdays"].split(",") if x.strip() != ""}


# --- status logic (self-definable thresholds) ---------------------------------
def status_for(count, target, hard, yellow_margin):
    """ok = meets target · warn = within yellow_margin of target (soft blocks only)
    · bad = below that — and ANY shortfall on a hard block is bad."""
    if count >= target:
        return "ok"
    if hard:
        return "bad"
    return "warn" if count >= target - yellow_margin else "bad"


STATUS_COLORS = {"ok": "#70ad47", "warn": "#ffc000", "bad": "#e06666"}
STATUS_TEXT = {"ok": "#ffffff", "warn": "#000000", "bad": "#ffffff"}

# --- sheet-style colors (matching the 25/10 photo) ---------------------------
SHEET_COLORS = {"M1": "#f8cbad", "M2": "#f8cbad", "E": "#bdd7ee",
                "T1": "#d5c6e0", "T2": "#d5c6e0", "T3": "#d5c6e0", "T4": "#d5c6e0",
                "OFF": "#f2f2f2", "CLOSED": "#a6a6a6", "PH": "#a6a6a6"}
SHEET_TEXT = {"M1": "#833c00", "M2": "#833c00", "E": "#1f4e79",
              "T1": "#5b2d8e", "T2": "#5b2d8e", "T3": "#5b2d8e", "T4": "#5b2d8e",
              "OFF": "#808080", "CLOSED": "#ffffff", "PH": "#ffffff"}


def style_sheet_grid(df):
    """Color the shift grid exactly like the paper roster: orange mornings,
    blue evening, purple PT slots, grey off/closed."""
    def cell(v):
        v = str(v).split(" ")[0]
        if v in SHEET_COLORS:
            return f"background-color: {SHEET_COLORS[v]}; color: {SHEET_TEXT[v]}"
        return ""
    return df.style.map(cell)


def style_status(df, status_col="_status", paint_col="Cover"):
    def row_style(row):
        return [f"background-color: {STATUS_COLORS[row[status_col]]}; color: {STATUS_TEXT[row[status_col]]}; font-weight: 600"
                if c == paint_col else "" for c in row.index]
    return df.style.apply(row_style, axis=1).hide(axis="columns", subset=[status_col])


def unavailability_maps(dates):
    d0, d1 = dates[0].isoformat(), dates[-1].isoformat()
    day = db.query("SELECT * FROM availability WHERE date BETWEEN ? AND ?", (d0, d1))
    weekly = db.query("SELECT * FROM availability_weekly")
    day_map, weekly_map = {}, {}
    for r in day:
        day_map.setdefault(r["staff_id"], []).append(r)
    for r in weekly:
        weekly_map.setdefault(r["staff_id"], []).append(r)
    return day_map, weekly_map


@st.cache_data(ttl=10)
def available_dates_map():
    rows = db.query("SELECT staff_id, date, template_code FROM available_dates")
    out = {}
    for r in rows:
        out.setdefault(r["staff_id"], {})[r["date"]] = r["template_code"]
    return out


def roster_rows(dates):
    d0, d1 = dates[0].isoformat(), dates[-1].isoformat()
    return db.query(
        """SELECT r.*, s.name, s.staff_type FROM roster r
           JOIN staff s ON s.id = r.staff_id
           WHERE r.date BETWEEN ? AND ? ORDER BY r.date, s.priority, s.name""", (d0, d1))


# ---------------------------------------------------------------- sidebar ---
page = st.sidebar.radio("Go to", ["📋 Roster", "🙋 Availability", "⚙️ Settings"])
st.sidebar.markdown("---")
staff_all = load_staff()
me = st.sidebar.selectbox("Who are you?", ["(manager)"] + [s["name"] for s in staff_all])
st.sidebar.caption("Prototype auth — anyone with the link can edit. Real login comes in Phase 2.")

# --------------------------------------------------------------- ROSTER -----
if page == "📋 Roster":
    st.title("📋 Roster")
    with st.expander("❓ How to use this tool (3 steps)", expanded=False):
        st.markdown(
            "**1️⃣ One-time setup (manager):** ⚙️ Settings → check staff list, shift templates (M1/M2/E/T1–T4), "
            "lunch targets, and closed days (Sundays + HK holidays are pre-loaded).\n\n"
            "**2️⃣ Every week (staff):** 🙋 Availability → full-timers mark OFF days · part-timers mark the days "
            "they CAN work (and their preferred T-slot).\n\n"
            "**3️⃣ Publish (manager):** 📋 Roster → pick the week → **⚡ Auto-generate** → check the colored "
            "coverage table (🟢🟡🔴) → fix gaps with *Manual add* (e.g. Jona's E) → done. "
            "Manual shifts are never wiped by re-generating.")
    settings = load_settings()
    staff = staff_all
    templates = load_templates()
    closed = closed_weekdays(settings)
    closed_dates = {r["date"]: r["name"] for r in db.query("SELECT * FROM closed_dates")}

    # -------------------------------------------- LAYER 1: STRUCTURE CHECK --
    weekly_all = {}
    for r in db.query("SELECT * FROM availability_weekly"):
        weekly_all.setdefault(r["staff_id"], []).append(r)
    weekdays_open = [wd for wd in range(7) if wd not in closed]
    outlook = solver.best_case_outlook(staff, templates, weekly_all,
                                       {wd: targets_for(wd) for wd in weekdays_open}, weekdays_open)
    st.subheader("🎯 Structure check — best case (all PT available, before availability input)")
    st.caption("Layer 1: staffing structure only — name · FT/PT · priority (T-slot) · opt-in/out. "
               "If this row fails, no amount of availability juggling fixes it.")
    yellow_margin = int(float(settings["yellow_margin"]))
    rows_, ok_all = [], True
    for wd in weekdays_open:
        cov, assigns = outlook[wd]
        ft = sum(1 for a in assigns if a["staff_type"] == "full_time")
        pt = len(assigns) - ft
        hard = next((b for b in cov if b["hard"]), None)
        lunch_txt, verdict = "", "✅ met"
        if hard:
            lunch_txt = f"{hard['count']}/{hard['target']} (PT {hard['parttimers']}/{hard['pt_target']})"
            st_ = status_for(hard["count"], hard["target"], hard["hard"], yellow_margin)
            if st_ != "ok":
                ok_all = False
                short_pt = max(0, hard["pt_target"] - hard["parttimers"])
                short_all = max(0, hard["target"] - hard["count"])
                missing = solver.missing_pt_slots(assigns, templates, hard, short_pt) if short_pt else []
                parts = []
                if short_pt:
                    parts.append(f"short {short_pt} PT" + (f" — need {', '.join(missing)}" if missing else ""))
                if short_all > short_pt:
                    parts.append(f"short {short_all - short_pt} staff")
                verdict = ("🟡 borderline — " if st_ == "warn" else "🔴 ") + "; ".join(parts)
        rows_.append({"Day": DOW[wd], "FT on": ft, "PT (best case)": pt,
                      "Lunch service": lunch_txt, "Verdict": verdict})
    st.dataframe(pd.DataFrame(rows_), use_container_width=True, hide_index=True)
    if ok_all:
        st.success("✅ Structure meets all targets Mon–Sat (best case). "
                   "Layer 2 below: staff availability + manager manual adds turn this into the actual roster.")
    else:
        st.error("🔴 Structure alone cannot meet lunch targets — hire/activate more PT or adjust targets in Settings. "
                 "No availability juggling will fix a structural shortfall.")

    anchor = st.date_input("Week of", value=date.today())
    dates = solver.week_dates(anchor)
    min_shift = int(float(settings["min_shift_hours"]) * 60)
    day_map, weekly_map = unavailability_maps(dates)
    avail_map = available_dates_map()

    hol_in_week = {d: n for d, n in closed_dates.items() if d in [x.isoformat() for x in dates]}
    if hol_in_week:
        st.info("🎌 Public holiday(s) this week: " + ", ".join(f"{date.fromisoformat(d).day}/{date.fromisoformat(d).month} {n}" for d, n in hol_in_week.items()))

    c1, c2 = st.columns([1, 3])
    if c1.button("⚡ Auto-generate week", type="primary"):
        for d in dates:
            db.execute("DELETE FROM roster WHERE date = ? AND source = 'auto'", (d.isoformat(),))
            if d.weekday() in closed or d.isoformat() in closed_dates:
                continue
            assigns = solver.build_day(staff, templates, load_blocks(), d,
                                       day_map, weekly_map, avail_map, min_shift)
            for a in assigns:
                db.execute("INSERT INTO roster(date, staff_id, template_code, start, end, source) VALUES (?,?,?,?,?,'auto')",
                           (d.isoformat(), a["staff_id"], a["code"], m2t(a["start_m"]), m2t(a["end_m"])))
        refresh()
        st.success("Week generated. Shortfalls flagged below.")

    rows = roster_rows(dates)
    open_days = [d for d in dates if d.weekday() not in closed and d.isoformat() not in closed_dates]

    # --- shift grid (template codes, like the sheet) ---
    grid = {s["name"]: {} for s in staff}
    for d in dates:
        label = f"{DOW[d.weekday()]} {d.day}/{d.month}"
        for s in staff:
            if d.weekday() in closed:
                grid[s["name"]][label] = "CLOSED"
            elif d.isoformat() in closed_dates:
                grid[s["name"]][label] = "PH"
            else:
                grid[s["name"]][label] = "OFF"
    for r in rows:
        label = f"{DOW[date.fromisoformat(r['date']).weekday()]} {date.fromisoformat(r['date']).day}/{date.fromisoformat(r['date']).month}"
        grid[r["name"]][label] = r["template_code"]
    df = pd.DataFrame(grid).T
    df.index.name = "Staff"
    st.subheader("Shift grid")
    st.dataframe(style_sheet_grid(df), use_container_width=True)

    # --- day notes ---
    notes = db.query("SELECT * FROM day_notes WHERE date BETWEEN ? AND ? ORDER BY date",
                     (dates[0].isoformat(), dates[-1].isoformat()))
    if notes:
        st.caption(" · ".join(f"**{date.fromisoformat(n['date']).day}/{date.fromisoformat(n['date']).month}** {n['text']}" for n in notes))

    # --- coverage ---
    st.subheader("Coverage vs targets")
    st.caption(f"🟢 meets target · 🟡 within {yellow_margin} of target (soft blocks) · 🔴 below "
               "— hard blocks (Lunch) turn 🔴 at ANY shortfall. Thresholds in ⚙️ Settings.")
    cov_rows, any_short = [], False
    for d in open_days:
        assigns = [{"staff_id": r["staff_id"], "name": r["name"], "staff_type": r["staff_type"],
                    "start_m": t2m(r["start"]), "end_m": t2m(r["end"]), "code": r["template_code"],
                    "requires_pairing": False}
                   for r in rows if r["date"] == d.isoformat()]
        for b in solver.coverage(assigns, targets_for(d.weekday())):
            cover = f"{b['count']}/{b['target']}"
            if b["pt_target"]:
                cover += f" · PT {b['parttimers']}/{b['pt_target']}"
            st_ = status_for(b["count"], b["target"], b["hard"], yellow_margin)
            if not b["pairing_ok"]:
                st_ = "bad"
                cover += " · ⚠️pairing"
            cov_rows.append({"Day": f"{DOW[d.weekday()]} {d.day}/{d.month}", "Block": b["block"],
                             "Cover": cover, "_status": st_})
            any_short = any_short or (st_ != "ok")
    cov_df = pd.DataFrame(cov_rows)
    if not cov_df.empty:
        st.dataframe(style_status(cov_df), use_container_width=True, hide_index=True)
    if any_short:
        st.warning("🔴 = action needed (lunch at risk if it's the hard block). 🟡 = watch it.")
        tgt_blocks = load_blocks()
        for d in open_days:
            assigns = [{"staff_id": r["staff_id"], "name": r["name"], "staff_type": r["staff_type"],
                        "start_m": t2m(r["start"]), "end_m": t2m(r["end"]), "code": r["template_code"]}
                       for r in rows if r["date"] == d.isoformat()]
            sugg = solver.switch_suggestions(assigns, templates, targets_for(d.weekday()))
            for s_ in sugg:
                st.info(f"{DOW[d.weekday()]} {d.day}/{d.month}: 💡 {s_} (manager approves — shifts never auto-extend)")

    # --- manual adjustments ---
    st.subheader("Manual add / remove")
    ca, cb = st.columns(2)
    with ca:
        with st.form("add_shift"):
            st.markdown("**Add shift**")
            d_in = st.selectbox("Date", open_days, format_func=lambda d: f"{DOW[d.weekday()]} {d.day}/{d.month}")
            s_in = st.selectbox("Staff", staff, format_func=lambda s: s["name"])
            tpl_codes = [t["code"] for t in templates]
            default_idx = tpl_codes.index(s_in["default_template"]) if s_in["default_template"] in tpl_codes else 0
            tpl_in = st.selectbox("Shift", tpl_codes, index=default_idx)
            if st.form_submit_button("Add"):
                tpl = next(t for t in templates if t["code"] == tpl_in)
                db.execute("INSERT INTO roster(date, staff_id, template_code, start, end, source) VALUES (?,?,?,?,'manual')",
                           (d_in.isoformat(), s_in["id"], tpl_in, tpl["start"], tpl["end"]))
                refresh()
                st.rerun()
    with cb:
        st.markdown("**Remove shift**")
        if rows:
            opts = {f"{r['date']} · {r['name']} · {r['template_code']} ({r['source']})": r["id"] for r in rows}
            pick = st.selectbox("Shift", list(opts.keys()))
            if st.button("Delete selected"):
                db.execute("DELETE FROM roster WHERE id = ?", (opts[pick],))
                refresh()
                st.rerun()

    with st.expander("Add day note (pest control, trial person, event…)"):
        with st.form("add_note"):
            n1, n2 = st.columns(2)
            nd = n1.date_input("Date", value=anchor)
            nt = n2.text_input("Note")
            if st.form_submit_button("Save note"):
                db.execute("INSERT INTO day_notes(date, text) VALUES (?,?)", (nd.isoformat(), nt))
                refresh()
                st.rerun()

# ---------------------------------------------------------- AVAILABILITY ----
elif page == "🙋 Availability":
    st.title("🙋 Availability")
    staff = staff_all
    default_i = next((i for i, s in enumerate(staff) if s["name"] == me), 0) if me != "(manager)" else 0
    person = st.selectbox("Staff member", staff, format_func=lambda s: f"{s['name']}  ({s['availability_mode']})",
                          index=default_i)

    if person["availability_mode"] == "opt_in":
        st.caption(f"**{person['name']} works PT — mark the days you CAN work.** "
                   "You are only scheduled on days you mark here.")
        tab_a, tab_b = st.tabs(["Add available days", "My available days"])
        with tab_a:
            with st.form("add_avail"):
                c1, c2 = st.columns(2)
                d1 = c1.date_input("From", value=date.today() + timedelta(days=1))
                d2 = c2.date_input("To", value=d1)
                tpl_options = ["(default shift)"] + [t["code"] for t in load_templates()]
                tpl_pick = st.selectbox("Preferred shift for these day(s)", tpl_options)
                note = st.text_input("Note (optional)")
                if st.form_submit_button("Save"):
                    days = [(d1 + timedelta(days=i)) for i in range((d2 - d1).days + 1)]
                    tpl_code = None if tpl_pick == "(default shift)" else tpl_pick
                    for d in days:
                        db.execute("INSERT OR IGNORE INTO available_dates(staff_id, date, template_code, note) VALUES (?,?,?,?)",
                                   (person["id"], d.isoformat(), tpl_code, note))
                    refresh()
                    st.success(f"Marked available for {len(days)} day(s).")
        with tab_b:
            rows = db.query("SELECT * FROM available_dates WHERE staff_id = ? AND date >= ? ORDER BY date",
                            (person["id"], date.today().isoformat()))
            if rows:
                opts = {f"{r['date']} · {r['template_code'] or person['default_template']} {r['note'] or ''}": r["id"] for r in rows}
                pick = st.selectbox("Entry", list(opts.keys()), key="del_avail")
                if st.button("Remove"):
                    db.execute("DELETE FROM available_dates WHERE id = ?", (opts[pick],))
                    refresh()
                    st.rerun()
            else:
                st.caption("None upcoming — you won't be scheduled until you add days.")
    else:
        st.caption(f"**{person['name']} is on fixed shifts** — mark days/times you are OFF.")
        tab1, tab2, tab3 = st.tabs(["Add one-off OFF", "Recurring OFF (weekly)", "My OFF entries"])
        with tab1:
            with st.form("add_oneoff"):
                c1, c2, c3 = st.columns(3)
                d1 = c1.date_input("From", value=date.today() + timedelta(days=1))
                d2 = c2.date_input("To", value=d1)
                all_day = c3.checkbox("Full day", value=True)
                c4, c5 = st.columns(2)
                w_start = c4.time_input("OFF from", datetime.strptime("09:00", "%H:%M").time(), disabled=all_day)
                w_end = c5.time_input("OFF until", datetime.strptime("17:00", "%H:%M").time(), disabled=all_day)
                note = st.text_input("Note (optional)")
                if st.form_submit_button("Save"):
                    days = [(d1 + timedelta(days=i)) for i in range((d2 - d1).days + 1)]
                    for d in days:
                        db.execute("INSERT INTO availability(staff_id, date, all_day, start, end, note) VALUES (?,?,?,?,?,?)",
                                   (person["id"], d.isoformat(), 1 if all_day else 0,
                                    None if all_day else m2t(t2m(w_start)), None if all_day else m2t(t2m(w_end)), note))
                    refresh()
                    st.success(f"Marked OFF for {len(days)} day(s).")
        with tab2:
            with st.form("add_weekly"):
                days_picked = st.multiselect("Every", list(range(7)), format_func=lambda i: DOW[i], default=[0, 1, 2, 3, 4])
                all_day = st.checkbox("Full day", value=True, key="w_all")
                c3, c4 = st.columns(2)
                w_start = c3.time_input("OFF from", datetime.strptime("09:00", "%H:%M").time(), disabled=all_day, key="w_s")
                w_end = c4.time_input("OFF until", datetime.strptime("13:00", "%H:%M").time(), disabled=all_day, key="w_e")
                if st.form_submit_button("Save recurring OFF"):
                    for wd in days_picked:
                        db.execute("INSERT INTO availability_weekly(staff_id, weekday, all_day, start, end) VALUES (?,?,?,?,?)",
                                   (person["id"], wd, 1 if all_day else 0,
                                    None if all_day else m2t(t2m(w_start)), None if all_day else m2t(t2m(w_end))))
                    refresh()
                    st.success("Saved.")
        with tab3:
            oneoff = db.query("SELECT * FROM availability WHERE staff_id = ? AND date >= ? ORDER BY date",
                              (person["id"], date.today().isoformat()))
            st.markdown("**One-off OFF**")
            if oneoff:
                opts = {f"{r['date']} — {'ALL DAY' if r['all_day'] else r['start'] + '–' + r['end']} {r['note'] or ''}": r["id"] for r in oneoff}
                pick = st.selectbox("Entry", list(opts.keys()), key="del_oneoff")
                if st.button("Delete one-off"):
                    db.execute("DELETE FROM availability WHERE id = ?", (opts[pick],))
                    refresh()
                    st.rerun()
            else:
                st.caption("None.")
            st.markdown("**Recurring OFF**")
            weekly = db.query("SELECT * FROM availability_weekly WHERE staff_id = ?", (person["id"],))
            if weekly:
                opts = {f"{DOW[r['weekday']]} — {'ALL DAY' if r['all_day'] else r['start'] + '–' + r['end']}": r["id"] for r in weekly}
                pick = st.selectbox("Entry", list(opts.keys()), key="del_weekly")
                if st.button("Delete recurring"):
                    db.execute("DELETE FROM availability_weekly WHERE id = ?", (opts[pick],))
                    refresh()
                    st.rerun()
            else:
                st.caption("None.")

    st.markdown("---")
    st.subheader("Team availability snapshot (next 14 days)")
    d0 = date.today()
    dates = [d0 + timedelta(days=i) for i in range(14)]
    day_map, weekly_map = unavailability_maps(dates)
    avail_map = available_dates_map()
    settings = load_settings()
    closed = closed_weekdays(settings)
    holiday_set = {r["date"] for r in db.query("SELECT date FROM closed_dates")}
    snap = {}
    for s in staff:
        row = []
        for d in dates:
            if d.weekday() in closed or d.isoformat() in holiday_set:
                row.append("—")
            elif s["availability_mode"] == "opt_in":
                row.append("✅" if d.isoformat() in avail_map.get(s["id"], set()) else "·")
            else:
                row.append("❌" if solver.is_off(s, day_map.get(s["id"], []), weekly_map.get(s["id"], []), d.weekday()) else "✅")
        snap[s["name"]] = row
    sdf = pd.DataFrame(snap).T
    sdf.columns = [f"{DOW[d.weekday()]} {d.day}/{d.month}" for d in dates]
    sdf.index.name = "Staff"
    st.dataframe(sdf, use_container_width=True)

# -------------------------------------------------------------- SETTINGS ----
else:
    st.title("⚙️ Settings")
    settings = load_settings()

    st.subheader("General")
    with st.form("general"):
        c1, c2, c3, c4 = st.columns(4)
        o = c1.time_input("Opening time", datetime.strptime(settings["open_time"], "%H:%M").time())
        cl = c2.time_input("Closing time", datetime.strptime(settings["close_time"], "%H:%M").time())
        ms = c3.number_input("Min shift (hrs)", min_value=1.0, max_value=11.0, value=float(settings["min_shift_hours"]), step=0.5)
        cw = c4.multiselect("Closed days", list(range(7)), format_func=lambda i: DOW[i],
                            default=[int(x) for x in settings["closed_weekdays"].split(",") if x.strip()])
        ym = st.number_input("Yellow zone: how many below target turns red (yellow = within this many)",
                             min_value=0, max_value=4, value=int(float(settings["yellow_margin"])),
                             help="0 = any shortfall is red. 1 = 1 short = yellow, 2+ short = red.")
        if st.form_submit_button("Save general"):
            db.set_setting("open_time", m2t(t2m(o)))
            db.set_setting("close_time", m2t(t2m(cl)))
            db.set_setting("min_shift_hours", ms)
            db.set_setting("closed_weekdays", ",".join(str(x) for x in sorted(cw)))
            db.set_setting("yellow_margin", ym)
            refresh()
            st.success("Saved.")

    st.subheader("Closed dates (public holidays & special closures)")
    st.caption("HK gazetted general holidays 2026–2027 are pre-loaded. Add shop-specific closures "
               "(e.g. CNY eve, typhoon make-up) or edit names as rows below.")
    cdf = pd.DataFrame(db.query("SELECT * FROM closed_dates ORDER BY date"))
    cedited = st.data_editor(cdf, num_rows="dynamic", use_container_width=True, hide_index=True,
                             column_config={
                                 "date": st.column_config.TextColumn("Date (YYYY-MM-DD)"),
                                 "name": st.column_config.TextColumn("Name"),
                             })
    if st.button("Save closed dates"):
        db.sync_table("closed_dates", cedited, ["date", "name"])
        refresh()
        st.success("Saved.")

    st.subheader("Shift templates")
    st.caption("Named shifts as on the sheet. FT: M1/M2/E · PT: T1–T4. 'applies_to' controls who can take which template.")
    tdf = pd.DataFrame(db.query("SELECT * FROM shift_templates ORDER BY start, code"))
    tedited = st.data_editor(tdf, num_rows="dynamic", use_container_width=True, hide_index=True,
                             column_config={
                                 "code": st.column_config.TextColumn("Code"),
                                 "start": st.column_config.TextColumn("Start"),
                                 "end": st.column_config.TextColumn("End"),
                                 "applies_to": st.column_config.SelectboxColumn("Applies to",
                                     options=["full_time", "part_time", "any"]),
                                 "active": st.column_config.CheckboxColumn("Active"),
                             })
    if st.button("Save templates"):
        db.sync_table("shift_templates", tedited, ["code", "start", "end", "applies_to", "active"])
        refresh()
        st.success("Saved.")

    st.subheader("Time blocks")
    bdf = pd.DataFrame(db.query("SELECT * FROM time_blocks ORDER BY start"))
    bedited = st.data_editor(bdf, num_rows="dynamic", use_container_width=True, hide_index=True,
                             column_config={
                                 "name": st.column_config.TextColumn("Block"),
                                 "start": st.column_config.TextColumn("Start"),
                                 "end": st.column_config.TextColumn("End"),
                                 "hard": st.column_config.CheckboxColumn("Hard rule"),
                             })
    if st.button("Save blocks"):
        db.sync_table("time_blocks", bedited, ["name", "start", "end", "hard"])
        refresh()
        st.success("Saved. (Note: deleting a block also deletes its targets.)")

    st.subheader("Targets per block per weekday")
    st.caption("Row per block × weekday. Lunch Mon–Fri is the hard 8+4 rule; Saturday is the skeleton target.")
    gdf = pd.DataFrame(db.query("""SELECT bt.id, bt.block_id, bt.weekday, bt.min_headcount, bt.min_parttimers, tb.name AS block
                                   FROM block_targets bt JOIN time_blocks tb ON tb.id = bt.block_id
                                   ORDER BY tb.start, bt.weekday"""))
    gedited = st.data_editor(gdf, num_rows="dynamic", use_container_width=True, hide_index=True,
                             column_config={
                                 "block": st.column_config.TextColumn("Block", disabled=True),
                                 "weekday": st.column_config.SelectboxColumn("Day", options=list(range(7)),
                                     format_func=lambda i: DOW[i]),
                                 "min_headcount": st.column_config.NumberColumn("Min headcount", min_value=0),
                                 "min_parttimers": st.column_config.NumberColumn("Min part-timers", min_value=0),
                             })
    if st.button("Save targets"):
        db.sync_table("block_targets", gedited, ["block_id", "weekday", "min_headcount", "min_parttimers"])
        refresh()
        st.success("Saved.")

    st.subheader("Staff")
    st.caption("Priority is derived automatically — no manual ranking needed: "
               "**FT first, then PT by T-slot (T1 → T4), trial last.** The default shift column sets the T-slot.")
    sdf = pd.DataFrame(db.query("SELECT * FROM staff ORDER BY priority, name"))
    tpl_options = [t["code"] for t in db.query("SELECT code FROM shift_templates WHERE active = 1")]
    sedited = st.data_editor(sdf, num_rows="dynamic", use_container_width=True, hide_index=True,
                             column_config={
                                 "name": st.column_config.TextColumn("Name"),
                                 "staff_type": st.column_config.SelectboxColumn("Type",
                                     options=["full_time", "fixed_part_time", "trial"]),
                                 "contracted_hours": st.column_config.NumberColumn("Contracted hrs/wk", min_value=0.0),
                                 "max_shift_hours": st.column_config.NumberColumn("Max hrs/shift", min_value=1.0, max_value=11.0),
                                 "default_template": st.column_config.SelectboxColumn("Default shift", options=tpl_options),
                                 "availability_mode": st.column_config.SelectboxColumn("Availability mode",
                                     options=["opt_out", "opt_in"],
                                     help="opt_out = fixed shifts, mark OFF days · opt_in = PT, mark days you CAN work"),
                                 "requires_pairing": st.column_config.CheckboxColumn("Must pair w/ FT"),
                                 "active": st.column_config.CheckboxColumn("Active"),
                             })
    if st.button("Save staff"):
        db.sync_table("staff", sedited, ["name", "staff_type", "contracted_hours", "max_shift_hours",
                                         "requires_pairing", "default_template", "availability_mode", "active"])
        tpl_rank = {t["code"]: i for i, t in enumerate(db.query(
            "SELECT code FROM shift_templates WHERE applies_to='part_time' AND active=1 ORDER BY start, code"))}
        for s_ in db.query("SELECT id, staff_type, default_template FROM staff"):
            if s_["staff_type"] == "full_time":
                pr = 1
            elif s_["staff_type"] == "trial":
                pr = 90
            else:
                pr = 10 + tpl_rank.get(s_["default_template"], 9)
            db.execute("UPDATE staff SET priority = ? WHERE id = ?", (pr, s_["id"]))
        refresh()
        st.success("Saved.")