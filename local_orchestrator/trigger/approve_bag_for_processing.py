#!/usr/bin/env python3
"""Record the human review decision for an extracted bag.

After extraction, each bag WAITS until someone watches its videos
(02_Post_Mission/camera/videos/<ts>/original/) and decides whether the
imagery is worth GPU processing. This script records that decision in
DATA_ROOT/_state/approvals/<job_id>.json, which pipeline_trigger.py reads.

Usage:
    # process the whole bag:
    python local_orchestrator/trigger/approve_bag_for_processing.py <bag_path>

    # process only the interesting frames (video second N ~= frame N*fps):
    python local_orchestrator/trigger/approve_bag_for_processing.py <bag_path> --ranges 0:500 1400:2100

    # not worth processing at all:
    python local_orchestrator/trigger/approve_bag_for_processing.py <bag_path> --reject
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline_helpers import fail, resolve_data_root, resolve_under_data_root, utc_now

try:
    import subdron_utils
except ImportError:
    sys.exit("[ERROR] subdron_utils missing — run ../subdron_missions_database/setup_host.sh first")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", help="Bag file or any mission-tree path containing the timestamp")
    parser.add_argument("--ranges", nargs="+", default=None,
                        help="Image ranges start:end[:step] (default: all images)")
    parser.add_argument("--reject", action="store_true", help="Mark as not worth GPU processing")
    parser.add_argument("--data-root", help="Data root (default: /srv/subdron/data; $DATA_ROOT overrides)")
    args = parser.parse_args()

    for r in args.ranges or []:
        if not re.fullmatch(r"\d+:\d+(:\d+)?", r):
            fail(f"Bad range '{r}' — expected start:end[:step], e.g. 0:500")

    data_root = resolve_data_root(args.data_root)
    path = resolve_under_data_root(args.path, data_root)
    source = subdron_utils.infer_source_from_path(str(path))
    mission_id, timestamp, _ = subdron_utils.get_mission_info(str(path))
    job_id = f"{source}_{mission_id}_{timestamp}"

    approval = {
        "job_id": job_id,
        "approved": not args.reject,
        "ranges": args.ranges,
        "decided_utc": utc_now(),
    }
    approvals_dir = data_root / "_state" / "approvals"
    approvals_dir.mkdir(parents=True, exist_ok=True)
    out = approvals_dir / f"{job_id}.json"
    out.write_text(json.dumps(approval, indent=2))

    verdict = "REJECTED" if args.reject else f"APPROVED (ranges: {args.ranges or 'all'})"
    print(f"[{job_id}] {verdict} -> {out}")


if __name__ == "__main__":
    main()
