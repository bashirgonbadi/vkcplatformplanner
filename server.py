#!/usr/bin/env python3
"""
server.py  —  Lab Planning Live Server
=======================================
Run alongside the Streamlit manager app:

    # Terminal 1 — manager
    streamlit run ui.py

    # Terminal 2 — live researcher view
    python server.py

Then share http://<your-ip>:5001  with researchers.
They always see the latest planning.  Feedback is submitted directly
to the database (no token files needed).

Requirements:
    pip install flask
"""
from __future__ import annotations

import json
import os
import sys

try:
    from flask import Flask, jsonify, request, Response
except ImportError:
    print("Flask not installed.  Run:  pip install flask")
    sys.exit(1)

from db import (
    get_assignments_df,
    add_feedback_items,
    get_db_path,
    init_db,
)
from planner_html import (
    expand_to_daily,
    week_monday,
    _CSS,
    _JS_HELPERS,
    _FB_MODAL_HTML,
    _FB_JS_SHARED,
    _RENDER_JS_STATIC,   # we re-use the same render logic, data comes via fetch
)


# ══════════════════════════════════════════════════════════════════
#  Flask app
# ══════════════════════════════════════════════════════════════════
app = Flask(__name__)


# ── API: planning data ────────────────────────────────────────────

@app.route("/api/data")
def api_data():
    """Returns JSON: {weeks, records} — same shape as static HTML payload."""
    df    = get_assignments_df()
    daily = expand_to_daily(df)

    if daily.empty:
        return jsonify({"weeks": [], "records": []})

    weeks   = sorted(daily["week"].unique().tolist())
    records = daily.to_dict(orient="records")
    return jsonify({"weeks": weeks, "records": records})


# ── API: submit feedback ──────────────────────────────────────────

@app.route("/api/feedback", methods=["POST"])
def api_feedback():
    """
    POST body: {items: [...]}
    Each item is the same schema as the token-based items.
    Returns {ok, inserted, message}.
    """
    data  = request.get_json(force=True, silent=True) or {}
    items = data.get("items", [])
    if not items:
        return jsonify({"ok": False, "message": "No items received"}), 400

    n = add_feedback_items(items)
    return jsonify({
        "ok":       True,
        "inserted": n,
        "message":  f"{n} new item(s) saved." if n else "No new items (duplicates ignored).",
    })


# ── Live HTML page ────────────────────────────────────────────────

@app.route("/")
def index():
    return Response(_build_live_html(), mimetype="text/html")


def _build_live_html() -> str:
    """
    Generates the live planning page.
    Data is fetched from /api/data on load and every REFRESH_SECS seconds.
    Feedback is submitted directly to /api/feedback.
    """
    REFRESH_SECS = 60   # auto-refresh interval

    # Extra CSS for the live badge + refresh bar
    extra_css = """
.live-bar{background:#f0fdf4;border:1px solid #bbf7d0;border-radius:10px;
  padding:8px 16px;margin-bottom:14px;display:flex;align-items:center;
  gap:12px;font-size:.85em;color:#166534;flex-wrap:wrap}
.live-dot{width:10px;height:10px;border-radius:50%;background:#22c55e;
  animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.refresh-timer{margin-left:auto;color:#64748b;font-size:.82em}
.status-msg{font-size:.82em;padding:4px 10px;border-radius:6px}
.status-ok{background:#f0fdf4;color:#166534}
.status-err{background:#fef2f2;color:#dc2626}
"""

    # Live-mode feedback JS — saves locally AND submits to API
    fb_live_extras = f"""
let submitInProgress = false;

function buildFbActions(hasExisting) {{
  const el = document.getElementById("fbActions");
  el.innerHTML = `
    <button class="btn-save" onclick="saveFb()">
      💾 Save ${{LIVE_MODE ? "& Submit" : ""}}
    </button>
    <button class="sec" onclick="closeFb()">✖ Cancel</button>
    ${{hasExisting ? '<button class="btn-remove sec" onclick="removeFb()">🗑 Remove</button>' : ''}}
  `;
}}

function saveFb() {{
  const name   = document.getElementById("fbName").value.trim();
  const reason = document.getElementById("fbReason").value.trim();
  if (!name)    {{ alert("Enter your name."); return; }}
  if (!curType) {{ alert("Select a feedback type."); return; }}
  if (curType==="objection" && !reason) {{ alert("Reason required."); return; }}
  const item = {{
    db_id:curData.id, assignment_id:curData.aid, tech:curData.tech,
    instrument:curData.instr, project:curData.proj,
    day:curData.date, researcher:name, type:curType, reason,
    proposed_date: curType==="objection"
      ? (document.getElementById("fbPropDate").value||null) : null,
  }};
  fbMap[curKey] = item;
  document.getElementById("globalName").value=name;
  try {{ localStorage.setItem(LS_NAME,name); }} catch(e) {{}}
  fbSave();

  // Submit directly to server
  if (submitInProgress) {{ closeFb(); updateTray(); return; }}
  submitInProgress = true;
  fetch("/api/feedback", {{
    method:"POST",
    headers:{{"Content-Type":"application/json"}},
    body:JSON.stringify({{items:[item]}})
  }})
  .then(r => r.json())
  .then(d => {{
    submitInProgress = false;
    showStatus(d.message, d.ok ? "ok" : "err");
  }})
  .catch(() => {{ submitInProgress=false; showStatus("Submission failed — feedback saved locally.", "err"); }});
  closeFb(); updateTray();
}}

function removeFb() {{
  if (curKey) {{ delete fbMap[curKey]; fbSave(); }}
  closeFb(); updateTray();
}}
function clearAll() {{
  if (!confirm("Clear all local feedback?")) return;
  fbMap={{}}; fbSave(); updateTray();
}}

// Tray extra = status only (no token needed in live mode)
document.getElementById("trayExtra").innerHTML = "";

const LIVE_MODE = true;

// No token functions needed in live mode
function openTok()  {{}}
function closeTok() {{}}
function dlToken()  {{}}
function copyToken(){{}}
function tokOverlayClick() {{}}
"""

    render_and_fetch = f"""
const weekSel  = document.getElementById("weekSel");
const rangeLbl = document.getElementById("rangeLbl");
const tblDiv   = document.getElementById("tbl");
let weekIdx = 0;
let DATA = {{weeks:[], records:[]}};

function showStatus(msg, type) {{
  const el = document.getElementById("statusMsg");
  if (!el) return;
  el.textContent = msg;
  el.className = "status-msg status-" + type;
  setTimeout(() => {{ el.textContent=""; el.className="status-msg"; }}, 5000);
}}

async function fetchData() {{
  try {{
    const r = await fetch("/api/data");
    DATA = await r.json();
    rebuildWeekSelector();
    renderTable();
    document.getElementById("lastUpdated").textContent =
      "Last updated: " + new Date().toLocaleTimeString();
  }} catch(e) {{
    showStatus("Could not load data from server.", "err");
  }}
}}

function rebuildWeekSelector() {{
  weekSel.innerHTML = "";
  DATA.weeks.forEach((w,i) => {{
    const o = document.createElement("option");
    o.value=i; o.textContent=w;
    if (w <= TODAY) weekIdx=i;
    weekSel.appendChild(o);
  }});
  weekSel.value = weekIdx;
}}
weekSel.onchange = () => {{ weekIdx=+weekSel.value; renderTable(); }};

{_RENDER_JS_STATIC}

function prevWeek() {{ weekIdx=Math.max(0,weekIdx-1); renderTable(); }}
function nextWeek() {{ weekIdx=Math.min(DATA.weeks.length-1,weekIdx+1); renderTable(); }}

// Auto-refresh
let countdown = {REFRESH_SECS};
setInterval(() => {{
  countdown--;
  const el = document.getElementById("countdown");
  if (el) el.textContent = countdown + "s";
  if (countdown <= 0) {{
    countdown = {REFRESH_SECS};
    fetchData();
  }}
}}, 1000);

fetchData();
"""

    html = (
        "<!doctype html>\n<html lang='en'>\n<head>\n"
        "<meta charset='utf-8'>\n"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>\n"
        "<title>Lab Planning — Live</title>\n"
        f"<style>\n{_CSS}\n{extra_css}\n</style>\n"
        "</head>\n<body>\n"

        "<div class='topbar'>\n"
        "  <h1>🔬 Lab Planning <span style='font-size:.6em;font-weight:500;"
        "     color:#22c55e;letter-spacing:.02em'>● LIVE</span></h1>\n"
        "  <button class='sec' onclick='prevWeek()'>◀ Prev</button>\n"
        "  <select id='weekSel'></select>\n"
        "  <button class='sec' onclick='nextWeek()'>Next ▶</button>\n"
        "  <span class='range-lbl' id='rangeLbl'></span>\n"
        "  <button class='sec' onclick='fetchData()' title='Reload now'"
        "    style='margin-left:auto'>🔄 Refresh</button>\n"
        "</div>\n"

        "<div class='live-bar'>\n"
        "  <span class='live-dot'></span>\n"
        "  <span>Live — data refreshes automatically</span>\n"
        "  <span id='lastUpdated' style='color:#475569'></span>\n"
        "  <span class='refresh-timer'>Next refresh in <b id='countdown'>"
        f"  {REFRESH_SECS}</b>s</span>\n"
        "  <span id='statusMsg' class='status-msg'></span>\n"
        "</div>\n"

        "<div class='name-bar'>\n"
        "  <label for='globalName'>👤 Your name:</label>\n"
        "  <input type='text' id='globalName' placeholder='Enter your name once…'"
        "         oninput='saveName()' autocomplete='name'>\n"
        "  <span style='font-size:.8em;color:#64748b'>"
        "     Feedback is submitted directly — no token needed</span>\n"
        "</div>\n"

        "<div class='table-wrap'><div id='tbl'></div></div>\n"
        + _FB_MODAL_HTML +
        "\n<script>\n'use strict';\n"
        + _JS_HELPERS
        + _FB_JS_SHARED
        + fb_live_extras
        + render_and_fetch
        + "\nfbLoad();\n"
        "</script>\n</body>\n</html>\n"
    )
    return html


# ══════════════════════════════════════════════════════════════════
#  Entry point
# ══════════════════════════════════════════════════════════════════
def run(host: str = "0.0.0.0", port: int = 5001, debug: bool = False) -> None:
    init_db()
    db_path = get_db_path()
    print()
    print("=" * 55)
    print("  🔬  Lab Planning Live Server")
    print("=" * 55)
    print(f"  Database : {db_path}")
    print(f"  URL      : http://localhost:{port}")
    print(f"  Auto-refresh : every 60 s")
    print()
    print("  Share the URL with researchers.")
    print("  Run  streamlit run ui.py  for the manager interface.")
    print("=" * 55)
    print()
    app.run(host=host, port=port, debug=debug, use_reloader=False)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Lab Planning Live Server")
    p.add_argument("--host",  default="0.0.0.0",  help="Bind address (default: 0.0.0.0)")
    p.add_argument("--port",  default=5001, type=int, help="Port (default: 5001)")
    p.add_argument("--debug", action="store_true",   help="Flask debug mode")
    args = p.parse_args()
    run(args.host, args.port, args.debug)
