"""Business logic — pure Python, no Streamlit imports. Port 1:1 to TS in Phase 2.

v2 — template-based, mirrors the real SEVENS roster:
- Everyone is assigned their default shift template (M1/M2/E/T1-T4).
- opt_out staff (FT): assumed available; OFF entries remove/shorten.
- opt_in staff (PT): only scheduled on dates they marked as available.
- Shifts can only be SHORTER than the template, never longer.
- Coverage is checked per time block against per-weekday targets.
"""

from datetime import timedelta


def t2m(t):
    if hasattr(t, "hour"):
        return t.hour * 60 + t.minute
    h, m = str(t).split(":")[:2]
    return int(h) * 60 + int(m)


def m2t(mins):
    return f"{int(mins)//60:02d}:{int(mins)%60:02d}"


def overlaps(s1, e1, s2, e2):
    return s1 < e2 and e1 > s2


def subtract_windows(windows, u_start, u_end):
    out = []
    for s, e in windows:
        if not overlaps(s, e, u_start, u_end):
            out.append((s, e))
            continue
        if u_start > s:
            out.append((s, min(u_start, e)))
        if u_end < e:
            out.append((max(u_end, s), e))
    return [(s, e) for s, e in out if e > s]


def is_off(p, day_unavail, weekly_unavail, weekday):
    """opt_out: True if OFF entries make the whole template window unusable."""
    for u in day_unavail:
        if u["all_day"]:
            return True
    for u in weekly_unavail:
        if u["weekday"] == weekday and u["all_day"]:
            return True
    return False


def pick_template(p, templates, pref_code=None):
    """Per-day preference > default template > best active template by type."""
    for code in (pref_code, p["default_template"]):
        if not code:
            continue
        for t in templates:
            if t["code"] == code and tpl_applies(t, p) and t["active"]:
                return t
    exact = [t for t in templates if t["active"] and t["applies_to"] == ("full_time" if p["staff_type"] == "full_time" else "part_time")]
    if exact:
        return exact[0]
    anyt = [t for t in templates if t["active"] and t["applies_to"] == "any"]
    return anyt[0] if anyt else None


def tpl_applies(t, p):
    if t["applies_to"] == "any":
        return True
    want = "full_time" if p["staff_type"] == "full_time" else "part_time"
    return t["applies_to"] == want


def build_day(staff_list, templates, blocks, d, day_unavail, weekly_unavail, avail_dates, min_shift_minutes):
    """Assign default templates for one date. Returns list of shift dicts."""
    weekday = d.weekday()
    iso = d.isoformat()
    assigns = []
    for p in sorted(staff_list, key=lambda x: (x["priority"], x["name"])):
        if not p["active"]:
            continue
        pref_code = None
        if p["availability_mode"] == "opt_in":
            dates_map = avail_dates.get(p["id"], {})
            if iso not in dates_map:
                continue
            pref_code = dates_map[iso]
        elif is_off(p, day_unavail.get(p["id"], []), weekly_unavail.get(p["id"], []), weekday):
            continue

        tpl = pick_template(p, templates, pref_code)
        if tpl is None:
            continue
        s, e = t2m(tpl["start"]), t2m(tpl["end"])
        if e - s > int(p["max_shift_hours"] * 60):
            e = s + int(p["max_shift_hours"] * 60)     # cap, never extend

        windows = [(s, e)]
        for u in day_unavail.get(p["id"], []):         # partial OFF windows shorten the shift
            if u["all_day"]:
                windows = []
                break
            windows = subtract_windows(windows, t2m(u["start"]), t2m(u["end"]))
        for u in weekly_unavail.get(p["id"], []):
            if u["weekday"] != weekday or not windows:
                continue
            if u["all_day"]:
                windows = []
                break
            windows = subtract_windows(windows, t2m(u["start"]), t2m(u["end"]))
        if not windows:
            continue
        s2, e2 = max(windows, key=lambda w: w[1] - w[0])
        if e2 - s2 < min_shift_minutes:
            continue
        assigns.append({
            "staff_id": p["id"], "name": p["name"], "staff_type": p["staff_type"],
            "code": tpl["code"], "start_m": s2, "end_m": e2,
            "requires_pairing": bool(p["requires_pairing"]),
        })
    return assigns


def coverage(assignments, targets):
    """targets: block_targets rows for this weekday."""
    rows = []
    for b in targets:
        bs, be = t2m(b["start"]), t2m(b["end"])
        on = [a for a in assignments if overlaps(a["start_m"], a["end_m"], bs, be)]
        pts = [a for a in on if a["staff_type"] != "full_time"]
        pairing_ok = all(
            any(o["staff_type"] == "full_time" and o["staff_id"] != a["staff_id"] for o in on)
            for a in on if a["requires_pairing"])
        rows.append({
            "block": b["block"], "start": b["start"], "end": b["end"],
            "count": len(on), "target": b["min_headcount"],
            "parttimers": len(pts), "pt_target": b["min_parttimers"],
            "hard": bool(b["hard"]), "pairing_ok": pairing_ok,
            "short": len(on) < b["min_headcount"] or len(pts) < b["min_parttimers"] or not pairing_ok,
        })
    return rows


def switch_suggestions(assignments, templates, targets):
    """For short blocks: staff on a short template who could switch to a longer
    same-type template that covers the gap. Manager decides — shifts never
    auto-extend."""
    out = []
    for b in targets:
        bs, be = t2m(b["start"]), t2m(b["end"])
        on = [a for a in assignments if overlaps(a["start_m"], a["end_m"], bs, be)]
        if len(on) >= b["min_headcount"]:
            continue
        for a in assignments:
            if overlaps(a["start_m"], a["end_m"], bs, be):
                continue    # already covers this block
            want = "full_time" if a["staff_type"] == "full_time" else "part_time"
            for t in templates:
                if not t["active"] or t["code"] == a["code"]:
                    continue
                if t["applies_to"] not in (want, "any"):
                    continue
                if t2m(t["end"]) - t2m(t["start"]) <= (a["end_m"] - a["start_m"]):
                    continue    # only suggest LONGER templates
                if overlaps(t2m(t["start"]), t2m(t["end"]), bs, be):
                    out.append(f"{a['name']} {a['code']}→{t['code']} would cover {b['block']}")
                    break
    return out


def missing_pt_slots(assignments, templates, block, n):
    """First n part-time template codes (chronological) that do NOT cover the
    block — i.e. which T-slots you still need people for."""
    bs, be = t2m(block["start"]), t2m(block["end"])
    covered = {a["code"] for a in assignments
               if a["staff_type"] != "full_time" and overlaps(a["start_m"], a["end_m"], bs, be)}
    pool = [t["code"] for t in sorted(templates, key=lambda t: (t2m(t["start"]), t2m(t["end"])))
            if t["applies_to"] == "part_time" and t["active"]]
    return [c for c in pool if c not in covered][:n]


def best_case_outlook(staff_list, templates, weekly_map, targets, weekdays):
    """LAYER 1 — structural check, independent of availability input:
    FT on their fixed recurring pattern, ALL PT assumed available (best case).
    Returns {weekday: (coverage_rows, assignments)}."""
    out = {}
    for wd in weekdays:
        assigns = []
        for p in staff_list:
            if not p["active"]:
                continue
            tpl = pick_template(p, templates)
            if tpl is None:
                continue
            if p["availability_mode"] == "opt_out":
                if any(u["weekday"] == wd and u["all_day"] for u in weekly_map.get(p["id"], [])):
                    continue
            assigns.append({
                "staff_id": p["id"], "name": p["name"], "staff_type": p["staff_type"],
                "code": tpl["code"], "start_m": t2m(tpl["start"]), "end_m": t2m(tpl["end"]),
                "requires_pairing": bool(p["requires_pairing"]),
            })
        out[wd] = (coverage(assigns, targets.get(wd, [])), assigns)
    return out


def week_dates(anchor, week_start=0):
    start = anchor - timedelta(days=(anchor.weekday() - week_start) % 7)
    return [start + timedelta(days=i) for i in range(7)]