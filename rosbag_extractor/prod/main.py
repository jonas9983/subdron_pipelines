import os
from google.cloud import batch_v1
from google.cloud import storage

def process_bag_file_prod(event, context):
    """Triggered by GCS - PRODUCTION VERSION."""
    
    file_name = event['name']
    bucket_name = event['bucket']
    
    if not file_name.endswith('.bag'):
        return
    
    # Ignore test bags in production
    if '/03_Logs/test_bags/' in file_name:
        return
    
    # Dynamically determine the base directory (tests or data)
    base_dir = "tests" if file_name.startswith("tests/") else "data"
    
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(file_name)
    blob.reload()
    file_size_gb = blob.size / (1024**3)
    
    cpu = 4000 if file_size_gb < 5 else 8000
    memory = 16384 if file_size_gb < 5 else 32768
    
    # Read the tag we want to test from the Cloud Function's own Environment Variables
    tag_to_test = os.getenv('TARGET_IMAGE_TAG', 'production')
    
    create_batch_job(
        file_path=f"gs://{bucket_name}/{file_name}",
        cpu=cpu,
        memory=memory,
        tag=tag_to_test,
        bucket_name=bucket_name,
        base_dir=base_dir
    )

def create_batch_job(file_path, cpu, memory, tag, bucket_name, base_dir):
    client = batch_v1.BatchServiceClient()
    job = batch_v1.Job()
    job.task_groups = [batch_v1.TaskGroup()]
    
    runnable = batch_v1.Runnable()
    runnable.container = batch_v1.Runnable.Container()
    
    runnable.container.image_uri = f"europe-west1-docker.pkg.dev/eighth-saga-468413-c9/subdron-rosbag-extractor/subdron-rosbag-extractor:{tag}"
    
    runnable.container.entrypoint = "/bin/bash"
    runnable.container.commands = [
        "-c",
        f"source /opt/ros/noetic/setup.bash && source /catkin_ws/devel/setup.bash && python main.py -i {file_path} --extract-images --post-process --upload-results --create-video --video-fps 30"
    ]
    
    runnable.environment = batch_v1.Environment()
    
    # 1. Standard Environment Variables
    runnable.environment.variables = {
        "PIPELINE_ENV": "production",
        "CLOUD_BUCKET_NAME": bucket_name,
        "CLOUD_PROJECT_ID": "eighth-saga-468413-c9",
        "BASE_UPLOAD_DIR": base_dir
    }
    
    # Secret Environment Variables
    runnable.environment.secret_variables = {
        "DATABASE_URL": "projects/eighth-saga-468413-c9/secrets/DB_URL/versions/latest"
    }
    
    job.task_groups[0].task_spec = batch_v1.TaskSpec()
    job.task_groups[0].task_spec.runnables = [runnable]
    job.task_groups[0].task_spec.compute_resource = batch_v1.ComputeResource()
    job.task_groups[0].task_spec.compute_resource.cpu_milli = cpu
    job.task_groups[0].task_spec.compute_resource.memory_mib = memory
    job.task_groups[0].task_spec.compute_resource.boot_disk_mib = 51200
    job.task_groups[0].task_spec.max_run_duration = "7200s"
    
    job.allocation_policy = batch_v1.AllocationPolicy()
    instance_policy = batch_v1.AllocationPolicy.InstancePolicy()
    instance_policy.machine_type = "n2-standard-8" if cpu > 4000 else "n2-standard-4"
    job.allocation_policy.instances = [
        batch_v1.AllocationPolicy.InstancePolicyOrTemplate(policy=instance_policy)
    ]
    
    job.logs_policy = batch_v1.LogsPolicy()
    job.logs_policy.destination = batch_v1.LogsPolicy.Destination.CLOUD_LOGGING
    
    safe_tag = tag.replace('.', '-').lower()
    request = batch_v1.CreateJobRequest(
        parent="projects/eighth-saga-468413-c9/locations/europe-west1",
        job=job,
        job_id=f"prod-bag-{safe_tag}-{os.urandom(2).hex()}"
    )
    
    client.create_job(request)
    print(f"Successfully triggered Batch Job with tag: {tag}")
