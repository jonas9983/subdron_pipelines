import os
import json
from google.cloud import batch_v1
from google.cloud import storage

def camera_has_images(storage_client, bucket_name, images_prefix, camera):
    """Checks if any files exist that start with the camera name as a prefix."""
    search_prefix = f"{images_prefix.rstrip('/')}/{camera}_"
    bucket = storage_client.bucket(bucket_name)
    blobs = list(storage_client.list_blobs(bucket, prefix=search_prefix, max_results=1))
    return len(blobs) > 0

def process_colmap_dev(event, context):
    """Triggered when image enhancement completes - DEVELOPMENT VERSION."""
    file_name = event['name']
    bucket_name = event['bucket']
    
    if not file_name.endswith('ENHANCEMENT_COMPLETE.marker'):
        return
    
    if '/sparus/tests/' not in file_name:
        print(f"Ignoring non-test marker in dev: {file_name}")
        return
    
    if 'data/sparus/' not in file_name or '/02_Post_Mission/camera/' not in file_name:
        return
    
    print(f"Processing test completion marker: {file_name}")
    
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(file_name)
    marker_data = json.loads(blob.download_as_string())
    
    # Extract metadata from marker
    mission_id = marker_data.get('mission_id')
    timestamp = marker_data.get('timestamp')
    method = marker_data.get('method')
    images_prefix = marker_data.get('images_prefix')
    poses_prefix = marker_data.get('poses_prefix')
    use_checkpoint = marker_data.get("use_checkpoint", False)
    image_range = marker_data.get("image_range", "")

    if method == 'CLAHE' or 'CLAHE' in images_prefix:
        print(f"Skipping job: Data uses CLAHE method ({images_prefix})")
        return

    # Read the tag we want to test from Env Vars
    tag_to_use = os.getenv('TARGET_IMAGE_TAG', 'dev')

    # Define the matrix of jobs to run
    cameras = ["bottom", "right", "left", "top"]
    matching_strategies = [
        {'type': 'sequential', 'window': 10},
        {'type': 'exhaustive', 'window': None}
    ]
    
    for camera in cameras:
        print(f"Checking for images starting with: {camera}_")
        if not camera_has_images(storage_client, bucket_name, images_prefix, camera):
            print(f"Skipping {camera}: No files found with prefix {camera}_")
            continue

        for strategy in matching_strategies:
            print(f"Launching test job for Camera: {camera} | Strategy: {strategy['type']} using tag: {tag_to_use}")
            create_colmap_job(
                images_prefix=images_prefix,
                poses_prefix=poses_prefix,
                use_checkpoint=use_checkpoint,
                image_range=image_range,
                mission_id=mission_id,
                timestamp=timestamp,
                method=method,
                camera=camera,
                strategy=strategy,
                tag=tag_to_use
            )

def create_colmap_job(images_prefix, poses_prefix, use_checkpoint, image_range, 
                      mission_id, timestamp, method, camera, strategy, tag):
    client = batch_v1.BatchServiceClient()
    job = batch_v1.Job()
    job.task_groups = [batch_v1.TaskGroup()]
    
    matching_type = strategy['type']
    dir_version = f"{method}_{camera}_{matching_type}"
    
    cmd_parts = [
        "python main.py",
        f"--images-prefix {images_prefix}",
        f"--poses-prefix {poses_prefix}",
        f"--camera {camera}",
        f"--dir-version {dir_version}",
        "--upload-results"
    ]
    
    if matching_type == 'sequential':
        cmd_parts.append("--sequential")
        cmd_parts.append(f"--window-size {strategy['window']}")
    
    if image_range:
        cmd_parts.append(f"--image-range {image_range}")

    if use_checkpoint:
        cmd_parts.append("--use-checkpoint")
    
    command = " ".join(cmd_parts)
    
    runnable = batch_v1.Runnable()
    runnable.container = batch_v1.Runnable.Container()
    runnable.container.image_uri = f"europe-west1-docker.pkg.dev/eighth-saga-468413-c9/subdron-colmap/subdron-colmap:{tag}"
    runnable.container.entrypoint = "/bin/bash"
    runnable.container.commands = ["-c", command]

    runnable.environment = batch_v1.Environment()
    runnable.environment.variables = {
        "ENV": "dev",
        "MISSION_ID": mission_id,
        "TIMESTAMP": timestamp,
        "CAMERA": camera
    }
    
    job.task_groups[0].task_spec = batch_v1.TaskSpec()
    job.task_groups[0].task_spec.runnables = [runnable]
    
    job.task_groups[0].task_spec.compute_resource = batch_v1.ComputeResource()
    job.task_groups[0].task_spec.compute_resource.cpu_milli = 16000 
    job.task_groups[0].task_spec.compute_resource.memory_mib = 65536 
    job.task_groups[0].task_spec.compute_resource.boot_disk_mib = 102400 
    job.task_groups[0].task_count = 1
    
    instance_policy = batch_v1.AllocationPolicy.InstancePolicy()
    instance_policy.machine_type = "n1-highmem-16"
    
    accelerator = batch_v1.AllocationPolicy.Accelerator()
    accelerator.type_ = "nvidia-tesla-t4"
    accelerator.count = 1
    instance_policy.accelerators = [accelerator]
    
    instance_policy_or_template = batch_v1.AllocationPolicy.InstancePolicyOrTemplate()
    instance_policy_or_template.policy = instance_policy
    instance_policy_or_template.install_gpu_drivers = True
    
    job.allocation_policy = batch_v1.AllocationPolicy()
    job.allocation_policy.instances = [instance_policy_or_template]
    job.allocation_policy.location = batch_v1.AllocationPolicy.LocationPolicy(
        allowed_locations=["zones/europe-west1-b", "zones/europe-west1-c"]
    )
    
    job.logs_policy = batch_v1.LogsPolicy()
    job.logs_policy.destination = batch_v1.LogsPolicy.Destination.CLOUD_LOGGING
    
    job_id = f"dev-colmap-{camera[:3]}-{matching_type[:3]}-{os.urandom(3).hex()}"
    
    request = batch_v1.CreateJobRequest(
        parent="projects/eighth-saga-468413-c9/locations/europe-west1",
        job=job,
        job_id=job_id
    )
    
    client.create_job(request)
    print(f"Successfully queued: {job_id}")
