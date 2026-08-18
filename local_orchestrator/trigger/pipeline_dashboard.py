#!/usr/bin/env python3
"""Live pipeline status dashboard: one row per bag, one colored cell per stage.

Reads the same job records the trigger uses (DATA_ROOT/_state/) on every request.
Serves self-contained HTML on port 8090 (override with PIPELINE_DASHBOARD_PORT);
binds 0.0.0.0 for LAN access. Also serves job logs at /log/<job_id>.

Usage:
    python local_orchestrator/trigger/pipeline_dashboard.py
"""

from __future__ import annotations

import html
import json
import os
import re
import socket
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline_helpers import resolve_data_root
from pipeline_trigger import bag_stages, find_bags
from label_studio_sync import load_config as load_ls_config

COLUMNS = ("MISSION", "TIMESTAMP", "EXTRACT", "REVIEW", "ENHANCE", "COLMAP")
PCT_RE = re.compile(r"(\d+)%")
CSS = """
:root { --bg:#f6f7f9; --card:#ffffff; --ink:#1f2430; --muted:#6b7280; --line:#e5e7eb;
        --green:#d7efd9; --yellow:#fdf3c4; --red:#fbd9d3; --blue:#d8e6fb; --grey:#e5e7eb; }
* { box-sizing: border-box; }
body { font-family: system-ui, -apple-system, sans-serif; margin: 0; padding: 2rem;
       background: var(--bg); color: var(--ink); }
h1 { font-size: 1.05rem; margin: 0; font-weight: 650; }
.meta { color: var(--muted); font-size: 0.8rem; margin-top: 0.25rem; }
.gpu { float: right; font-size: 0.8rem; padding: 0.25rem 0.7rem; border-radius: 99px; }
.gpu.busy { background: var(--yellow); } .gpu.idle { background: var(--green); }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 10px;
        padding: 1.25rem 1.5rem; max-width: 75rem; margin: 0 auto;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
table { border-collapse: collapse; width: 100%; margin-top: 1rem; }
th { text-align: left; font-size: 0.68rem; letter-spacing: 0.08em; color: var(--muted);
     padding: 0.5rem 0.9rem; border-bottom: 2px solid var(--line); }
td { padding: 0.65rem 0.9rem; border-bottom: 1px solid var(--line);
     font-size: 0.85rem; vertical-align: top; }
tr:hover td { filter: brightness(0.985); }
td.done { background: var(--green); } td.running { background: var(--yellow); }
td.failed { background: var(--red); } td.pending { background: var(--blue); }
td.rejected { background: var(--grey); color: var(--muted); }
td a { color: inherit; text-decoration: underline dotted; }
.detail { display: block; font-size: 0.7rem; color: var(--muted); margin-top: 0.25rem;
          max-width: 22rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bar { height: 5px; background: rgba(0,0,0,0.10); border-radius: 3px; margin-top: 0.4rem; }
.bar > div { height: 100%; border-radius: 3px; background: #b98f00; }
.legend { margin-top: 1rem; font-size: 0.75rem; color: var(--muted); }
.legend span { padding: 0.15rem 0.65rem; border-radius: 4px; margin-right: 0.4rem; }
"""


def read_record(jobs_dir: Path, job_id: str) -> dict | None:
    p = jobs_dir / f"{job_id}.json"
    try:
        return json.loads(p.read_text()) if p.exists() else None
    except (json.JSONDecodeError, OSError):
        return None


def last_log_line(record: dict | None) -> str:
    log = (record or {}).get("outputs", {}).get("log")
    if not log or not Path(log).exists():
        return ""
    with open(log, "rb") as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - 4096))
        lines = f.read().decode(errors="replace").splitlines()
    return next((ln.strip() for ln in reversed(lines) if ln.strip()), "")


def elapsed_minutes(record: dict | None) -> int | None:
    started = (record or {}).get("started_utc")
    if not started:
        return None
    t0 = datetime.strptime(started, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return int((datetime.now(timezone.utc) - t0).total_seconds() // 60)


def job_cell(jobs_dir: Path, job_id: str, status: str | None) -> dict:
    cell = {"label": status or "pending", "cls": "pending", "detail": "", "href": None, "pct": None}
    if status is None:
        return cell
    if status == "done":
        return {**cell, "cls": "done"}
    if status == "corrupt":
        return {**cell, "label": "corrupt record", "cls": "failed",
                "detail": f"unreadable: {jobs_dir / f'{job_id}.json'}"}
    record = read_record(jobs_dir, job_id)
    if status == "running":
        mins = elapsed_minutes(record)
        detail = last_log_line(record)
        pct = PCT_RE.search(detail)
        return {**cell, "label": f"running {mins}min" if mins is not None else "running",
                "cls": "running", "detail": detail, "href": f"/log/{job_id}",
                "pct": int(pct.group(1)) if pct else None}
    tail = ((record or {}).get("error_tail") or "").strip().splitlines()
    last = next((ln.strip() for ln in reversed(tail) if ln.strip()), "")
    return {**cell, "label": "failed", "cls": "failed", "detail": last, "href": f"/log/{job_id}"}


def review_cell(stages: dict, ls_cfg: dict | None, host: str) -> dict:
    cell = {"label": "-", "cls": "pending", "detail": "", "href": None, "pct": None}
    approval = stages["approval"]
    if approval is None:
        if stages["extract"] != "done":
            return cell
        if stages.get("ls_task") and ls_cfg:
            # Reuse the viewer's hostname so the link works from any machine.
            ls_port = urlsplit(ls_cfg["url"]).port or 80
            base = f"http://{host}:{ls_port}" if host else ls_cfg["url"]
            href = (f"{base}/projects/{ls_cfg['project_id']}/data"
                    f"?task={stages['ls_task']}")
            return {**cell, "label": f"in review (task {stages['ls_task']})",
                    "detail": "annotate in Label Studio", "href": href}
        return {**cell, "label": "waiting for review",
                "detail": "run approve_bag_for_processing.py"}
    if not approval.get("approved"):
        return {**cell, "label": "rejected", "cls": "rejected"}
    ranges = approval.get("ranges")
    label = f"approved ({len(ranges)} ranges)" if ranges else "approved"
    return {**cell, "label": label, "cls": "done",
            "detail": ", ".join(ranges) if ranges else ""}


def render_cell(cell: dict) -> str:
    label = html.escape(cell["label"])
    if cell["href"]:
        label = f'<a href="{html.escape(cell["href"])}">{label}</a>'
    inner = label
    if cell["pct"] is not None:
        inner += f'<div class="bar"><div style="width:{cell["pct"]}%"></div></div>'
    if cell["detail"]:
        inner += f'<span class="detail">{html.escape(cell["detail"])}</span>'
    return f'<td class="{cell["cls"]}" title="{html.escape(cell["detail"])}">{inner}</td>'


def render(data_root: Path, host: str = "") -> str:
    state_dir = data_root / "_state"
    jobs_dir, approvals_dir = state_dir / "jobs", state_dir / "approvals"
    ls_cfg = load_ls_config(state_dir)
    rows, gpu_busy = [], False
    counts = {"complete": 0, "failed": 0, "running": 0, "in review": 0, "pending": 0}
    for bag in find_bags(data_root):
        stages = bag_stages(bag, jobs_dir, approvals_dir)
        if stages is None:
            continue
        cells = [
            job_cell(jobs_dir, stages["job_id"], stages["extract"]),
            review_cell(stages, ls_cfg, host),
            job_cell(jobs_dir, f"{stages['job_id']}__phaseformer", stages["enhance"]),
            job_cell(jobs_dir, f"{stages['job_id']}__colmap-spatial", stages["colmap"]),
        ]
        gpu_busy = gpu_busy or "running" in (stages["enhance"], stages["colmap"])
        classes = [c["cls"] for c in cells]
        if "failed" in classes:
            counts["failed"] += 1
        elif "running" in classes:
            counts["running"] += 1
        elif stages["colmap"] == "done":
            counts["complete"] += 1
        elif stages["extract"] == "done" and stages["approval"] is None:
            counts["in review"] += 1
        else:
            counts["pending"] += 1
        rows.append(f"<tr><td>{html.escape(stages['mission_id'])}</td>"
                    f"<td>{html.escape(stages['timestamp'])}</td>"
                    + "".join(render_cell(c) for c in cells) + "</tr>")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    summary = " · ".join(f"{v} {k}" for k, v in counts.items() if v) or "no bags"
    gpu = ('<span class="gpu busy">GPU busy</span>' if gpu_busy
           else '<span class="gpu idle">GPU idle</span>')
    header_cells = "".join(f"<th>{c}</th>" for c in COLUMNS)
    body = "".join(rows) or '<tr><td colspan="6">No bags found</td></tr>'
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="10"><title>SUBDRON Pipeline</title>
<style>{CSS}</style></head><body>
<div class="card">{gpu}
<h1>SUBDRON Pipeline — {html.escape(socket.gethostname())}</h1>
<p class="meta">{html.escape(str(data_root))} · {now} · {summary}</p>
<table><tr>{header_cells}</tr>{body}</table>
<p class="legend"><span style="background:var(--green)">done</span>
<span style="background:var(--yellow)">running</span>
<span style="background:var(--red)">failed</span>
<span style="background:var(--blue)">pending</span>
<span style="background:var(--grey)">rejected</span>
— running/failed cells link to the job log, review cells to Label Studio</p>
</div></body></html>"""


def main() -> None:
    data_root = resolve_data_root(None)
    port = int(os.getenv("PIPELINE_DASHBOARD_PORT", "8090"))
    logs_dir = (data_root / "_state" / "logs").resolve()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/log/"):
                job_id = self.path[len("/log/"):]
                log = (logs_dir / f"{job_id}.log").resolve()
                if re.fullmatch(r"[\w.-]+", job_id) and log.is_file():
                    self._send(log.read_bytes(), "text/plain; charset=utf-8")
                else:
                    self.send_error(404)
                return
            host = (self.headers.get("Host") or "").split(":")[0]
            self._send(render(data_root, host).encode(), "text/html; charset=utf-8")

        def _send(self, body: bytes, ctype: str):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # keep journald quiet
            pass

    print(f"Dashboard on http://0.0.0.0:{port} (data root: {data_root})")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
