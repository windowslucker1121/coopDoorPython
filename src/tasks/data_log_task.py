"""CSV data-logging background task.

Appends all telemetry data to a daily CSV file every 5 s.

Bug-fix (Issue #3):
    The previous implementation called ``os.remove()`` unconditionally on
    the current day's file at startup, destroying data collected before a
    mid-day restart.  The deletion is now gated behind a ``reset_on_start``
    parameter (default ``False``).  Pass ``True`` only when the caller
    explicitly wants a fresh file (e.g. ``--reset-log`` CLI flag).
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from typing import Any

from protected_dict import protected_dict as global_vars

logger = logging.getLogger(__name__)


def get_log_file_name(root_path: str) -> str:
    """Return today's CSV log path in ``<root>/log/YYYY_MM_DD.csv``."""
    formatted_date = datetime.now().strftime("%Y_%m_%d")
    return os.path.join(root_path, "log", formatted_date + ".csv")


def data_log_task_main(
    root_path: str,
    get_all_data_fn: Any,
    *,
    reset_on_start: bool = False,
) -> None:
    """Background loop — runs forever in a daemon thread.

    Parameters
    ----------
    root_path:
        Repository root directory (parent of ``src/``).
    get_all_data_fn:
        Callable returning the telemetry dict.
    reset_on_start:
        When ``True`` the current day's CSV file is deleted before the loop
        starts.  Default is ``False`` to preserve data across restarts.
    """
    log_dir = os.path.join(root_path, "log")
    os.makedirs(log_dir, exist_ok=True)

    if reset_on_start:
        today_log = get_log_file_name(root_path)
        if os.path.exists(today_log):
            try:
                os.remove(today_log)
                logger.info("data_log_task: reset_on_start — deleted %s", today_log)
            except OSError as exc:
                logger.warning("data_log_task: could not delete %s: %s", today_log, exc)

    last_log_file_name = ""

    while True:
        try:
            data = get_all_data_fn()
            log_file_name = get_log_file_name(root_path)

            # Write CSV header when the file is new or a day boundary is crossed
            if log_file_name != last_log_file_name:
                with open(log_file_name, "a", encoding="utf-8") as fh:
                    header = "# " + ", ".join(data.keys()) + "\n"
                    fh.write(header)
                last_log_file_name = log_file_name

            with open(log_file_name, "a", encoding="utf-8") as fh:
                row = ", ".join(str(v) for v in data.values()) + "\n"
                fh.write(row)

        except Exception as exc:  # noqa: BLE001
            logger.error("data_log_task: error writing log: %s", exc)

        time.sleep(5.0)
