"""Configuration and project-path helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ProjectPaths:
    root: Path
    raw: Path
    processed: Path
    outputs: Path
    maps: Path
    figures: Path
    tables: Path
    reports: Path

    @classmethod
    def from_root(cls, root: Path) -> "ProjectPaths":
        root = root.resolve()
        return cls(
            root=root,
            raw=root / "data" / "raw",
            processed=root / "data" / "processed",
            outputs=root / "outputs",
            maps=root / "outputs" / "maps",
            figures=root / "outputs" / "figures",
            tables=root / "outputs" / "tables",
            reports=root / "reports",
        )

    def create(self) -> None:
        for directory in (
            self.raw,
            self.processed,
            self.outputs,
            self.maps,
            self.figures,
            self.tables,
            self.reports,
        ):
            directory.mkdir(parents=True, exist_ok=True)


def load_config(project_root: Path) -> dict[str, Any]:
    path = project_root / "config.json"
    with path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    required = {
        "analysis_date",
        "local_crs",
        "grid_size_m",
        "threshold_minutes",
        "recommendation_count",
        "districts",
    }
    missing = sorted(required.difference(config))
    if missing:
        raise ValueError(f"Missing required configuration keys: {', '.join(missing)}")
    if sorted(config["threshold_minutes"]) != [5, 10, 15]:
        raise ValueError("This study requires threshold_minutes to be [5, 10, 15].")
    return config

