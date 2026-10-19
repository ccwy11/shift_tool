"""Business logic — pure Python, no Streamlit imports. Port this 1:1 to
TypeScript in Phase 2.

Core rules:
- Shifts can only be SHORTER than the template, never longer.
- Allocation order: priority (full_time < fixed_part_time < trial).
- Trial/support staff must overlap at least one full-timer (pairing).
- Hard blocks (lunch) must meet min_headcount / min_parttimers.
"""

from datetime import date, timedelta


def t2m(t):
    """'HH:MM' or datetime.time -> minutes since midnight."""
    if hasattr(t, "hour"):
        return t.hour * 60 + t.minute
    h, m = str(t).split(":")[:2]
    return int(h) * 60 + int(m)


def m2t(mins):
    return f"{int(mins)//60:02d}:{int(mins)%60:02d}"


def overlaps(s1, e1, s2, e2):
    return s1 < e2 and e1 > s2


def subtract_windows(windows, u_start, u_end):
    """Subtract one unavailable window from a list of (start, end) windows."""
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


def available_windows(staff, day_unavail, weekly_unavail, weekday, open_m, close_m):
    """Free minutes within the staff member's template shift for a given date."""
    base = (max(open_m, t2m(staff["shift_start"])), min(close_m, t2m(staff["shift_end"])))
    windows = [base] if base[1] > base[0] else []

    for u in day_unavail:
        if not windows:
            break
        if u["all_day"]:
            return []
        windows = subtract_windows(windows, t2m(u["start"]), t2m(u["end"]))

    for u in weekly_unavail:
        if u["weekday"] != weekday or not windows:
            continue
        if u["all_day"]:
            return []
        windows = subtract_windows(windows, t2m(u["start"]), t2m(u["end"]))

    return windows


def build_day(staff_list, blocks, d, day_unavail, weekly_unavail, min_shift_minutes):
    """Greedy allocation for one date. Returns list of shift dicts."""
    weekday = d.weekday()
    assignments = []
    ordered = sorted(staff_list, key=lambda p: (p["priority"], p["name"]))
    for p in ordered:
        if not p["active"]:
            continue
        wins = available_windows(p, day_unavail.get(p["id"], []), weekly_unavail.get(p["id"], []),
                                 weekday, t2m(blocks["_open"]), t2m(blocks["_close"]))
        if not wins:
            continue
        s, e = max(wins, key=lambda w: w[1] - w[0])       # longest free window
        max_len = int(p["max_shift_hours"] * 60)
        if e - s > max_len:
            e = s + max_len                               # cap, never extend
        if e - s < min_shift_minutes:
            continue
        assignments.append({
            "staff_id": p["id"], "name": p["name"], "staff_type": p["staff_type"],
            "start_m": s, "end_m": e, "requires_pairing": bool(p["requires_pairing"]),
        })
    return assignments


def coverage(assignments, blocks):
    """Per-block headcount + part-timer count + pairing check. Returns rows."""
    rows = []
    for b in blocks["blocks"]:
        bs, be = t2m(b["start"]), t2m(b["end"])
        on = [a for a in assignments if overlaps(a["start_m"], a["end_m"], bs, be)]
        pts = [a for a in on if a["staff_type"] != "full_time"]
        pairing_ok = True
        for a in on:
            if a["requires_pairing"]:
                if not any(o["staff_type"] == "full_time" and o["staff_id"] != a["staff_id"]
                           for o in on):
                    pairing_ok = False
        rows.append({
            "block": b["name"], "start": b["start"], "end": b["end"],
            "count": len(on), "target": b["min_headcount"],
            "parttimers": len(pts), "pt_target": b["min_parttimers"],
            "hard": bool(b["hard"]), "pairing_ok": pairing_ok,
            "short": len(on) < b["min_headcount"] or len(pts) < b["min_parttimers"] or not pairing_ok,
        })
    return rows


def week_dates(anchor, week_start=0):
    """Monday-Sunday (or week_start offset) containing anchor date."""
    start = anchor - timedelta(days=(anchor.weekday() - week_start) % 7)
    return [start + timedelta(days=i) for i in range(7)]
