import streamlit as st
import pandas as pd
from datetime import date, datetime, timedelta

import db
import solver
from solver import t2m, m2t

st.set_page_config(page_title="Kitchen Roster", page_icon="🍳", layout="wide")
db.init_db()

DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
TYPE_LABELS = {"full_time": "Full-time", "fixed_part_time": "Fixed PT", "trial": "Trial/Support"}


@st.cache_data(ttl=10)
def load_staff():
    return db.query("SELECT * FROM staff WHERE active = 1 ORDER BY priority, name")


@st.cache_data(ttl=10)
def load_blocks():
    return db.query("SELECT * FROM time_blocks ORDER BY start")


@st.cache_data(ttl=10)
def load_settings():
    return db.get_settings()


def refresh():
    st.cache_data.clear()


def blocks_ctx(settings, blocks):
    return {"_open": settings["open_time"], "_close": settings["close_time"], "blocks": blocks}


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


def roster_rows(dates):
    d0, d1 = dates[0].isoformat(), dates[-1].isoformat()
    return db.query(
        """SELECT r.*, s.name, s.staff_type FROM roster r
           JOIN staff s ON s.id = r.staff_id
           WHERE r.date BETWEEN ? AND ? ORDER BY r.date, s.priority, s.name""", (d0, d1))


# ---------------------------------------------------------------- sidebar ---
page = st.sidebar.radio("Go to", ["📋 Roster", "🙋 Availability", "⚙️ Settings"])
st.sidebar.markdown("---")
me = st.sidebar.selectbox("Who are you?", ["(manager)"] + [s["name"] for s in load_staff()])
st.sidebar.caption("Prototype auth — anyone with the link can edit. Real login comes in Phase 2.")

# --------------------------------------------------------------- ROSTER -----
if page == "📋 Roster":
    st.title("📋 Roster")
    settings = load_settings()
    staff = load_staff()
    blocks = load_blocks()

    c1, c2, c3 = st.columns([1, 1, 2])
    with c1:
        anchor = st.date_input("Week of", value=date.today())
    dates = solver.week_dates(anchor)
    min_shift = int(float(settings["min_shift_hours"]) * 60)

    with c2:
        st.write("")
        st.write("")
        if st.button("⚡ Auto-generate week", type="primary"):
            day_map, weekly_map = unavailability_maps(dates)
            ctx = blocks_ctx(settings, blocks)
            for d in dates:
                db.execute("DELETE FROM roster WHERE date = ? AND source = 'auto'", (d.isoformat(),))
                assigns = solver.build_day(staff, ctx, d, day_map, weekly_map, min_shift)
                for a in assigns:
                    db.execute("INSERT INTO roster(date, staff_id, start, end, source) VALUES (?,?,?,?,'auto')",
                               (d.isoformat(), a["staff_id"], m2t(a["start_m"]), m2t(a["end_m"])))
            refresh()
            st.success(f"Generated {sum(1 for _ in roster_rows(dates))} shifts. Shortfalls flagged below.")

    rows = roster_rows(dates)

    # --- shift grid ---
    grid = {s["name"]: {d: "" for d in dates} for s in staff}
    for r in rows:
        grid.setdefault(r["name"], {d: "" for d in dates})
        grid[r["name"]][r["date"]] = f"{r['start']}–{r['end']}"
    df = pd.DataFrame(grid).T
    df.index.name = "Staff"
    df.columns = [f"{DOW[d.weekday()]} {d.day}/{d.month}" for d in dates]
    st.subheader("Shift grid")
    st.dataframe(df, use_container_width=True)

    # --- coverage ---
    st.subheader("Coverage vs targets")
    day_map, weekly_map = unavailability_maps(dates)
    ctx = blocks_ctx(settings, blocks)
    cov_rows, short_found = [], False
    for d in dates:
        assigns = []
        for r in [x for x in rows if x["date"] == d.isoformat()]:
            assigns.append({"staff_id": r["staff_id"], "name": r["name"], "staff_type": r["staff_type"],
                            "start_m": t2m(r["start"]), "end_m": t2m(r["end"]), "requires_pairing": False})
        for b in solver.coverage(assigns, ctx):
            status = f"{b['count']}/{b['target']}"
            if b["pt_target"]:
                status += f" · PT {b['parttimers']}/{b['pt_target']}"
            if not b["pairing_ok"]:
                status += " · ⚠️pairing"
            flag = "🔴" if b["hard"] and b["short"] else ("🟠" if b["short"] else "🟢")
            cov_rows.append({"Day": f"{DOW[d.weekday()]} {d.day}/{d.month}",
                             "Block": b["block"], f"{flag}": status, "_short": b["short"], "_hard": b["hard"]})
            short_found = short_found or b["short"]
    cov_df = pd.DataFrame(cov_rows).drop(columns=["_short", "_hard"])
    st.dataframe(cov_df, use_container_width=True, hide_index=True)
    if short_found:
        st.warning("🔴 hard block short = lunch service at risk. 🟠 soft short = below target but operational.")

    # --- manual adjustments ---
    st.subheader("Manual add / remove")
    ca, cb = st.columns(2)
    with ca:
        with st.form("add_shift"):
            st.markdown("**Add shift**")
            d_in = st.selectbox("Date", dates, format_func=lambda d: f"{DOW[d.weekday()]} {d.day}/{d.month}")
            s_in = st.selectbox("Staff", staff, format_func=lambda s: s["name"])
            t1, t2 = st.columns(2)
            start_in = t1.time_input("Start", datetime.strptime(s_in["shift_start"], "%H:%M").time())
            end_in = t2.time_input("End", datetime.strptime(s_in["shift_end"], "%H:%M").time())
            if st.form_submit_button("Add"):
                if t2m(end_in) <= t2m(start_in):
                    st.error("End must be after start.")
                else:
                    db.execute("INSERT INTO roster(date, staff_id, start, end, source) VALUES (?,?,?,?,'manual')",
                               (d_in.isoformat(), s_in["id"], m2t(t2m(start_in)), m2t(t2m(end_in))))
                    refresh()
                    st.rerun()
    with cb:
        st.markdown("**Remove shift**")
        if rows:
            opts = {f"{r['date']} · {r['name']} · {r['start']}–{r['end']} ({r['source']})": r["id"] for r in rows}
            pick = st.selectbox("Shift", list(opts.keys()))
            if st.button("Delete selected"):
                db.execute("DELETE FROM roster WHERE id = ?", (opts[pick],))
                refresh()
                st.rerun()

# ---------------------------------------------------------- AVAILABILITY ----
elif page == "🙋 Availability":
    st.title("🙋 My Availability")
    staff = load_staff()
    person = st.selectbox("Staff member", staff, format_func=lambda s: s["name"],
                          index=max(0, [s["name"] for s in staff].index(me) - 1) if me != "(manager)" else 0)

    tab1, tab2, tab3 = st.tabs(["Add one-off", "Add recurring (weekly)", "My entries"])

    with tab1:
        with st.form("add_oneoff"):
            c1, c2, c3 = st.columns(3)
            d1 = c1.date_input("From", value=date.today() + timedelta(days=1))
            d2 = c2.date_input("To", value=d1)
            all_day = c3.checkbox("Full day", value=True)
            c4, c5 = st.columns(2)
            w_start = c4.time_input("Unavailable from", datetime.strptime("09:00", "%H:%M").time(), disabled=all_day)
            w_end = c5.time_input("Unavailable until", datetime.strptime("17:00", "%H:%M").time(), disabled=all_day)
            note = st.text_input("Note (optional)", placeholder="e.g. doctor appointment")
            if st.form_submit_button("Save"):
                days = [(d1 + timedelta(days=i)) for i in range((d2 - d1).days + 1)]
                for d in days:
                    db.execute(
                        "INSERT INTO availability(staff_id, date, all_day, start, end, note) VALUES (?,?,?,?,?,?)",
                        (person["id"], d.isoformat(), 1 if all_day else 0,
                         None if all_day else m2t(t2m(w_start)), None if all_day else m2t(t2m(w_end)), note))
                refresh()
                st.success(f"Marked {len(days)} day(s).")

    with tab2:
        st.caption("Applies every week, e.g. 'school run' every weekday morning.")
        with st.form("add_weekly"):
            days_picked = st.multiselect("Days of week", list(range(7)), format_func=lambda i: DOW[i],
                                         default=[0, 1, 2, 3, 4])
            c1, c2 = st.columns(2)
            all_day = c1.checkbox("Full day", value=False, key="w_all")
            c2.write("")
            c3, c4 = st.columns(2)
            w_start = c3.time_input("Unavailable from", datetime.strptime("09:00", "%H:%M").time(), disabled=all_day, key="w_s")
            w_end = c4.time_input("Unavailable until", datetime.strptime("12:00", "%H:%M").time(), disabled=all_day, key="w_e")
            if st.form_submit_button("Save recurring"):
                for wd in days_picked:
                    db.execute(
                        "INSERT INTO availability_weekly(staff_id, weekday, all_day, start, end) VALUES (?,?,?,?,?)",
                        (person["id"], wd, 1 if all_day else 0,
                         None if all_day else m2t(t2m(w_start)), None if all_day else m2t(t2m(w_end))))
                refresh()
                st.success(f"Saved for {len(days_picked)} weekday(s) / week.")

    with tab3:
        st.markdown("**One-off entries**")
        oneoff = db.query("SELECT * FROM availability WHERE staff_id = ? ORDER BY date", (person["id"],))
        upcoming = [r for r in oneoff if r["date"] >= date.today().isoformat()]
        if upcoming:
            opts = {f"{r['date']} — {'ALL DAY' if r['all_day'] else r['start'] + '–' + r['end']} {r['note'] or ''}": r["id"] for r in upcoming}
            pick = st.selectbox("Entry", list(opts.keys()), key="del_oneoff")
            if st.button("Delete one-off"):
                db.execute("DELETE FROM availability WHERE id = ?", (opts[pick],))
                refresh()
                st.rerun()
        else:
            st.caption("None upcoming.")
        st.markdown("**Recurring entries**")
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
    snap = {}
    for s in staff:
        row = []
        for d in dates:
            wins = solver.available_windows(s, day_map.get(s["id"], []), weekly_map.get(s["id"], []),
                                            d.weekday(), 0, 24 * 60)
            total = sum(e - ss for ss, e in wins)
            row.append("✅" if total >= 8 * 60 else ("🟡" if total > 0 else "❌"))
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
        c1, c2, c3 = st.columns(3)
        o = c1.time_input("Opening time", datetime.strptime(settings["open_time"], "%H:%M").time())
        cl = c2.time_input("Closing time", datetime.strptime(settings["close_time"], "%H:%M").time())
        ms = c3.number_input("Min shift length (hours)", min_value=1.0, max_value=11.0,
                             value=float(settings["min_shift_hours"]), step=0.5)
        if st.form_submit_button("Save general"):
            db.set_setting("open_time", m2t(t2m(o)))
            db.set_setting("close_time", m2t(t2m(cl)))
            db.set_setting("min_shift_hours", ms)
            refresh()
            st.success("Saved.")

    st.subheader("Time blocks & targets")
    st.caption("Lunch is seeded as a HARD block — it must meet its targets or service is at risk.")
    bdf = pd.DataFrame(load_blocks())
    edited = st.data_editor(bdf, num_rows="dynamic", use_container_width=True, hide_index=True,
                            column_config={
                                "name": st.column_config.TextColumn("Block"),
                                "start": st.column_config.TextColumn("Start (HH:MM)"),
                                "end": st.column_config.TextColumn("End (HH:MM)"),
                                "min_headcount": st.column_config.NumberColumn("Min headcount", min_value=0),
                                "min_parttimers": st.column_config.NumberColumn("Min part-timers", min_value=0),
                                "hard": st.column_config.CheckboxColumn("Hard rule"),
                            })
    if st.button("Save blocks"):
        db.sync_table("time_blocks", edited, ["name", "start", "end", "min_headcount", "min_parttimers", "hard"])
        refresh()
        st.success("Saved.")

    st.subheader("Staff")
    sdf = pd.DataFrame(load_staff())
    sedited = st.data_editor(sdf, num_rows="dynamic", use_container_width=True, hide_index=True,
                             column_config={
                                 "name": st.column_config.TextColumn("Name"),
                                 "staff_type": st.column_config.SelectboxColumn("Type",
                                     options=["full_time", "fixed_part_time", "trial"]),
                                 "contracted_hours": st.column_config.NumberColumn("Contracted hrs/wk", min_value=0.0),
                                 "shift_start": st.column_config.TextColumn("Shift start"),
                                 "shift_end": st.column_config.TextColumn("Shift end"),
                                 "max_shift_hours": st.column_config.NumberColumn("Max hrs/shift", min_value=1.0, max_value=11.0),
                                 "requires_pairing": st.column_config.CheckboxColumn("Must pair w/ FT"),
                                 "priority": st.column_config.NumberColumn("Priority (1 first)", min_value=1, max_value=9),
                                 "active": st.column_config.CheckboxColumn("Active"),
                             })
    if st.button("Save staff"):
        db.sync_table("staff", sedited, ["name", "staff_type", "contracted_hours", "shift_start", "shift_end",
                                         "max_shift_hours", "requires_pairing", "priority", "active"])
        refresh()
        st.success("Saved.")
