#!/usr/bin/env python3
"""The pipeline trigger: one pass over DATA_ROOT, launching each bag's next stage.

Level-triggered and marker-free — state comes from the job records the runners
write (DATA_ROOT/_state/jobs/), never from filesystem events. For every bag
under <source>/<mission>/03_Logs/*.bag:

    no extraction record            -> runners/run_extraction.py
    extracted + approved, no enh.   -> runners/run_phaseformer.py (per approved range)
    enhanced, no colmap record      -> runners/run_colmap.py

The approval gate is fed by approve_bag_for_processing.py. Failed jobs are
reported but never retried automatically — re-run manually with --force.
One job at a time; a lock prevents overlapping passes, so a systemd timer can
invoke this at any interval.

Usage:
    python local_orchestrator/trigger/pipeline_trigger.py [--dry-run] [--dev-code]
"""

from __future__ import annotations

import argparse
import fcntl
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline_helpers import resolve_data_root

try:
    import subdron_utils
except ImportError:
    sys.exit("[ERROR] subdron_utils missing — run ./scripts/setup_local_orchestrator.sh first")

RUNNERS_DIR = Path(__file__).resolve().parent.parent / "runners"


def record_status(jobs_dir: Path, job_id: str) -> str | None:
    p = jobs_dir / f"{job_id}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text()).get("status")


def find_bags(data_root: Path) -> list[Path]:
    return [b for b in sorted(data_root.glob("*/*/03_Logs/*.bag"))
            if "transformed" not in b.parts]


def run_stage(runner: str, arg: str, extra: list[str], dry_run: bool) -> bool:
    cmd = [sys.executable, str(RUNNERS_DIR / runner), arg] + extra
    if dry_run:
        print(f"  would run: {' '.join(cmd)}")
        return True
    print(f"  running: {runner} {arg}")
    return subprocess.run(cmd).returncode == 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-root", help="Data root (default: $DATA_ROOT or <repo>/data)")
    parser.add_argument("--dry-run", action="store_true", help="Report decisions without launching anything")
    parser.add_argument("--dev-code", action="store_true", help="Pass --dev-code to every runner")
    args = parser.parse_args()

    data_root = resolve_data_root(args.data_root)
    state_dir = data_root / "_state"
    jobs_dir = state_dir / "jobs"
    approvals_dir = state_dir / "approvals"
    state_dir.mkdir(parents=True, exist_ok=True)

    lock = (state_dir / "trigger.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("Another trigger pass is running — skipping.")
        return

    extra = ["--dev-code"] if args.dev_code else []
    bags = find_bags(data_root)
    print(f"Trigger pass: {len(bags)} bag(s) under {data_root}")

    for bag in bags:
        try:
            source = subdron_utils.infer_source_from_path(str(bag))
            mission_id, timestamp, _ = subdron_utils.get_mission_info(str(bag))
        except ValueError as e:
            print(f"  SKIP {bag}: cannot derive identity ({e})")
            continue
        job_id = f"{source}__{mission_id}__{timestamp}"
        mission_dir = data_root / source / mission_id / "02_Post_Mission"

        # Stage 1: extraction
        status = record_status(jobs_dir, job_id)
        if status is None:
            print(f"[{job_id}] new bag -> extraction")
            run_stage("run_extraction.py", str(bag), extra, args.dry_run)
            continue
        if status in ("failed", "running"):
            print(f"[{job_id}] extraction {status}"
                  + (" — fix and re-run with --force" if status == "failed" else ""))
            continue

        # Stage 2: human approval gate, then phaseformer
        approval_path = approvals_dir / f"{job_id}.json"
        if not approval_path.exists():
            print(f"[{job_id}] extracted — waiting for review "
                  f"(watch videos, then run approve_bag_for_processing.py)")
            continue
        approval = json.loads(approval_path.read_text())
        if not approval.get("approved"):
            print(f"[{job_id}] rejected — skipping GPU stages")
            continue

        pf_status = record_status(jobs_dir, f"{job_id}__phaseformer")
        if pf_status is None:
            input_dir = mission_dir / "camera" / timestamp / "image_pose_pairs"
            ranges = approval.get("ranges") or [None]
            print(f"[{job_id}] approved -> phaseformer ({len(ranges)} range(s))")
            ok = True
            for i, r in enumerate(ranges):
                range_args = extra + (["--image-range", r] if r else [])
                if i > 0:
                    range_args += ["--force"]  # later ranges resume into the same output
                ok = run_stage("run_phaseformer.py", str(input_dir), range_args, args.dry_run) and ok
            if not ok or args.dry_run:
                continue
        elif pf_status in ("failed", "running"):
            print(f"[{job_id}] phaseformer {pf_status}")
            continue

        # Stage 3: colmap
        cm_status = record_status(jobs_dir, f"{job_id}__colmap-spatial")
        if cm_status is None:
            enhanced = mission_dir / "camera" / timestamp / "enhanced" / "PHASEFORMER"
            print(f"[{job_id}] enhanced -> colmap")
            run_stage("run_colmap.py", str(enhanced), extra, args.dry_run)
        elif cm_status == "done":
            print(f"[{job_id}] complete")
        else:
            print(f"[{job_id}] colmap {cm_status}")


if __name__ == "__main__":
    main()
