"""Storage layer. SQLite for the prototype; schema is Postgres-shaped so it
maps 1:1 onto Supabase tables in Phase 2.

v2 — matches the real SEVENS roster model:
- shift_templates: named shift codes (M1/M2/E/T1-T4) instead of per-person windows
- staff.availability_mode: 'opt_out' (FT: assumed available, mark OFF days)
  vs 'opt_in' (PT: only work days they explicitly mark as available)
- block_targets: per-weekday headcount targets (weekday skeleton crew, Sun closed)
- day_notes: per-date notes (pest control, trial person, etc.)
"""

import os
import sqlite3

DB_PATH = os.environ.get("ROSTER_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "roster.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS shift_templates (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,          -- M1, M2, E, T1..T4
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    applies_to TEXT NOT NULL DEFAULT 'any',  -- full_time | part_time | any
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS staff (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    staff_type TEXT NOT NULL,               -- full_time | fixed_part_time | trial
    contracted_hours REAL NOT NULL DEFAULT 40,
    max_shift_hours REAL NOT NULL DEFAULT 11,
    requires_pairing INTEGER NOT NULL DEFAULT 0,
    priority INTEGER NOT NULL DEFAULT 9,    -- lower = allocated first
    default_template TEXT,                  -- shift_templates.code
    availability_mode TEXT NOT NULL DEFAULT 'opt_out',  -- opt_out | opt_in
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS time_blocks (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    hard INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS block_targets (
    id INTEGER PRIMARY KEY,
    block_id INTEGER NOT NULL REFERENCES time_blocks(id) ON DELETE CASCADE,
    weekday INTEGER NOT NULL,               -- 0=Mon .. 6=Sun
    min_headcount INTEGER NOT NULL DEFAULT 0,
    min_parttimers INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS availability (       -- "I am OFF" entries (opt_out staff)
    id INTEGER PRIMARY KEY,
    staff_id INTEGER NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    all_day INTEGER NOT NULL DEFAULT 1,
    start TEXT,
    end TEXT,
    note TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS availability_weekly ( -- recurring OFF pattern (opt_out staff)
    id INTEGER PRIMARY KEY,
    staff_id INTEGER NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
    weekday INTEGER NOT NULL,
    all_day INTEGER NOT NULL DEFAULT 1,
    start TEXT,
    end TEXT
);

CREATE TABLE IF NOT EXISTS available_dates (     -- "I CAN work" entries (opt_in staff)
    id INTEGER PRIMARY KEY,
    staff_id INTEGER NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    template_code TEXT,                         -- optional preferred shift for that day
    note TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS day_notes (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    text TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS roster (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    staff_id INTEGER NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
    template_code TEXT NOT NULL,
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'auto'     -- auto | manual
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS closed_dates (   -- HK public holidays + shop-specific closures
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL
);
"""

TEMPLATES = [
    ("M1", "09:00", "17:00", "full_time"),
    ("M2", "09:00", "20:00", "full_time"),
    ("E",  "10:00", "21:00", "full_time"),  # evening shift FT-only; PT cover via manual add
    ("T1", "10:00", "14:00", "part_time"),
    ("T2", "10:30", "14:30", "part_time"),
    ("T3", "11:00", "15:00", "part_time"),
    ("T4", "11:00", "14:00", "part_time"),
]

BLOCKS = [
    ("Opening",   "09:00", "11:00", 0),
    ("Lunch",     "11:00", "14:30", 1),
    ("Afternoon", "14:30", "17:30", 0),
    ("Dinner",    "17:30", "20:30", 0),
]

# block name -> (weekdays Mon-Fri target, Saturday target)  (headcount, parttimers)
TARGETS = {
    "Opening":   ((2, 0), (1, 0)),
    "Lunch":     ((8, 4), (2, 0)),
    "Afternoon": ((2, 0), (1, 0)),
    "Dinner":    ((2, 0), (2, 0)),
}

STAFF = [
    # name, type, contracted, max_hrs, pairing, priority, default_tpl, mode
    ("Cheryl",  "full_time",       44, 11, 0, 1, "M2", "opt_out"),
    ("Natalie", "full_time",       40, 11, 0, 1, "M1", "opt_out"),  # sheet: M1 Mon-Fri, Sat off
    ("Angie",   "full_time",       44, 11, 0, 1, "E",  "opt_out"),
    ("Jhaz",    "full_time",       44, 11, 0, 1, "E",  "opt_out"),
    ("Maya",    "fixed_part_time", 20,  4, 0, 3, "T1", "opt_in"),
    ("Louise",  "fixed_part_time", 20,  4, 0, 2, "T2", "opt_in"),
    ("Elaine",  "fixed_part_time", 20,  4, 0, 3, "T3", "opt_in"),
    ("Kamala",  "fixed_part_time", 12,  4, 0, 3, "T4", "opt_in"),
    ("Chun",    "fixed_part_time", 12,  4, 0, 4, "T4", "opt_in"),
    ("Jona",    "fixed_part_time", 20, 11, 0, 2, "T2", "opt_in"),   # can cover E
    ("Puji",    "fixed_part_time", 12,  5, 0, 3, "T4", "opt_in"),
]

# recurring OFF days (staff name -> [weekdays])
WEEKLY_OFFS = {
    "Cheryl": [5], "Natalie": [5], "Jhaz": [5],
    "Louise": [1], "Kamala": [4], "Chun": [3, 4],
}

# opt-in available dates for demo week Mon 2026-10-26 .. Sat 2026-10-31
# (staff name -> [(date, preferred template or None)])
AVAILABLE_DATES = {
    "Maya":   [("2026-10-26", None), ("2026-10-27", None), ("2026-10-28", None),
               ("2026-10-29", None), ("2026-10-30", None)],
    "Louise": [("2026-10-26", None), ("2026-10-28", None), ("2026-10-29", None), ("2026-10-30", None)],
    "Elaine": [("2026-10-26", None), ("2026-10-27", None), ("2026-10-28", None),
               ("2026-10-29", None), ("2026-10-30", None)],
    "Kamala": [("2026-10-26", None), ("2026-10-27", None), ("2026-10-28", None), ("2026-10-29", None)],
    "Chun":   [("2026-10-26", None), ("2026-10-27", None), ("2026-10-28", None)],
    "Jona":   [("2026-10-27", "T2"), ("2026-10-30", "T3")],
    "Puji":   [("2026-10-29", None)],
}

# HK gazetted general holidays (GovHK), 2026-2027. Sundays excluded — already a closed weekday.
HK_HOLIDAYS = [
    ("2026-01-01", "New Year's Day"),
    ("2026-02-17", "Lunar New Year's Day"), ("2026-02-18", "LNY Day 2"), ("2026-02-19", "LNY Day 3"),
    ("2026-04-03", "Good Friday"), ("2026-04-04", "Day after Good Friday"),
    ("2026-04-06", "Easter Monday / Ching Ming (sub)"), ("2026-04-07", "Easter Monday (sub)"),
    ("2026-05-01", "Labour Day"), ("2026-05-25", "Buddha's Birthday (sub)"),
    ("2026-06-19", "Tuen Ng Festival"), ("2026-07-01", "HKSAR Establishment Day"),
    ("2026-09-26", "Day after Mid-Autumn Festival"), ("2026-10-01", "National Day"),
    ("2026-10-19", "Chung Yeung Festival (sub)"),
    ("2026-12-25", "Christmas Day"), ("2026-12-26", "First weekday after Christmas"),
    ("2027-01-01", "New Year's Day"),
    ("2027-02-06", "Lunar New Year's Day"), ("2027-02-08", "LNY Day 3"), ("2027-02-09", "LNY Day 4 (sub)"),
    ("2027-03-26", "Good Friday"), ("2027-03-27", "Day after Good Friday"), ("2027-03-29", "Easter Monday"),
    ("2027-04-05", "Ching Ming Festival"), ("2027-05-01", "Labour Day"), ("2027-05-13", "Buddha's Birthday"),
    ("2027-06-09", "Tuen Ng Festival"), ("2027-07-01", "HKSAR Establishment Day"),
    ("2027-09-16", "Day after Mid-Autumn Festival"), ("2027-10-01", "National Day"),
    ("2027-10-08", "Chung Yeung Festival"),
    ("2027-12-25", "Christmas Day"), ("2027-12-27", "First weekday after Christmas"),
]

SETTINGS_DEFAULTS = {
    "open_time": "09:00",
    "close_time": "21:00",
    "min_shift_hours": "3",
    "closed_weekdays": "6",     # 6 = Sunday closed
    "yellow_margin": "1",       # how many below target = yellow (worse = red)
}


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def query(sql, params=()):
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def execute(sql, params=()):
    with get_conn() as conn:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        for k, v in SETTINGS_DEFAULTS.items():
            conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))

        if conn.execute("SELECT COUNT(*) FROM shift_templates").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO shift_templates(code, start, end, applies_to) VALUES (?,?,?,?)", TEMPLATES)

        if conn.execute("SELECT COUNT(*) FROM time_blocks").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO time_blocks(name, start, end, hard) VALUES (?,?,?,?)", BLOCKS)
            for name, (wd_target, sat_target) in TARGETS.items():
                bid = conn.execute("SELECT id FROM time_blocks WHERE name=?", (name,)).fetchone()[0]
                for wd in range(5):
                    conn.execute("INSERT INTO block_targets(block_id, weekday, min_headcount, min_parttimers) VALUES (?,?,?,?)",
                                 (bid, wd, wd_target[0], wd_target[1]))
                conn.execute("INSERT INTO block_targets(block_id, weekday, min_headcount, min_parttimers) VALUES (?,?,?,?)",
                             (bid, 5, sat_target[0], sat_target[1]))

        if conn.execute("SELECT COUNT(*) FROM closed_dates").fetchone()[0] == 0:
            conn.executemany("INSERT OR IGNORE INTO closed_dates(date, name) VALUES (?,?)", HK_HOLIDAYS)

        if conn.execute("SELECT COUNT(*) FROM staff").fetchone()[0] == 0:
            conn.executemany(
                """INSERT INTO staff(name, staff_type, contracted_hours, max_shift_hours,
                   requires_pairing, priority, default_template, availability_mode)
                   VALUES (?,?,?,?,?,?,?,?)""", STAFF)
            for name, wds in WEEKLY_OFFS.items():
                sid = conn.execute("SELECT id FROM staff WHERE name=?", (name,)).fetchone()[0]
                for wd in wds:
                    conn.execute("INSERT INTO availability_weekly(staff_id, weekday, all_day) VALUES (?,?,1)", (sid, wd))
            for name, dates in AVAILABLE_DATES.items():
                sid = conn.execute("SELECT id FROM staff WHERE name=?", (name,)).fetchone()[0]
                for d, tpl in dates:
                    conn.execute("INSERT INTO available_dates(staff_id, date, template_code) VALUES (?,?,?)", (sid, d, tpl))
            conn.execute("INSERT INTO day_notes(date, text) VALUES ('2026-10-26', 'Pest Control')")
            # Saturday E for Jona is a manager decision (E reserved for FT) -> manual roster entry
            jona_id = conn.execute("SELECT id FROM staff WHERE name='Jona'").fetchone()[0]
            conn.execute("""INSERT INTO roster(date, staff_id, template_code, start, end, source)
                            VALUES ('2026-10-31', ?, 'E', '10:00', '21:00', 'manual')""", (jona_id,))
        conn.commit()


# --- settings ---------------------------------------------------------------

def get_settings():
    return {r["key"]: r["value"] for r in query("SELECT key, value FROM settings")}


def set_setting(key, value):
    execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)))


# --- generic save for data_editor results ------------------------------------

def pd_isna(v):
    try:
        import pandas as pd
        return pd.isna(v)
    except Exception:
        return v is None


def sync_table(table, df, columns):
    """Upsert rows present in df, delete rows whose ids disappeared."""
    with get_conn() as conn:
        existing = {r["id"] for r in conn.execute(f"SELECT id FROM {table}").fetchall()}
        keep = set()
        for _, row in df.iterrows():
            vals = [row[c] for c in columns]
            if pd_isna(row.get("id")):
                cur = conn.execute(
                    f"INSERT INTO {table}({','.join(columns)}) VALUES ({','.join('?'*len(columns))})", vals)
                keep.add(cur.lastrowid)
            else:
                keep.add(int(row["id"]))
                sets = ",".join(f"{c}=?" for c in columns)
                conn.execute(f"UPDATE {table} SET {sets} WHERE id=?", vals + [int(row["id"])])
        for gone in existing - keep:
            conn.execute(f"DELETE FROM {table} WHERE id=?", (gone,))
        conn.commit()