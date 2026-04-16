import copy
from datetime import timedelta
from typing import Any

from celery.schedules import crontab

from docubrain.configs.app_configs import AUTO_LLM_CONFIG_URL
from docubrain.configs.app_configs import AUTO_LLM_UPDATE_INTERVAL_SECONDS
from docubrain.configs.app_configs import DISABLE_VECTOR_DB
from docubrain.configs.app_configs import ENABLE_OPENSEARCH_INDEXING_FOR_DOCUBRAIN
from docubrain.configs.app_configs import SCHEDULED_EVAL_DATASET_NAMES
from docubrain.configs.constants import DOCUBRAIN_CLOUD_CELERY_TASK_PREFIX
from docubrain.configs.constants import DocubrainCeleryPriority
from docubrain.configs.constants import DocubrainCeleryQueues
from docubrain.configs.constants import DocubrainCeleryTask
from shared_configs.configs import MULTI_TENANT

# choosing 15 minutes because it roughly gives us enough time to process many tasks
# we might be able to reduce this greatly if we can run a unified
# loop across all tenants rather than tasks per tenant

# we set expires because it isn't necessary to queue up these tasks
# it's only important that they run relatively regularly
BEAT_EXPIRES_DEFAULT = 15 * 60  # 15 minutes (in seconds)

# hack to slow down task dispatch in the cloud until
# we have a better implementation (backpressure, etc)
# Note that DynamicTenantScheduler can adjust the runtime value for this via Redis
CLOUD_BEAT_MULTIPLIER_DEFAULT = 8.0
CLOUD_DOC_PERMISSION_SYNC_MULTIPLIER_DEFAULT = 1.0

# tasks that run in either self-hosted on cloud
beat_task_templates: list[dict] = [
    {
        "name": "check-for-user-file-processing",
        "task": DocubrainCeleryTask.CHECK_FOR_USER_FILE_PROCESSING,
        "schedule": timedelta(seconds=20),
        "options": {
            "priority": DocubrainCeleryPriority.MEDIUM,
            "expires": BEAT_EXPIRES_DEFAULT,
        },
    },
    {
        "name": "check-for-user-file-project-sync",
        "task": DocubrainCeleryTask.CHECK_FOR_USER_FILE_PROJECT_SYNC,
        "schedule": timedelta(seconds=20),
        "options": {
            "priority": DocubrainCeleryPriority.MEDIUM,
            "expires": BEAT_EXPIRES_DEFAULT,
        },
    },
    {
        "name": "check-for-user-file-delete",
        "task": DocubrainCeleryTask.CHECK_FOR_USER_FILE_DELETE,
        "schedule": timedelta(seconds=20),
        "options": {
            "priority": DocubrainCeleryPriority.MEDIUM,
            "expires": BEAT_EXPIRES_DEFAULT,
        },
    },
    {
        "name": "check-for-indexing",
        "task": DocubrainCeleryTask.CHECK_FOR_INDEXING,
        "schedule": timedelta(seconds=15),
        "options": {
            "priority": DocubrainCeleryPriority.MEDIUM,
            "expires": BEAT_EXPIRES_DEFAULT,
        },
    },
    {
        "name": "check-for-checkpoint-cleanup",
        "task": DocubrainCeleryTask.CHECK_FOR_CHECKPOINT_CLEANUP,
        "schedule": timedelta(hours=1),
        "options": {
            "priority": DocubrainCeleryPriority.LOW,
            "expires": BEAT_EXPIRES_DEFAULT,
            # Run on gated tenants too — they may still have stale checkpoints to clean.
            "skip_gated": False,
        },
    },
    {
        "name": "check-for-index-attempt-cleanup",
        "task": DocubrainCeleryTask.CHECK_FOR_INDEX_ATTEMPT_CLEANUP,
        "schedule": timedelta(minutes=30),
        "options": {
            "priority": DocubrainCeleryPriority.MEDIUM,
            "expires": BEAT_EXPIRES_DEFAULT,
            # Run on gated tenants too — they may still have stale index attempts.
            "skip_gated": False,
        },
    },
    {
        "name": "check-for-connector-deletion",
        "task": DocubrainCeleryTask.CHECK_FOR_CONNECTOR_DELETION,
        "schedule": timedelta(seconds=20),
        "options": {
            "priority": DocubrainCeleryPriority.MEDIUM,
            "expires": BEAT_EXPIRES_DEFAULT,
            # Gated tenants may still have connectors awaiting deletion.
            "skip_gated": False,
        },
    },
    {
        "name": "check-for-vespa-sync",
        "task": DocubrainCeleryTask.CHECK_FOR_VESPA_SYNC_TASK,
        "schedule": timedelta(seconds=20),
        "options": {
            "priority": DocubrainCeleryPriority.MEDIUM,
            "expires": BEAT_EXPIRES_DEFAULT,
        },
    },
    {
        "name": "check-for-pruning",
        "task": DocubrainCeleryTask.CHECK_FOR_PRUNING,
        "schedule": timedelta(seconds=20),
        "options": {
            "priority": DocubrainCeleryPriority.MEDIUM,
            "expires": BEAT_EXPIRES_DEFAULT,
        },
    },
    {
        "name": "check-for-hierarchy-fetching",
        "task": DocubrainCeleryTask.CHECK_FOR_HIERARCHY_FETCHING,
        "schedule": timedelta(hours=1),  # Check hourly, but only fetch once per day
        "options": {
            "priority": DocubrainCeleryPriority.LOW,
            "expires": BEAT_EXPIRES_DEFAULT,
        },
    },
    {
        "name": "monitor-background-processes",
        "task": DocubrainCeleryTask.MONITOR_BACKGROUND_PROCESSES,
        "schedule": timedelta(minutes=5),
        "options": {
            "priority": DocubrainCeleryPriority.LOW,
            "expires": BEAT_EXPIRES_DEFAULT,
            "queue": DocubrainCeleryQueues.MONITORING,
        },
    },
    # Sandbox cleanup tasks
    {
        "name": "cleanup-idle-sandboxes",
        "task": DocubrainCeleryTask.CLEANUP_IDLE_SANDBOXES,
        # SANDBOX_IDLE_TIMEOUT_SECONDS defaults to 1 hour, so there is no
        # functional reason to scan more often than every ~15 minutes. In the
        # cloud this is multiplied by CLOUD_BEAT_MULTIPLIER_DEFAULT (=8) so
        # the effective cadence becomes ~2 hours, which still meets the
        # idle-detection SLA. The previous 1-minute base schedule produced
        # an 8-minute per-tenant fan-out and was the dominant source of
        # background DB load on the cloud cluster.
        "schedule": timedelta(minutes=15),
        "options": {
            "priority": DocubrainCeleryPriority.LOW,
            "expires": BEAT_EXPIRES_DEFAULT,
            "queue": DocubrainCeleryQueues.SANDBOX,
        },
    },
    {
        "name": "cleanup-old-snapshots",
        "task": DocubrainCeleryTask.CLEANUP_OLD_SNAPSHOTS,
        "schedule": timedelta(hours=24),
        "options": {
            "priority": DocubrainCeleryPriority.LOW,
            "expires": BEAT_EXPIRES_DEFAULT,
            "queue": DocubrainCeleryQueues.SANDBOX,
        },
    },
]

# Add the Auto LLM update task if the config URL is set (has a default)
if AUTO_LLM_CONFIG_URL:
    beat_task_templates.append(
        {
            "name": "check-for-auto-llm-update",
            "task": DocubrainCeleryTask.CHECK_FOR_AUTO_LLM_UPDATE,
            "schedule": timedelta(seconds=AUTO_LLM_UPDATE_INTERVAL_SECONDS),
            "options": {
                "priority": DocubrainCeleryPriority.LOW,
                "expires": BEAT_EXPIRES_DEFAULT,
            },
        }
    )

# Add scheduled eval task if datasets are configured
if SCHEDULED_EVAL_DATASET_NAMES:
    beat_task_templates.append(
        {
            "name": "scheduled-eval-pipeline",
            "task": DocubrainCeleryTask.SCHEDULED_EVAL_TASK,
            # run every Sunday at midnight UTC
            "schedule": crontab(
                hour=0,
                minute=0,
                day_of_week=0,
            ),
            "options": {
                "priority": DocubrainCeleryPriority.LOW,
                "expires": BEAT_EXPIRES_DEFAULT,
            },
        }
    )

# Add OpenSearch migration task if enabled.
if ENABLE_OPENSEARCH_INDEXING_FOR_DOCUBRAIN:
    beat_task_templates.append(
        {
            "name": "migrate-chunks-from-vespa-to-opensearch",
            "task": DocubrainCeleryTask.MIGRATE_CHUNKS_FROM_VESPA_TO_OPENSEARCH_TASK,
            # Try to enqueue an invocation of this task with this frequency.
            "schedule": timedelta(seconds=120),  # 2 minutes
            "options": {
                "priority": DocubrainCeleryPriority.LOW,
                # If the task was not dequeued in this time, revoke it.
                "expires": BEAT_EXPIRES_DEFAULT,
                "queue": DocubrainCeleryQueues.OPENSEARCH_MIGRATION,
            },
        }
    )


# Beat task names that require a vector DB. Filtered out when DISABLE_VECTOR_DB.
_VECTOR_DB_BEAT_TASK_NAMES: set[str] = {
    "check-for-indexing",
    "check-for-connector-deletion",
    "check-for-vespa-sync",
    "check-for-pruning",
    "check-for-hierarchy-fetching",
    "check-for-checkpoint-cleanup",
    "check-for-index-attempt-cleanup",
    "check-for-doc-permissions-sync",
    "check-for-external-group-sync",
    "migrate-chunks-from-vespa-to-opensearch",
}

if DISABLE_VECTOR_DB:
    beat_task_templates = [
        t for t in beat_task_templates if t["name"] not in _VECTOR_DB_BEAT_TASK_NAMES
    ]


def make_cloud_generator_task(task: dict[str, Any]) -> dict[str, Any]:
    cloud_task: dict[str, Any] = {}

    # constant options for cloud beat task generators
    task_schedule: timedelta = task["schedule"]
    cloud_task["schedule"] = task_schedule
    cloud_task["options"] = {}
    cloud_task["options"]["priority"] = DocubrainCeleryPriority.HIGHEST
    cloud_task["options"]["expires"] = BEAT_EXPIRES_DEFAULT

    # settings dependent on the original task
    cloud_task["name"] = f"{DOCUBRAIN_CLOUD_CELERY_TASK_PREFIX}_{task['name']}"
    cloud_task["task"] = DocubrainCeleryTask.CLOUD_BEAT_TASK_GENERATOR
    cloud_task["kwargs"] = {}
    cloud_task["kwargs"]["task_name"] = task["task"]

    optional_fields = ["queue", "priority", "expires", "skip_gated"]
    for field in optional_fields:
        if field in task["options"]:
            cloud_task["kwargs"][field] = task["options"][field]

    return cloud_task


# tasks that only run in the cloud and are system wide
# the name attribute must start with DOCUBRAIN_CLOUD_CELERY_TASK_PREFIX = "cloud" to be seen
# by the DynamicTenantScheduler as system wide task and not a per tenant task
beat_cloud_tasks: list[dict] = [
    # cloud specific tasks
    {
        "name": f"{DOCUBRAIN_CLOUD_CELERY_TASK_PREFIX}_monitor-alembic",
        "task": DocubrainCeleryTask.CLOUD_MONITOR_ALEMBIC,
        "schedule": timedelta(hours=1),
        "options": {
            "queue": DocubrainCeleryQueues.MONITORING,
            "priority": DocubrainCeleryPriority.HIGH,
            "expires": BEAT_EXPIRES_DEFAULT,
        },
    },
    {
        "name": f"{DOCUBRAIN_CLOUD_CELERY_TASK_PREFIX}_monitor-celery-queues",
        "task": DocubrainCeleryTask.CLOUD_MONITOR_CELERY_QUEUES,
        "schedule": timedelta(seconds=30),
        "options": {
            "queue": DocubrainCeleryQueues.MONITORING,
            "priority": DocubrainCeleryPriority.HIGH,
            "expires": BEAT_EXPIRES_DEFAULT,
        },
    },
    {
        "name": f"{DOCUBRAIN_CLOUD_CELERY_TASK_PREFIX}_check-available-tenants",
        "task": DocubrainCeleryTask.CLOUD_CHECK_AVAILABLE_TENANTS,
        "schedule": timedelta(minutes=2),
        "options": {
            "queue": DocubrainCeleryQueues.MONITORING,
            "priority": DocubrainCeleryPriority.HIGH,
            "expires": BEAT_EXPIRES_DEFAULT,
        },
    },
    {
        "name": f"{DOCUBRAIN_CLOUD_CELERY_TASK_PREFIX}_monitor-celery-pidbox",
        "task": DocubrainCeleryTask.CLOUD_MONITOR_CELERY_PIDBOX,
        "schedule": timedelta(hours=4),
        "options": {
            "queue": DocubrainCeleryQueues.MONITORING,
            "priority": DocubrainCeleryPriority.HIGH,
            "expires": BEAT_EXPIRES_DEFAULT,
        },
    },
]

# tasks that only run self hosted
tasks_to_schedule: list[dict] = []
if not MULTI_TENANT:
    tasks_to_schedule.extend(
        [
            {
                "name": "monitor-celery-queues",
                "task": DocubrainCeleryTask.MONITOR_CELERY_QUEUES,
                "schedule": timedelta(seconds=10),
                "options": {
                    "priority": DocubrainCeleryPriority.MEDIUM,
                    "expires": BEAT_EXPIRES_DEFAULT,
                    "queue": DocubrainCeleryQueues.MONITORING,
                },
            },
            {
                "name": "monitor-process-memory",
                "task": DocubrainCeleryTask.MONITOR_PROCESS_MEMORY,
                "schedule": timedelta(minutes=5),
                "options": {
                    "priority": DocubrainCeleryPriority.LOW,
                    "expires": BEAT_EXPIRES_DEFAULT,
                    "queue": DocubrainCeleryQueues.MONITORING,
                },
            },
            {
                "name": "celery-beat-heartbeat",
                "task": DocubrainCeleryTask.CELERY_BEAT_HEARTBEAT,
                "schedule": timedelta(minutes=1),
                "options": {
                    "priority": DocubrainCeleryPriority.HIGHEST,
                    "expires": BEAT_EXPIRES_DEFAULT,
                    "queue": DocubrainCeleryQueues.PRIMARY,
                },
            },
        ]
    )

    # `skip_gated` is a cloud-only hint consumed by `cloud_beat_task_generator`. Strip
    # it before extending the self-hosted schedule so it doesn't leak into apply_async
    # as an unrecognised option on every fired task message.
    for _template in beat_task_templates:
        _self_hosted_template = copy.deepcopy(_template)
        _self_hosted_template["options"].pop("skip_gated", None)
        tasks_to_schedule.append(_self_hosted_template)


def generate_cloud_tasks(
    beat_tasks: list[dict], beat_templates: list[dict], beat_multiplier: float
) -> list[dict[str, Any]]:
    """
    beat_tasks: system wide tasks that can be sent as is
    beat_templates: task templates that will be transformed into per tenant tasks via
    the cloud_beat_task_generator
    beat_multiplier: a multiplier that can be applied on top of the task schedule
    to speed up or slow down the task generation rate. useful in production.

    Returns a list of cloud tasks, which consists of incoming tasks + tasks generated
    from incoming templates.
    """

    if beat_multiplier <= 0:
        raise ValueError("beat_multiplier must be positive!")

    cloud_tasks: list[dict] = []

    # generate our tenant aware cloud tasks from the templates
    for beat_template in beat_templates:
        cloud_task = make_cloud_generator_task(beat_template)
        cloud_tasks.append(cloud_task)

    # factor in the cloud multiplier for the above
    for cloud_task in cloud_tasks:
        cloud_task["schedule"] = cloud_task["schedule"] * beat_multiplier

    # add the fixed cloud/system beat tasks. No multiplier for these.
    cloud_tasks.extend(copy.deepcopy(beat_tasks))
    return cloud_tasks


def get_cloud_tasks_to_schedule(beat_multiplier: float) -> list[dict[str, Any]]:
    return generate_cloud_tasks(beat_cloud_tasks, beat_task_templates, beat_multiplier)


def get_tasks_to_schedule() -> list[dict[str, Any]]:
    return tasks_to_schedule
