#!/usr/bin/env python3
"""Run the bag-extraction stage for a single bag, locally, with a job record.

Usage:
    python local_orchestrator/run_extraction.py <bag_path> [options]

The bag path may be absolute or relative to DATA_ROOT. Outputs land in the
mission tree under DATA_ROOT; a JSON job record is written to
DATA_ROOT/_state/jobs/ and the full container log to DATA_ROOT/_state/logs/.
"""

from __future__ import annotations

import argparse
import shutil

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline_helpers import (
    CONTAINER_DATA,
    DB_ENV,
    REPO_ROOT,
    check_existing_record,
    check_mounts,
    docker_run_base,
    fail,
    image_digest,
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

IMAGE_NAME = "subdron-rosbag-extractor"
DISK_HEADROOM_FACTOR = 10


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("bag", help="Bag path (absolute, or relative to DATA_ROOT)")
    parser.add_argument("--data-root", help="Data root (default: $DATA_ROOT or <repo>/data)")
    parser.add_argument("--tag", default="dev", help="Image tag (default: dev)")
    parser.add_argument("--video-fps", type=int, default=30)
    parser.add_argument("--force", action="store_true", help="Re-run a completed job")
    parser.add_argument("--no-db", action="store_true", help="Do not pass DB env into the container")
    parser.add_argument("--dev-code", action="store_true",
                        help="Bind-mount local stage + utils checkouts over the image code")
    args = parser.parse_args()

    data_root = resolve_data_root(args.data_root)
    bag = resolve_under_data_root(args.bag, data_root)
    if not bag.is_file() or bag.stat().st_size == 0:
        fail(f"Not a usable bag file: {bag}")
    bag_rel = bag.relative_to(data_root).as_posix()

    try:
        source = subdron_utils.infer_source_from_path(str(bag))
        mission_id, timestamp, _ = subdron_utils.get_mission_info(str(bag))
    except ValueError as e:
        fail(f"Could not derive mission identity from path: {e}")
    job_id = f"{source}__{mission_id}__{timestamp}"

    state_dir = data_root / "_state"
    record_path = state_dir / "jobs" / f"{job_id}.json"
    log_path = state_dir / "logs" / f"{job_id}.log"
    check_existing_record(record_path, args.force)

    free = shutil.disk_usage(data_root).free
    needed = bag.stat().st_size * DISK_HEADROOM_FACTOR
    if free < needed:
        fail(f"Insufficient disk space on {data_root}: {free / 1e9:.1f} GB free, "
             f"want {needed / 1e9:.1f} GB ({DISK_HEADROOM_FACTOR}x bag size)")

    image = f"{IMAGE_NAME}:{args.tag}"
    mounts = [(data_root, CONTAINER_DATA)]
    if args.dev_code:
        git_root = REPO_ROOT.parent
        mounts += [
            (git_root / "subdron_camera_transformation", "/catkin_ws/src/subdron_camera_transformation"),
            (git_root / "subdron-utils" / "subdron_utils",
             "/usr/local/lib/python3.8/dist-packages/subdron_utils"),
        ]
    check_mounts(mounts)
    digest = image_digest(image)

    cmd = docker_run_base(
        name=f"extract-{job_id}",
        mounts=mounts,
        env={} if args.no_db else DB_ENV,
        entrypoint="/bin/bash",
    )
    stage_cmd = (
        "source /opt/ros/noetic/setup.bash && "
        "source /catkin_ws/devel/setup.bash && "
        f"python3 main.py -i {CONTAINER_DATA}/{bag_rel} "
        f"-m {mission_id} -ts {timestamp} "
        f"--local-path {CONTAINER_DATA}/_state/tmp/{job_id} "
        f"--extract-images --post-process --create-video --video-fps {args.video_fps}"
    )
    cmd += [image, "-c", stage_cmd]

    record = {
        "job_id": job_id,
        "stage": "extraction",
        "bag_path": str(bag),
        "bag_size_bytes": bag.stat().st_size,
        "source": source,
        "mission_id": mission_id,
        "timestamp": timestamp,
        "image": image,
        "image_digest": digest,
        "dev_code": args.dev_code,
        "outputs": {
            "mission_dir": str(data_root / source / mission_id),
            "image_pose_pairs": str(data_root / source / mission_id / "02_Post_Mission" / "camera" / timestamp / "image_pose_pairs"),
            "navigation_dir": str(data_root / source / mission_id / "02_Post_Mission" / "navigation" / timestamp),
            "videos_dir": str(data_root / source / mission_id / "02_Post_Mission" / "camera" / "videos" / timestamp / "original"),
            "log": str(log_path),
        },
    }

    (state_dir / "tmp").mkdir(parents=True, exist_ok=True)
    print(f"[{job_id}] starting extraction ({record['bag_size_bytes'] / 1e6:.0f} MB bag)")
    print(f"[{job_id}] image {digest}")
    rc = run_job(record, cmd, record_path, log_path)
    shutil.rmtree(state_dir / "tmp" / job_id, ignore_errors=True)
    sys.exit(rc)


if __name__ == "__main__":
    main()
