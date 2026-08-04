#!/usr/bin/env python3
"""Run COLMAP reconstruction for one enhanced bag, locally, with a job record.

Usage:
    python local_orchestrator/run_colmap.py <enhanced_dir> [options]

Input is the enhanced/PHASEFORMER directory produced by run_phaseformer.py
(absolute, or relative to DATA_ROOT). The poses CSV is derived from the mission
tree unless --poses is given. Results land under
results/colmap/runs/<ts>/<dir-version> in the mission tree; colmap's scratch
space lives in _state/tmp/<job_id> and is removed afterwards.
Holds the shared GPU lock (one GPU job at a time).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline_helpers import (
    CONTAINER_DATA,
    DB_ENV,
    REPO_ROOT,
    acquire_gpu_lock,
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

IMAGE_NAME = "subdron-colmap"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("enhanced_dir", help="enhanced/PHASEFORMER dir (absolute, or relative to DATA_ROOT)")
    parser.add_argument("--poses", help="Poses CSV (default: navigation_not_converted.csv from the mission tree)")
    parser.add_argument("--data-root", help="Data root (default: $DATA_ROOT or <repo>/data)")
    parser.add_argument("--tag", default="dev", help="Image tag (default: dev)")
    parser.add_argument("--matching-strategy", choices=["sequential", "exhaustive", "spatial"], default="spatial")
    parser.add_argument("--spatial-radius", type=float, default=5.0)
    parser.add_argument("--feature-extractor", choices=["sift", "aliked"], default="sift")
    parser.add_argument("--plane-mode", choices=["single", "windowed"], default="single")
    parser.add_argument("--force", action="store_true", help="Re-run a completed job")
    parser.add_argument("--no-db", action="store_true", help="Do not pass DB env into the container")
    parser.add_argument("--dev-code", action="store_true",
                        help="Bind-mount local stage + utils checkouts over the image code")
    args = parser.parse_args()

    data_root = resolve_data_root(args.data_root)
    enhanced_dir = resolve_under_data_root(args.enhanced_dir, data_root)
    if not enhanced_dir.is_dir() or not any(enhanced_dir.glob("*.jpg")):
        fail(f"No images in enhanced directory: {enhanced_dir}")
    enhanced_rel = enhanced_dir.relative_to(data_root).as_posix()

    try:
        source = subdron_utils.infer_source_from_path(str(enhanced_dir))
        mission_id, timestamp, ip_method = subdron_utils.get_mission_info(str(enhanced_dir))
    except ValueError as e:
        fail(f"Could not derive mission identity from path: {e}")

    mission_dir = data_root / source / mission_id / "02_Post_Mission"
    if args.poses:
        poses = resolve_under_data_root(args.poses, data_root)
    else:
        poses = mission_dir / "navigation" / timestamp / "navigation_not_converted.csv"
        if not poses.is_file():
            fail(f"Poses CSV not found (run extraction first?): {poses}")
    poses_rel = poses.relative_to(data_root).as_posix()

    dir_version = f"{ip_method or 'RAW'}_{args.matching_strategy}"
    job_id = f"{source}__{mission_id}__{timestamp}__colmap-{args.matching_strategy}"

    state_dir = data_root / "_state"
    record_path = state_dir / "jobs" / f"{job_id}.json"
    log_path = state_dir / "logs" / f"{job_id}.log"
    check_existing_record(record_path, args.force)

    if subprocess.run(["nvidia-smi"], capture_output=True).returncode != 0:
        fail("nvidia-smi failed — COLMAP needs a usable GPU")

    # Scratch (projects/ tree) is per-job and discarded; results are a nested
    # mount so they land directly in the mission tree.
    scratch = state_dir / "tmp" / job_id
    results_host = mission_dir / "results" / "colmap" / "runs" / timestamp
    (scratch / source / "results").mkdir(parents=True, exist_ok=True)
    results_host.mkdir(parents=True, exist_ok=True)

    image = f"{IMAGE_NAME}:{args.tag}"
    if args.dev_code:
        workdir = "/app"
        mounts = [
            (REPO_ROOT.parent / "subdron_colmap", "/app"),
            (REPO_ROOT.parent / "subdron-utils" / "subdron_utils",
             "/opt/venv/lib/python3.10/site-packages/subdron_utils"),
        ]
    else:
        workdir = "/colmap"
        mounts = []
    mounts += [
        (data_root, CONTAINER_DATA),
        (scratch, f"{workdir}/projects"),
        (results_host, f"{workdir}/projects/{source}/results"),
    ]
    check_mounts(mounts)
    digest = image_digest(image)

    cmd = docker_run_base(
        name=f"colmap-{mission_id}-{timestamp}-{args.matching_strategy}",
        mounts=mounts,
        env={"ENV": "dev"} if args.no_db else {**DB_ENV, "ENV": "dev"},
        gpu=True,
        workdir=workdir,
        entrypoint="python",
    )
    cmd += [
        image,
        "main.py",
        "--images-prefix", f"{CONTAINER_DATA}/{enhanced_rel}",
        "--poses-prefix", f"{CONTAINER_DATA}/{poses_rel}",
        "--dir-version", dir_version,
        "--matching-strategy", args.matching_strategy,
        "--spatial-radius", str(args.spatial_radius),
        "--plane-mode", args.plane_mode,
        "--feature-extractor", args.feature_extractor,
    ]

    gpu_lock = acquire_gpu_lock(state_dir)  # noqa: F841 — held until exit

    record = {
        "job_id": job_id,
        "stage": "colmap",
        "enhanced_dir": str(enhanced_dir),
        "poses_csv": str(poses),
        "source": source,
        "mission_id": mission_id,
        "timestamp": timestamp,
        "matching_strategy": args.matching_strategy,
        "dir_version": dir_version,
        "image": image,
        "image_digest": digest,
        "dev_code": args.dev_code,
        "outputs": {
            "results_dir": str(results_host / dir_version),
            "log": str(log_path),
        },
    }

    n_images = len(list(enhanced_dir.glob("*.jpg")))
    print(f"[{job_id}] starting COLMAP ({args.matching_strategy}) on {n_images} images")
    print(f"[{job_id}] image {digest}")
    rc = run_job(record, cmd, record_path, log_path)
    shutil.rmtree(scratch, ignore_errors=True)
    sys.exit(rc)


if __name__ == "__main__":
    main()
