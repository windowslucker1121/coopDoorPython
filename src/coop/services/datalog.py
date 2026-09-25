"""CSV data logging and the log / data viewer back-end.

* :class:`CsvDataLogger` appends the dashboard snapshot to
  ``log/YYYY_MM_DD.csv``.  Values are CSV-quoted (uptime and error texts
  contain commas).
* :func:`read_csv` parses a data file for the chart viewer; rows from older
  files, written without quoting, are realigned.
* :func:`list_app_logs` / :func:`read_app_log` back the log viewer.
"""

from __future__ import annotations

import csv
import logging
import os
import re
from datetime import datetime

logger = logging.getLogger(__name__)

_APP_LOG_ROTATED = re.compile(r"^app\.log\.\d{4}-\d{2}-\d{2}$")


# ─────────────────────────────── CSV logging ────────────────────────────────

def csv_file_name(log_dir: str, day: datetime) -> str:
    return os.path.join(log_dir, day.strftime("%Y_%m_%d") + ".csv")


class CsvDataLogger:
    INTERVAL_S = 5.0

    def __init__(self, log_dir: str, snapshot, now):
        self._log_dir = log_dir
        self._snapshot = snapshot
        self._now = now

    def current_file(self) -> str:
        return csv_file_name(self._log_dir, self._now())

    def write_row(self) -> float:
        try:
            data = self._snapshot()
            os.makedirs(self._log_dir, exist_ok=True)
            path = self.current_file()
            new_file = not os.path.exists(path)
            with open(path, "a", newline="", encoding="utf-8") as f:
                if new_file:
                    f.write("# " + ", ".join(data.keys()) + "\n")
                csv.writer(f, lineterminator="\n").writerow(str(v) for v in data.values())
        except Exception as e:
            logger.error("CSV logging failed: %s", e)
        return self.INTERVAL_S


# ─────────────────────────────── viewers ────────────────────────────────────

def is_csv_name(name: str) -> bool:
    return name.endswith(".csv") and os.path.basename(name) == name and not name.startswith(".")


def is_app_log_name(name: str) -> bool:
    return (name == "app.log" or bool(_APP_LOG_ROTATED.match(name))
            or (name.startswith("app_") and name.endswith(".log")))


def _list(log_dir: str, predicate) -> list[dict]:
    files = []
    if os.path.isdir(log_dir):
        for name in os.listdir(log_dir):
            if not predicate(name):
                continue
            try:
                st = os.stat(os.path.join(log_dir, name))
            except OSError:
                continue
            files.append({"name": name, "size": st.st_size, "modified": st.st_mtime})
    return files


def list_csv_files(log_dir: str) -> list[dict]:
    return sorted(_list(log_dir, is_csv_name), key=lambda f: f["name"], reverse=True)


def list_app_logs(log_dir: str) -> list[dict]:
    return sorted(_list(log_dir, is_app_log_name), key=lambda f: f["modified"], reverse=True)


def read_app_log(path: str) -> list[dict]:
    """Parse ``TIMESTAMP - LOGGER - LEVEL - MESSAGE`` lines; others are RAW."""
    lines = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if not line:
                continue
            parts = line.split(" - ", 3)
            if len(parts) == 4:
                lines.append({"t": parts[0], "lg": parts[1], "lv": parts[2].strip().upper(), "m": parts[3]})
            else:
                lines.append({"t": "", "lg": "", "lv": "RAW", "m": line})
    return lines


CSV_NUMERIC_FIELDS = frozenset({
    "temp_in", "temp_in_min", "temp_in_max", "temp_out", "temp_out_min", "temp_out_max",
    "hum_in", "hum_in_min", "hum_in_max", "hum_out", "hum_out_min", "hum_out_max",
    "cpu_temp", "cpu_temp_min", "cpu_temp_max",
})
CSV_STRING_FIELDS = frozenset({"state", "override", "auto_mode", "errorstate"})
_NUMBER = re.compile(r"[^\d.\-]")


def align_values(headers: list[str], values: list[str]) -> list[str] | None:
    """Map a parsed row onto *headers*.

    Legacy rows (unquoted) have extra columns from the commas inside the
    ``uptime`` value: columns before ``uptime`` are taken from the start,
    the ones after it from the end.  ``None`` for rows that are too short.
    """
    if len(values) == len(headers):
        return values
    if len(values) < len(headers):
        return None
    if "uptime" not in headers:
        return values[:len(headers)]
    idx = headers.index("uptime")
    n_after = len(headers) - idx - 1
    tail = values[len(values) - n_after:] if n_after else []
    return values[:idx] + [", ".join(values[idx:len(values) - n_after])] + tail


def read_csv(path: str, max_rows: int = 600) -> dict:
    rows = []
    headers: list[str] | None = None
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            if headers is None:
                headers = [h.strip().lstrip("#").strip() for h in line.split(",")]
                continue
            values = align_values(headers, [v.strip() for v in next(csv.reader([line], skipinitialspace=True))])
            if values is None:
                continue
            row: dict = {}
            for h, v in zip(headers, values):
                if h == "time":
                    row["time"] = v.split(".")[0].strip()
                elif h in CSV_NUMERIC_FIELDS:
                    try:
                        row[h] = round(float(_NUMBER.sub("", v)), 2)
                    except ValueError:
                        row[h] = None
                elif h in CSV_STRING_FIELDS:
                    row[h] = v
            if "time" in row:
                rows.append(row)
    total = len(rows)
    if total > max_rows:
        step = total / max_rows
        rows = [rows[int(i * step)] for i in range(max_rows)]
    return {"count": len(rows), "total": total, "rows": rows}
