#!/usr/bin/env python3
"""Run anomaly detection for one extracted bag through the local anomaly API (CPU).

Usage:
    python local_orchestrator/runners/run_anomaly.py <image_pose_pairs_dir> [options]

Frames are subsampled to --fps and posted to /predict; scores.csv and
original-vs-heatmap images land in 02_Post_Mission/results/anomaly/<ts>/<model>/.
"""

from __future__ import annotations

import argparse
import base64
import csv
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline_helpers import (
    ANOMALY_API_URL,
    anomaly_api_health,
    check_existing_record,
    fail,
    resolve_data_root,
    resolve_under_data_root,
    utc_now,
)

try:
    import subdron_utils
except ImportError:
    sys.exit(
        "[ERROR] subdron_utils is not installed in this Python environment.\n"
        "        Run the one-time setup first:  ./scripts/setup_local_orchestrator.sh"
    )

MODELS = ("padim", "patchcore", "stfpm", "efficientad")
WEIGHTS_GS = "gs://subdron_dev/machine_learning/anomaly_detection/weights"
IMAGE_GLOBS = ("*.jpg", "*.png")
TS_RE = re.compile(r"(\d{17})")  # YYYYMMDDHHMMSSmmm in the extractor's file names
ASSUMED_FPS = 30
REQUEST_TIMEOUT_S = 120


def check_api(api_url: str, patience_s: float = 60) -> dict:
    """/health, waiting up to patience_s (the single worker is silent while it scores)."""
    deadline = time.monotonic() + patience_s
    while True:
        health = anomaly_api_health(api_url, timeout=5)
        if health is not None:
            return health
        if time.monotonic() >= deadline:
            fail(f"Anomaly API not reachable at {api_url} for {patience_s:.0f}s.\n"
                 f"        Start it with:  docker compose -f local_orchestrator/docker-compose.yml up -d anomaly-api")
        time.sleep(2)


def frame_time_s(name: str) -> float | None:
    m = TS_RE.search(name)
    if not m:
        return None
    ts = m.group(1)
    return datetime.strptime(ts[:14], "%Y%m%d%H%M%S").timestamp() + int(ts[14:]) / 1000


def subsample(images: list[Path], fps: float) -> list[Path]:
    """One frame per 1/fps seconds of footage (0 = every frame)."""
    if fps <= 0:
        return images
    times = [frame_time_s(p.name) for p in images]
    if any(t is None for t in times):
        return images[::max(1, round(ASSUMED_FPS / fps))]
    picked, next_t = [], None
    for p, t in zip(images, times):
        if next_t is None or t >= next_t:
            picked.append(p)
            next_t = t + 1 / fps
    return picked


def predict(api_url: str, image: Path, model: str, heatmap: bool) -> dict:
    """POST one image file to /predict (multipart) and return the JSON result."""
    boundary = uuid.uuid4().hex
    ctype = "image/png" if image.suffix.lower() == ".png" else "image/jpeg"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"{image.name}\"\r\nContent-Type: {ctype}\r\n\r\n").encode()
    body += image.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{api_url}/predict?model={model}&include_heatmap={'true' if heatmap else 'false'}",
        data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        if e.code == 404 and "weights" in detail.lower():
            raise RuntimeError(
                f"API has no weights for model '{model}' ({detail}). One-time download on any "
                f"machine with gcloud auth, into DATA_ROOT/_state/models/anomaly/{model}/:\n"
                f"        gcloud storage cp {WEIGHTS_GS}/{model}/model.xml {WEIGHTS_GS}/{model}/model.bin "
                f"<DATA_ROOT>/_state/models/anomaly/{model}/") from None
        raise RuntimeError(f"API error {e.code} for {image.name}: {detail}") from None


CAPTION_H = 30


def _caption_font():
    try:
        return ImageFont.load_default(size=18)  # Pillow >= 10.1
    except TypeError:
        return ImageFont.load_default()


def save_comparison(frame: Path, heatmap_b64: str, result: dict, dest: Path) -> None:
    original = Image.open(frame).convert("RGB")
    overlay = Image.open(io.BytesIO(base64.b64decode(heatmap_b64))).convert("RGB")
    if overlay.size != original.size:
        overlay = overlay.resize(original.size)
    w, h = original.size
    canvas = Image.new("RGB", (2 * w, h + CAPTION_H), "black")
    canvas.paste(original, (0, CAPTION_H))
    canvas.paste(overlay, (w, CAPTION_H))
    draw, font = ImageDraw.Draw(canvas), _caption_font()
    verdict = "ANOMALY" if result.get("is_anomaly") else "normal"
    draw.text((8, 6), f"{frame.name}   original", fill="white", font=font)
    draw.text((w + 8, 6), f"{result.get('model')} heatmap   score {result.get('anomaly_score')} "
                          f"/ threshold {result.get('threshold')}   {verdict}", fill="white", font=font)
    canvas.save(dest, quality=90)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input_dir", help="image_pose_pairs dir (absolute, or relative to DATA_ROOT)")
    parser.add_argument("--data-root", help="Data root (default: /srv/subdron/data; $DATA_ROOT overrides)")
    parser.add_argument("--model", default="padim", choices=MODELS)
    parser.add_argument("--fps", type=float, default=1.0,
                        help="Frames per second of footage to analyse (0 = every frame)")
    parser.add_argument("--save-heatmaps", default="anomalous", choices=["anomalous", "all", "none"])
    parser.add_argument("--api-url", default=ANOMALY_API_URL,
                        help=f"Anomaly API base URL (default: {ANOMALY_API_URL}; $ANOMALY_API_URL overrides)")
    parser.add_argument("--force", action="store_true", help="Re-run a completed job")
    args = parser.parse_args()

    data_root = resolve_data_root(args.data_root)
    input_dir = resolve_under_data_root(args.input_dir, data_root)
    images = sorted(p for g in IMAGE_GLOBS for p in input_dir.glob(g))
    if not input_dir.is_dir() or not images:
        fail(f"No images in input directory: {input_dir}")

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
    api_url = args.api_url.rstrip("/")

    post_mission = input_dir.parents[2]
    output_dir = post_mission / "results" / "anomaly" / timestamp / args.model
    scores_csv = output_dir / "scores.csv"
    frames = subsample(images, args.fps)

    record = {
        "job_id": job_id,
        "stage": "anomaly",
        "input_dir": str(input_dir),
        "source": source,
        "mission_id": mission_id,
        "timestamp": timestamp,
        "api_url": api_url,
        "model": args.model,
        "fps": args.fps,
        "frames_total": len(images),
        "frames_analysed": len(frames),
        "outputs": {
            "anomaly_dir": str(output_dir),
            "scores_csv": str(scores_csv),
            "log": str(log_path),
        },
        "runner_pid": os.getpid(), "status": "running", "started_utc": utc_now(),
        "ended_utc": None, "duration_seconds": None, "exit_code": None, "error_tail": None,
    }
    for d in (record_path.parent, log_path.parent, output_dir):
        d.mkdir(parents=True, exist_ok=True)
    record_path.write_text(json.dumps(record, indent=2))
    for old in output_dir.glob("*_anomaly.*"):
        old.unlink()

    t0 = time.monotonic()

    def finish(status: str, code: int, error: str | None = None) -> None:
        record.update(status=status, ended_utc=utc_now(), exit_code=code, error_tail=error,
                      duration_seconds=round(time.monotonic() - t0, 1))
        record_path.write_text(json.dumps(record, indent=2))

    want_heatmap = args.save_heatmaps != "none"
    rows, anomalies = [], 0
    with log_path.open("w") as log:
        def out(msg: str) -> None:
            log.write(msg + "\n"); log.flush()
            print(msg, flush=True)

        out(f"[{job_id}] {len(images)} images, analysing {len(frames)} at {args.fps} fps with "
            f"{args.model} (api {api_url}, loaded: {health.get('loaded_models')})")
        try:
            for i, frame in enumerate(frames, 1):
                res = predict(api_url, frame, args.model, want_heatmap)
                is_anomaly = bool(res.get("is_anomaly"))
                anomalies += is_anomaly
                rows.append({
                    "image": frame.name,
                    "footage_time_s": frame_time_s(frame.name),
                    "anomaly_score": res.get("anomaly_score"),
                    "threshold": res.get("threshold"),
                    "is_anomaly": int(is_anomaly),
                    "inference_ms": res.get("inference_time_ms"),
                })
                if res.get("heatmap_b64") and (args.save_heatmaps == "all" or is_anomaly):
                    save_comparison(frame, res["heatmap_b64"], res, output_dir / f"{frame.stem}_anomaly.jpg")
                if i % 25 == 0 or i == len(frames):
                    out(f"  {i}/{len(frames)} frames scored ({100 * i // len(frames)}%), {anomalies} anomalous")
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            out(f"[{job_id}] ERROR after {len(rows)} frames: {msg}")
            finish("failed", 1, msg)
            sys.exit(1)

        with scores_csv.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        summary = {
            "processed": len(rows), "anomaly_count": anomalies,
            "anomaly_rate": round(anomalies / len(rows), 4) if rows else 0.0,
            "model": args.model, "fps": args.fps, "output_dir": str(output_dir),
        }
        out(json.dumps(summary))  # the dashboard reads this last line

    finish("done", 0)
    print(f"[{job_id}] done in {record['duration_seconds']}s — {anomalies}/{len(rows)} frames anomalous "
          f"-> {output_dir} (record: {record_path})")


if __name__ == "__main__":
    main()
