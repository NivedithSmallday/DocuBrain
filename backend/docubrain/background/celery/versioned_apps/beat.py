"""Factory stub for running celery worker / celery beat."""

from celery import Celery

from docubrain.background.celery.apps.beat import celery_app
from docubrain.utils.variable_functionality import set_is_ee_based_on_env_variable

set_is_ee_based_on_env_variable()
app: Celery = celery_app
