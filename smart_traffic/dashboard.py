"""Web dashboard for the Smart Traffic Challan System (no extra dependencies).

Usage:
    ..\\.venv\\Scripts\\python dashboard.py            # open http://localhost:8000

- Upload a video/image (or pick one from assets/) -> it is processed with a live progress bar
- Result view: annotated video, violations, rider/plate evidence, challans and SMS
- Officer review: approve (with corrected plate) or reject manual-review cases
"""
import html
import json
import mimetypes
import os
import re
import subprocess
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config
from plate_reader import is_valid

BASE = config.BASE_DIR
OUT = config.OUTPUT_DIR
ASSETS = os.path.join(BASE, "assets")
LOGS = os.path.join(OUT, "_logs")
PORT = int(os.getenv("DASHBOARD_PORT", 8000))
MEDIA_EXT = (".mp4", ".avi", ".mov", ".mkv", ".jpg", ".jpeg", ".png")
IMAGE_EXT = (".jpg", ".jpeg", ".png")
JOBS = {}      # job id -> {"proc", "log", "run", "source", "started"}


def esc(x):
    return html.escape(str(x if x is not None else "-"))


def rel_url(path):
    """Turn an evidence path into a /file/... URL (only inside the project folder)."""
    if not path:
        return None
    p = os.path.normpath(path if os.path.isabs(path) else os.path.join(BASE, path))
    if not (p.startswith(os.path.normpath(BASE)) and os.path.exists(p)):
        # Results copied from another machine keep that machine's absolute paths:
        # re-anchor them at the "outputs" folder of this installation.
        parts = [x for x in re.split(r"[\\/]", path) if x]
        if "outputs" in parts:
            i = len(parts) - 1 - parts[::-1].index("outputs")
            p = os.path.normpath(os.path.join(BASE, *parts[i:]))
    if not p.startswith(os.path.normpath(BASE)) or not os.path.exists(p):
        return None
    return "/file/" + urllib.parse.quote(os.path.relpath(p, BASE).replace("\\", "/"))


def load_runs():
    runs = []
    if not os.path.isdir(OUT):
        return runs
    for name in os.listdir(OUT):
        d = os.path.join(OUT, name)
        cj, sj = os.path.join(d, "challans.json"), os.path.join(d, "summary.json")
        if not (os.path.exists(cj) and os.path.exists(sj)):
            continue
        with open(cj, encoding="utf-8") as f:
            challans = json.load(f)
        with open(sj, encoding="utf-8") as f:
            summary = json.load(f)
        fdir = os.path.join(d, "frames")
        frames = sorted(os.listdir(fdir), key=lambda x: int(re.sub(r"\D", "", x) or 0)) if os.path.isdir(fdir) else []
        runs.append({"name": name, "dir": d, "challans": challans, "summary": summary,
                     "mtime": os.path.getmtime(sj), "frames": [os.path.join(fdir, x) for x in frames]})
    runs.sort(key=lambda r: -r["mtime"])          # newest first
    return runs


# ---------------------------------------------------------------- jobs
def start_job(source_path, send_sms):
    stem, ext = os.path.splitext(os.path.basename(source_path))
    run = stem + ("_image" if ext.lower() in IMAGE_EXT else "")
    os.makedirs(LOGS, exist_ok=True)
    log = os.path.join(LOGS, f"{run}.log")
    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    if not send_sms:
        env["SMS_DRY_RUN"] = "1"
    job = f"{run}-{int(time.time())}"
    with open(log, "w", encoding="utf-8") as lf:
        proc = subprocess.Popen([sys.executable, "main.py", "--source", source_path,
                                 "--out", os.path.join(OUT, run)],
                                cwd=BASE, stdout=lf, stderr=subprocess.STDOUT, env=env)
    JOBS[job] = {"proc": proc, "log": log, "run": run, "source": os.path.basename(source_path),
                 "started": time.time(), "sms": send_sms}
    return job


def job_status(job):
    j = JOBS.get(job)
    if not j:
        return {"state": "unknown"}
    try:
        with open(j["log"], encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        lines = []
    frame = total = 0
    stage, case, ncase = "detect", 0, 0
    for ln in lines:
        if ln.startswith("PROGRESS"):
            _, a, b = ln.split()[:3]
            frame, total = int(a), int(b)
        elif ln.startswith("STAGE ocr"):
            stage = "ocr"
        elif ln.startswith("STAGE case"):
            stage, case, ncase = "ocr", int(ln.split()[2]), int(ln.split()[3])
        elif ln.startswith("STAGE done"):
            stage = "done"
    rc = j["proc"].poll()
    results_saved = os.path.exists(os.path.join(OUT, j["run"], "summary.json")) and \
        os.path.getmtime(os.path.join(OUT, j["run"], "summary.json")) >= j["started"] - 1
    if stage == "done" and results_saved:
        state = "done"            # results are written; a non-zero exit during interpreter shutdown is harmless
    elif rc is None:
        state = "running"
    else:
        state = "error"
    if stage == "detect":
        pct = 5 + 75 * (frame / total) if total else 40
        msg = f"Detecting & tracking: frame {frame}" + (f" / {total}" if total else "")
    elif stage == "ocr":
        pct = 80 + 19 * ((case - 1) / ncase if ncase else 0)
        msg = f"Reading number plates & issuing challans: case {case} / {ncase}" if ncase else "Post-processing"
    else:
        pct, msg = 100, "Done"
    out = {"state": state, "pct": round(pct, 1), "msg": msg, "run": j["run"], "source": j["source"],
           "elapsed": round(time.time() - j["started"])}
    if state == "error":
        tb = next((i for i, ln in enumerate(lines) if ln.startswith("Traceback")), None)
        err = lines[tb:] if tb is not None else [ln for ln in lines if not ln.startswith(("PROGRESS", "STAGE"))][-8:]
        out["error"] = "\n".join(err[-20:]) or f"Process exited with code {rc} before finishing."
    return out


# ---------------------------------------------------------------- rendering
STATUS_STYLE = {"challan_issued": ("Challan issued", "ok"), "manual_review": ("Manual review", "warn"),
                "rejected": ("Rejected", "muted"), "sms_failed": ("SMS failed", "bad"),
                "owner_not_found": ("Owner not found", "bad")}

CSS = """
:root{--bg:#f4f6fa;--card:#fff;--ink:#1c2330;--sub:#5d6b82;--line:#e2e7ef;--brand:#1f4fd1;
--ok:#16803c;--okbg:#e5f6ea;--warn:#a15c00;--warnbg:#fff3dc;--bad:#b42318;--badbg:#fdecea;--mutedbg:#eef0f4}
*{box-sizing:border-box}body{margin:0;font-family:Segoe UI,system-ui,sans-serif;background:var(--bg);color:var(--ink)}
header{background:#14213d;color:#fff;padding:18px 28px}
header h1{margin:0;font-size:20px}header .sub{color:#b8c2d9;font-size:13px;margin-top:2px}
header a{color:#fff;text-decoration:none}
main{padding:22px 28px;max-width:1400px;margin:auto}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:14px;margin-bottom:22px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.kpi .v{font-size:28px;font-weight:700}.kpi .l{color:var(--sub);font-size:13px}
.kpi.red .v{color:var(--bad)}.kpi.green .v{color:var(--ok)}.kpi.amber .v{color:var(--warn)}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin-bottom:22px}
h2{font-size:16px;margin:0 0 12px}h3{font-size:14px;margin:14px 0 8px}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{color:var(--sub);font-weight:600;background:#fafbfd}
img.ev{max-height:90px;max-width:160px;border-radius:6px;border:1px solid var(--line);cursor:zoom-in}
img.pl{max-height:40px;max-width:160px;border-radius:4px;border:1px solid var(--line);cursor:zoom-in}
.badge{display:inline-block;padding:2px 9px;border-radius:999px;font-size:12px;font-weight:600}
.ok{background:var(--okbg);color:var(--ok)}.warn{background:var(--warnbg);color:var(--warn)}
.bad{background:var(--badbg);color:var(--bad)}.muted{background:var(--mutedbg);color:var(--sub)}
.plate{font-family:Consolas,monospace;font-weight:700;background:#fffbe6;border:1px solid #e6d27a;padding:1px 6px;border-radius:4px}
.small{font-size:12px;color:var(--sub)}form.inline{display:flex;gap:6px;flex-wrap:wrap;margin-top:6px}
input[type=text]{padding:5px 7px;border:1px solid var(--line);border-radius:6px;width:120px;font-family:Consolas,monospace}
button,select{padding:7px 12px;border-radius:6px;border:1px solid var(--line);background:#fff;cursor:pointer;font-size:13px}
button.p{background:var(--brand);color:#fff;border-color:var(--brand)}button.r{color:var(--bad)}
button:disabled{opacity:.5;cursor:default}
.gallery{display:flex;gap:10px;overflow-x:auto}.gallery img{height:150px;border-radius:6px;border:1px solid var(--line);cursor:zoom-in}
details summary{cursor:pointer;color:var(--brand);font-size:12px}
.sms{white-space:pre-wrap;font-size:12px;background:#f7f9fc;border:1px solid var(--line);padding:6px;border-radius:6px;max-width:340px}
#zoom{position:fixed;inset:0;background:rgba(0,0,0,.8);display:none;align-items:center;justify-content:center;z-index:9}
#zoom img{max-width:92vw;max-height:92vh;background:#fff}
.flash{background:#e8f0ff;border:1px solid #b9cdfa;padding:10px 14px;border-radius:8px;margin-bottom:16px;white-space:pre-wrap;font-size:13px}
.upload{display:grid;grid-template-columns:1.3fr 1fr;gap:18px}
.drop{border:2px dashed #b9c4d8;border-radius:10px;padding:26px;text-align:center;background:#fafcff;cursor:pointer}
.drop.hover{border-color:var(--brand);background:#eef3ff}.drop b{color:var(--brand)}
.opts{display:flex;flex-direction:column;gap:10px;font-size:13px}
.bar{height:14px;background:#e8ecf3;border-radius:999px;overflow:hidden;margin:8px 0}
.bar>div{height:100%;background:linear-gradient(90deg,#1f4fd1,#3d7bff);width:0;transition:width .5s}
#prog{display:none}.err{white-space:pre-wrap;font-family:Consolas,monospace;font-size:12px;color:var(--bad)}
.result{display:grid;grid-template-columns:1.4fr 1fr;gap:18px}
video{width:100%;max-height:520px;background:#000;border-radius:8px}
.stats{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.stat{border:1px solid var(--line);border-radius:8px;padding:10px}.stat .v{font-size:22px;font-weight:700}
.stat .l{font-size:12px;color:var(--sub)}
.flashbad{background:var(--badbg);border-color:#f3b6b0;color:var(--bad)}
.queue{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:12px}
.card{border:1px solid var(--line);border-left:4px solid var(--warn);border-radius:8px;padding:12px;display:flex;flex-direction:column;gap:8px}
.ev2{display:flex;gap:10px;align-items:flex-start}
.verdict{font-size:15px;font-weight:600;padding:10px 12px;border-radius:8px;margin-bottom:10px}
@media(max-width:900px){.upload,.result{grid-template-columns:1fr}main{padding:14px}header{padding:14px}}
"""

ZOOM_JS = ("document.querySelectorAll('img.ev,img.pl,.gallery img').forEach(i=>i.onclick=()=>{"
           "const z=document.getElementById('zoom');z.querySelector('img').src=i.src;z.style.display='flex'});")

UPLOAD_JS = r"""
const $=s=>document.querySelector(s);
const PLATE_RE=/^([A-Z]{2}\d{1,2}[A-Z]{0,3}\d{4}|\d{2}BH\d{4}[A-Z]{1,2})$/;
function checkReview(ev,f){
  const act=ev.submitter?ev.submitter.dataset.act:'approve';
  f.act.value=act;
  if(act==='approve'){
    const p=f.plate.value.toUpperCase().replace(/[^A-Z0-9]/g,'');
    if(!p){alert('Type the number plate shown in the evidence photo before approving.');f.plate.focus();return false}
    if(!PLATE_RE.test(p)){alert(p+' is not a valid Indian plate number (e.g. MH12AB1234).');f.plate.focus();return false}
    f.plate.value=p;
  } else if(!confirm('Reject this case? No challan will be issued.')) return false;
  f.querySelectorAll('button').forEach(b=>b.disabled=true);
  const s=ev.submitter; if(s) s.textContent=act==='approve'?'Issuing challan...':'Rejecting...';
  return true;
}
const drop=$('#drop'),file=$('#file');
drop.onclick=()=>file.click();
['dragover','dragenter'].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.add('hover')}));
['dragleave','drop'].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.remove('hover')}));
drop.addEventListener('drop',ev=>{if(ev.dataTransfer.files.length){file.files=ev.dataTransfer.files;showName()}});
file.onchange=showName;
function showName(){$('#fname').textContent=file.files[0]?file.files[0].name+' ('+(file.files[0].size/1048576).toFixed(1)+' MB)':'';}
function sms(){return $('#sms').checked?1:0}
function lock(b){document.querySelectorAll('.go').forEach(x=>x.disabled=b)}
async function uploadGo(){
  const f=file.files[0]; if(!f){alert('Choose a video or image first');return}
  lock(true);showProg('Uploading '+f.name+' ...',2);
  const r=await fetch('/upload?name='+encodeURIComponent(f.name)+'&sms='+sms(),{method:'POST',body:f});
  const j=await r.json(); if(j.error){alert(j.error);lock(false);return}
  poll(j.job);
}
async function sampleGo(){
  lock(true);showProg('Starting ...',2);
  const r=await fetch('/run?source='+encodeURIComponent($('#sample').value)+'&sms='+sms(),{method:'POST'});
  const j=await r.json(); if(j.error){alert(j.error);lock(false);return}
  poll(j.job);
}
function showProg(msg,pct){$('#prog').style.display='block';$('#pmsg').textContent=msg;$('#pbar').style.width=pct+'%';}
async function poll(job){
  const s=await (await fetch('/status?job='+encodeURIComponent(job))).json();
  showProg(s.msg+'  ·  '+s.elapsed+'s',s.pct);
  if(s.state==='running'){setTimeout(()=>poll(job),1000);return}
  if(s.state==='done'){location.href='/?run='+encodeURIComponent(s.run)+'#result';return}
  $('#perr').textContent='Processing failed:\n'+(s.error||'unknown error');lock(false);
}
"""


SMS_OK = ("queued", "sending", "sent", "delivered", "accepted")


def sms_badge(c):
    st = c.get("sms_status")
    if st in SMS_OK:
        b = f"<span class='badge ok'>SMS sent to demo receiver ({esc(st)})</span>"
    elif st == "failed":
        b = (f"<span class='badge bad'>SMS failed</span>"
             f"<div class='small' style='color:var(--bad);max-width:320px'>{esc(c.get('sms_error') or 'see console')}</div>")
    elif st == "dry-run":
        b = "<span class='badge muted'>SMS dry-run (not sent)</span>"
    else:
        return ""
    return f"<div style='margin-bottom:4px'>{b}</div>"


def challan_rows(pairs, show_source=True):
    h = []
    order = {"challan_issued": 0, "manual_review": 1}
    for r, c in sorted(pairs, key=lambda rc: (order.get(rc[1].get("status"), 2), -rc[0]["mtime"])):
        lbl, cls = STATUS_STYLE.get(c.get("status"), (c.get("status"), "muted"))
        ri, pi = rel_url(c.get("rider_img")), rel_url(c.get("plate_img"))
        match = c.get("plate_match") or c.get("plate_source")
        match_txt = {"exact": "OCR exact", "db_fuzzy": f"OCR + DB match (d={c.get('match_distance')})",
                     "officer_corrected": "officer corrected", "ocr": "OCR"}.get(match, "")
        plate = c.get("plate") if c.get("status") != "manual_review" else None
        h.append("<tr>")
        h.append(f"<td><b>{esc(c['challan_id'])}</b><div class='small'>track {esc(c.get('track_id'))}"
                 f" &middot; {esc(c.get('hits'))} frames</div><div class='small'>{esc(c.get('timestamp'))}</div></td>")
        if show_source:
            h.append(f"<td><a href='/?run={urllib.parse.quote(r['name'])}#result'>{esc(r['summary'].get('source'))}</a></td>")
        h.append(f"<td>{f'<img class=ev src={ri}>' if ri else '-'}</td>")
        h.append(f"<td>{f'<img class=pl src={pi}>' if pi else '-'}<div class='small'>OCR: {esc(c.get('raw_ocr'))}</div></td>")
        h.append(f"<td>{f'<span class=plate>{esc(plate)}</span>' if plate else '-'}"
                 f"<div class='small'>{esc(match_txt) if match_txt else ''}</div></td>")
        h.append(f"<td>{esc(c.get('owner'))}<div class='small'>{esc(c.get('sms_to'))}</div></td>")
        reason = f"<div class='small'>{esc(c.get('reason'))}</div>" if c.get("status") == "manual_review" else ""
        h.append(f"<td><span class='badge {cls}'>{esc(lbl)}</span>{reason}"
                 f"{'<div class=small>Fine Rs. ' + esc(c.get('fine')) + '</div>' if c.get('fine') else ''}</td><td>")
        if c.get("sms_text"):
            h.append(sms_badge(c) + f"<details><summary>{'Alert' if c.get('sms_kind') == 'alert' else 'e-Challan'} SMS text</summary>"
                     f"<div class='sms'>{esc(c['sms_text'])}</div></details>")
        if c.get("status") == "manual_review":
            h.append(f"<a href='#case-{esc(r['name'])}-{esc(c['challan_id'])}'>Review this case &darr;</a>")
        if c.get("reviewed_by"):
            h.append(f"<div class='small'>{esc(c.get('review_action', '')).capitalize()} by {esc(c['reviewed_by'])}"
                     f"<br>{esc(c.get('reviewed_at', '').replace('T', ' '))}</div>")
        h.append("</td></tr>")
    return "".join(h)


def review_section(runs):
    """Officer review: pending queue (approve / reject) + history of officer actions."""
    pending = [(r, c) for r in runs for c in r["challans"] if c.get("status") == "manual_review"]
    done = sorted([(r, c) for r in runs for c in r["challans"] if c.get("reviewed_by")],
                  key=lambda rc: rc[1].get("reviewed_at", ""), reverse=True)
    h = [f"<section id='review'><h2>Officer review &mdash; {len(pending)} pending</h2>"
         "<div class='small' style='margin-bottom:10px'>Cases the system could not finish on its own "
         "(plate unreadable / not registered). Check the evidence, type the plate and approve to issue the "
         "e-challan and send the SMS, or reject a false detection.</div>"]
    if not pending:
        h.append("<div class='small'>No cases waiting for review.</div>")
    h.append("<div class='queue'>")
    for r, c in pending:
        ri, pi = rel_url(c.get("rider_img")), rel_url(c.get("plate_img"))
        pre = esc(c["plate"]) if c.get("plate") and is_valid(c["plate"]) else ""
        plate_html = (f"<div><img class='pl' src='{pi}'><div class='small'>OCR: {esc(c.get('raw_ocr'))}</div></div>"
                      if pi else "<div class='small'>No plate crop</div>")
        rider_html = f"<img class='ev' src='{ri}'>" if ri else ""
        h.append(f"<div class='card' id='case-{esc(r['name'])}-{esc(c['challan_id'])}'>"
                 f"<div><b>{esc(c['challan_id'])}</b><div class='small'>{esc(r['summary'].get('source'))} &middot; "
                 f"{esc(c.get('hits'))} frames &middot; {esc((c.get('timestamp') or '').replace('T', ' '))}</div>"
                 f"<div class='small' style='color:var(--warn)'>{esc(c.get('reason'))}</div></div>"
                 f"<div class='ev2'>{rider_html}{plate_html}</div>"
                 f"<form method='post' action='/review' class='inline' onsubmit='return checkReview(event,this)'>"
                 f"<input type='hidden' name='run' value='{esc(r['name'])}'>"
                 f"<input type='hidden' name='case' value='{esc(c['challan_id'])}'>"
                 f"<input type='hidden' name='act' value=''>"
                 f"<input type='text' name='plate' placeholder='e.g. MH12AB1234' value='{pre}'>"
                 f"<button class='p' type='submit' data-act='approve'>Approve &amp; send</button>"
                 f"<button class='r' type='submit' data-act='reject'>Reject</button></form>"
                 f"{sms_badge(c) if c.get('sms_kind') == 'alert' else ''}</div>")
    h.append("</div>")
    h.append("<h3>Review history</h3>")
    if not done:
        h.append("<div class='small'>No officer actions yet.</div>")
    else:
        h.append("<div style='overflow-x:auto'><table><tr><th>When</th><th>Officer</th><th>Case</th><th>Source</th>"
                 "<th>Action</th><th>Plate</th><th>Owner</th><th>SMS</th></tr>")
        for r, c in done:
            act = c.get("review_action") or ("rejected" if c.get("status") == "rejected" else "approved")
            cls = "ok" if act == "approved" else "muted"
            plate_cell = f"<span class='plate'>{esc(c.get('plate'))}</span>" if act == "approved" else "-"
            h.append(f"<tr><td>{esc((c.get('reviewed_at') or '').replace('T', ' '))}</td><td>{esc(c['reviewed_by'])}</td>"
                     f"<td><b>{esc(c['challan_id'])}</b></td>"
                     f"<td><a href='/?run={urllib.parse.quote(r['name'])}#result'>{esc(r['summary'].get('source'))}</a></td>"
                     f"<td><span class='badge {cls}'>{esc(act.capitalize())}</span>"
                     f"{'<div class=small>Fine Rs. ' + esc(c.get('fine')) + '</div>' if act == 'approved' and c.get('fine') else ''}</td>"
                     f"<td>{plate_cell}</td>"
                     f"<td>{esc(c.get('owner')) if act == 'approved' else '-'}</td>"
                     f"<td>{sms_badge(c) if act == 'approved' else '-'}</td></tr>")
        h.append("</table></div>")
    h.append("</section>")
    return "".join(h)


def table(rows_html, show_source=True):
    head = ("<th>Challan</th>" + ("<th>Source</th>" if show_source else "") +
            "<th>Rider evidence</th><th>Plate evidence</th><th>Plate</th><th>Owner</th><th>Status</th><th>SMS / Action</th>")
    return f"<div style='overflow-x:auto'><table><tr>{head}</tr>{rows_html}</table></div>"


def result_section(r):
    s, ch = r["summary"], r["challans"]
    issued = sum(1 for c in ch if c.get("status") == "challan_issued")
    review = sum(1 for c in ch if c.get("status") == "manual_review")
    nv = s.get("confirmed_violations", 0)
    if nv == 0:
        d = s.get("detections_per_class", {})
        why = s.get("skipped_riders") or {}
        if not d.get("rider"):
            expl = "No motorcycle rider was detected in this input."
        elif why:
            expl = "Riders were detected but not fined: " + "; ".join(
                f"{k} ({v} {'frame' if v == 1 else 'frames'})" for k, v in sorted(why.items(), key=lambda kv: -kv[1])) + "."
        else:
            expl = "All detected riders are wearing helmets."
        verdict = (f"<div class='verdict ok'>No helmet violation detected in this input.</div>"
                   f"<div class='small' style='margin:-4px 0 10px'>{esc(expl)} "
                   f"Detected: {esc(d.get('rider', 0))} rider, {esc(d.get('helmet', 0))} helmet, "
                   f"{esc(d.get('no_helmet', 0))} bare head, {esc(d.get('number_plate', 0))} plate. "
                   f"A violation needs a bare head linked to a riding person fully inside the frame.</div>")
    else:
        verdict = (f"<div class='verdict bad'>{nv} no-helmet violation{'s' if nv != 1 else ''} detected &mdash; "
                   f"{issued} e-challan{'s' if issued != 1 else ''} issued, {review} sent to officer review.</div>")
    vid = os.path.join(r["dir"], "output_annotated.mp4")
    img = os.path.join(r["dir"], "frames", "annotated_image.png")
    if os.path.exists(vid):
        media = f"<video controls autoplay muted loop src='{rel_url(vid)}'></video>"
    elif os.path.exists(img):
        media = f"<div class='gallery'><img style='height:auto;max-width:100%;max-height:520px' src='{rel_url(img)}'></div>"
    else:
        media = "<div class='small'>No annotated output.</div>"
    d = s.get("detections_per_class", {})
    stats = [("Frames", s.get("frames")), ("Resolution", s.get("resolution")),
             ("Detect + track", f"{s.get('avg_detect_track_ms')} ms"), ("Processing speed", f"{s.get('processing_fps')} fps"),
             ("Riders detected", d.get("rider")), ("No-helmet detections", d.get("no_helmet")),
             ("Helmet detections", d.get("helmet")), ("Plates detected", d.get("number_plate"))]
    h = [f"<section id='result'><h2>Result &mdash; {esc(s.get('source'))}</h2>{verdict}<div class='result'><div>{media}"
         f"<div class='small' style='margin-top:6px'>Red box = violating rider &middot; green = helmet &middot; "
         f"yellow = number plate. <a href='{rel_url(vid) or '#'}' download>Download video</a></div></div>"
         "<div class='stats'>" + "".join(f"<div class='stat'><div class='v'>{esc(v)}</div><div class='l'>{esc(l)}</div></div>"
                                        for l, v in stats) + "</div></div>"]
    if ch:
        h.append("<h3>Violations in this input</h3>" + table(challan_rows([(r, c) for c in ch], False), False))
    imgs = [u for u in (rel_url(p) for p in r["frames"]) if u and not u.endswith("annotated_image.png")]
    if imgs:
        h.append("<h3>Evidence frames</h3><div class='gallery'>" + "".join(f"<img src='{u}' loading='lazy'>" for u in imgs) + "</div>")
    h.append("</section>")
    return "".join(h)


def render(flash="", run_name=None):
    runs = load_runs()
    allc = [(r, c) for r in runs for c in r["challans"]]
    n_viol = sum(r["summary"].get("confirmed_violations", 0) for r in runs)
    n_issued = sum(1 for _, c in allc if c.get("status") == "challan_issued")
    n_review = sum(1 for _, c in allc if c.get("status") == "manual_review")
    n_sms = sum(1 for _, c in allc if c.get("sms_status") in SMS_OK)
    fines = sum(c.get("fine", 0) for _, c in allc if c.get("status") == "challan_issued")
    frames = sum(r["summary"].get("frames", 0) for r in runs)
    samples = sorted(x for x in os.listdir(ASSETS) if x.lower().endswith(MEDIA_EXT))

    h = [f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
         f"<title>Challan Dashboard</title><style>{CSS}</style></head><body>"
         f"<header><h1><a href='/'>Smart Traffic Safety &amp; Automated e-Challan</a></h1>"
         f"<div class='sub'>{esc(config.CAMERA_ID)} &middot; {esc(config.LOCATION)} &middot; "
         f"YOLO11 + DeepSORT + EasyOCR + Mistral (Ollama) + Twilio</div></header><main>"]
    if flash:
        bad = flash.startswith("ERROR") or flash.startswith("Test SMS FAILED")
        h.append(f"<div id='flash' class='flash{' flashbad' if bad else ''}'>{esc(flash)}</div>")

    # ---- upload / run ----
    h.append("<section><h2>Process a new video or image</h2><div class='upload'>"
             "<div><div id='drop' class='drop'><div style='font-size:30px'>&#11014;</div>"
             "<div><b>Click to choose</b> or drag a video / image here</div>"
             "<div class='small'>mp4, avi, mov, mkv, jpg, png</div><div id='fname' style='margin-top:8px;font-weight:600'></div></div>"
             "<input type='file' id='file' accept='video/*,image/*' hidden>"
             "<div style='margin-top:10px'><button class='p go' onclick='uploadGo()'>Upload &amp; process</button></div></div>"
             "<div class='opts'><div><b>Or use a sample file</b></div><div style='display:flex;gap:6px'><select id='sample'>"
             + "".join(f"<option>{esc(a)}</option>" for a in samples) +
             "</select><button class='go' onclick='sampleGo()'>Process sample</button></div>"
             f"<label><input type='checkbox' id='sms' checked> Send SMS to demo receiver "
             f"({esc(('...' + config.DEMO_RECEIVER[-4:]) if config.DEMO_RECEIVER else 'not set')}) when a violation is detected "
             "<span class='small'>(unchecked = dry run, message only shown)</span></label>"
             "<form method='post' action='/test-sms' style='margin:0'><button>Send test SMS</button></form>"
             "<div class='small'>Pipeline: detection &rarr; tracking &rarr; helmet/plate association &rarr; 3-frame confirmation "
             "&rarr; plate OCR (5 best frames) &rarr; owner lookup &rarr; e-challan SMS.</div></div></div>"
             "<div id='prog'><div class='bar'><div id='pbar'></div></div><div id='pmsg' class='small'></div>"
             "<div id='perr' class='err'></div></div></section>")

    # ---- selected result ----
    sel = next((r for r in runs if r["name"] == run_name), None)
    if sel:
        h.append(result_section(sel))
    h.append(review_section(runs))

    kp = [("Inputs processed", len(runs), ""), ("Frames analysed", f"{frames:,}", ""),
          ("No-helmet violations", n_viol, "red"), ("Challans issued", n_issued, "green"),
          ("Pending officer review", n_review, "amber"), ("SMS delivered", n_sms, "green"),
          ("Fines raised (Rs.)", f"{fines:,}", "")]
    h.append("<div class='kpis'>" + "".join(
        f"<div class='kpi {c}'><div class='v'>{esc(v)}</div><div class='l'>{esc(l)}</div></div>" for l, v, c in kp) + "</div>")

    h.append("<section><h2>All violations &amp; e-challans</h2>")
    h.append(table(challan_rows(allc)) if allc else "<div class='small'>No violations yet.</div>")
    h.append("</section>")

    h.append("<section><h2>Processing history</h2><div style='overflow-x:auto'><table><tr><th>Input</th><th>Frames</th>"
             "<th>Resolution</th><th>Speed</th><th>Riders / helmet / no-helmet / plate</th>"
             "<th>Violations</th><th>Issued</th><th>Review</th><th></th></tr>")
    for r in runs:
        s = dict(r["summary"])
        # counts from challans.json so officer actions show up immediately
        s["challans_issued"] = sum(1 for c in r["challans"] if c.get("status") == "challan_issued")
        s["manual_review"] = sum(1 for c in r["challans"] if c.get("status") == "manual_review")
        d = s.get("detections_per_class", {})
        h.append(f"<tr><td><b>{esc(s.get('source'))}</b><div class='small'>{esc(time.strftime('%d-%m-%Y %H:%M', time.localtime(r['mtime'])))}</div></td>"
                 f"<td>{esc(s.get('frames'))}</td><td>{esc(s.get('resolution'))}</td>"
                 f"<td>{esc(s.get('processing_fps'))} fps</td>"
                 f"<td>{esc(d.get('rider'))} / {esc(d.get('helmet'))} / {esc(d.get('no_helmet'))} / {esc(d.get('number_plate'))}</td>"
                 f"<td>{esc(s.get('confirmed_violations'))}</td><td>{esc(s.get('challans_issued'))}</td>"
                 f"<td>{esc(s.get('manual_review'))}</td><td><a href='/?run={urllib.parse.quote(r['name'])}#result'>View result</a></td></tr>")
    h.append("</table></div></section></main><div id='zoom' onclick=\"this.style.display='none'\"><img></div>"
             f"<script>{ZOOM_JS}{UPLOAD_JS}</script></body></html>")
    return "".join(h)


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj), "application/json")

    def _file(self, p):
        """Serve a file with HTTP Range support (needed for video seeking)."""
        size = os.path.getsize(p)
        ctype = mimetypes.guess_type(p)[0] or "application/octet-stream"
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        m = re.match(r"bytes=(\d*)-(\d*)", rng or "")
        if m and size:
            if m.group(1):
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2)))
            end = min(end, size - 1)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with open(p, "rb") as f:
            f.seek(start)
            left = end - start + 1
            try:
                while left > 0:
                    chunk = f.read(min(65536, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
                pass

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        if url.path == "/":
            return self._send(200, render(q.get("msg", ""), q.get("run")))
        if url.path == "/status":
            return self._json(job_status(q.get("job", "")))
        if url.path.startswith("/file/"):
            p = os.path.normpath(os.path.join(BASE, urllib.parse.unquote(url.path[len("/file/"):])))
            if not p.startswith(os.path.normpath(BASE)) or not os.path.isfile(p) or \
                    os.path.basename(p).startswith(".env"):
                return self._send(404, "not found", "text/plain")
            return self._file(p)
        self._send(404, "not found", "text/plain")

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        n = int(self.headers.get("Content-Length", 0))
        if url.path == "/upload":
            name = os.path.basename(q.get("name", ""))
            stem, ext = os.path.splitext(name)
            stem = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_")[:40] or "upload"
            if ext.lower() not in MEDIA_EXT:
                self.rfile.read(n)
                return self._json({"error": f"Unsupported file type '{ext}'."}, 400)
            dest = os.path.join(ASSETS, "uploads", f"{stem}_{time.strftime('%H%M%S')}{ext.lower()}")
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                left = n
                while left > 0:
                    chunk = self.rfile.read(min(1 << 20, left))
                    if not chunk:
                        break
                    f.write(chunk)
                    left -= len(chunk)
            return self._json({"job": start_job(dest, q.get("sms") == "1")})
        if url.path == "/run":
            path = os.path.join(ASSETS, os.path.basename(q.get("source", "")))
            if not os.path.isfile(path):
                return self._json({"error": "Unknown sample file."}, 400)
            return self._json({"job": start_job(path, q.get("sms") == "1")})
        if url.path == "/test-sms":
            import notifier
            self.rfile.read(n)
            env_dry = os.environ.pop("SMS_DRY_RUN", None)
            st, sid = notifier.send_sms(None, f"Test SMS from {config.CAMERA_ID} e-Challan system, "
                                              f"{time.strftime('%d-%m-%Y %H:%M')}. Twilio is working.")
            if env_dry is not None:
                os.environ["SMS_DRY_RUN"] = env_dry
            msg = (f"Test SMS sent to demo receiver (status {st}, SID {sid})." if st != "failed"
                   else f"Test SMS FAILED: {notifier.LAST_ERROR}")
            return self._redirect(msg)
        if url.path == "/review":
            from review import apply_review
            form = {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(n).decode()).items()}
            run = os.path.basename(form.get("run", ""))
            run_dir = os.path.join(OUT, run)
            if not os.path.exists(os.path.join(run_dir, "challans.json")):
                return self._redirect("ERROR: unknown run.")
            try:
                ok, msg = apply_review(run_dir, form.get("case", ""), form.get("act") != "reject",
                                       form.get("plate") or None, form.get("officer") or "Officer-01")
            except Exception as e:          # never leave the officer without feedback
                ok, msg = False, f"{type(e).__name__}: {e}"
            return self._redirect(msg if ok else "ERROR: " + msg, anchor="flash")
        self._send(404, "not found", "text/plain")

    def _redirect(self, msg, run=None, anchor=None):
        self.send_response(303)
        loc = "/?msg=" + urllib.parse.quote(msg) + (f"&run={urllib.parse.quote(run)}" if run else "")
        loc += f"#{anchor}" if anchor else ("#result" if run else "#flash")
        self.send_header("Location", loc)
        self.end_headers()

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"Dashboard running at http://localhost:{PORT}  (Ctrl+C to stop)")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
