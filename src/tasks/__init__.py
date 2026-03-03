"""Background daemon task entry-points."""

from tasks.door_task import door_task_main
from tasks.temperature_task import temperature_task_main
from tasks.data_update_task import data_update_task_main
from tasks.data_log_task import data_log_task_main
from tasks.camera_task import camera_task_main

__all__ = [
    "camera_task_main",
    "data_log_task_main",
    "data_update_task_main",
    "door_task_main",
    "temperature_task_main",
]
