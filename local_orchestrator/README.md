# Local Orchestrator (offline processing)

Runs the SUBDRON processing stages on a machine with no cloud access. Bags dropped
into a shared data tree are picked up automatically, a human approves each bag in
Label Studio before the GPU stages run, and a web dashboard shows the state of every
bag. Design notes, installation details and the release description live in
[`release.md`](../release.md); this file is the day-to-day reference.

## How it works

```
/srv/subdron/data/<robot>/<mission>/03_Logs/<bag>.bag
        │   every minute: pipeline_trigger.py (systemd timer) makes one pass
        ▼
   EXTRACTION ── images + poses + review video ──►  ANOMALY (background side branch)
        │
        ▼
     REVIEW ───── one Label Studio task per bag: mark segments, submit Approve / Reject
        │
        ▼
  PHASEFORMER ── enhanced images, one run per approved range        (GPU)
        │
        ▼
     COLMAP ───── 3D reconstruction                                 (GPU)
```

- State lives in **job records**: `_state/jobs/<job_id>.json`, with
  `job_id = <robot>_<mission>_<timestamp>` and a stage suffix (`_anomaly`,
  `_phaseformer`, `_colmap-spatial`). No record means "not started", so the trigger
  launches the next missing stage. Failed jobs are reported, **never retried** on their own.
- **Nothing runs on the GPU until the Approve choice is submitted** in Label Studio.
  Timeline segments alone are a draft. Timeline frame N is image N-1 (the videos are
  rendered at 30 fps from the extracted images); the sync converts the ranges.
- Anomaly detection needs only the extracted images, so it starts right after
  extraction and never blocks the review or the GPU stages.

## Where things are

| What | Where |
| :--- | :--- |
| Data root | `/srv/subdron/data` (`DATA_ROOT` overrides) |
| Bags in | `<root>/<robot>/<mission>/03_Logs/<mission>_<YYYY-MM-DD-HH-MM-SS>.bag` |
| Extraction out | `<mission>/02_Post_Mission/camera/<ts>/image_pose_pairs`, `navigation/<ts>/`, `camera/videos/<ts>/original/` |
| Anomaly out | `<mission>/02_Post_Mission/camera/<ts>/anomaly/<model>/` (`scores.csv` + heatmaps) |
| Enhanced images | `<mission>/02_Post_Mission/camera/<ts>/enhanced/PHASEFORMER/` |
| COLMAP results | `<mission>/02_Post_Mission/results/colmap/runs/<ts>/` |
| Job records, logs | `<root>/_state/jobs/`, `<root>/_state/logs/<job_id>.log` |
| Approvals, LS task ids | `<root>/_state/approvals/`, `<root>/_state/ls_tasks/` |
| Model weights | `<root>/_state/models/PHASEFORMER/`, `<root>/_state/models/anomaly/<model>/` |
| Dashboard | `http://<machine>:8090` |
| Label Studio | `http://<machine>:8080`, project "SUBDRON bag review" |
| Anomaly API | `http://localhost:8095` (compose service `anomaly-api`) |
| Code | `runners/` one script per stage · `trigger/` trigger, dashboard, Label Studio sync, CLI approval |

## Setup (once per user)

```bash
./scripts/setup_local_orchestrator.sh
```

Creates the data root, joins the groups, builds the Python env, starts the tracking DB
and, for the first user on a machine, installs the trigger timer and the dashboard as
systemd units. Label Studio is connected once per machine: see "Label Studio setup"
in `release.md`.

## Troubleshooting

Run the Python commands below from the repo root with the `pipelines` environment
active. `MISSION_DB_PORT` is only needed when the tracking DB is not on port 5433;
the setup script writes the export to `~/.bashrc`.

### What is the pipeline doing right now?

```bash
python local_orchestrator/trigger/pipeline_trigger.py --dry-run       # every bag: next action, or why it waits
journalctl -u subdron-pipeline-trigger -f                              # live trigger output, one pass per minute
journalctl -u subdron-pipeline-trigger --since "1 hour ago"
systemctl status subdron-pipeline-trigger.timer subdron-pipeline-dashboard
```

### The dashboard shows something different from the trigger

The dashboard loads the code once at start. After **every `git pull`** or code change:

```bash
sudo systemctl restart subdron-pipeline-dashboard
```

The trigger needs nothing: each pass is a fresh process and picks up new code.

### Redo a stage

```bash
python local_orchestrator/trigger/pipeline_trigger.py --reset <stage> <bag.bag>
```

| `<stage>` | Deletes | Then the next pass |
| :--- | :--- | :--- |
| `extraction` | everything for the bag: outputs, records, approval, Label Studio task | re-extracts and creates a new review task |
| `review` | approval, Label Studio task, PHASEFORMER and COLMAP outputs | creates a new review task |
| `phaseformer` | PHASEFORMER and COLMAP outputs and records | re-runs PHASEFORMER with the existing approval |
| `colmap` | COLMAP outputs and record | re-runs COLMAP |
| `anomaly` | anomaly outputs and record only (not part of the cascade) | re-scores the images |

### A stage failed

The red dashboard cell links to the log, or read `_state/logs/<job_id>.log` and the
`error_tail` in the job record. Fix the cause, then `--reset` that stage. Failures
are never retried automatically.

### A stage shows "running" but nothing is happening

The runner was killed (reboot, OOM). Check the `runner_pid` in the record with
`ps -p <pid>` and `docker ps`; if both are gone, `--reset` that stage.

### PHASEFORMER log shows "CUDA out of memory" / "Batch failed"

Batch size 8 at 720p is borderline on an 8 GB desktop card: anything else using the
GPU (a browser playing the review video, an IDE) tips it over. The stage skips the
whole batch and keeps going, so the runner checks the outputs afterwards, resumes the
missing images with a halved batch size, and marks the job failed only if images are
still missing. A running cell saying "errors in log" is that situation: let it finish.
`--batch-size N` on `run_phaseformer.py` sets the starting size by hand.
`nvidia-smi` shows who else holds GPU memory.

### "Another GPU job is running"

Only one PHASEFORMER or COLMAP job runs at a time (`_state/gpu.lock`). `docker ps`
shows which one; wait, or stop the container to abort it (the job is then marked failed).

### A bag is not picked up

- The path must be `<root>/<robot>/<mission>/03_Logs/*.bag`; anything under a
  `transformed/` directory is ignored.
- `SKIP ... cannot derive identity` in the trigger output: the mission directory or the
  timestamp in the filename does not match `<mission>_<YYYY-MM-DD-HH-MM-SS>.bag`.
- `Another trigger pass is running`: passes are serial and an extraction is in
  progress; the bag is handled by the next free pass.

### Label Studio

- **Annotated but nothing starts**: the Approve choice was not selected. Select it and
  submit; the next pass converts it into the approval.
- **Duplicate or orphan tasks**: the trigger only knows the task ids in
  `_state/ls_tasks/`. Delete extra tasks in the UI, or `--reset review` the bag to get
  one fresh task.
- **Label Studio is down**: the trigger prints one warning per pass and skips the sync;
  everything else keeps running. Approve from the command line meanwhile:
  ```bash
  python local_orchestrator/trigger/approve_bag_for_processing.py <bag.bag> --ranges 0:500 1400:2100
  python local_orchestrator/trigger/approve_bag_for_processing.py <bag.bag> --reject
  ```
- **Video does not load in the task**: the data root must be mounted read-only inside
  the Label Studio container under its local-files document root (see `release.md`).
- **Disable the integration**: remove `_state/label_studio.json`.

### Anomaly column says "api down"

```bash
docker compose -f local_orchestrator/docker-compose.yml up -d anomaly-api
curl localhost:8095/health
```

The service needs the image `subdron-anomaly-api:<version>` present locally and the
weights at `_state/models/anomaly/<model>/model.ckpt`. While it is down the trigger
prints one warning per pass and skips the stage; once it is up, every extracted bag
without an anomaly record is scored on the next pass. The runner's own output is in
`_state/logs/<job_id>_anomaly.launch.log`, the API output in `_state/logs/<job_id>_anomaly.log`.

### PHASEFORMER weights missing

```bash
gcloud storage cp gs://subdron_dev/machine_learning/image_processing/weights/best.pth /srv/subdron/data/_state/models/PHASEFORMER/
```

### Upgrade a stage image

Edit the version in `local_orchestrator/images.env`, commit, then on each machine:

```bash
./scripts/update_local_images.sh
```

### Permission denied under the data root

Everything is owned by group `subdron` and group-writable. Run
`./scripts/setup_local_orchestrator.sh` again (it joins the groups) and log out and in.

### Clean slate for a demo

`--reset extraction` on every bag. Within a minute the trigger re-extracts them one by
one and creates fresh review tasks; approve in Label Studio to continue.
