# db.py
from __future__ import annotations

import os
import sqlite3
from typing import List, Optional, Tuple
from datetime import datetime, date, timedelta
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE_DIR, "lab_planner.db")


def _conn() -> sqlite3.Connection:
    return sqlite3.connect(DB, check_same_thread=False)


def normalize(s: Optional[str]) -> str:
    return (s or "").strip()


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def _columns(conn: sqlite3.Connection, table: str) -> List[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]


# ── Business-day arithmetic ────────────────────────────────────

def add_business_days(d: date, n: int) -> date:
    """Shift a date by n business days (negative = backward)."""
    if n == 0:
        return d
    step = 1 if n > 0 else -1
    remaining = abs(n)
    while remaining > 0:
        d += timedelta(days=step)
        if d.weekday() < 5:
            remaining -= 1
    return d


# ── Schema init + live migrations ─────────────────────────────

def init_db() -> None:
    conn = _conn()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS technicians (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS instruments (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        )
    """)

    # Core table.
    # Each row = ONE day-slot for an assignment.
    # Multiple rows share the same assignment_id (logical job key).
    # Scheduled slots: start_date = end_date = the day.
    # Waiting slots:  start_date = end_date = NULL.
    c.execute("""
        CREATE TABLE IF NOT EXISTS assignments (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            assignment_id TEXT NOT NULL,
            technician    TEXT NOT NULL,
            instrument    TEXT NOT NULL,
            project       TEXT NOT NULL,
            material      TEXT,
            preprocess    TEXT,
            session       TEXT DEFAULT 'FULL',
            start_date    TEXT,
            end_date      TEXT,
            notes         TEXT,
            status        TEXT NOT NULL DEFAULT 'waiting',
            locked        INTEGER NOT NULL DEFAULT 0,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL
        )
    """)

    # Live migrations for existing DBs
    existing = _columns(conn, "assignments")
    if "locked" not in existing:
        conn.execute("ALTER TABLE assignments ADD COLUMN locked INTEGER NOT NULL DEFAULT 0")

    # Researcher feedback
    c.execute("""
        CREATE TABLE IF NOT EXISTS feedback (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            assignment_db_id INTEGER,
            assignment_id    TEXT,
            technician       TEXT,
            instrument       TEXT,
            project          TEXT,
            day              TEXT,
            researcher_name  TEXT,
            feedback_type    TEXT,
            reason           TEXT,
            proposed_date    TEXT,
            status           TEXT NOT NULL DEFAULT 'pending',
            received_at      TEXT NOT NULL,
            processed_at     TEXT
        )
    """)

    conn.commit()
    conn.close()


# ── Lookup helpers ─────────────────────────────────────────────

def ensure_lookup(table: str, name: str) -> None:
    name = normalize(name)
    if not name:
        raise ValueError(f"{table}: empty value not allowed")
    conn = _conn()
    conn.execute(f"INSERT OR IGNORE INTO {table}(name) VALUES (?)", (name,))
    conn.commit()
    conn.close()


def get_lookup(table: str) -> List[str]:
    conn = _conn()
    rows = conn.execute(
        f"SELECT name FROM {table} ORDER BY name COLLATE NOCASE"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


# ── Assignment / slot CRUD ─────────────────────────────────────

def _insert_slot(conn: sqlite3.Connection, data: dict) -> int:
    """Low-level insert of one slot row. Returns new id."""
    now = _now()
    cur = conn.execute("""
        INSERT INTO assignments (
            assignment_id, technician, instrument, project,
            material, preprocess, session,
            start_date, end_date, notes,
            status, locked, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,0,?,?)
    """, (
        normalize(data.get("assignment_id")),
        normalize(data.get("technician")),
        normalize(data.get("instrument")),
        normalize(data.get("project")),
        normalize(data.get("material")),
        normalize(data.get("preprocess")),
        normalize(data.get("session")) or "FULL",
        data.get("start_date") or None,
        data.get("end_date")   or None,
        normalize(data.get("notes")),
        normalize(data.get("status")) or "waiting",
        now, now,
    ))
    return cur.lastrowid


def add_assignment_slots(data: dict, n_days: int = 1) -> List[int]:
    """
    Create n_days waiting slots for a logical assignment.
    All slots share the same assignment_id, tech, instrument, project, etc.
    Returns list of created DB ids.
    """
    technician = normalize(data.get("technician"))
    instrument = normalize(data.get("instrument"))
    project    = normalize(data.get("project"))

    if not technician or not instrument or not project:
        raise ValueError("Technician, Instrument and Project are required.")

    for val, tbl in ((technician, "technicians"),
                     (instrument, "instruments"),
                     (project,    "projects")):
        ensure_lookup(tbl, val)

    slot_data = dict(data)
    slot_data["status"]     = "waiting"
    slot_data["start_date"] = None
    slot_data["end_date"]   = None

    conn = _conn()
    ids = [_insert_slot(conn, slot_data) for _ in range(max(1, int(n_days)))]
    conn.commit()
    conn.close()
    return ids


def add_extra_slot(parent_id: int) -> int:
    """
    Clone an existing slot's metadata to add one more waiting slot
    for the same logical assignment. Returns new DB id.
    """
    conn = _conn()
    row = conn.execute(
        "SELECT assignment_id, technician, instrument, project, "
        "       material, preprocess, session, notes "
        "FROM assignments WHERE id=?",
        (parent_id,),
    ).fetchone()
    if row is None:
        conn.close()
        raise ValueError(f"Slot {parent_id} not found")
    aid, tech, instr, proj, mat, prep, sess, notes = row

    new_id = _insert_slot(conn, {
        "assignment_id": aid,
        "technician":    tech,
        "instrument":    instr,
        "project":       proj,
        "material":      mat or "",
        "preprocess":    prep or "",
        "session":       sess or "FULL",
        "notes":         notes or "",
        "status":        "waiting",
    })
    conn.commit()
    conn.close()
    return new_id


# Legacy single-slot add (kept for backward-compat with dialog forms)
def add_assignment(data: dict) -> None:
    technician = normalize(data.get("technician"))
    instrument = normalize(data.get("instrument"))
    project    = normalize(data.get("project"))
    if not technician or not instrument or not project:
        raise ValueError("Technician, Instrument and Project are required.")
    for val, tbl in ((technician, "technicians"),
                     (instrument, "instruments"),
                     (project,    "projects")):
        ensure_lookup(tbl, val)
    conn = _conn()
    _insert_slot(conn, data)
    conn.commit()
    conn.close()


def update_assignment(assign_id: int, data: dict) -> None:
    allowed = {
        "assignment_id", "technician", "instrument", "project",
        "material", "preprocess", "session",
        "start_date", "end_date", "notes", "status",
    }
    filtered = {k: v for k, v in data.items() if k in allowed}
    if not filtered:
        return
    for field, table in (("technician", "technicians"),
                         ("instrument",  "instruments"),
                         ("project",     "projects")):
        if field in filtered and filtered[field]:
            ensure_lookup(table, filtered[field])
    sets = ", ".join(f"{k}=?" for k in filtered)
    vals = list(filtered.values()) + [_now(), assign_id]
    conn = _conn()
    conn.execute(f"UPDATE assignments SET {sets}, updated_at=? WHERE id=?", vals)
    conn.commit()
    conn.close()


def schedule_slot(slot_id: int, technician: str, day: str, session: str = "FULL") -> None:
    """Schedule a single waiting slot to one specific day."""
    conn = _conn()
    conn.execute("""
        UPDATE assignments
        SET technician=?, start_date=?, end_date=?,
            session=?, status='scheduled', updated_at=?
        WHERE id=?
    """, (normalize(technician), day, day,
          normalize(session) or "FULL", _now(), slot_id))
    conn.commit()
    conn.close()


# Keep old name for backward-compat (drag-drop section)
def schedule_assignment(
    assign_id: int,
    technician: str,
    start_date: str,
    end_date: str,
    session: str,
) -> None:
    """Backward-compat wrapper; in new model start_date == end_date."""
    schedule_slot(assign_id, technician, start_date, session)


def set_assignment_waiting(assign_id: int) -> None:
    conn = _conn()
    conn.execute("""
        UPDATE assignments
        SET status='waiting', start_date=NULL, end_date=NULL, updated_at=?
        WHERE id=?
    """, (_now(), assign_id))
    conn.commit()
    conn.close()


def delete_assignment(assign_id: int) -> None:
    conn = _conn()
    conn.execute("DELETE FROM assignments WHERE id=?", (assign_id,))
    conn.commit()
    conn.close()


def update_assignment_session(assign_id: int, session: str) -> None:
    conn = _conn()
    conn.execute(
        "UPDATE assignments SET session=?, updated_at=? WHERE id=?",
        (normalize(session) or "FULL", _now(), assign_id),
    )
    conn.commit()
    conn.close()


# ── Grouped / aggregated queries ──────────────────────────────

def get_assignments_df() -> pd.DataFrame:
    conn = _conn()
    df = pd.read_sql_query("SELECT * FROM assignments ORDER BY assignment_id, id", conn)
    conn.close()
    return df


def get_waiting_df() -> pd.DataFrame:
    conn = _conn()
    df = pd.read_sql_query(
        "SELECT * FROM assignments WHERE status='waiting' ORDER BY assignment_id, id", conn
    )
    conn.close()
    return df


def get_assignment_groups(status_filter: Optional[str] = None) -> List[dict]:
    """
    Return all logical assignments grouped by assignment_id.
    Each entry has metadata + a list of slots.

    status_filter: if 'waiting', only groups that have at least one waiting slot.
    """
    conn = _conn()
    df = pd.read_sql_query(
        "SELECT * FROM assignments ORDER BY assignment_id, start_date, id", conn
    )
    conn.close()
    if df.empty:
        return []

    groups = []
    for aid, grp in df.groupby("assignment_id", sort=False):
        slots = grp.to_dict(orient="records")
        n_wait  = sum(1 for s in slots if s["status"] == "waiting")
        n_sched = sum(1 for s in slots if s["status"] == "scheduled")
        if status_filter == "waiting" and n_wait == 0:
            continue
        first = slots[0]
        groups.append({
            "assignment_id": aid,
            "technician":    first["technician"],
            "instrument":    first["instrument"],
            "project":       first["project"],
            "material":      first.get("material")   or "",
            "preprocess":    first.get("preprocess") or "",
            "session":       (first.get("session") or "FULL").upper(),
            "notes":         first.get("notes") or "",
            "slots":         slots,
            "n_total":       len(slots),
            "n_waiting":     n_wait,
            "n_scheduled":   n_sched,
        })

    # Groups with waiting slots first, then alphabetical
    groups.sort(key=lambda g: (g["n_waiting"] == 0, g["assignment_id"]))
    return groups


# ── Lock / unlock ──────────────────────────────────────────────

def toggle_lock(assign_id: int) -> bool:
    conn = _conn()
    row = conn.execute(
        "SELECT locked FROM assignments WHERE id=?", (assign_id,)
    ).fetchone()
    if row is None:
        conn.close()
        return False
    new_val = 0 if row[0] else 1
    conn.execute(
        "UPDATE assignments SET locked=?, updated_at=? WHERE id=?",
        (new_val, _now(), assign_id),
    )
    conn.commit()
    conn.close()
    return bool(new_val)


# ── Technician availability ────────────────────────────────────

def get_busy_days_with_sessions(tech: str, from_date: str) -> dict:
    """
    Return {date_iso: set_of_sessions} for all scheduled assignments
    of tech on or after from_date.
    """
    conn = _conn()
    rows = conn.execute(
        "SELECT start_date, session FROM assignments "
        "WHERE technician=? AND status='scheduled' AND start_date >= ?",
        (normalize(tech), from_date),
    ).fetchall()
    conn.close()
    busy: dict = {}
    for day, sess in rows:
        if day:
            busy.setdefault(day, set()).add((sess or "FULL").upper())
    return busy


def _day_free_for_session(day_sessions: set, slot_session: str) -> bool:
    """True if the day still has capacity for slot_session."""
    if not day_sessions:
        return True
    ss = (slot_session or "FULL").upper()
    if "FULL" in day_sessions:
        return False
    if ss == "FULL":
        return False      # any existing booking blocks a FULL slot
    if ss == "AM":
        return "AM" not in day_sessions
    if ss == "PM":
        return "PM" not in day_sessions
    return False


# ── Schedule shift ─────────────────────────────────────────────

def preview_shift(
    tech: str, from_date: str, delta: int
) -> Tuple[List[dict], List[dict]]:
    conn = _conn()
    rows = conn.execute(
        "SELECT id, assignment_id, instrument, project, "
        "       start_date, end_date, locked "
        "FROM assignments "
        "WHERE technician=? AND status='scheduled' AND start_date >= ? "
        "ORDER BY start_date",
        (normalize(tech), from_date),
    ).fetchall()
    conn.close()

    will_shift, will_skip = [], []
    for rid, aid, instr, proj, sd, ed, locked in rows:
        item = {
            "id":            rid,
            "assignment_id": aid   or "",
            "instrument":    instr or "",
            "project":       proj  or "",
            "old_start":     sd,
            "old_end":       ed,
        }
        if locked:
            will_skip.append(item)
        else:
            try:
                item["new_start"] = add_business_days(date.fromisoformat(sd), delta).isoformat()
                item["new_end"]   = add_business_days(date.fromisoformat(ed), delta).isoformat()
            except Exception:
                item["new_start"] = sd
                item["new_end"]   = ed
            will_shift.append(item)
    return will_shift, will_skip


def shift_technician_assignments(
    tech: str, from_date: str, delta: int
) -> Tuple[int, int]:
    will_shift, will_skip = preview_shift(tech, from_date, delta)
    if not will_shift:
        return 0, len(will_skip)
    conn = _conn()
    for item in will_shift:
        conn.execute(
            "UPDATE assignments SET start_date=?, end_date=?, updated_at=? WHERE id=?",
            (item["new_start"], item["new_end"], _now(), item["id"]),
        )
    conn.commit()
    conn.close()
    return len(will_shift), len(will_skip)


# ── Auto-assign ────────────────────────────────────────────────

def auto_assign_waiting_slots(
    slot_ids: List[int],
    from_date: Optional[str] = None,
) -> List[dict]:
    """
    For each slot_id (must be status='waiting'), find the first free
    business day >= from_date for the slot's technician and schedule it.

    AM/PM-aware: a FULL slot needs a fully free day; an AM/PM slot only
    needs its half to be free.

    Returns list of {id, assignment_id, technician, instrument, project, day}.
    """
    if not slot_ids:
        return []
    if from_date is None:
        from_date = date.today().isoformat()

    conn = _conn()
    placeholders = ",".join("?" * len(slot_ids))
    rows = conn.execute(
        f"SELECT id, assignment_id, technician, instrument, project, session "
        f"FROM assignments "
        f"WHERE id IN ({placeholders}) AND status='waiting' "
        f"ORDER BY id",
        slot_ids,
    ).fetchall()
    conn.close()

    if not rows:
        return []

    # Pre-load busy-day maps for all involved technicians
    techs = list({r[2] for r in rows})
    busy: dict = {t: get_busy_days_with_sessions(t, from_date) for t in techs}

    start_d   = date.fromisoformat(from_date)
    scheduled = []

    for rid, aid, tech, instr, proj, sess in rows:
        session = (sess or "FULL").upper()
        d       = start_d

        for _ in range(365):   # safety cap: 1 year
            if d.weekday() < 5:
                day_iso = d.isoformat()
                if _day_free_for_session(busy.get(tech, {}).get(day_iso, set()), session):
                    break
            d += timedelta(days=1)
        else:
            continue   # no free day found within cap

        day_iso = d.isoformat()
        schedule_slot(rid, tech, day_iso, session)

        # Update local cache so the next slot in this batch doesn't conflict
        busy.setdefault(tech, {}).setdefault(day_iso, set()).add(session)
        if session == "FULL":
            busy[tech][day_iso] = {"FULL"}   # FULL blocks everything

        scheduled.append({
            "id":            rid,
            "assignment_id": aid,
            "technician":    tech,
            "instrument":    instr,
            "project":       proj,
            "day":           day_iso,
        })

    return scheduled


# ── Researcher feedback ────────────────────────────────────────

def add_feedback_items(items: List[dict]) -> int:
    conn = _conn()
    now      = _now()
    inserted = 0
    for it in items:
        dup = conn.execute(
            "SELECT id FROM feedback "
            "WHERE assignment_db_id=? AND researcher_name=? "
            "  AND feedback_type=? AND status='pending'",
            (it.get("db_id"), it.get("researcher"), it.get("type")),
        ).fetchone()
        if dup:
            continue
        conn.execute("""
            INSERT INTO feedback (
                assignment_db_id, assignment_id, technician, instrument, project,
                day, researcher_name, feedback_type, reason, proposed_date,
                status, received_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,'pending',?)
        """, (
            it.get("db_id"),
            it.get("assignment_id", ""),
            it.get("tech", ""),
            it.get("instrument", ""),
            it.get("project", ""),
            it.get("day", ""),
            it.get("researcher", ""),
            it.get("type", ""),
            it.get("reason", ""),
            it.get("proposed_date") or None,
            now,
        ))
        inserted += 1
    conn.commit()
    conn.close()
    return inserted


def get_feedback_df() -> pd.DataFrame:
    conn = _conn()
    df = pd.read_sql_query("SELECT * FROM feedback ORDER BY id DESC", conn)
    conn.close()
    return df


def set_feedback_status(fid: int, status: str) -> None:
    conn = _conn()
    conn.execute(
        "UPDATE feedback SET status=?, processed_at=? WHERE id=?",
        (status, _now(), fid),
    )
    conn.commit()
    conn.close()


def delete_feedback(fid: int) -> None:
    conn = _conn()
    conn.execute("DELETE FROM feedback WHERE id=?", (fid,))
    conn.commit()
    conn.close()


def get_db_path() -> str:
    return DB
