"""Shared helpers for the local stage runners (run_extraction, run_phaseformer, ...)."""

from __future__ import annotations

import warnings

# Google libs nag about EOL Python on every import; not actionable here.
warnings.filterwarnings("ignore", category=FutureWarning, module="google.*")
warnings.filterwarnings("ignore", message=".*Python 3.8.*")

import fcntl
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTAINER_DATA = "/data"

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


DEFAULT_DATA_ROOT = "/srv/subdron/data"


def resolve_data_root(cli_value: str | None) -> Path:
    candidate = cli_value or os.getenv("DATA_ROOT") or DEFAULT_DATA_ROOT
    root = Path(candidate).resolve()
    if not root.is_dir():
        fail(f"DATA_ROOT is not a directory: {root} — run ./scripts/setup_local_orchestrator.sh")
    if not any(root.iterdir()):
        fail(f"DATA_ROOT is empty: {root} (wrong machine layout?)")
    return root


def resolve_under_data_root(arg: str, data_root: Path) -> Path:
    """Resolve a path argument (absolute or relative) and require it under DATA_ROOT."""
    p = Path(arg)
    if not p.is_absolute():
        for base in (Path.cwd(), data_root, data_root.parent):
            if (base / p).exists():
                p = base / p
                break
    p = p.resolve()
    if not p.exists():
        fail(f"Path not found: {arg}")
    try:
        p.relative_to(data_root)
    except ValueError:
        fail(f"Path must live under DATA_ROOT ({data_root}), got: {p}")
    return p


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


def image_digest(image: str) -> str:
    for fmt in ("{{index .RepoDigests 0}}", "{{.Id}}"):
        result = subprocess.run(
            ["docker", "inspect", "--format", fmt, image],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    fail(f"Docker image not found locally: {image}")


def acquire_gpu_lock(state_dir: Path):
    """One GPU: a second GPU job fails fast instead of OOMing both. Held until exit."""
    lock_path = state_dir / "gpu.lock"
    lock_file = lock_path.open("w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fail(f"Another GPU job is running (lock: {lock_path})")
    return lock_file


def docker_run_base(name: str, mounts: list[tuple[Path, str]], env: dict | None = None,
                    gpu: bool = False, workdir: str | None = None,
                    entrypoint: str | None = None) -> list[str]:
    cmd = ["docker", "run", "--rm", "--name", name,
           "--user", f"{os.getuid()}:{os.getgid()}",
           "--add-host", "host.docker.internal:host-gateway",  # reach the host's mission DB
           "-e", "HOME=/tmp"]  # --user has no home; stops matplotlib/CUDA cache noise
    if entrypoint:
        cmd += ["--entrypoint", entrypoint]
    if workdir:
        cmd += ["-w", workdir]
    if gpu:
        cmd += ["--runtime=nvidia"]
    for src, dst in mounts:
        cmd += ["-v", f"{src}:{dst}"]
    for key, value in (env or {}).items():
        cmd += ["-e", f"{key}={value}"]
    return cmd


def run_job(record: dict, cmd: list[str], record_path: Path, log_path: Path) -> int:
    """Write the running record, stream the container to log+stdout, finalize, return exit code."""
    for d in (record_path.parent, log_path.parent):
        d.mkdir(parents=True, exist_ok=True)
    record.update(runner_pid=os.getpid(), status="running",
                  started_utc=utc_now(), ended_utc=None,
                  duration_seconds=None, exit_code=None, error_tail=None)
    record_path.write_text(json.dumps(record, indent=2))

    start = datetime.now(timezone.utc)
    tail: list[str] = []
    # Progress bars (tqdm) flood non-interactive logs (journald): when stdout is
    # not a tty, echo them only every 10%. The log file always gets everything.
    progress_re = re.compile(r"(\d+)%\|")
    echo_bars = sys.stdout.isatty()
    last_pct = -1
    with log_path.open("w") as log_file:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
        for line in proc.stdout:
            log_file.write(line)
            m = progress_re.search(line)
            if m and not echo_bars:
                pct = int(m.group(1))
                if pct // 10 > last_pct // 10 or pct == 100 and last_pct != 100:
                    sys.stdout.write(line)
                    sys.stdout.flush()
                    last_pct = pct
            else:
                sys.stdout.write(line)
                sys.stdout.flush()
            tail.append(line)
            if len(tail) > 50:
                tail.pop(0)
        proc.wait()

    record["status"] = "done" if proc.returncode == 0 else "failed"
    record["ended_utc"] = utc_now()
    record["duration_seconds"] = round((datetime.now(timezone.utc) - start).total_seconds(), 1)
    record["exit_code"] = proc.returncode
    if proc.returncode != 0:
        record["error_tail"] = "".join(tail)
    record_path.write_text(json.dumps(record, indent=2))

    print(f"[{record['job_id']}] {record['status']} in {record['duration_seconds']}s "
          f"(exit {proc.returncode}) — record: {record_path}")
    return proc.returncode
