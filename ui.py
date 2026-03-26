# ui.py  —  Lab Planning Tool
from __future__ import annotations

import base64
import json
import os
from datetime import date, timedelta

import pandas as pd
import streamlit as st

try:
    from streamlit_sortables import sort_items
    _HAS_SORTABLES = True
except ImportError:
    _HAS_SORTABLES = False

from db import (
    init_db,
    add_assignment,
    add_assignment_slots,
    add_extra_slot,
    get_assignments_df,
    get_assignment_groups,
    get_lookup,
    get_waiting_df,
    set_assignment_waiting,
    schedule_slot,
    schedule_assignment,
    update_assignment,
    update_assignment_session,
    toggle_lock,
    delete_assignment,
    preview_shift,
    shift_technician_assignments,
    auto_assign_waiting_slots,
    add_feedback_items,
    get_feedback_df,
    set_feedback_status,
    delete_feedback,
    get_db_path,
    add_business_days,
)
from planner_html import build_single_html


# ══════════════════════════════════════════════════════════════════
#  Bootstrap
# ══════════════════════════════════════════════════════════════════
st.set_page_config(page_title="Lab Planner", layout="wide", page_icon="🔬")
init_db()


# ══════════════════════════════════════════════════════════════════
#  Date helpers
# ══════════════════════════════════════════════════════════════════
def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def week_days(d: date) -> list[date]:
    m = monday(d)
    return [m + timedelta(days=i) for i in range(5)]


# ══════════════════════════════════════════════════════════════════
#  Cell map  {(tech, day_iso): [row_dict, ...]}
# ══════════════════════════════════════════════════════════════════
def build_cell_map(df: pd.DataFrame, days: list[date]) -> dict:
    cell: dict = {}
    if df is None or df.empty:
        return cell
    s = (df[df["status"] == "scheduled"].copy()
         if "status" in df.columns else df.copy())
    s["sd"] = pd.to_datetime(s["start_date"], errors="coerce").dt.date
    s = s.dropna(subset=["sd"])
    ws, we = days[0], days[-1]
    s = s[(s["sd"] >= ws) & (s["sd"] <= we)]
    for _, r in s.iterrows():
        k = (r["technician"], r["sd"].isoformat())
        cell.setdefault(k, []).append(r.to_dict())
    return cell


# ══════════════════════════════════════════════════════════════════
#  Slot position helpers
# ══════════════════════════════════════════════════════════════════
def get_slot_positions(df: pd.DataFrame) -> dict:
    """
    Returns {db_id: (slot_pos, slot_total)} for all scheduled slots,
    where slot_pos is 1-based rank ordered by date within assignment_id.
    """
    if df is None or df.empty:
        return {}

    # Count total slots per assignment (all statuses)
    totals: dict = {}
    for _, r in df.iterrows():
        aid = str(r.get("assignment_id", ""))
        totals[aid] = totals.get(aid, 0) + 1

    sched = df[df["status"] == "scheduled"].copy()
    sched["_sd"] = pd.to_datetime(sched["start_date"], errors="coerce").dt.date
    sched = sched.dropna(subset=["_sd"])
    sched = sched.sort_values(["assignment_id", "_sd"])
    sched["_pos"] = sched.groupby("assignment_id").cumcount() + 1

    result: dict = {}
    for _, r in sched.iterrows():
        aid = str(r.get("assignment_id", ""))
        result[int(r["id"])] = (int(r["_pos"]), totals.get(aid, 1))
    return result


# ══════════════════════════════════════════════════════════════════
#  HTML card renderer
# ══════════════════════════════════════════════════════════════════
def card_html(r: dict, accent: str = "#0072B2",
              slot_pos: int = 0, slot_total: int = 0) -> str:
    sess   = (r.get("session") or "FULL").upper()
    locked = bool(r.get("locked", 0))
    badge  = (
        f"<span style='background:#dbeafe;color:#1e3a8a;border-radius:99px;"
        f"padding:1px 8px;font-size:10px;margin-left:5px;font-weight:700'>{sess}</span>"
        if sess != "FULL" else ""
    )
    slot_badge = (
        f"<span style='background:#f0fdf4;color:#166534;border:1px solid #bbf7d0;"
        f"border-radius:99px;padding:1px 7px;font-size:10px;"
        f"font-weight:700;margin-left:5px;vertical-align:middle'>"
        f"{slot_pos}/{slot_total}</span>"
        if slot_total > 1 else ""
    )
    lock_icon = " 🔒" if locked else ""
    instr = r.get("instrument",    "") or ""
    aid   = r.get("assignment_id", "") or ""
    proj  = r.get("project",       "") or ""
    mat   = r.get("material",      "") or ""
    prep  = r.get("preprocess",    "") or ""
    notes = r.get("notes",         "") or ""
    body  = (
        f"<div style='font-weight:800;font-size:.9em;color:#0f172a'>"
        f"{instr}{badge}{slot_badge}{lock_icon}</div>"
        f"<div style='font-weight:600;font-size:.83em;color:#1a4a7a'>{aid} — {proj}</div>"
        + (f"<div style='font-size:.76em;color:#64748b;margin-top:2px'>📦 {mat}</div>"  if mat   else "")
        + (f"<div style='font-size:.76em;color:#64748b'>⚗️ {prep}</div>"               if prep  else "")
        + (f"<div style='font-size:.76em;color:#64748b;font-style:italic'>📝 {notes}</div>" if notes else "")
    )
    border = "#dc2626" if locked else accent
    return (
        f"<div style='border-left:5px solid {border};border-radius:8px;"
        f"padding:8px 10px;background:#ffffff;"
        f"box-shadow:0 1px 4px rgba(0,0,0,.08);margin:3px 0'>{body}</div>"
    )


def waiting_slot_label(s: dict) -> str:
    return (
        f"{s.get('assignment_id','')} | "
        f"{s.get('instrument','')} | "
        f"{s.get('project','')} | "
        f"{s.get('technician','')} | "
        f"DB#{s['id']}"
    )


# ══════════════════════════════════════════════════════════════════
#  Pick-or-add widget
# ══════════════════════════════════════════════════════════════════
def _pick(label: str, options: list[str], key: str, default: str = "") -> str:
    opts = [""] + options
    idx  = opts.index(default) if default and default in opts else 0
    sel  = st.selectbox(label, opts, index=idx, key=f"{key}_sel")
    new  = st.text_input(f"↳ or add new {label.lower()}", key=f"{key}_new").strip()
    return (new or sel).strip()


# ══════════════════════════════════════════════════════════════════
#  Drag-and-drop helpers
# ══════════════════════════════════════════════════════════════════
def _card_str(row) -> str:
    """Compact string label used inside sortable containers."""
    if isinstance(row, dict):
        r = row
    else:
        r = row.to_dict() if hasattr(row, "to_dict") else {}
    aid   = r.get("assignment_id", "") or ""
    instr = r.get("instrument",    "") or ""
    proj  = r.get("project",       "") or ""
    tech  = r.get("technician",    "") or ""
    sess  = (r.get("session") or "FULL").upper()
    rid   = int(r.get("id", 0))
    return f"#{rid} {aid} | {instr} | {proj} | {tech} [{sess}]"


def _parse_id(s: str):
    try:
        # Format: "#<id> ..."
        return int(s.split()[0].lstrip("#"))
    except Exception:
        try:
            return int(s.split("|", 1)[0].strip().replace("ID:", "").strip())
        except Exception:
            return None


# ══════════════════════════════════════════════════════════════════
#  DIALOGS
# ══════════════════════════════════════════════════════════════════

@st.dialog("📅 Assign to cell", width="large")
def dlg_assign_cell(tech: str, day_iso: str) -> None:
    day = date.fromisoformat(day_iso)
    st.markdown(
        f"<span style='background:#eff6ff;border-radius:8px;padding:6px 14px;"
        f"display:inline-block;margin-bottom:10px'>"
        f"👷 <b>{tech}</b> &nbsp;·&nbsp; 📅 <b>{day.strftime('%A %d %B %Y')}</b></span>",
        unsafe_allow_html=True,
    )
    tab_pick, tab_new = st.tabs(["🔗 Pick from waiting list", "➕ Create new"])

    with tab_pick:
        waiting_groups = get_assignment_groups(status_filter="waiting")
        tech_slots  = [s for g in waiting_groups for s in g["slots"]
                       if s["status"] == "waiting" and s["technician"] == tech]
        other_slots = [s for g in waiting_groups for s in g["slots"]
                       if s["status"] == "waiting" and s["technician"] != tech]
        all_slots = tech_slots + other_slots

        if not all_slots:
            st.info("No waiting slots — use the **Create new** tab.")
        else:
            labels = [waiting_slot_label(s) for s in all_slots]
            chosen_label = st.selectbox(
                f"Select slot  ({len(tech_slots)} for {tech}, {len(other_slots)} other)",
                labels, key="cell_pick_slot",
            )
            chosen = all_slots[labels.index(chosen_label)]
            sess_opts = ["FULL", "AM", "PM"]
            cur_sess  = (chosen.get("session") or "FULL").upper()
            sess = st.selectbox("Session", sess_opts,
                                index=sess_opts.index(cur_sess) if cur_sess in sess_opts else 0,
                                key="cell_pick_sess")
            c1, c2, _ = st.columns([1.3, 1, 4])
            if c1.button("✅ Assign here", type="primary", use_container_width=True):
                schedule_slot(int(chosen["id"]), tech, day_iso, sess)
                st.rerun()
            if c2.button("✖ Cancel", use_container_width=True):
                st.rerun()

    with tab_new:
        with st.form("dlg_new_assign", clear_on_submit=True):
            aid = st.text_input("Assignment ID *")
            c1, c2 = st.columns(2)
            with c1:
                instr = _pick("Instrument *", get_lookup("instruments"), "cn_i")
                proj  = _pick("Project *",    get_lookup("projects"),    "cn_p")
            with c2:
                mat   = st.text_input("Material")
                prep  = st.text_input("Preprocess")
                sess  = st.selectbox("Session", ["FULL", "AM", "PM"])
                notes = st.text_area("Notes", height=60)
            b1, b2, _ = st.columns([1.3, 1, 4])
            ok  = b1.form_submit_button("✅ Create & assign", type="primary",
                                        use_container_width=True)
            can = b2.form_submit_button("✖ Cancel", use_container_width=True)
            if ok:
                errs = []
                if not aid.strip(): errs.append("Assignment ID required.")
                if not instr:       errs.append("Instrument required.")
                if not proj:        errs.append("Project required.")
                if errs:
                    for e in errs: st.error(e)
                else:
                    add_assignment({
                        "assignment_id": aid.strip(), "technician": tech,
                        "instrument": instr, "project": proj,
                        "material": mat, "preprocess": prep, "session": sess,
                        "start_date": day_iso, "end_date": day_iso,
                        "notes": notes, "status": "scheduled",
                    })
                    st.rerun()
            if can:
                st.rerun()


@st.dialog("✏️ Edit Slot", width="large")
def dlg_edit(row: dict) -> None:
    rid    = int(row["id"])
    locked = bool(row.get("locked", 0))
    is_waiting = row.get("status", "scheduled") == "waiting"

    st.markdown(
        f"<span style='background:{'#fff1f2' if locked else '#f0fdf4'};"
        f"border-radius:8px;padding:6px 12px;display:inline-block;margin-bottom:8px'>"
        f"{'🔒 Locked &nbsp;·&nbsp; ' if locked else ''}"
        f"📋 <b>{row.get('assignment_id','')}</b> &nbsp;·&nbsp; DB #{rid}"
        f"{'&nbsp;·&nbsp; ⏳ Waiting' if is_waiting else ''}</span>",
        unsafe_allow_html=True,
    )
    if locked:
        st.warning("🔒 Locked. Unlock first to change dates or move to waiting.")

    with st.form("dlg_edit_form"):
        c1, c2 = st.columns(2)
        with c1:
            instr = _pick("Instrument *", get_lookup("instruments"), "de_i",
                          default=row.get("instrument", ""))
            proj  = _pick("Project *",    get_lookup("projects"),    "de_p",
                          default=row.get("project", ""))
        with c2:
            mat  = st.text_input("Material",   value=row.get("material",  "") or "")
            prep = st.text_input("Preprocess", value=row.get("preprocess","") or "")
            sess_opts = ["FULL", "AM", "PM"]
            cur_sess  = (row.get("session") or "FULL").upper()
            sess = st.selectbox("Session", sess_opts,
                                index=sess_opts.index(cur_sess) if cur_sess in sess_opts else 0)
            tech_opts = get_lookup("technicians")
            cur_tech  = row.get("technician", "")
            tech = st.selectbox("Technician", tech_opts,
                                index=tech_opts.index(cur_tech) if cur_tech in tech_opts else 0)
            if not is_waiting:
                cur_sd = row.get("start_date")
                day = st.date_input(
                    "Day",
                    value=date.fromisoformat(cur_sd) if cur_sd else date.today(),
                    disabled=locked,
                )
            notes = st.text_area("Notes", value=row.get("notes","") or "", height=60)

        n_cols = 5 if not is_waiting else 4
        cols = st.columns([1.3, 1.5, 1.2, 1, 1] if not is_waiting else [1.3, 1.2, 1, 1, 1])
        ok      = cols[0].form_submit_button("✅ Save",        type="primary", use_container_width=True)
        if not is_waiting:
            wait = cols[1].form_submit_button("⏳ → Waiting",  use_container_width=True, disabled=locked)
        lock_lbl = "🔓 Unlock" if locked else "🔒 Lock"
        lock_col = cols[2] if not is_waiting else cols[1]
        do_lock = lock_col.form_submit_button(lock_lbl, use_container_width=True)
        can_col = cols[3] if not is_waiting else cols[2]
        can     = can_col.form_submit_button("✖", use_container_width=True)

        if ok:
            errs = []
            if not instr: errs.append("Instrument required.")
            if not proj:  errs.append("Project required.")
            if errs:
                for e in errs: st.error(e)
            else:
                update_data: dict = {
                    "technician": tech, "instrument": instr, "project": proj,
                    "material": mat, "preprocess": prep, "session": sess,
                    "notes": notes,
                }
                if is_waiting:
                    update_data["status"] = "waiting"
                    update_data["start_date"] = None
                    update_data["end_date"]   = None
                else:
                    update_data["start_date"] = day.isoformat()
                    update_data["end_date"]   = day.isoformat()
                    update_data["status"]     = "scheduled"
                update_assignment(rid, update_data)
                st.rerun()

        if not is_waiting and not locked:
            if "wait" in dir() and wait:
                set_assignment_waiting(rid)
                st.rerun()
        if do_lock:
            toggle_lock(rid)
            st.rerun()
        if can:
            st.rerun()


@st.dialog("📅 Schedule waiting slot", width="small")
def dlg_schedule_slot(slot: dict) -> None:
    rid = int(slot["id"])
    st.markdown(
        f"**{slot.get('assignment_id','')}** &nbsp;·&nbsp; "
        f"{slot.get('instrument','')} / {slot.get('project','')}  \n"
        f"👷 {slot.get('technician','')}",
    )
    with st.form("dlg_sched_slot"):
        tech_opts = get_lookup("technicians")
        cur_tech  = slot.get("technician", "")
        tech = st.selectbox("Technician", tech_opts,
                            index=tech_opts.index(cur_tech) if cur_tech in tech_opts else 0)
        day  = st.date_input("Day", value=date.today())
        sess_opts = ["FULL", "AM", "PM"]
        cur_sess  = (slot.get("session") or "FULL").upper()
        sess = st.selectbox("Session", sess_opts,
                            index=sess_opts.index(cur_sess) if cur_sess in sess_opts else 0)
        b1, b2, _ = st.columns([1.3, 1, 3])
        ok  = b1.form_submit_button("📅 Schedule", type="primary", use_container_width=True)
        can = b2.form_submit_button("✖ Cancel", use_container_width=True)
        if ok:
            schedule_slot(rid, tech, day.isoformat(), sess)
            st.rerun()
        if can:
            st.rerun()


# ══════════════════════════════════════════════════════════════════
#  Navigation
# ══════════════════════════════════════════════════════════════════
st.sidebar.title("🔬 Lab Planner")
page = st.sidebar.radio(
    "Navigation",
    ["📅 Manager Board", "📝 Add to Waiting", "📤 Export HTML"],
    index=0,
)
st.sidebar.caption(f"DB: `{get_db_path()}`")

df    = get_assignments_df()
techs = get_lookup("technicians")
slot_positions = get_slot_positions(df)


# ══════════════════════════════════════════════════════════════════
#  PAGE 1 — MANAGER WEEK BOARD
# ══════════════════════════════════════════════════════════════════
if page == "📅 Manager Board":
    st.header("📅 Manager Week Board")

    if not techs:
        st.warning("No technicians yet — add one via **Add to Waiting** first.")
        st.stop()

    # ── Week navigation ────────────────────────────────────────────
    if "mgr_week" not in st.session_state:
        st.session_state["mgr_week"] = date.today()

    nc1, nc2, nc3, nc4 = st.columns([1, 1, 2, 6])
    if nc1.button("◀ Prev", use_container_width=True):
        st.session_state["mgr_week"] = monday(st.session_state["mgr_week"]) - timedelta(days=7)
        st.rerun()
    if nc2.button("Next ▶", use_container_width=True):
        st.session_state["mgr_week"] = monday(st.session_state["mgr_week"]) + timedelta(days=7)
        st.rerun()
    picked = nc3.date_input("", value=st.session_state["mgr_week"],
                             label_visibility="collapsed")
    if picked != st.session_state["mgr_week"]:
        st.session_state["mgr_week"] = picked
        st.rerun()

    days = week_days(st.session_state["mgr_week"])
    nc4.markdown(
        f"<span style='line-height:2.4;color:#475569'>Week of "
        f"<b>{days[0].strftime('%d %b')} – {days[-1].strftime('%d %b %Y')}</b></span>",
        unsafe_allow_html=True,
    )

    cell_map = build_cell_map(df, days)
    today    = date.today()
    COL_W    = [1.6] + [2.2] * 5

    # ── Header row ─────────────────────────────────────────────────
    hcols = st.columns(COL_W)
    hcols[0].markdown(
        "<div style='font-size:.8em;font-weight:700;color:#64748b;"
        "letter-spacing:.08em;padding:8px 2px'>TECHNICIAN</div>",
        unsafe_allow_html=True,
    )
    for i, d in enumerate(days):
        is_today = d == today
        hcols[i + 1].markdown(
            f"<div style='font-weight:700;padding:8px 8px;border-radius:10px;"
            f"background:{'#eff6ff' if is_today else '#f8fafc'};font-size:.88em'>"
            f"{'🔵&nbsp;' if is_today else ''}{d.strftime('%A')}<br>"
            f"<span style='font-weight:400;color:#94a3b8;font-size:.82em'>"
            f"{d.strftime('%d/%m/%Y')}</span></div>",
            unsafe_allow_html=True,
        )
    st.markdown("<hr style='margin:4px 0 0 0;border-color:#e2e8f0'>",
                unsafe_allow_html=True)

    # ── Technician rows ────────────────────────────────────────────
    for tech in techs:
        rcols = st.columns(COL_W)
        rcols[0].markdown(
            f"<div style='font-weight:600;padding:10px 4px;color:#1e293b'>{tech}</div>",
            unsafe_allow_html=True,
        )
        for i, d in enumerate(days):
            day_iso     = d.isoformat()
            assignments = cell_map.get((tech, day_iso), [])

            with rcols[i + 1]:
                if not assignments:
                    if st.button("➕", key=f"add_{tech}_{day_iso}",
                                 use_container_width=True,
                                 help=f"Assign to {tech} — {d.strftime('%A %d/%m')}"):
                        dlg_assign_cell(tech, day_iso)
                else:
                    for r in assignments:
                        is_locked = bool(r.get("locked", 0))
                        sp, st_ = slot_positions.get(int(r["id"]), (0, 0))
                        st.markdown(
                            card_html(r, slot_pos=sp, slot_total=st_),
                            unsafe_allow_html=True,
                        )
                        ec1, ec2, ec3 = st.columns(3)
                        if ec1.button("✏️", key=f"edit_{r['id']}_{day_iso}_{tech}",
                                      use_container_width=True, help="Edit"):
                            dlg_edit(r)
                        if ec2.button("⏳", key=f"wait_{r['id']}_{day_iso}_{tech}",
                                      use_container_width=True,
                                      disabled=is_locked, help="→ Waiting"):
                            set_assignment_waiting(int(r["id"]))
                            st.rerun()
                        lbl = "🔓" if is_locked else "🔒"
                        if ec3.button(lbl, key=f"lock_{r['id']}_{day_iso}_{tech}",
                                      use_container_width=True,
                                      help="Lock / Unlock"):
                            toggle_lock(int(r["id"]))
                            st.rerun()

        st.markdown(
            "<hr style='margin:2px 0;border:none;border-top:1px solid #f1f5f9'>",
            unsafe_allow_html=True,
        )

    # ══════════════════════════════════════════════════════════════
    #  WAITING LIST
    # ══════════════════════════════════════════════════════════════
    st.markdown("---")
    waiting_groups = get_assignment_groups(status_filter="waiting")
    n_waiting_slots = sum(g["n_waiting"] for g in waiting_groups)

    st.markdown(
        f"### ⏳ Waiting List &nbsp;"
        f"<span style='background:#fef3c7;color:#92400e;border-radius:99px;"
        f"padding:2px 12px;font-size:.75em;font-weight:700'>"
        f"{n_waiting_slots} slot{'s' if n_waiting_slots!=1 else ''} · "
        f"{len(waiting_groups)} assignment{'s' if len(waiting_groups)!=1 else ''}</span>",
        unsafe_allow_html=True,
    )

    if not waiting_groups:
        st.info("No assignments waiting to be scheduled.")
    else:
        for g in waiting_groups:
            uid = f"{g['assignment_id'].replace(' ','_')}_{g['slots'][0]['id']}"
            with st.container():
                h1, h2, h3 = st.columns([4, 2, 1.2])
                h1.markdown(
                    f"<div style='background:#fff;border:1px solid #e2e8f0;border-radius:10px;"
                    f"padding:8px 14px;margin:4px 0'>"
                    f"<b>{g['assignment_id']}</b> &nbsp;"
                    f"<span style='color:#64748b'>{g['instrument']} · "
                    f"{g['project']} · 👷 {g['technician']}</span></div>",
                    unsafe_allow_html=True,
                )
                h2.markdown(
                    f"<div style='padding:10px 4px;font-size:.84em;color:#475569'>"
                    f"✅ {g['n_scheduled']} sched &nbsp; ⏳ {g['n_waiting']} waiting"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                if h3.button("➕ Slot", key=f"addslot_{uid}",
                             use_container_width=True,
                             help="Add one more day-slot to this assignment"):
                    add_extra_slot(g["slots"][0]["id"])
                    st.rerun()

                for slot in g["slots"]:
                    if slot["status"] != "waiting":
                        continue
                    sc1, sc2, sc3, sc4 = st.columns([4.5, 1.3, 1.3, 0.9])
                    sc1.markdown(
                        f"<div style='padding:4px 14px 4px 24px;font-size:.85em;color:#475569'>"
                        f"⏳ DB#{slot['id']} &nbsp;"
                        f"<span style='background:#f1f5f9;border-radius:6px;padding:2px 8px'>"
                        f"{(slot.get('session') or 'FULL').upper()}</span>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                    if sc2.button("📅 Schedule", key=f"sched_{slot['id']}",
                                  use_container_width=True):
                        dlg_schedule_slot(slot)
                    if sc3.button("✏️ Edit", key=f"editw_{slot['id']}",
                                  use_container_width=True):
                        dlg_edit(slot)
                    if sc4.button("🗑️", key=f"del_{slot['id']}",
                                  use_container_width=True, help="Delete this slot"):
                        delete_assignment(int(slot["id"]))
                        st.rerun()

    # ══════════════════════════════════════════════════════════════
    #  AUTO-ASSIGN
    # ══════════════════════════════════════════════════════════════
    st.markdown("---")
    with st.expander("🤖 Auto-assign from waiting list",
                     expanded=(n_waiting_slots > 0 and n_waiting_slots <= 20)):
        st.caption(
            "Select assignments to auto-schedule. Each waiting slot will be placed on the "
            "**first free business day** ≥ start date for its technician. "
            "AM/PM-aware: a FULL slot needs a fully free day."
        )

        if not waiting_groups:
            st.info("Nothing in the waiting list.")
        else:
            group_labels: list[str] = []
            group_slot_ids: dict[str, list[int]] = {}
            for g in waiting_groups:
                w_ids = [s["id"] for s in g["slots"] if s["status"] == "waiting"]
                if not w_ids:
                    continue
                lbl = (
                    f"{g['assignment_id']}  |  {g['instrument']} / {g['project']}"
                    f"  |  👷 {g['technician']}  |  {len(w_ids)} slot(s)"
                )
                group_labels.append(lbl)
                group_slot_ids[lbl] = w_ids

            selected_labels = st.multiselect(
                "Select assignments to auto-assign",
                group_labels, default=group_labels,
                key="auto_assign_multi",
            )
            from_d = st.date_input("Start searching from", value=date.today(),
                                   key="auto_assign_from")
            n_sel = sum(len(group_slot_ids[l]) for l in selected_labels)
            col_btn, _ = st.columns([2, 5])
            if col_btn.button(
                f"🤖 Auto-assign {n_sel} slot(s)", type="primary",
                disabled=not selected_labels, use_container_width=True,
            ):
                all_ids = [sid for lbl in selected_labels
                           for sid in group_slot_ids[lbl]]
                results = auto_assign_waiting_slots(all_ids, from_d.isoformat())
                if results:
                    st.success(f"Scheduled {len(results)} slot(s):")
                    st.dataframe(
                        pd.DataFrame(results)[
                            ["assignment_id", "technician", "instrument", "day"]
                        ],
                        hide_index=True, use_container_width=True,
                    )
                    st.rerun()
                else:
                    st.warning("Could not find free days for any selected slot.")

    # ══════════════════════════════════════════════════════════════
    #  SHIFT
    # ══════════════════════════════════════════════════════════════
    st.markdown("---")
    with st.expander("🔀 Shift technician schedule"):
        st.caption("Move all non-locked scheduled slots for a technician forward/backward.")
        sc1, sc2, sc3, sc4 = st.columns([2, 1.5, 1.2, 1.2])
        shift_tech = sc1.selectbox("Technician", techs, key="shift_tech")
        shift_from = sc2.date_input("From date", value=date.today(), key="shift_from")
        shift_dir  = sc3.selectbox("Direction", ["Forward ▶", "Backward ◀"], key="shift_dir")
        shift_n    = sc4.number_input("Business days", 1, 60, 1, key="shift_n")
        delta      = int(shift_n) if shift_dir.startswith("Forward") else -int(shift_n)

        if st.button("🔍 Preview", key="prev_shift"):
            ws, wsk = preview_shift(shift_tech, shift_from.isoformat(), delta)
            st.session_state["shift_preview"] = (ws, wsk)
            st.session_state["shift_params"]  = (shift_tech, shift_from.isoformat(), delta)

        if "shift_preview" in st.session_state:
            ws, wsk = st.session_state["shift_preview"]
            if not ws and not wsk:
                st.info("No scheduled slots from that date onward.")
            else:
                if ws:
                    st.dataframe(
                        pd.DataFrame(ws)[
                            ["assignment_id","instrument","project","old_start","new_start"]
                        ],
                        hide_index=True, use_container_width=True,
                    )
                if wsk:
                    st.markdown(f"**{len(wsk)} locked slot(s) will be skipped.**")
                a1, a2, _ = st.columns([1.5, 1, 4])
                if a1.button("✅ Apply", type="primary", key="apply_shift"):
                    n_sh, n_sk = shift_technician_assignments(
                        *st.session_state["shift_params"]
                    )
                    st.session_state.pop("shift_preview", None)
                    st.session_state.pop("shift_params",  None)
                    st.success(f"Shifted {n_sh} slot(s). {n_sk} locked skipped.")
                    st.rerun()
                if a2.button("✖ Cancel", key="cancel_shift"):
                    st.session_state.pop("shift_preview", None)
                    st.session_state.pop("shift_params",  None)
                    st.rerun()

    # ══════════════════════════════════════════════════════════════
    #  RESEARCHER FEEDBACK
    # ══════════════════════════════════════════════════════════════
    st.markdown("---")
    with st.expander("💬 Process Researcher Feedback"):
        tab_paste, tab_upload = st.tabs(["📋 Paste token", "📁 Upload JSON"])
        raw_token = None
        with tab_paste:
            pasted = st.text_area("Paste base-64 token or raw JSON", height=100, key="fb_paste")
            if pasted.strip():
                raw_token = pasted.strip()
        with tab_upload:
            uploaded = st.file_uploader("Upload feedback JSON", type=["json"], key="fb_upload")
            if uploaded:
                raw_token = uploaded.read().decode("utf-8")

        if raw_token:
            parsed = None
            try:
                decoded = base64.b64decode(raw_token).decode("utf-8")
                parsed  = json.loads(decoded)
            except Exception:
                try:
                    parsed = json.loads(raw_token)
                except Exception:
                    st.error("Could not parse token.")

            if parsed and "items" in parsed:
                n_new = add_feedback_items(parsed["items"])
                st.success(
                    f"Token read ({len(parsed['items'])} item(s)). "
                    + (f"{n_new} new." if n_new else "No new items.")
                )

        fb_df = get_feedback_df()
        if not fb_df.empty:
            for sf, label, bg in [
                ("pending",   "⏳ Pending",   "#fffbeb"),
                ("accepted",  "✅ Accepted",  "#f0fdf4"),
                ("dismissed", "❌ Dismissed", "#fef2f2"),
            ]:
                subset = fb_df[fb_df["status"] == sf]
                if subset.empty:
                    continue
                st.markdown(
                    f"<div style='background:{bg};border-radius:8px;padding:4px 10px;"
                    f"margin:8px 0;font-weight:700'>{label} ({len(subset)})</div>",
                    unsafe_allow_html=True,
                )
                for _, fb in subset.iterrows():
                    fc1, fc2, fc3, fc4 = st.columns([3, 1.2, 1.2, 1.1])
                    icon = "📍" if fb["feedback_type"] == "presence" else "❌"
                    fc1.markdown(
                        f"**{icon} {fb['assignment_id']}** &nbsp; "
                        f"{fb['instrument']} / {fb['project']}<br>"
                        f"<span style='font-size:.85em;color:#475569'>"
                        f"👤 {fb['researcher_name']} &nbsp;·&nbsp; 📅 {fb['day']}"
                        + (f"&nbsp;·&nbsp; 🔄 **{fb['proposed_date']}**"
                           if fb.get("proposed_date") else "")
                        + f"</span><br>"
                        f"<span style='font-size:.82em;color:#64748b;font-style:italic'>"
                        f"{fb['reason'] or ''}</span>",
                        unsafe_allow_html=True,
                    )
                    if sf == "pending":
                        if fc2.button("✅ Accept",  key=f"fba_{fb['id']}", use_container_width=True):
                            set_feedback_status(int(fb["id"]), "accepted")
                            st.rerun()
                        if fc3.button("❌ Dismiss", key=f"fbd_{fb['id']}", use_container_width=True):
                            set_feedback_status(int(fb["id"]), "dismissed")
                            st.rerun()
                    if fc4.button("🗑️", key=f"fbdel_{fb['id']}", use_container_width=True):
                        delete_feedback(int(fb["id"]))
                        st.rerun()

    # ══════════════════════════════════════════════════════════════
    #  DRAG & DROP  — Redesigned layout
    # ══════════════════════════════════════════════════════════════
    if _HAS_SORTABLES:
        st.markdown("---")
        with st.expander("🔀 Drag & Drop (advanced)"):
            st.caption(
                "Drag cards from the **Waiting** pool to a technician/day slot, "
                "or drag any card back to **Waiting**. "
                "Click **✅ Apply** to save changes."
            )

            # ── Build containers ──────────────────────────────────
            waiting_df_dd = get_waiting_df()
            waiting_cards = [_card_str(r) for _, r in waiting_df_dd.iterrows()]

            sched_dd = df.copy()
            if not sched_dd.empty and "status" in sched_dd.columns:
                sched_dd = sched_dd[sched_dd["status"] == "scheduled"].copy()
            if not sched_dd.empty:
                sched_dd["_sd"] = pd.to_datetime(
                    sched_dd["start_date"], errors="coerce"
                ).dt.date
                sched_dd = sched_dd.dropna(subset=["_sd"])
                sched_dd = sched_dd[
                    (sched_dd["_sd"] >= days[0]) & (sched_dd["_sd"] <= days[-1])
                ]

            slot_cards_dd: dict = {}
            if not sched_dd.empty:
                for _, r in sched_dd.iterrows():
                    t = r.get("technician", "")
                    if t in techs:
                        slot_cards_dd.setdefault(
                            (t, r["_sd"].isoformat()), []
                        ).append(_card_str(r))

            # Containers: WAITING first, then one per (tech, day)
            containers    = [{"header": "⏳ WAITING  ▸ drag here to unschedule", "items": waiting_cards}]
            header_to_slot: dict = {}
            for t in techs:
                for d in days:
                    hdr = f"👷 {t} — {d.strftime('%a %d/%m')}"
                    header_to_slot[hdr] = (t, d.isoformat())
                    items = slot_cards_dd.get((t, d.isoformat()), [])
                    containers.append({"header": hdr, "items": items})

            # ── CSS overhaul ──────────────────────────────────────
            # Layout: WAITING full-width at top, then day cells at ~20% each
            # Items: dark text on blue background, red on hover
            dd_css = """
/* ── Outer wrapper ──────────────────────────────── */
.sortable-component {
  display: flex !important;
  flex-wrap: wrap !important;
  gap: 8px !important;
  background: #f1f5f9;
  padding: 10px;
  border-radius: 12px;
}

/* ── WAITING box ────────────────────────────────── */
.sortable-component > div:first-child {
  flex: 0 0 100% !important;
  width: 100% !important;
  background: #fefce8 !important;
  border: 2px dashed #fbbf24 !important;
  border-radius: 12px !important;
  padding: 10px 14px !important;
  min-height: 60px !important;
}
.sortable-component > div:first-child .sortable-container-header {
  color: #92400e !important;
  font-weight: 800 !important;
  font-size: .85em !important;
}

/* ── Day slot boxes ─────────────────────────────── */
.sortable-component > div:not(:first-child) {
  flex: 1 1 180px !important;
  max-width: calc(20% - 8px) !important;
  min-width: 155px !important;
  background: #fff !important;
  border: 1px solid #e2e8f0 !important;
  border-radius: 10px !important;
  padding: 8px 10px !important;
  min-height: 64px !important;
}
.sortable-component > div:not(:first-child) .sortable-container-header {
  font-size: .78em !important;
  font-weight: 700 !important;
  color: #1e293b !important;
  margin-bottom: 5px !important;
  line-height: 1.35 !important;
}

/* ── Draggable items — HIGH CONTRAST ────────────── */
.sortable-item {
  border-left: 4px solid #1d4ed8 !important;
  border-radius: 8px !important;
  padding: 7px 11px !important;
  margin: 5px 0 !important;
  background: #dbeafe !important;   /* blue-100  */
  color: #1e3a8a !important;         /* blue-900  */
  font-size: .8em !important;
  font-weight: 700 !important;
  cursor: grab !important;
  transition: background .15s, color .15s, border-left-color .15s !important;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sortable-item:hover {
  background: #fee2e2 !important;   /* red-100   */
  color: #7f1d1d !important;         /* red-900   */
  border-left-color: #dc2626 !important;
}
"""

            new_state = sort_items(
                containers,
                multi_containers=True,
                custom_style=dd_css,
                key=f"dd_{days[0].isoformat()}",
            )

            old_loc = {item: c["header"] for c in containers for item in c["items"]}
            new_loc = {item: c["header"] for c in new_state  for item in c["items"]}
            moved   = [card for card in new_loc if old_loc.get(card) != new_loc.get(card)]

            if moved:
                st.warning(f"{len(moved)} item(s) moved. Click **✅ Apply** to save.")
                ca, cb = st.columns([1.5, 1.5])
                if ca.button("✅ Apply changes", type="primary", key="dd_apply"):
                    waiting_hdr = containers[0]["header"]
                    for card in moved:
                        rid  = _parse_id(card)
                        if rid is None:
                            continue
                        dest = new_loc.get(card, waiting_hdr)
                        if dest == waiting_hdr:
                            set_assignment_waiting(rid)
                        else:
                            slot_tech, slot_day = header_to_slot[dest]
                            schedule_slot(rid, slot_tech, slot_day, "FULL")
                    st.success("Saved.")
                    st.rerun()
                if cb.button("✖ Reset", key="dd_reset"):
                    st.rerun()
    else:
        with st.expander("🔀 Drag & Drop"):
            st.info(
                "Install **streamlit-sortables** to enable drag & drop: "
                "`pip install streamlit-sortables`"
            )


# ══════════════════════════════════════════════════════════════════
#  PAGE 2 — ADD TO WAITING
# ══════════════════════════════════════════════════════════════════
elif page == "📝 Add to Waiting":
    st.header("📝 Add Assignment to Waiting List")
    st.info(
        "Create a logical assignment with an **estimated number of days**. "
        "This creates that many waiting slots to be scheduled later."
    )

    tech_opts  = get_lookup("technicians")
    instr_opts = get_lookup("instruments")
    proj_opts  = get_lookup("projects")

    with st.form("add_waiting_form", clear_on_submit=True):
        assignment_id = st.text_input("Assignment ID *  (e.g. A2601108)")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Who & What**")
            technician = _pick("Technician *", tech_opts,  "aw_t")
            instrument = _pick("Instrument *", instr_opts, "aw_i")
            project    = _pick("Project *",    proj_opts,  "aw_p")
        with c2:
            st.markdown("**Details**")
            material   = st.text_input("Material")
            preprocess = st.text_input("Preprocess")
            session    = st.selectbox("Session (per slot)", ["FULL", "AM", "PM"])
            notes      = st.text_area("Notes", height=80)
            est_days   = st.number_input(
                "Estimated number of days *", min_value=1, max_value=60,
                value=1, step=1,
                help="Creates this many waiting slots for the assignment",
            )

        if st.form_submit_button("➕ Add to waiting list", type="primary"):
            errs = []
            if not assignment_id.strip(): errs.append("Assignment ID required.")
            if not technician:            errs.append("Technician required.")
            if not instrument:            errs.append("Instrument required.")
            if not project:               errs.append("Project required.")
            if errs:
                for e in errs: st.error(e)
            else:
                ids = add_assignment_slots(
                    {
                        "assignment_id": assignment_id.strip(),
                        "technician":    technician,
                        "instrument":    instrument,
                        "project":       project,
                        "material":      material,
                        "preprocess":    preprocess,
                        "session":       session,
                        "notes":         notes,
                    },
                    n_days=int(est_days),
                )
                st.success(
                    f"✅ Added **{len(ids)} slot(s)** for **{assignment_id.strip()}**."
                )
                st.rerun()

    st.subheader("All assignments")
    groups = get_assignment_groups()
    if not groups:
        st.info("No assignments yet.")
    else:
        st.dataframe(
            pd.DataFrame([{
                "Assignment ID":  g["assignment_id"],
                "Technician":     g["technician"],
                "Instrument":     g["instrument"],
                "Project":        g["project"],
                "Total slots":    g["n_total"],
                "Scheduled":      g["n_scheduled"],
                "Waiting":        g["n_waiting"],
            } for g in groups]),
            use_container_width=True, hide_index=True,
        )


# ══════════════════════════════════════════════════════════════════
#  PAGE 3 — EXPORT HTML
# ══════════════════════════════════════════════════════════════════
else:
    st.header("📤 Export Researcher HTML")
    st.info(
        "Generates a **static self-contained HTML** that researchers can open in any browser.  \n"
        "For **live updates** (researchers see changes without re-exporting), "
        "run **`python server.py`** and share the URL instead."
    )

    BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
    export_path = os.path.join(BASE_DIR, "lab_planning.html")

    if st.button("📤 Generate & Export", type="primary"):
        html = build_single_html(df)
        with open(export_path, "w", encoding="utf-8") as f:
            f.write(html)
        st.success(f"Saved: `{export_path}`")
        st.download_button(
            "⬇️ Download lab_planning.html",
            data=html, file_name="lab_planning.html", mime="text/html",
        )
