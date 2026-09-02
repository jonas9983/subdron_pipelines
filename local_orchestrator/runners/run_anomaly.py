#!/usr/bin/env python3
"""Run anomaly detection for one extracted bag through the local anomaly API.

Usage:
    python local_orchestrator/runners/run_anomaly.py <image_pose_pairs_dir> [options]

Input is the image_pose_pairs directory produced by run_extraction.py. Frames are
subsampled to --fps (default 1 per second of footage) and scored by the anomaly
API container (see docker-compose.yml: service anomaly-api). Results land in the
mission tree under camera/<ts>/anomaly/<model>/ (scores.csv + heatmaps of the
anomalous frames). The API holds the model in memory, so no GPU lock is taken.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline_helpers import (
    ANOMALY_API_URL,
    CONTAINER_DATA,
    anomaly_api_health,
    check_existing_record,
    fail,
    resolve_data_root,
    resolve_under_data_root,
    run_job,
)

try:
    import subdron_utils
except ImportError:
    sys.exit(
        "[ERROR] subdron_utils is not installed in this Python environment.\n"
        "        Run the one-time setup first:  ./scripts/setup_local_orchestrator.sh"
    )

def check_api(api_url: str) -> dict:
    health = anomaly_api_health(api_url, timeout=5)
    if health is None:
        fail(f"Anomaly API not reachable at {api_url}.\n"
             f"        Start it with:  docker compose -f local_orchestrator/docker-compose.yml up -d anomaly-api")
    return health


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input_dir", help="image_pose_pairs dir (absolute, or relative to DATA_ROOT)")
    parser.add_argument("--data-root", help="Data root (default: /srv/subdron/data; $DATA_ROOT overrides)")
    parser.add_argument("--model", default="padim",
                        choices=["padim", "patchcore", "stfpm", "efficientad"])
    parser.add_argument("--fps", type=float, default=1.0,
                        help="Frames per second of footage to analyse (0 = every frame)")
    parser.add_argument("--save-heatmaps", default="anomalous", choices=["anomalous", "all", "none"])
    parser.add_argument("--api-url", default=ANOMALY_API_URL,
                        help=f"Anomaly API base URL (default: {ANOMALY_API_URL}; $ANOMALY_API_URL overrides)")
    parser.add_argument("--force", action="store_true", help="Re-run a completed job")
    args = parser.parse_args()

    data_root = resolve_data_root(args.data_root)
    input_dir = resolve_under_data_root(args.input_dir, data_root)
    if not input_dir.is_dir() or not (any(input_dir.glob("*.jpg")) or any(input_dir.glob("*.png"))):
        fail(f"No images in input directory: {input_dir}")
    input_rel = input_dir.relative_to(data_root).as_posix()

    try:
        source = subdron_utils.infer_source_from_path(str(input_dir))
        mission_id, timestamp, _ = subdron_utils.get_mission_info(str(input_dir))
    except ValueError as e:
        fail(f"Could not derive mission identity from path: {e}")
    job_id = f"{source}_{mission_id}_{timestamp}_anomaly"

    state_dir = data_root / "_state"
    record_path = state_dir / "jobs" / f"{job_id}.json"
    log_path = state_dir / "logs" / f"{job_id}.log"
    check_existing_record(record_path, args.force)

    health = check_api(args.api_url)

    # The API container mounts DATA_ROOT at /data; it writes results next to the
    # images, so the host output dir is camera/<ts>/anomaly/<model>
    output_dir = input_dir.parent / "anomaly" / args.model
    payload = {
        "image_dir": f"{CONTAINER_DATA}/{input_rel}",
        "model": args.model,
        "fps": args.fps,
        "save_heatmaps": args.save_heatmaps,
    }
    cmd = ["curl", "-sS", "--fail", "-X", "POST", f"{args.api_url}/predict/dir",
           "-H", "Content-Type: application/json", "-d", json.dumps(payload)]

    record = {
        "job_id": job_id,
        "stage": "anomaly",
        "input_dir": str(input_dir),
        "source": source,
        "mission_id": mission_id,
        "timestamp": timestamp,
        "api_url": args.api_url,
        "model": args.model,
        "fps": args.fps,
        "outputs": {
            "anomaly_dir": str(output_dir),
            "scores_csv": str(output_dir / "scores.csv"),
            "log": str(log_path),
        },
    }

    n_images = len(list(input_dir.glob("*.jpg")) + list(input_dir.glob("*.png")))
    print(f"[{job_id}] scoring {n_images} images at {args.fps} fps with {args.model} "
          f"(api {args.api_url}, loaded: {health.get('loaded_models')})")
    code = run_job(record, cmd, record_path, log_path)
    if code == 0:
        try:
            summary = json.loads(log_path.read_text().strip().splitlines()[-1])
            print(f"[{job_id}] {summary['processed']} frames scored, "
                  f"{summary['anomaly_count']} anomalous ({summary['anomaly_rate']:.0%}) "
                  f"-> {output_dir}")
        except (json.JSONDecodeError, KeyError, IndexError):
            pass
    sys.exit(code)


if __name__ == "__main__":
    main()
