#!/usr/bin/env python3
"""Run PHASEFORMER enhancement for one extracted bag, locally, with a job record.

Usage:
    python local_orchestrator/run_phaseformer.py <image_pose_pairs_dir> [options]

Input is the image_pose_pairs directory produced by run_extraction.py (absolute,
or relative to DATA_ROOT). Enhanced images land in the mission tree under
camera/<ts>/enhanced/PHASEFORMER, videos under camera/videos/<ts>/PHASEFORMER.
Holds a GPU lock so only one GPU job runs at a time.
"""

from __future__ import annotations

import argparse
import subprocess

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline_helpers import (
    CONTAINER_DATA,
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

IMAGE_NAME = "subdron-image-processing"
WEIGHTS_GS = "gs://subdron_dev/machine_learning/image_processing/weights/best.pth"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input_dir", help="image_pose_pairs dir (absolute, or relative to DATA_ROOT)")
    parser.add_argument("--data-root", help="Data root (default: /srv/subdron/data; $DATA_ROOT overrides)")
    parser.add_argument("--tag", default="dev", help="Image tag (default: dev)")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--video-fps", type=int, default=30)
    parser.add_argument("--device", choices=["gpu", "cpu"], default="gpu")
    parser.add_argument("--image-range", help="Only process images in range start:end[:step]")
    parser.add_argument("--force", action="store_true", help="Re-run a completed job")
    parser.add_argument("--dev-code", action="store_true",
                        help="Bind-mount local stage + utils checkouts over the image code")
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
    job_id = f"{source}_{mission_id}_{timestamp}_phaseformer"

    state_dir = data_root / "_state"
    record_path = state_dir / "jobs" / f"{job_id}.json"
    log_path = state_dir / "logs" / f"{job_id}.log"
    check_existing_record(record_path, args.force)

    if args.device == "gpu":
        if subprocess.run(["nvidia-smi"], capture_output=True).returncode != 0:
            fail("nvidia-smi failed — no usable GPU (pass --device cpu to run on CPU)")

    # Host output dirs, bind-mounted over the container's fixed write locations
    camera_dir = input_dir.parent                       # .../camera/<ts>
    enhanced_parent = camera_dir / "enhanced"           # over <cwd>/images -> images/PHASEFORMER
    videos_dir = camera_dir.parent / "videos" / timestamp / "PHASEFORMER"
    enhanced_parent.mkdir(parents=True, exist_ok=True)
    videos_dir.mkdir(parents=True, exist_ok=True)

    image = f"{IMAGE_NAME}:{args.tag}"
    # Model weights live on the host and are mounted in, so the container
    # never needs cloud credentials (its get_model() skips the GCS download).
    models_dir = state_dir / "models" / "PHASEFORMER"
    if not (models_dir / "best.pth").is_file():
        fail(f"PHASEFORMER weights not found: {models_dir}/best.pth\n"
             f"        One-time download (needs gcloud auth on any machine):\n"
             f"        mkdir -p {models_dir} && gsutil cp {WEIGHTS_GS} {models_dir}/")

    if args.dev_code:
        stage_repo = REPO_ROOT.parent / "subdron_image_processing"
        workdir = "/app"
        mounts = [
            (stage_repo, "/app"),
            (REPO_ROOT.parent / "subdron-utils" / "subdron_utils",
             "/opt/venv/lib/python3.9/site-packages/subdron_utils"),
        ]
    else:
        workdir = "/image-processing"
        mounts = []
    mounts += [
        (data_root, CONTAINER_DATA),
        (enhanced_parent, f"{workdir}/images"),
        (videos_dir, f"{workdir}/temp_videos"),
        (models_dir, f"{workdir}/phaseformer/checkpoints/UIEB"),
    ]
    check_mounts(mounts)
    digest = image_digest(image)

    cmd = docker_run_base(
        name=f"phaseformer-{mission_id}-{timestamp}",
        mounts=mounts,
        gpu=args.device == "gpu",
        workdir=workdir,
        entrypoint="python",
    )
    cmd += [
        image,
        "main.py", "PHASEFORMER",
        "--input_dir", f"{CONTAINER_DATA}/{input_rel}",
        "--batch_size", str(args.batch_size),
        "--device", args.device,
        "--resume", "--keep_input",
        *(["--image-range", args.image_range] if args.image_range else []),
        "--create-video", "--video-fps", str(args.video_fps),
    ]

    if args.device == "gpu":
        state_dir.mkdir(parents=True, exist_ok=True)
        gpu_lock = acquire_gpu_lock(state_dir)  # noqa: F841 — held until exit

    record = {
        "job_id": job_id,
        "stage": "phaseformer",
        "input_dir": str(input_dir),
        "source": source,
        "mission_id": mission_id,
        "timestamp": timestamp,
        "image": image,
        "image_digest": digest,
        "dev_code": args.dev_code,
        "outputs": {
            "enhanced_dir": str(enhanced_parent / "PHASEFORMER"),
            "videos_dir": str(videos_dir),
            "log": str(log_path),
        },
    }

    n_images = len(list(input_dir.glob("*.jpg")) + list(input_dir.glob("*.png")))
    print(f"[{job_id}] starting PHASEFORMER on {n_images} images ({args.device})")
    print(f"[{job_id}] image {digest}")
    sys.exit(run_job(record, cmd, record_path, log_path))


if __name__ == "__main__":
    main()
