"""Storage layer. SQLite for the prototype; schema is Postgres-shaped so it
maps 1:1 onto Supabase tables in Phase 2 (staff, availability, time_blocks,
roster, settings)."""

import os
import sqlite3

DB_PATH = os.environ.get("ROSTER_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "roster.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS staff (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    staff_type TEXT NOT NULL,               -- full_time | fixed_part_time | trial
    contracted_hours REAL NOT NULL DEFAULT 40,
    shift_start TEXT NOT NULL DEFAULT '10:00',
    shift_end TEXT NOT NULL DEFAULT '21:00',
    max_shift_hours REAL NOT NULL DEFAULT 11,
    requires_pairing INTEGER NOT NULL DEFAULT 0,  -- trial/support must overlap a full-timer
    priority INTEGER NOT NULL DEFAULT 9,    -- lower = allocated first (full_time=1)
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS time_blocks (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    min_headcount INTEGER NOT NULL DEFAULT 0,
    min_parttimers INTEGER NOT NULL DEFAULT 0,
    hard INTEGER NOT NULL DEFAULT 0         -- 1 = non-negotiable (lunch)
);

CREATE TABLE IF NOT EXISTS availability (
    id INTEGER PRIMARY KEY,
    staff_id INTEGER NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
    date TEXT NOT NULL,                     -- YYYY-MM-DD
    all_day INTEGER NOT NULL DEFAULT 1,
    start TEXT,                             -- NULL when all_day
    end TEXT,
    note TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS availability_weekly (
    id INTEGER PRIMARY KEY,
    staff_id INTEGER NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
    weekday INTEGER NOT NULL,               -- 0=Mon .. 6=Sun
    all_day INTEGER NOT NULL DEFAULT 1,
    start TEXT,
    end TEXT
);

CREATE TABLE IF NOT EXISTS roster (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    staff_id INTEGER NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'auto'     -- auto | manual
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

DEFAULT_BLOCKS = [
    ("Prep",   "09:00", "11:30", 2, 0, 0),
    ("Lunch",  "11:30", "14:30", 6, 2, 1),
    ("Afternoon", "14:30", "17:30", 2, 0, 0),
    ("Dinner", "17:30", "20:30", 3, 0, 0),
]

SETTINGS_DEFAULTS = {
    "open_time": "09:00",
    "close_time": "21:00",
    "min_shift_hours": "3",
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
        if conn.execute("SELECT COUNT(*) FROM time_blocks").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO time_blocks(name, start, end, min_headcount, min_parttimers, hard) VALUES (?,?,?,?,?,?)",
                DEFAULT_BLOCKS,
            )
        if conn.execute("SELECT COUNT(*) FROM staff").fetchone()[0] == 0:
            conn.executemany(
                """INSERT INTO staff(name, staff_type, contracted_hours, shift_start, shift_end,
                   max_shift_hours, requires_pairing, priority) VALUES (?,?,?,?,?,?,?,?)""",
                [
                    ("(Demo) Chef A", "full_time", 44, "10:00", "21:00", 11, 0, 1),
                    ("(Demo) Cook B", "full_time", 44, "10:00", "21:00", 11, 0, 1),
                    ("(Demo) PT C", "fixed_part_time", 20, "11:00", "15:00", 6, 0, 2),
                    ("(Demo) Trial D", "trial", 12, "11:30", "14:30", 4, 1, 3),
                ],
            )
        conn.commit()


# --- settings ---------------------------------------------------------------

def get_settings():
    rows = query("SELECT key, value FROM settings")
    return {r["key"]: r["value"] for r in rows}


def set_setting(key, value):
    execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


# --- generic save for data_editor results (staff / blocks) ------------------

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


def pd_isna(v):
    try:
        import pandas as pd
        return pd.isna(v)
    except Exception:
        return v is None
