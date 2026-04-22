import os
import json
from google.cloud import batch_v1
from google.cloud import storage

def process_images_prod(event, context):
    """Triggered when image extraction completes - PRODUCTION VERSION."""
    
    file_name = event['name']
    bucket_name = event['bucket']
    
    if not file_name.endswith('EXTRACTION_COMPLETE.marker'):
        return
    
    if '/tests/' in file_name:
        print(f"Ignoring test marker in production: {file_name}")
        return
    
    print(f"Processing completion marker: {file_name}")
    
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(file_name)
    
    marker_data = json.loads(blob.download_as_string())
    
    mission_id = marker_data['mission_id']
    timestamp = marker_data['timestamp']
    image_dir = marker_data['image_dir']
    
    # Prepend bucket name to ensure subdron_utils can resolve the path in Batch
    if not image_dir.startswith('gs://'):
        image_dir = f"gs://{bucket_name}/{image_dir.lstrip('/')}"
    
    # Read the tag we want to test from Env Vars
    tag_to_test = os.getenv('TARGET_IMAGE_TAG', 'production')
    
    # Methods to run
    methods_to_run = ["PHASEFORMER", "CLAHE"]
    
    for method in methods_to_run:
        print(f"Launching job for method: {method} using tag: {tag_to_test}")
        create_image_processing_job(
            image_dir=image_dir,
            method=method,
            mission_id=mission_id,
            timestamp=timestamp,
            tag=tag_to_test,
            bucket_name=bucket_name
        )

def create_image_processing_job(image_dir, method, mission_id, timestamp, tag, bucket_name):
    client = batch_v1.BatchServiceClient()
    job = batch_v1.Job()
    job.task_groups = [batch_v1.TaskGroup()]
    
    output_dir = image_dir.replace('/image_pose_pairs', f'/enhanced/{method}')
    
    runnable = batch_v1.Runnable()
    runnable.container = batch_v1.Runnable.Container()
    
    # Use the dynamic tag passed from the Cloud Function
    runnable.container.image_uri = f"europe-west1-docker.pkg.dev/eighth-saga-468413-c9/subdron-image-processing/subdron-image-processing:{tag}"
    
    runnable.container.entrypoint = "/bin/bash"
    runnable.container.commands = [
        "-c",
        f"python main.py {method} --download {image_dir} --upload {output_dir} --batch_size 8 --upload_every 512 --resume --create-video"
    ]

    runnable.environment = batch_v1.Environment()
    runnable.environment.variables = {
        "ENV": "production",
        "PIPELINE_ENV": "production",
        "MISSION_ID": mission_id,
        "TIMESTAMP": timestamp,
        "CLOUD_BUCKET_NAME": bucket_name,
        "BASE_UPLOAD_DIR": os.getenv("BASE_UPLOAD_DIR", "data")
    }
    
    # Inject Database Secret
    runnable.environment.secret_variables = {
        "DATABASE_URL": "projects/eighth-saga-468413-c9/secrets/DB_URL/versions/latest"
    }
    
    job.task_groups[0].task_spec = batch_v1.TaskSpec()
    job.task_groups[0].task_spec.runnables = [runnable]
    
    job.task_groups[0].task_spec.compute_resource = batch_v1.ComputeResource()
    job.task_groups[0].task_spec.compute_resource.cpu_milli = 8000
    job.task_groups[0].task_spec.compute_resource.memory_mib = 32768
    job.task_groups[0].task_spec.compute_resource.boot_disk_mib = 51200
    
    job.task_groups[0].task_spec.max_retry_count = 1
    job.task_groups[0].task_spec.max_run_duration = "21600s"
    
    job.allocation_policy = batch_v1.AllocationPolicy()
    instance_policy_or_template = batch_v1.AllocationPolicy.InstancePolicyOrTemplate()
    instance_policy = batch_v1.AllocationPolicy.InstancePolicy()
    
    if method == "PHASEFORMER":
        instance_policy.machine_type = "n1-standard-8"
        accelerator = batch_v1.AllocationPolicy.Accelerator()
        accelerator.type_ = "nvidia-tesla-t4"
        accelerator.count = 1
        instance_policy.accelerators = [accelerator]
        instance_policy_or_template.install_gpu_drivers = True
    else:
        instance_policy.machine_type = "e2-standard-8" 
        instance_policy_or_template.install_gpu_drivers = False
    
    instance_policy_or_template.policy = instance_policy
    job.allocation_policy.instances = [instance_policy_or_template]
    
    job.logs_policy = batch_v1.LogsPolicy()
    job.logs_policy.destination = batch_v1.LogsPolicy.Destination.CLOUD_LOGGING
    
    request = batch_v1.CreateJobRequest(
        parent="projects/eighth-saga-468413-c9/locations/europe-west1",
        job=job,
        job_id=f"prod-img-{method.lower()}-{os.urandom(2).hex()}"
    )
    
    client.create_job(request)
