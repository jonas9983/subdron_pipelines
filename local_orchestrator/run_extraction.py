#!/usr/bin/env python3
"""Run the bag-extraction stage for a single bag, locally, with a job record.

Usage:
    .venv/bin/python local_orchestrator/run_extraction.py <bag_path> [options]

The bag path may be absolute or relative to DATA_ROOT. Outputs land in the
mission tree under DATA_ROOT; a JSON job record is written to
DATA_ROOT/_state/jobs/ and the full container log to DATA_ROOT/_state/logs/.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import subdron_utils
except ImportError:
    sys.exit(
        "[ERROR] subdron_utils is not installed in this Python environment.\n"
        "        Run the one-time setup first:  ./scripts/setup_local_orchestrator.sh"
    )

REPO_ROOT = Path(__file__).resolve().parent.parent
IMAGE_NAME = "subdron-rosbag-extractor"
CONTAINER_DATA = "/data"
DISK_HEADROOM_FACTOR = 10

DB_ENV = {
    "ENABLE_DB_LOGGING": "true",
    "PIPELINE_ENV": "dev",
    "DB_POSTGRES_HOST": "host.docker.internal",
    "DB_POSTGRES_PORT": os.getenv("MISSION_DB_PORT", "5433"),
    "DB_POSTGRES_USER": "subdron",
    "DB_POSTGRES_PASSWORD": "subdron_local",
    "DB_POSTGRES_DB": "mission_data",
}


def fail(msg: str) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr)
    sys.exit(1)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def resolve_data_root(cli_value: str | None) -> Path:
    candidate = cli_value or os.getenv("DATA_ROOT") or str(REPO_ROOT / "data")
    root = Path(candidate).resolve()
    if not root.is_dir():
        fail(f"DATA_ROOT is not a directory: {root}")
    if not any(root.iterdir()):
        fail(f"DATA_ROOT is empty: {root} (wrong machine layout?)")
    return root


def resolve_bag(bag_arg: str, data_root: Path) -> Path:
    bag = Path(bag_arg)
    if not bag.is_absolute():
        for base in (Path.cwd(), data_root, data_root.parent):
            if (base / bag).is_file():
                bag = base / bag
                break
    bag = bag.resolve()
    if not bag.is_file():
        fail(f"Bag file not found: {bag_arg}")
    if bag.stat().st_size == 0:
        fail(f"Bag file is empty: {bag}")
    try:
        bag.relative_to(data_root)
    except ValueError:
        fail(f"Bag must live under DATA_ROOT ({data_root}), got: {bag}")
    return bag


def derive_identity(bag: Path) -> tuple[str, str, str]:
    try:
        source = subdron_utils.infer_source_from_path(str(bag))
    except ValueError as e:
        fail(f"Could not infer source from path: {e}")
    try:
        mission_id, timestamp, _ = subdron_utils.get_mission_info(str(bag))
    except ValueError as e:
        fail(f"Could not parse mission/timestamp from path: {e}")
    return source, mission_id, timestamp


def check_disk(data_root: Path, bag_size: int) -> None:
    free = shutil.disk_usage(data_root).free
    needed = bag_size * DISK_HEADROOM_FACTOR
    if free < needed:
        fail(
            f"Insufficient disk space on {data_root}: "
            f"{free / 1e9:.1f} GB free, want {needed / 1e9:.1f} GB "
            f"({DISK_HEADROOM_FACTOR}x bag size)"
        )


def check_mounts(mounts: list[tuple[Path, str]]) -> None:
    for src, dst in mounts:
        if not src.exists():
            fail(f"Bind-mount source missing: {src} (would mount empty over {dst})")


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (OSError, TypeError):
        return False
    return True


def check_existing_record(record_path: Path, force: bool) -> None:
    if not record_path.exists():
        return
    record = json.loads(record_path.read_text())
    status = record.get("status")
    if status == "running" and pid_alive(record.get("runner_pid", -1)):
        fail(f"Job already running (pid {record['runner_pid']}): {record_path}")
    if status == "done" and not force:
        fail(f"Job already completed: {record_path} (use --force to re-run)")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def image_digest(image: str) -> str:
    for fmt in ("{{index .RepoDigests 0}}", "{{.Id}}"):
        result = subprocess.run(
            ["docker", "inspect", "--format", fmt, image],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    fail(f"Docker image not found locally: {image}")


def runner_version() -> str:
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "describe", "--always", "--dirty", "--tags"],
        capture_output=True, text=True,
    )
    return result.stdout.strip() or "unknown"


def utils_version() -> str:
    try:
        from importlib.metadata import version
        return version("subdron_utils")
    except Exception:
        return "unknown"


def build_docker_command(args, bag_rel: str, mission_id: str, timestamp: str,
                         job_id: str, data_root: Path) -> tuple[list[str], list[tuple[Path, str]]]:
    image = f"{IMAGE_NAME}:{args.tag}"
    mounts = [(data_root, CONTAINER_DATA)]
    if args.dev_code:
        git_root = data_root.parent if (data_root.parent / "subdron_camera_transformation").is_dir() else REPO_ROOT.parent
        mounts.append((git_root / "subdron_camera_transformation", "/catkin_ws/src/subdron_camera_transformation"))
        mounts.append((git_root / "subdron-utils" / "subdron_utils",
                       "/usr/local/lib/python3.8/dist-packages/subdron_utils"))

    cmd = [
        "docker", "run", "--rm",
        "--name", f"extract-{job_id}",
        "--user", f"{os.getuid()}:{os.getgid()}",
        "--entrypoint", "/bin/bash",
        "--add-host", "host.docker.internal:host-gateway",
    ]
    for src, dst in mounts:
        cmd += ["-v", f"{src}:{dst}"]
    env = dict(DB_ENV)
    if args.no_db:
        env = {}
    for key, value in env.items():
        cmd += ["-e", f"{key}={value}"]

    stage_cmd = (
        "source /opt/ros/noetic/setup.bash && "
        "source /catkin_ws/devel/setup.bash && "
        f"python3 main.py -i {CONTAINER_DATA}/{bag_rel} "
        f"-m {mission_id} -ts {timestamp} "
        f"--local-path {CONTAINER_DATA}/_state/tmp/{job_id} "
        f"--extract-images --post-process --create-video --video-fps {args.video_fps}"
    )
    cmd += [image, "-c", stage_cmd]
    return cmd, mounts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("bag", help="Bag path (absolute, or relative to DATA_ROOT)")
    parser.add_argument("--data-root", help="Data root (default: $DATA_ROOT or <repo>/data)")
    parser.add_argument("--tag", default="dev", help="Image tag (default: dev)")
    parser.add_argument("--video-fps", type=int, default=30)
    parser.add_argument("--force", action="store_true", help="Re-run a completed job")
    parser.add_argument("--no-checksum", action="store_true", help="Skip bag sha256")
    parser.add_argument("--no-db", action="store_true", help="Do not pass DB env into the container")
    parser.add_argument("--dev-code", action="store_true",
                        help="Bind-mount local stage + utils checkouts over the image code")
    args = parser.parse_args()

    data_root = resolve_data_root(args.data_root)
    bag = resolve_bag(args.bag, data_root)
    bag_rel = bag.relative_to(data_root).as_posix()
    source, mission_id, timestamp = derive_identity(bag)
    job_id = f"{source}__{mission_id}__{timestamp}"

    state_dir = data_root / "_state"
    record_path = state_dir / "jobs" / f"{job_id}.json"
    log_path = state_dir / "logs" / f"{job_id}.log"

    check_existing_record(record_path, args.force)
    check_disk(data_root, bag.stat().st_size)

    docker_cmd, mounts = build_docker_command(args, bag_rel, mission_id, timestamp, job_id, data_root)
    check_mounts(mounts)
    digest = image_digest(f"{IMAGE_NAME}:{args.tag}")

    record = {
        "job_id": job_id,
        "stage": "extraction",
        "status": "running",
        "bag_path": str(bag),
        "bag_size_bytes": bag.stat().st_size,
        "bag_sha256": None if args.no_checksum else sha256_file(bag),
        "source": source,
        "mission_id": mission_id,
        "timestamp": timestamp,
        "image": f"{IMAGE_NAME}:{args.tag}",
        "image_digest": digest,
        "dev_code": args.dev_code,
        "db_logging": not args.no_db,
        "runner_version": runner_version(),
        "subdron_utils_version": utils_version(),
        "host": socket.gethostname(),
        "user": getpass.getuser(),
        "runner_pid": os.getpid(),
        "started_utc": utc_now(),
        "ended_utc": None,
        "duration_seconds": None,
        "exit_code": None,
        "outputs": {
            "mission_dir": str(data_root / source / mission_id),
            "image_pose_pairs": str(data_root / source / mission_id / "02_Post_Mission" / "camera" / timestamp / "image_pose_pairs"),
            "navigation_dir": str(data_root / source / mission_id / "02_Post_Mission" / "navigation" / timestamp),
            "videos_dir": str(data_root / source / mission_id / "02_Post_Mission" / "camera" / "videos" / timestamp / "original"),
            "log": str(log_path),
        },
        "error_tail": None,
    }

    for d in (record_path.parent, log_path.parent, state_dir / "tmp"):
        d.mkdir(parents=True, exist_ok=True)
    record_path.write_text(json.dumps(record, indent=2))

    print(f"[{job_id}] starting extraction ({record['bag_size_bytes'] / 1e6:.0f} MB bag)")
    print(f"[{job_id}] image {digest}")
    start = datetime.now(timezone.utc)

    tail: list[str] = []
    with log_path.open("w") as log_file:
        proc = subprocess.Popen(docker_cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
        for line in proc.stdout:
            log_file.write(line)
            sys.stdout.write(line)
            tail.append(line)
            if len(tail) > 50:
                tail.pop(0)
        proc.wait()

    end = datetime.now(timezone.utc)
    record["status"] = "done" if proc.returncode == 0 else "failed"
    record["ended_utc"] = utc_now()
    record["duration_seconds"] = round((end - start).total_seconds(), 1)
    record["exit_code"] = proc.returncode
    if proc.returncode != 0:
        record["error_tail"] = "".join(tail)
    record_path.write_text(json.dumps(record, indent=2))

    shutil.rmtree(state_dir / "tmp" / job_id, ignore_errors=True)

    print(f"[{job_id}] {record['status']} in {record['duration_seconds']}s "
          f"(exit {proc.returncode}) — record: {record_path}")
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main()
