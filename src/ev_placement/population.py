"""WorldPop acquisition and grid-level population aggregation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import rasterio
import requests
from rasterio.features import rasterize
from rasterio.windows import Window, from_bounds

from .config import ProjectPaths


def fetch_worldpop(
    paths: ProjectPaths, config: dict[str, Any], refresh: bool = False
) -> Path:
    """Download the 2026 WorldPop constrained 100 m population surface."""
    destination = paths.raw / "worldpop_aze_2026_100m_constrained.tif"
    if destination.exists() and not refresh:
        return destination
    response = requests.get(
        config["worldpop_2026_url"],
        timeout=600,
        headers={"User-Agent": config["nominatim_user_agent"]},
    )
    response.raise_for_status()
    destination.write_bytes(response.content)
    return destination


def attach_population_to_grid(
    grid: gpd.GeoDataFrame, raster_path: Path
) -> gpd.GeoDataFrame:
    """Sum people-per-pixel WorldPop values into the clipped analysis cells."""
    result = grid.copy()
    with rasterio.open(raster_path) as source:
        raster_grid = result.to_crs(source.crs)
        west, south, east, north = raster_grid.total_bounds
        raw_window = from_bounds(west, south, east, north, transform=source.transform)
        raw_window = raw_window.round_offsets().round_lengths()
        full_window = Window(0, 0, source.width, source.height)
        window = raw_window.intersection(full_window)
        values = source.read(1, window=window, masked=True).filled(0).astype(float)
        values[~np.isfinite(values)] = 0
        values[values < 0] = 0
        transform = source.window_transform(window)
        labels = rasterize(
            ((geometry, index + 1) for index, geometry in enumerate(raster_grid.geometry)),
            out_shape=values.shape,
            transform=transform,
            fill=0,
            dtype="int32",
            all_touched=False,
        )
    population = np.bincount(
        labels.ravel(), weights=values.ravel(), minlength=len(result) + 1
    )[1:]
    result["population_est"] = population
    return result

