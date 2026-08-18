#!/usr/bin/env python3
"""Label Studio review sync (used by pipeline_trigger.py).

One-time setup (creates the review project, saves DATA_ROOT/_state/label_studio.json):

    python local_orchestrator/trigger/label_studio_sync.py setup --url <url> --token <token>

While that file exists, each trigger pass creates a review task per extracted bag
and turns finished annotations (timeline segments + Approve/Reject) into the
standard approval files. approve_bag_for_processing.py stays as manual fallback.
API failures are logged and skipped — the pipeline never depends on LS being up.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline_helpers import utc_now

TIMEOUT_S = 10


def load_config(state_dir: Path) -> dict | None:
    p = state_dir / "label_studio.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        print("[label-studio] warning: unreadable _state/label_studio.json — sync disabled")
        return None


def api(cfg: dict, method: str, path: str, payload: dict | None = None) -> dict:
    req = urllib.request.Request(
        cfg["url"].rstrip("/") + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        method=method,
        headers={"Authorization": f"Token {cfg['token']}",
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return json.loads(resp.read() or "{}")


def task_id_for(state_dir: Path, job_id: str) -> int | None:
    p = state_dir / "ls_tasks" / f"{job_id}.json"
    try:
        return json.loads(p.read_text())["task_id"] if p.exists() else None
    except (json.JSONDecodeError, OSError, KeyError):
        return None


def _review_video(data_root: Path, job_id: str) -> Path | None:
    """The bag's review video, from the extraction record (overlaid preferred)."""
    record_path = data_root / "_state" / "jobs" / f"{job_id}.json"
    try:
        videos_dir = Path(json.loads(record_path.read_text())["outputs"]["videos_dir"])
    except (json.JSONDecodeError, OSError, KeyError):
        return None
    for prefix in ("overlaid_timestamp", "clean"):
        hit = next(iter(sorted(videos_dir.glob(f"{prefix}*.mp4"))), None)
        if hit:
            return hit
    return None


def _write_approval(data_root: Path, job_id: str, approved: bool, ranges: list[str] | None) -> None:
    approvals_dir = data_root / "_state" / "approvals"
    approvals_dir.mkdir(parents=True, exist_ok=True)
    (approvals_dir / f"{job_id}.json").write_text(json.dumps({
        "job_id": job_id,
        "approved": approved,
        "ranges": ranges if approved else None,
        "decided_utc": utc_now(),
        "source": "label-studio",
    }, indent=2))
    verdict = f"APPROVED (ranges: {ranges or 'all'})" if approved else "REJECTED"
    print(f"[{job_id}] {verdict} via Label Studio")


def _parse_annotation(annotations: list[dict]) -> tuple[bool, list[str] | None] | None:
    """(approved, ranges) from the latest usable annotation, or None if undecided."""
    for ann in sorted(annotations, key=lambda a: a.get("id", 0), reverse=True):
        if ann.get("was_cancelled"):
            continue
        choices, ranges = [], []
        for item in ann.get("result", []):
            value = item.get("value", {})
            choices += value.get("choices", [])
            for r in value.get("ranges", []):
                ranges.append(f"{int(r['start'])}:{int(r['end'])}")
        if "Reject" in choices:
            return False, None
        # Only an explicit Approve triggers processing — segments alone are a draft.
        if "Approve" in choices:
            return True, sorted(ranges, key=lambda s: int(s.split(":")[0])) or None
    return None


def sync_bag(cfg: dict, data_root: Path, job_id: str, mission_id: str, timestamp: str) -> None:
    """Ensure the bag has a review task; harvest its annotation into an approval file."""
    if cfg.get("_down"):
        return
    state_dir = data_root / "_state"
    try:
        task_id = task_id_for(state_dir, job_id)
        if task_id is None:
            video = _review_video(data_root, job_id)
            if video is None:
                return  # no video (e.g. record missing) — CLI approval still works
            prefix = cfg.get("video_prefix", "")
            task = api(cfg, "POST", "/api/tasks", {
                "project": cfg["project_id"],
                "data": {
                    "video": f"/data/local-files/?d={prefix}{video.relative_to(data_root)}",
                    "job_id": job_id, "mission": mission_id, "timestamp": timestamp,
                },
            })
            sidecar_dir = state_dir / "ls_tasks"
            sidecar_dir.mkdir(parents=True, exist_ok=True)
            (sidecar_dir / f"{job_id}.json").write_text(json.dumps(
                {"task_id": task["id"], "created_utc": utc_now()}, indent=2))
            print(f"[{job_id}] review task created in Label Studio (task {task['id']})")
            return
        decision = _parse_annotation(api(cfg, "GET", f"/api/tasks/{task_id}").get("annotations", []))
        if decision is not None:
            _write_approval(data_root, job_id, *decision)
    except (urllib.error.URLError, OSError, json.JSONDecodeError, KeyError) as e:
        cfg["_down"] = True  # one warning, then quiet for the rest of the pass
        print(f"[label-studio] warning: {e} — sync skipped this pass")


LABEL_CONFIG = """
<View>
  <Header value="Watch the video, then either Reject, or mark the segments worth processing"/>
  <Video name="video" value="$video" frameRate="30"/>
  <Choices name="verdict" toName="video" showInLine="true">
    <Choice value="Approve"/>
    <Choice value="Reject"/>
  </Choices>
  <TimelineLabels name="ranges" toName="video">
    <Label value="Process" background="#4caf50"/>
  </TimelineLabels>
</View>
"""


def setup(url: str, token: str, data_root_arg: str | None, video_prefix: str = "") -> None:
    from pipeline_helpers import fail, resolve_data_root
    data_root = resolve_data_root(data_root_arg)
    cfg = {"url": url.rstrip("/"), "token": token}
    if video_prefix:
        cfg["video_prefix"] = video_prefix.rstrip("/") + "/"
    try:
        version = api(cfg, "GET", "/api/version")
    except Exception as e:
        fail(f"Cannot reach Label Studio at {cfg['url']}: {e}")
    print(f"Label Studio {version.get('release', '?')} at {cfg['url']}")

    project = api(cfg, "POST", "/api/projects", {
        "title": "SUBDRON bag review",
        "description": "Approve/reject extracted bags and mark the frame ranges worth GPU processing.",
        "label_config": LABEL_CONFIG,
    })
    cfg["project_id"] = project["id"]
    print(f"Created project 'SUBDRON bag review' (id {project['id']})")

    out = data_root / "_state" / "label_studio.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(cfg, indent=2))
    out.chmod(0o660)  # holds the API token: owner+group only
    print(f"Connection saved to {out}")
    print("Done — the trigger now creates review tasks automatically. "
          f"Review at {cfg['url']}/projects/{project['id']}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("setup", help="Create the review project and save the connection")
    p.add_argument("--url", required=True, help="Label Studio base URL, e.g. http://localhost:8080")
    p.add_argument("--token", required=True, help="API token (LS UI -> Account & Settings)")
    p.add_argument("--data-root", help="Data root (default: /srv/subdron/data; $DATA_ROOT overrides)")
    p.add_argument("--video-prefix", default="",
                   help="Path of the data root inside LS's document root, when they differ "
                        "(e.g. subdron_data for a mount at <document root>/subdron_data)")
    args = parser.parse_args()
    setup(args.url, args.token, args.data_root, args.video_prefix)
