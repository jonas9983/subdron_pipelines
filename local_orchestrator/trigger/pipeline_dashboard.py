#!/usr/bin/env python3
"""Live pipeline status dashboard: one row per bag, one colored cell per stage.

Reads the same job records the trigger uses (DATA_ROOT/_state/) on every request,
so the page is always current. Serves plain self-contained HTML on port 8090
(override with PIPELINE_DASHBOARD_PORT); binds 0.0.0.0 for LAN access.

Usage:
    python local_orchestrator/trigger/pipeline_dashboard.py
"""

from __future__ import annotations

import html
import json
import os
import socket
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline_helpers import resolve_data_root
from pipeline_trigger import bag_stages, find_bags

COLUMNS = ("MISSION", "TIMESTAMP", "EXTRACT", "REVIEW", "ENHANCE", "COLMAP")
CSS = """
body { font-family: system-ui, sans-serif; margin: 2rem; background: #fafafa; color: #212121; }
h1 { font-size: 1.2rem; } .meta { color: #616161; font-size: 0.85rem; margin-bottom: 1rem; }
table { border-collapse: collapse; } th, td { padding: 0.5rem 1rem; text-align: left; }
th { background: #eeeeee; font-size: 0.8rem; } tr { border-bottom: 1px solid #e0e0e0; }
td.done     { background: #c8e6c9; }
td.running  { background: #fff9c4; }
td.failed   { background: #ffcdd2; }
td.pending  { background: #bbdefb; }
td.rejected { background: #e0e0e0; color: #757575; }
.detail { display: block; font-size: 0.7rem; color: #424242; max-width: 24rem;
          overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.legend span { padding: 0.1rem 0.6rem; margin-right: 0.5rem; font-size: 0.8rem; }
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


def job_cell(jobs_dir: Path, job_id: str, status: str | None) -> tuple[str, str, str]:
    """-> (label, css_class, detail) for a runner-backed stage cell."""
    if status is None:
        return "pending", "pending", ""
    if status == "done":
        return "done", "done", ""
    if status == "corrupt":
        return "corrupt record", "failed", f"unreadable: {jobs_dir / f'{job_id}.json'}"
    record = read_record(jobs_dir, job_id)
    if status == "running":
        mins = elapsed_minutes(record)
        label = f"running {mins}min" if mins is not None else "running"
        return label, "running", last_log_line(record)
    tail = ((record or {}).get("error_tail") or "").strip().splitlines()
    last = next((ln.strip() for ln in reversed(tail) if ln.strip()), "")
    log = (record or {}).get("outputs", {}).get("log", "")
    return "failed", "failed", f"{last} — {log}" if last or log else ""


def review_cell(stages: dict) -> tuple[str, str, str]:
    approval = stages["approval"]
    if approval is None:
        if stages["extract"] == "done":
            if stages.get("ls_task"):
                return (f"in review (task {stages['ls_task']})", "pending",
                        "annotate it in Label Studio")
            return "waiting for review", "pending", "run approve_bag_for_processing.py"
        return "-", "pending", ""
    if not approval.get("approved"):
        return "rejected", "rejected", ""
    ranges = approval.get("ranges")
    return (f"approved ({len(ranges)} ranges)" if ranges else "approved"), "done", ""


def render(data_root: Path) -> str:
    jobs_dir = data_root / "_state" / "jobs"
    approvals_dir = data_root / "_state" / "approvals"
    rows, counts = [], {"complete": 0, "failed": 0, "running": 0, "waiting": 0, "pending": 0}
    for bag in find_bags(data_root):
        stages = bag_stages(bag, jobs_dir, approvals_dir)
        if stages is None:
            continue
        cells = [
            job_cell(jobs_dir, stages["job_id"], stages["extract"]),
            review_cell(stages),
            job_cell(jobs_dir, f"{stages['job_id']}__phaseformer", stages["enhance"]),
            job_cell(jobs_dir, f"{stages['job_id']}__colmap-spatial", stages["colmap"]),
        ]
        classes = [c[1] for c in cells]
        if "failed" in classes:
            counts["failed"] += 1
        elif "running" in classes:
            counts["running"] += 1
        elif stages["colmap"] == "done":
            counts["complete"] += 1
        elif stages["extract"] == "done" and stages["approval"] is None:
            counts["waiting"] += 1
        else:
            counts["pending"] += 1
        tds = "".join(
            f'<td class="{cls}" title="{html.escape(detail)}">{html.escape(label)}'
            + (f'<span class="detail">{html.escape(detail)}</span>' if detail else "")
            + "</td>"
            for label, cls, detail in cells
        )
        rows.append(f"<tr><td>{html.escape(stages['mission_id'])}</td>"
                    f"<td>{html.escape(stages['timestamp'])}</td>{tds}</tr>")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    summary = " · ".join(f"{v} {k}" for k, v in counts.items() if v)
    header_cells = "".join(f"<th>{c}</th>" for c in COLUMNS)
    body = "".join(rows) or '<tr><td colspan="6">No bags found</td></tr>'
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="10"><title>SUBDRON Pipeline</title>
<style>{CSS}</style></head><body>
<h1>SUBDRON Pipeline — {html.escape(socket.gethostname())}</h1>
<p class="meta">{html.escape(str(data_root))} · {now} · {summary or "no bags"}</p>
<table><tr>{header_cells}</tr>{body}</table>
<p class="legend"><span class="done" style="background:#c8e6c9">done</span>
<span style="background:#fff9c4">running</span>
<span style="background:#ffcdd2">failed</span>
<span style="background:#bbdefb">pending</span>
<span style="background:#e0e0e0">rejected</span></p>
</body></html>"""


def main() -> None:
    data_root = resolve_data_root(None)
    port = int(os.getenv("PIPELINE_DASHBOARD_PORT", "8090"))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            page = render(data_root).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

        def log_message(self, *args):  # keep journald quiet
            pass

    print(f"Dashboard on http://0.0.0.0:{port} (data root: {data_root})")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
