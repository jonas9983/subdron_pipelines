#!/usr/bin/env python3
"""Run PHASEFORMER enhancement for one extracted bag, locally, with a job record.

Usage:
    python local_orchestrator/run_phaseformer.py <image_pose_pairs_dir> [options]

Input is the image_pose_pairs directory produced by run_extraction.py (absolute,
or relative to DATA_ROOT). Enhanced images land in the mission tree under
camera/<ts>/enhanced/PHASEFORMER, videos under camera/videos/<ts>/PHASEFORMER.
Holds a GPU lock so only one GPU job runs at a time.

The stage skips a whole batch when it runs out of GPU memory and still exits 0, so
the batch size defaults from the GPU's memory and, after the run, every image in the
range is checked: missing ones are retried with a halved batch (the stage resumes,
processing only what is missing) and the job is marked failed if any remain.
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
    gpu_memory_mib,
    image_digest,
    mark_failed,
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
IMAGE_GLOBS = ("*.jpg", "*.png")


def default_batch_size(device: str) -> int:
    """8 on the big cloud GPUs; smaller on desktop cards, where batch 8 at 720p OOMs."""
    mib = gpu_memory_mib() if device == "gpu" else None
    if mib is None:
        return 8
    return 2 if mib < 10_000 else 4 if mib < 16_000 else 8


def list_images(directory: Path) -> list[str]:
    return sorted(p.name for g in IMAGE_GLOBS for p in directory.glob(g))


def expected_outputs(input_dir: Path, image_range: str | None) -> list[str]:
    """Image names the stage should produce: same slicing as the stage (start:end[:step])."""
    names = list_images(input_dir)
    if not image_range:
        return names
    parts = [int(x) if x else None for x in image_range.split(":")]
    parts += [None] * (3 - len(parts))
    return names[slice(*parts)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input_dir", help="image_pose_pairs dir (absolute, or relative to DATA_ROOT)")
    parser.add_argument("--data-root", help="Data root (default: /srv/subdron/data; $DATA_ROOT overrides)")
    parser.add_argument("--tag", default="dev", help="Image tag (default: dev)")
    parser.add_argument("--batch-size", type=int, default=None,
                        help="Images per forward pass (default: from GPU memory — 2 below 10 GB, "
                             "4 below 16 GB, else 8)")
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
             f"        mkdir -p {models_dir} && gcloud storage cp {WEIGHTS_GS} {models_dir}/")

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

    batch_size = args.batch_size or default_batch_size(args.device)

    def build_cmd(batch: int) -> list[str]:
        cmd = docker_run_base(
            name=f"phaseformer-{mission_id}-{timestamp}",
            mounts=mounts,
            # expandable segments avoid the allocator fragmentation that turns a
            # near-full card into "CUDA out of memory" mid-run
            env={"PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True"},
            gpu=args.device == "gpu",
            workdir=workdir,
            entrypoint="python",
        )
        return cmd + [
            image,
            "main.py", "PHASEFORMER",
            "--input_dir", f"{CONTAINER_DATA}/{input_rel}",
            "--batch_size", str(batch),
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
        "batch_size": batch_size,
        "image_range": args.image_range,
        "outputs": {
            "enhanced_dir": str(enhanced_parent / "PHASEFORMER"),
            "videos_dir": str(videos_dir),
            "log": str(log_path),
        },
    }

    expected = expected_outputs(input_dir, args.image_range)
    enhanced_dir = enhanced_parent / "PHASEFORMER"
    print(f"[{job_id}] starting PHASEFORMER on {len(expected)} images "
          f"({args.device}, batch size {batch_size})")
    print(f"[{job_id}] image {digest}")

    attempt = 0
    while True:
        code = run_job(record, build_cmd(batch_size), record_path, log_path, append_log=attempt > 0)
        if code != 0:
            sys.exit(code)
        done = set(list_images(enhanced_dir))
        missing = [n for n in expected if n not in done]
        if not missing:
            print(f"[{job_id}] all {len(expected)} images enhanced -> {enhanced_dir}")
            sys.exit(0)
        if batch_size <= 1:
            mark_failed(record, record_path,
                        f"{len(missing)} of {len(expected)} images were not enhanced even at batch size 1 "
                        f"(the stage skips a batch when it fails, typically CUDA out of memory — see the log). "
                        f"First missing: {missing[0]}")
            sys.exit(1)
        attempt += 1
        batch_size //= 2
        record["batch_size"] = batch_size
        print(f"[{job_id}] {len(missing)} of {len(expected)} images missing after the run "
              f"(batches skipped, usually CUDA out of memory) — resuming with batch size {batch_size}")


if __name__ == "__main__":
    main()
