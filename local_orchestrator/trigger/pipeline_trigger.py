#!/usr/bin/env python3
"""The pipeline trigger: one pass over DATA_ROOT, launching each bag's next stage.

Level-triggered — state comes from the job records the runners
write (DATA_ROOT/_state/jobs/). For every bag under <source>/<mission>/03_Logs/*.bag:

    no extraction record            -> runners/run_extraction.py
    extracted + approved, no enh.   -> runners/run_phaseformer.py (per approved range)
    enhanced, no colmap record      -> runners/run_colmap.py

The approval defined by approve_bag_for_processing.py. Failed jobs are
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
from pipeline_helpers import resolve_data_root, resolve_under_data_root
import label_studio_sync

try:
    import subdron_utils
except ImportError:
    sys.exit("[ERROR] subdron_utils missing — run ./scripts/setup_local_orchestrator.sh first")

RUNNERS_DIR = Path(__file__).resolve().parent.parent / "runners"


def record_status(jobs_dir: Path, job_id: str) -> str | None:
    p = jobs_dir / f"{job_id}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text()).get("status")
    except (json.JSONDecodeError, OSError):
        return "corrupt"  # unreadable record (e.g. interrupted write) — treat like a failure


def failure_hint(jobs_dir: Path, job_id: str) -> str:
    """Last error line and log path from a failed job's record."""
    try:
        record = json.loads((jobs_dir / f"{job_id}.json").read_text())
    except (json.JSONDecodeError, OSError):
        return (f"\n      unreadable job record: {jobs_dir / f'{job_id}.json'}"
                f"\n      delete it to re-run the stage")
    tail = (record.get("error_tail") or "").strip().splitlines()
    last = next((ln for ln in reversed(tail) if ln.strip()), "(no output captured)")
    log = record.get("outputs", {}).get("log", "?")
    return f"\n      last output: {last}\n      full log:    {log}"


def find_bags(data_root: Path) -> list[Path]:
    return [b for b in sorted(data_root.glob("*/*/03_Logs/*.bag"))
            if "transformed" not in b.parts]


def bag_stages(bag: Path, jobs_dir: Path, approvals_dir: Path) -> dict | None:
    """Identity + per-stage status of one bag (shared with the dashboard).
    Returns None when the bag path yields no mission identity."""
    try:
        source = subdron_utils.infer_source_from_path(str(bag))
        mission_id, timestamp, _ = subdron_utils.get_mission_info(str(bag))
    except ValueError:
        return None
    job_id = f"{source}_{mission_id}_{timestamp}"
    approval = None
    approval_path = approvals_dir / f"{job_id}.json"
    if approval_path.exists():
        try:
            approval = json.loads(approval_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass  # unreadable approval = no decision yet
    return {
        "source": source, "mission_id": mission_id, "timestamp": timestamp,
        "job_id": job_id,
        "extract": record_status(jobs_dir, job_id),
        "approval": approval,
        "ls_task": label_studio_sync.task_id_for(jobs_dir.parent, job_id),
        "anomaly": record_status(jobs_dir, f"{job_id}_anomaly"),
        "enhance": record_status(jobs_dir, f"{job_id}_phaseformer"),
        "colmap": record_status(jobs_dir, f"{job_id}_colmap-spatial"),
    }


RESET_STAGES = ("extraction", "review", "anomaly", "phaseformer", "colmap")


def reset(stage: str, path_arg: str, data_root: Path) -> None:
    """Delete a stage's records and outputs (and everything downstream of it),
    so the next trigger pass redoes the bag from that stage."""
    import shutil
    path = resolve_under_data_root(path_arg, data_root)
    source = subdron_utils.infer_source_from_path(str(path))
    mission_id, timestamp, _ = subdron_utils.get_mission_info(str(path))
    job_id = f"{source}_{mission_id}_{timestamp}"
    state_dir = data_root / "_state"
    mission = data_root / source / mission_id / "02_Post_Mission"
    camera = mission / "camera" / timestamp

    targets: list[Path] = []
    if stage == "extraction":
        targets += [state_dir / "jobs" / f"{job_id}.json", camera,
                    mission / "camera" / "videos" / timestamp,
                    mission / "navigation" / timestamp]
        targets += (data_root / source / mission_id / "03_Logs" / "transformed").glob(f"*{timestamp}*")
    if stage in ("extraction", "anomaly"):
        # anomaly depends only on extraction, so it is not part of the
        # review -> phaseformer -> colmap cascade
        targets += [state_dir / "jobs" / f"{job_id}_anomaly.json", camera / "anomaly"]
    if stage in ("extraction", "review"):
        targets += [state_dir / "approvals" / f"{job_id}.json"]
        label_studio_sync.delete_task(label_studio_sync.load_config(state_dir), state_dir, job_id)
    if stage in ("extraction", "review", "phaseformer"):
        targets += [state_dir / "jobs" / f"{job_id}_phaseformer.json", camera / "enhanced"]
    if stage != "anomaly":
        targets += [state_dir / "jobs" / f"{job_id}_colmap-spatial.json",
                    mission / "results" / "colmap" / "runs" / timestamp]

    for t in targets:
        if t.exists():
            shutil.rmtree(t) if t.is_dir() else t.unlink()
            print(f"  removed {t}")
    print(f"[{job_id}] reset from {stage} — the next trigger pass redoes it from there")


def run_stage(runner: str, arg: str, extra: list[str], dry_run: bool) -> bool:
    cmd = [sys.executable, str(RUNNERS_DIR / runner), arg] + extra
    if dry_run:
        print(f"  would run: {' '.join(cmd)}")
        return True
    print(f"  running: {runner} {arg}")
    return subprocess.run(cmd).returncode == 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-root", help="Data root (default: /srv/subdron/data; $DATA_ROOT overrides)")
    parser.add_argument("--dry-run", action="store_true", help="Report decisions without launching anything")
    parser.add_argument("--reset", nargs=2, metavar=("STAGE", "BAG"),
                        help=f"Redo a bag from a stage ({'/'.join(RESET_STAGES)}): deletes that "
                             "stage's records and outputs plus everything downstream, then exits")
    parser.add_argument("--dev-code", nargs="*", choices=["extraction", "phaseformer", "colmap"],
                        default=None, metavar="STAGE",
                        help="Run stages from local checkouts: bare flag = all stages, "
                             "or name specific ones (e.g. --dev-code extraction)")
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
        print("Another trigger pass is running — skipping." if not args.reset
              else "A trigger pass is running — retry the reset when it finishes.")
        return

    if args.reset:
        stage, bag_path = args.reset
        if stage not in RESET_STAGES:
            sys.exit(f"[ERROR] unknown stage '{stage}' — expected one of: {', '.join(RESET_STAGES)}")
        reset(stage, bag_path, data_root)
        return

    dev_stages = (["extraction", "phaseformer", "colmap"] if args.dev_code == []
                  else (args.dev_code or []))

    def stage_extra(stage: str) -> list[str]:
        return ["--dev-code"] if stage in dev_stages else []

    # Label Studio sync is active only when configured, and never in dry runs.
    ls_cfg = None if args.dry_run else label_studio_sync.load_config(state_dir)

    bags = find_bags(data_root)
    print(f"Trigger pass: {len(bags)} bag(s) under {data_root}")

    for bag in bags:
        try:
            source = subdron_utils.infer_source_from_path(str(bag))
            mission_id, timestamp, _ = subdron_utils.get_mission_info(str(bag))
        except ValueError as e:
            print(f"  SKIP {bag}: cannot derive identity ({e})")
            continue
        job_id = f"{source}_{mission_id}_{timestamp}"
        mission_dir = data_root / source / mission_id / "02_Post_Mission"

        # Stage 1: extraction
        status = record_status(jobs_dir, job_id)
        if status is None:
            print(f"[{job_id}] new bag -> extraction")
            run_stage("run_extraction.py", str(bag), stage_extra("extraction"), args.dry_run)
            continue
        if status != "done":
            hint = failure_hint(jobs_dir, job_id) if status in ("failed", "corrupt") else ""
            print(f"[{job_id}] extraction {status}{hint}")
            continue

        # Stage 1b: anomaly detection — needs only extracted images, so it runs
        # right away, independent of the review gate (results help the reviewer).
        # Launched in the background: the API does the work, the rest of the
        # pass (and the GPU stages) never wait for it.
        an_status = record_status(jobs_dir, f"{job_id}_anomaly")
        if an_status is None:
            input_dir = mission_dir / "camera" / timestamp / "image_pose_pairs"
            if input_dir.is_dir():
                cmd = [sys.executable, str(RUNNERS_DIR / "run_anomaly.py"), str(input_dir)]
                if args.dry_run:
                    print(f"  would run in background: {' '.join(cmd)}")
                else:
                    print(f"[{job_id}] extracted -> anomaly detection (background)")
                    subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, start_new_session=True)
        elif an_status not in ("done", "running"):
            print(f"[{job_id}] anomaly {an_status}{failure_hint(jobs_dir, f'{job_id}_anomaly')}")

        # Stage 2: human approval gate, then phaseformer
        approval_path = approvals_dir / f"{job_id}.json"
        if not approval_path.exists() and ls_cfg:
            # create the review task / harvest a finished annotation into an approval
            label_studio_sync.sync_bag(ls_cfg, data_root, job_id, mission_id, timestamp)
        if not approval_path.exists():
            task_id = label_studio_sync.task_id_for(state_dir, job_id)
            how = (f"in Label Studio (task {task_id})" if task_id
                   else "watch videos, then run approve_bag_for_processing.py")
            print(f"[{job_id}] extracted — waiting for review ({how})")
            continue
        approval = json.loads(approval_path.read_text())
        if not approval.get("approved"):
            print(f"[{job_id}] rejected — skipping GPU stages")
            continue

        pf_status = record_status(jobs_dir, f"{job_id}_phaseformer")
        if pf_status is None:
            input_dir = mission_dir / "camera" / timestamp / "image_pose_pairs"
            ranges = approval.get("ranges") or [None]
            print(f"[{job_id}] approved -> phaseformer ({len(ranges)} range(s))")
            ok = True
            for i, r in enumerate(ranges):
                range_args = stage_extra("phaseformer") + (["--image-range", r] if r else [])
                if i > 0:
                    range_args += ["--force"]  # later ranges resume into the same output
                ok = run_stage("run_phaseformer.py", str(input_dir), range_args, args.dry_run) and ok
            if not ok or args.dry_run:
                continue
        elif pf_status != "done":
            hint = failure_hint(jobs_dir, f"{job_id}_phaseformer") if pf_status in ("failed", "corrupt") else ""
            print(f"[{job_id}] phaseformer {pf_status}{hint}")
            continue

        # Stage 3: colmap
        cm_status = record_status(jobs_dir, f"{job_id}_colmap-spatial")
        if cm_status is None:
            enhanced = mission_dir / "camera" / timestamp / "enhanced" / "PHASEFORMER"
            print(f"[{job_id}] enhanced -> colmap")
            run_stage("run_colmap.py", str(enhanced), stage_extra("colmap"), args.dry_run)
        elif cm_status == "done":
            print(f"[{job_id}] complete")
        else:
            hint = failure_hint(jobs_dir, f"{job_id}_colmap-spatial") if cm_status in ("failed", "corrupt") else ""
            print(f"[{job_id}] colmap {cm_status}{hint}")


if __name__ == "__main__":
    main()
