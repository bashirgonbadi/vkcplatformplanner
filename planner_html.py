# planner_html.py
from __future__ import annotations

import json
from datetime import timedelta
import pandas as pd


# ── Date helpers ──────────────────────────────────────────────────

def week_monday(date_in_week) -> pd.Timestamp:
    d = pd.to_datetime(date_in_week)
    return (d - timedelta(days=d.weekday())).normalize()


# ── Slot counts  {assignment_id: {"total": N, "scheduled": M}} ───

def _slot_counts(df: pd.DataFrame) -> dict:
    if df is None or df.empty:
        return {}
    counts: dict = {}
    for _, r in df.iterrows():
        aid  = str(r.get("assignment_id", ""))
        stat = str(r.get("status", "scheduled"))
        e    = counts.setdefault(aid, {"total": 0, "scheduled": 0})
        e["total"] += 1
        if stat == "scheduled":
            e["scheduled"] += 1
    return counts


# ── Expand to per-slot rows, with slot_pos + slot_total ──────────

def expand_to_daily(df: pd.DataFrame) -> pd.DataFrame:
    """
    Returns one row per scheduled slot.
    Adds:
      slot_pos   – position of this slot among all scheduled slots
                   for the same assignment_id, ordered by date  (1-based)
      slot_total – total slots (scheduled + waiting) for this assignment_id
    """
    if df is None or df.empty:
        return pd.DataFrame()

    # Total count per assignment (all statuses)
    totals: dict = {}
    for _, r in df.iterrows():
        aid = str(r.get("assignment_id", ""))
        totals[aid] = totals.get(aid, 0) + 1

    rows = []
    for _, r in df.iterrows():
        if str(r.get("status", "scheduled")) != "scheduled":
            continue
        start = pd.to_datetime(r.get("start_date"), errors="coerce")
        if pd.isna(start):
            continue
        d = start.normalize()
        rows.append({
            "id":            int(r["id"]),
            "assignment_id": str(r.get("assignment_id", "")),
            "technician":    r.get("technician",    "") or "",
            "instrument":    r.get("instrument",    "") or "",
            "project":       r.get("project",       "") or "",
            "material":      r.get("material",      "") or "",
            "preprocess":    r.get("preprocess",    "") or "",
            "notes":         r.get("notes",         "") or "",
            "session":       (r.get("session") or "FULL").upper(),
            "date":          d.strftime("%Y-%m-%d"),
            "week":          week_monday(d).strftime("%Y-%m-%d"),
            "slot_total":    totals.get(str(r.get("assignment_id", "")), 1),
        })

    if not rows:
        return pd.DataFrame()

    daily = pd.DataFrame(rows)
    # slot_pos: rank within same assignment, sorted by date
    daily = daily.sort_values(["assignment_id", "date"]).reset_index(drop=True)
    daily["slot_pos"] = daily.groupby("assignment_id").cumcount() + 1
    return daily


# ── Shared JS/CSS constants injected into both static + live ─────

# toIso() helper must appear before TODAY so we define it first.
_JS_HELPERS = r"""
function toIso(d) {
  // Local-date ISO string — avoids UTC-offset shift from toISOString()
  return d.getFullYear() + '-' +
    String(d.getMonth()+1).padStart(2,'0') + '-' +
    String(d.getDate()).padStart(2,'0');
}
const TODAY = toIso(new Date());

function lDate(iso) {
  const [y,m,d] = iso.split('-').map(Number);
  return new Date(y, m-1, d);
}
"""

_CSS = """
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#f1f5f9;
  color:#1e293b;padding:24px 20px 100px}

.topbar{background:#fff;border-radius:14px;padding:14px 20px;
  display:flex;align-items:center;gap:10px;flex-wrap:wrap;
  box-shadow:0 1px 4px rgba(0,0,0,.08);margin-bottom:18px}
h1{font-size:1.3em;font-weight:800;color:#0f172a;letter-spacing:-.02em;flex:1}
.range-lbl{font-size:.86em;color:#64748b;font-weight:500}
button{background:#0072B2;color:#fff;border:none;border-radius:8px;
  padding:7px 15px;font-size:.88em;font-weight:600;cursor:pointer;
  transition:background .15s}
button:hover{background:#005a8e}
button.sec{background:#f1f5f9;color:#334155}
button.sec:hover{background:#e2e8f0}
select{border:1px solid #e2e8f0;border-radius:8px;padding:6px 10px;
  font-size:.88em;color:#334155;background:#f8fafc;cursor:pointer}

.table-wrap{background:#fff;border-radius:14px;overflow:hidden;
  box-shadow:0 1px 4px rgba(0,0,0,.08)}
table{border-collapse:collapse;width:100%}
th,td{border:1px solid #e2e8f0;padding:10px 11px;vertical-align:top}
th{background:#f8fafc;font-size:.78em;font-weight:700;color:#475569;
  letter-spacing:.05em;text-transform:uppercase}
th.today{background:#eff6ff;color:#1d4ed8}
td.today{background:#fafcff}
td.tech-col{background:#f8fafc;font-weight:700;font-size:.88em;
  white-space:nowrap;min-width:130px}
td.empty{background:#fafafa}

.card{border-left:5px solid #0072B2;border-radius:9px;padding:8px 11px;
  margin:5px 0;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.07);
  position:relative;transition:box-shadow .15s,border-left-color .15s}
.card:hover{box-shadow:0 3px 10px rgba(0,0,0,.12)}
.card.has-presence{border-left-color:#2563eb;background:#eff6ff}
.card.has-objection{border-left-color:#d97706;background:#fffbeb}
.c-instr{font-weight:800;font-size:.88em;color:#0f172a}
.c-id{font-weight:600;font-size:.82em;color:#1a4a7a;margin-top:1px}
.c-detail{font-size:.77em;color:#64748b;margin-top:2px}
.badge{display:inline-block;padding:1px 8px;border-radius:99px;
  background:#dbeafe;color:#1e3a8a;font-size:10px;font-weight:700;
  margin-left:5px;vertical-align:middle}
.slot-badge{display:inline-block;padding:1px 7px;border-radius:99px;
  background:#f0fdf4;color:#166534;border:1px solid #bbf7d0;
  font-size:10px;font-weight:700;margin-left:5px;vertical-align:middle}
.fb-btn{position:absolute;top:6px;right:7px;background:none;border:none;
  font-size:15px;cursor:pointer;padding:2px;opacity:.5;
  transition:opacity .15s;color:#000}
.fb-btn:hover{opacity:1;background:none}
.fb-indicator{position:absolute;top:6px;right:30px;font-size:12px}

/* Feedback modal */
.overlay{display:none;position:fixed;inset:0;
  background:rgba(15,23,42,.45);z-index:1000;
  align-items:center;justify-content:center}
.overlay.open{display:flex}
.modal{background:#fff;border-radius:16px;padding:28px 30px;
  width:480px;max-width:95vw;max-height:90vh;overflow-y:auto;
  box-shadow:0 20px 60px rgba(0,0,0,.25)}
.modal h3{font-size:1.05em;font-weight:800;margin-bottom:4px;color:#0f172a}
.card-info{background:#f8fafc;border-radius:8px;padding:10px 13px;
  margin:10px 0 16px;font-size:.85em;color:#334155}
.card-info .ci-main{font-weight:700;font-size:.95em;color:#0f172a;margin-bottom:3px}
label{display:block;font-size:.83em;font-weight:600;color:#374151;margin:12px 0 4px}
input[type=text],input[type=date],textarea{width:100%;
  border:1px solid #d1d5db;border-radius:8px;padding:8px 10px;
  font-size:.88em;font-family:inherit;outline:none;transition:border-color .15s}
input:focus,textarea:focus{border-color:#0072B2}
textarea{resize:vertical;min-height:70px}
.type-row{display:flex;gap:10px;margin:12px 0}
.type-btn{flex:1;padding:10px;border-radius:10px;border:2px solid #e2e8f0;
  background:#f8fafc;color:#374151;font-size:.85em;font-weight:600;
  cursor:pointer;transition:all .15s;text-align:center}
.type-btn:hover{border-color:#93c5fd;background:#eff6ff}
.type-btn.ap{border-color:#2563eb;background:#dbeafe;color:#1e40af}
.type-btn.ao{border-color:#d97706;background:#fef3c7;color:#92400e}
.modal-actions{display:flex;gap:8px;margin-top:18px;flex-wrap:wrap}
.modal-actions button{padding:8px 16px;font-size:.88em}
.btn-save{background:#0072B2}
.btn-remove{background:#fee2e2;color:#dc2626}
.btn-remove:hover{background:#fecaca;color:#dc2626}

/* Bottom tray */
.fb-tray{position:fixed;bottom:0;left:0;right:0;background:#fff;
  border-top:1px solid #e2e8f0;padding:12px 24px;
  display:flex;align-items:center;gap:12px;
  box-shadow:0 -2px 12px rgba(0,0,0,.08);z-index:900}
.fb-tray .count{font-weight:700;font-size:.95em;color:#0f172a;flex:1}
.fb-tray .count span{color:#d97706}
.token-box{background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;
  padding:12px;font-size:.78em;font-family:monospace;word-break:break-all;
  max-height:140px;overflow-y:auto;margin:12px 0;color:#334155;cursor:text}
.copy-hint{font-size:.78em;color:#64748b;margin-top:4px}
.name-bar{background:#eff6ff;border-radius:10px;padding:10px 14px;
  margin-bottom:14px;display:flex;align-items:center;gap:10px}
.name-bar input{border:1px solid #bfdbfe;border-radius:6px;
  padding:5px 8px;font-size:.88em;flex:1}
.name-bar label{font-size:.84em;font-weight:700;color:#1e40af;white-space:nowrap}
"""

# The shared JS logic for feedback (works with embedded data OR fetch)
_FB_MODAL_HTML = """
<!-- Feedback modal -->
<div class="overlay" id="fbOverlay" onclick="overlayClick(event)">
  <div class="modal" id="fbModal">
    <h3 id="fbTitle">Leave feedback</h3>
    <div class="card-info" id="fbInfo"></div>
    <label>Your name *</label>
    <input type="text" id="fbName" placeholder="Full name">
    <div class="type-row">
      <button class="type-btn" id="btnP" onclick="setFbType('presence')">
        📍 I want to be present
      </button>
      <button class="type-btn" id="btnO" onclick="setFbType('objection')">
        ❌ I have an objection
      </button>
    </div>
    <label>Reason / notes
      <span style="color:#94a3b8;font-weight:400">(required for objection)</span>
    </label>
    <textarea id="fbReason" placeholder="Describe…"></textarea>
    <div id="propWrap" style="display:none">
      <label>Proposed alternative date</label>
      <input type="date" id="fbPropDate">
    </div>
    <div class="modal-actions" id="fbActions"></div>
  </div>
</div>

<!-- Token modal (static only) -->
<div class="overlay" id="tokOverlay" onclick="tokOverlayClick(event)">
  <div class="modal">
    <h3>📤 Feedback Token</h3>
    <p style="font-size:.88em;color:#475569;margin-top:6px">
      Download the JSON file or copy the text token and send it to the lab manager.
    </p>
    <div style="display:flex;gap:8px;margin-top:14px;flex-wrap:wrap">
      <button onclick="dlToken()">⬇ Download JSON</button>
      <button class="sec" onclick="copyToken()">📋 Copy token</button>
      <button class="sec" onclick="closeTok()">✖ Close</button>
    </div>
    <div class="token-box" id="tokBox"></div>
    <div class="copy-hint" id="copyHint"></div>
  </div>
</div>

<!-- Bottom tray -->
<div class="fb-tray" id="fbTray" style="display:none">
  <div class="count">💬 Feedback: <span id="fbCount">0</span> item(s)</div>
  <button class="sec" onclick="clearAll()"
    style="font-size:.82em;padding:6px 12px;background:#fee2e2;color:#dc2626">
    🗑 Clear all
  </button>
  <span id="trayExtra"></span>
</div>
"""

_FB_JS_SHARED = r"""
let fbMap = {};
const LS_FB   = "lab_fb_v3";
const LS_NAME = "lab_name";

function fbSave() {
  try { localStorage.setItem(LS_FB, JSON.stringify(fbMap)); } catch(e) {}
}
function fbLoad() {
  try { const r=localStorage.getItem(LS_FB); if(r) fbMap=JSON.parse(r); } catch(e) {}
  try { document.getElementById("globalName").value=localStorage.getItem(LS_NAME)||""; } catch(e) {}
  updateTray();
}
function saveName() {
  try { localStorage.setItem(LS_NAME, document.getElementById("globalName").value); } catch(e) {}
}
function updateTray() {
  const n = Object.keys(fbMap).length;
  document.getElementById("fbCount").textContent = n;
  document.getElementById("fbTray").style.display = n>0 ? "flex" : "none";
  renderTable();
}

let curKey=null, curData=null, curType=null;
function openFb(btn) {
  const card = btn.closest(".card");
  const raw  = card.getAttribute("data-c");
  curData = JSON.parse(raw.replace(/&quot;/g,'"'));
  curKey  = curData.id + "_" + curData.date;
  const ex = fbMap[curKey];
  document.getElementById("fbTitle").textContent = ex ? "Edit feedback" : "Leave feedback";
  document.getElementById("fbInfo").innerHTML = `
    <div class="ci-main">${curData.instr}
      <span style="color:#64748b;font-weight:500"> — ${curData.aid}</span></div>
    <div>📁 ${curData.proj} &nbsp;·&nbsp; 👷 ${curData.tech} &nbsp;·&nbsp; 📅 ${curData.date}</div>`;
  document.getElementById("fbName").value    = ex ? ex.researcher : (document.getElementById("globalName").value.trim());
  document.getElementById("fbReason").value  = ex ? (ex.reason||"") : "";
  document.getElementById("fbPropDate").value = ex ? (ex.proposed_date||"") : "";
  document.getElementById("btnP").className = "type-btn";
  document.getElementById("btnO").className = "type-btn";
  curType = ex ? ex.type : null;
  updFbTypes();
  // Actions row (differs between static and live)
  buildFbActions(!!ex);
  document.getElementById("fbOverlay").classList.add("open");
}
function setFbType(t) { curType=t; updFbTypes(); }
function updFbTypes() {
  document.getElementById("btnP").className = "type-btn"+(curType==="presence"?" ap":"");
  document.getElementById("btnO").className = "type-btn"+(curType==="objection"?" ao":"");
  document.getElementById("propWrap").style.display = curType==="objection" ? "block":"none";
}
function closeFb() {
  document.getElementById("fbOverlay").classList.remove("open");
  curKey=null; curData=null; curType=null;
}
function overlayClick(e) { if(e.target.id==="fbOverlay") closeFb(); }
function removeFb() {
  if(curKey) { delete fbMap[curKey]; fbSave(); }
  closeFb(); updateTray();
}
function clearAll() {
  if(!confirm("Clear all feedback?")) return;
  fbMap={}; fbSave(); updateTray();
}
"""


# ── Build static self-contained HTML ─────────────────────────────

def build_single_html(df: pd.DataFrame) -> str:
    daily = expand_to_daily(df)

    if daily.empty:
        return (
            "<html><body style='font-family:sans-serif;padding:40px'>"
            "<h1>No planning data</h1></body></html>"
        )

    weeks     = sorted(daily["week"].unique().tolist())
    payload   = {"weeks": weeks, "records": daily.to_dict(orient="records")}
    data_json = json.dumps(payload)

    # Embed CSS/JS as raw strings (not f-strings) via concatenation
    css    = _CSS
    fb_html = _FB_MODAL_HTML
    js_helpers = _JS_HELPERS
    fb_js  = _FB_JS_SHARED

    return (
        "<!doctype html>\n<html lang='en'>\n<head>\n"
        "<meta charset='utf-8'>\n"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>\n"
        "<title>Lab Planning</title>\n"
        f"<style>\n{css}\n</style>\n"
        "</head>\n<body>\n"

        "<div class='topbar'>\n"
        "  <h1>🔬 Lab Planning</h1>\n"
        "  <button class='sec' onclick='prevWeek()'>◀ Prev</button>\n"
        "  <select id='weekSel'></select>\n"
        "  <button class='sec' onclick='nextWeek()'>Next ▶</button>\n"
        "  <span class='range-lbl' id='rangeLbl'></span>\n"
        "</div>\n"

        "<div class='name-bar'>\n"
        "  <label for='globalName'>👤 Your name:</label>\n"
        "  <input type='text' id='globalName' placeholder='Enter your name once…'"
        "         oninput='saveName()' autocomplete='name'>\n"
        "  <span style='font-size:.8em;color:#64748b'>Used in all feedback you submit</span>\n"
        "</div>\n"

        "<div class='table-wrap'><div id='tbl'></div></div>\n"
        + fb_html +
        "\n<script>\n'use strict';\n"
        + js_helpers
        + f"\nconst DATA = {data_json};\n"
        + _RENDER_JS_STATIC
        + fb_js
        + _FB_STATIC_EXTRAS
        + "\nfbLoad();\nrenderTable();\n"
        "</script>\n</body>\n</html>\n"
    )


# ── Render JS (shared table-rendering logic, DATA already defined) ─

_RENDER_JS_STATIC = r"""
const weekSel  = document.getElementById("weekSel");
const rangeLbl = document.getElementById("rangeLbl");
const tblDiv   = document.getElementById("tbl");
let weekIdx = 0;

DATA.weeks.forEach((w,i) => {
  const o = document.createElement("option");
  o.value=i; o.textContent=w;
  if (w <= TODAY) weekIdx=i;
  weekSel.appendChild(o);
});
weekSel.onchange = () => { weekIdx=+weekSel.value; renderTable(); };

function renderTable() {
  const week = DATA.weeks[weekIdx];
  weekSel.value = weekIdx;
  const mon  = lDate(week);
  const days = Array.from({length:5}, (_,i) => {
    const d=new Date(mon); d.setDate(d.getDate()+i); return d;
  });
  rangeLbl.textContent = days[0].toLocaleDateString()+" – "+days[4].toLocaleDateString();

  const recs  = DATA.records.filter(r => r.week===week);
  const techs = [...new Set(recs.map(r => r.technician))].sort();

  let html = "<table><thead><tr><th>Technician</th>";
  days.forEach(d => {
    const iso = toIso(d);
    const it  = iso===TODAY;
    html += `<th class="${it?'today':''}">`
      + d.toLocaleDateString(undefined,{weekday:'long'})
      + `<br><span style="font-weight:400;color:#94a3b8;font-size:.82em">${d.toLocaleDateString()}</span>`
      + (it?"<br><span style='color:#2563eb;font-size:.75em'>● Today</span>":"")
      + "</th>";
  });
  html += "</tr></thead><tbody>";

  techs.forEach(tech => {
    html += `<tr><td class="tech-col">${tech}</td>`;
    days.forEach(d => {
      const iso = toIso(d);
      const it  = iso===TODAY;
      const dayRecs = recs.filter(r => r.technician===tech && r.date===iso);
      let cell="";
      dayRecs.forEach(r => {
        const key   = r.id+"_"+r.date;
        const fb    = fbMap[key];
        const fbType = fb ? fb.type : null;
        const sBadge = r.session!=="FULL"
          ? `<span class="badge">${r.session}</span>` : "";
        const slotBadge = r.slot_total>1
          ? `<span class="slot-badge">${r.slot_pos}/${r.slot_total}</span>` : "";
        const fbInd = fbType==="presence"
          ? `<span class="fb-indicator" title="You: want to be present">📍</span>`
          : fbType==="objection"
          ? `<span class="fb-indicator" title="You: objection">❌</span>` : "";
        const cc = fbType==="presence" ? "card has-presence"
                 : fbType==="objection" ? "card has-objection" : "card";
        const dStr = JSON.stringify({
          id:r.id,date:r.date,aid:r.assignment_id,
          tech:r.technician,instr:r.instrument,proj:r.project
        }).replace(/"/g,"&quot;");
        cell += `<div class="${cc}" data-c="${dStr}">
          ${fbInd}
          <button class="fb-btn" onclick="openFb(this)" title="Leave feedback">💬</button>
          <div class="c-instr">${r.instrument}${sBadge}${slotBadge}</div>
          <div class="c-id">${r.assignment_id} — ${r.project}</div>
          ${r.material   ? `<div class="c-detail">📦 ${r.material}</div>`   : ""}
          ${r.preprocess ? `<div class="c-detail">⚗️ ${r.preprocess}</div>` : ""}
          ${r.notes      ? `<div class="c-detail" style="font-style:italic">📝 ${r.notes}</div>` : ""}
        </div>`;
      });
      html += `<td class="${it?'today':dayRecs.length?'':'empty'}">${cell}</td>`;
    });
    html += "</tr>";
  });

  html += "</tbody></table>";
  tblDiv.innerHTML = html;
}

function prevWeek() { weekIdx=Math.max(0,weekIdx-1); renderTable(); }
function nextWeek() { weekIdx=Math.min(DATA.weeks.length-1,weekIdx+1); renderTable(); }
"""

# Static-mode feedback extras (token generation, no live POST)
_FB_STATIC_EXTRAS = r"""
let tokJSON = "";

function buildFbActions(hasExisting) {
  const el = document.getElementById("fbActions");
  el.innerHTML = `
    <button class="btn-save" onclick="saveFb()">💾 Save feedback</button>
    <button class="sec" onclick="closeFb()">✖ Cancel</button>
    ${hasExisting
      ? '<button class="btn-remove sec" onclick="removeFb()">🗑 Remove</button>'
      : ''}
  `;
}

function saveFb() {
  const name   = document.getElementById("fbName").value.trim();
  const reason = document.getElementById("fbReason").value.trim();
  if (!name)   { alert("Enter your name."); return; }
  if (!curType){ alert("Select a feedback type."); return; }
  if (curType==="objection" && !reason) { alert("Reason required."); return; }
  fbMap[curKey] = {
    db_id:curData.id, assignment_id:curData.aid, tech:curData.tech,
    instrument:curData.instr, project:curData.proj,
    day:curData.date, researcher:name, type:curType, reason,
    proposed_date: curType==="objection"
      ? (document.getElementById("fbPropDate").value||null) : null,
  };
  document.getElementById("globalName").value=name;
  try { localStorage.setItem(LS_NAME,name); } catch(e) {}
  fbSave(); closeFb(); updateTray();
}

// Token tray
document.getElementById("trayExtra").innerHTML =
  `<button onclick="openTok()">📤 Generate token</button>`;

function openTok() {
  const items = Object.values(fbMap);
  if (!items.length) { alert("No feedback to export."); return; }
  tokJSON = JSON.stringify({v:3,ts:new Date().toISOString(),items},null,2);
  document.getElementById("tokBox").textContent = btoa(unescape(encodeURIComponent(tokJSON)));
  document.getElementById("copyHint").textContent="";
  document.getElementById("tokOverlay").classList.add("open");
}
function dlToken() {
  const a=Object.assign(document.createElement("a"),{
    href:URL.createObjectURL(new Blob([tokJSON],{type:"application/json"})),
    download:"lab_feedback_"+toIso(new Date())+".json",
  }); a.click();
}
function copyToken() {
  const t=document.getElementById("tokBox").textContent;
  (navigator.clipboard||{writeText:()=>Promise.reject()}).writeText(t)
    .then(()=>document.getElementById("copyHint").textContent="✅ Copied!")
    .catch(()=>{
      const r=document.createRange();
      r.selectNodeContents(document.getElementById("tokBox"));
      const s=window.getSelection(); s.removeAllRanges(); s.addRange(r);
      document.execCommand("copy");
      document.getElementById("copyHint").textContent="✅ Copied!";
    });
}
function closeTok() { document.getElementById("tokOverlay").classList.remove("open"); }
function tokOverlayClick(e) { if(e.target.id==="tokOverlay") closeTok(); }
"""
