"""Running log of anything that goes wrong (or is worth a second look)."""
import csv
import os
from datetime import datetime

import pandas as pd

LOG_COLUMNS = ["run_id", "time", "level", "stage", "application_id", "message"]


class RunLog:
    def __init__(self, run_id: str | None = None):
        self.run_id = run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
        self.entries: list[dict] = []

    def add(self, level: str, stage: str, message: str, application_id: str = ""):
        self.entries.append({
            "run_id": self.run_id,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "level": level,
            "stage": stage,
            "application_id": application_id or "",
            "message": message,
        })

    def info(self, stage, message, application_id=""):
        self.add("INFO", stage, message, application_id)

    def warn(self, stage, message, application_id=""):
        self.add("WARNING", stage, message, application_id)

    def error(self, stage, message, application_id=""):
        self.add("ERROR", stage, message, application_id)

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.entries, columns=LOG_COLUMNS)

    def counts(self) -> dict:
        out = {"INFO": 0, "WARNING": 0, "ERROR": 0}
        for e in self.entries:
            out[e["level"]] = out.get(e["level"], 0) + 1
        return out

    def append_to_file(self, path: str, entries: list | None = None):
        """Append entries (default: all of this run's) to a CSV that keeps growing across runs."""
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            new_file = not os.path.exists(path)
            with open(path, "a", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=LOG_COLUMNS)
                if new_file:
                    w.writeheader()
                w.writerows(self.entries if entries is None else entries)
        except OSError as e:          # read-only disk (some hosts): keep the in-memory log
            self.add("WARNING", "log", f"Could not write log file {path}: {e}")

    def add_and_save(self, level, stage, message, application_id="", path: str | None = None):
        """For one-off events (e.g. a reviewer action): record it and append just that line."""
        self.add(level, stage, message, application_id)
        if path:
            self.append_to_file(path, [self.entries[-1]])
