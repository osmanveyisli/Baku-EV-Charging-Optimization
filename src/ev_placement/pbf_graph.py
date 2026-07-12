"""Build a local directed driving graph from a Geofabrik OSM PBF extract."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import geopandas as gpd
import networkx as nx
import osmium
import requests
from shapely import intersects_xy

from .config import ProjectPaths


ALLOWED_HIGHWAYS = {
    "motorway",
    "motorway_link",
    "trunk",
    "trunk_link",
    "primary",
    "primary_link",
    "secondary",
    "secondary_link",
    "tertiary",
    "tertiary_link",
    "unclassified",
    "residential",
    "living_street",
    "service",
    "road",
}

DEFAULT_SPEED_KPH = {
    "motorway": 90.0,
    "motorway_link": 50.0,
    "trunk": 70.0,
    "trunk_link": 45.0,
    "primary": 60.0,
    "primary_link": 40.0,
    "secondary": 50.0,
    "secondary_link": 35.0,
    "tertiary": 40.0,
    "tertiary_link": 30.0,
    "unclassified": 35.0,
    "residential": 30.0,
    "living_street": 15.0,
    "service": 20.0,
    "road": 30.0,
}


def download_geofabrik_pbf(
    paths: ProjectPaths, config: dict[str, Any], refresh: bool = False
) -> Path:
    """Download and cache the daily Azerbaijan OSM PBF extract."""
    destination = paths.raw / "azerbaijan-latest.osm.pbf"
    metadata_path = paths.raw / "azerbaijan-latest.osm.pbf.metadata.json"
    if destination.exists() and destination.stat().st_size > 1_000_000 and not refresh:
        return destination
    partial = destination.with_suffix(destination.suffix + ".part")
    response = requests.get(
        config["geofabrik_pbf_url"],
        stream=True,
        timeout=600,
        headers={"User-Agent": config["nominatim_user_agent"]},
    )
    response.raise_for_status()
    with partial.open("wb") as handle:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                handle.write(chunk)
    partial.replace(destination)
    metadata_path.write_text(
        json.dumps(
            {
                "url": config["geofabrik_pbf_url"],
                "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
                "last_modified": response.headers.get("Last-Modified"),
                "etag": response.headers.get("ETag"),
                "content_length_bytes": destination.stat().st_size,
                "license": "OpenStreetMap ODbL 1.0; distributed by Geofabrik",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return destination


def _haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    radius = 6_371_009.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * radius * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _parse_speed(value: str | None, highway: str) -> float:
    fallback = DEFAULT_SPEED_KPH.get(highway, 30.0)
    if not value:
        return fallback
    first = value.split(";")[0].strip().lower()
    match = re.search(r"(\d+(?:\.\d+)?)", first)
    if not match:
        return fallback
    speed = float(match.group(1))
    if "mph" in first:
        speed *= 1.609344
    return min(max(speed, 5.0), 130.0)


class _DriveGraphHandler(osmium.SimpleHandler):
    def __init__(self, query_geometry) -> None:
        super().__init__()
        self.query_geometry = query_geometry
        self.graph = nx.MultiDiGraph(crs="EPSG:4326")
        self._inside: dict[int, bool] = {}

    def _node_inside(self, node) -> bool:
        node_id = int(node.ref)
        cached = self._inside.get(node_id)
        if cached is not None:
            return cached
        inside = bool(
            intersects_xy(
                self.query_geometry,
                float(node.location.lon),
                float(node.location.lat),
            )
        )
        self._inside[node_id] = inside
        return inside

    def way(self, way) -> None:
        tags = {tag.k: tag.v for tag in way.tags}
        highway = tags.get("highway")
        if highway not in ALLOWED_HIGHWAYS:
            return
        if tags.get("area") == "yes":
            return
        if tags.get("access") in {"no", "private"}:
            return
        if tags.get("motor_vehicle") == "no" or tags.get("motorcar") == "no":
            return
        if highway == "service" and tags.get("service") in {
            "driveway",
            "parking_aisle",
            "private",
            "emergency_access",
        }:
            return
        nodes = list(way.nodes)
        if len(nodes) < 2:
            return
        try:
            coordinates = [
                (
                    int(node.ref),
                    float(node.location.lon),
                    float(node.location.lat),
                    self._node_inside(node),
                )
                for node in nodes
            ]
        except osmium.InvalidLocationError:
            return

        speed = _parse_speed(tags.get("maxspeed"), highway)
        oneway_value = tags.get("oneway", "").lower()
        reverse_only = oneway_value == "-1"
        is_oneway = reverse_only or oneway_value in {"yes", "1", "true"}
        if tags.get("junction") == "roundabout":
            is_oneway = True
        for first, second in zip(coordinates[:-1], coordinates[1:], strict=True):
            first_id, first_lon, first_lat, first_inside = first
            second_id, second_lon, second_lat, second_inside = second
            if not (first_inside or second_inside):
                continue
            length = _haversine_m(first_lon, first_lat, second_lon, second_lat)
            if length <= 0:
                continue
            travel_time = length / (speed / 3.6)
            self.graph.add_node(first_id, x=first_lon, y=first_lat)
            self.graph.add_node(second_id, x=second_lon, y=second_lat)
            attributes = {
                "osmid": int(way.id),
                "highway": highway,
                "length": length,
                "speed_kph": speed,
                "travel_time": travel_time,
                "name": tags.get("name", ""),
            }
            if reverse_only:
                self.graph.add_edge(second_id, first_id, **attributes)
            else:
                self.graph.add_edge(first_id, second_id, **attributes)
                if not is_oneway:
                    self.graph.add_edge(second_id, first_id, **attributes)


def build_drive_graph_from_pbf(
    pbf_path: Path,
    study_area: gpd.GeoDataFrame,
    config: dict[str, Any],
):
    """Extract motorcar-accessible ways in a buffered Baku study polygon."""
    local = study_area.to_crs(config["local_crs"])
    query_geometry = gpd.GeoSeries(
        [
            local.geometry.union_all()
            .buffer(float(config["road_network_buffer_m"]))
            .simplify(100)
        ],
        crs=config["local_crs"],
    ).to_crs("EPSG:4326").iloc[0]
    handler = _DriveGraphHandler(query_geometry)
    handler.apply_file(str(pbf_path), locations=True, idx="flex_mem")
    graph = handler.graph
    if graph.number_of_nodes() == 0 or graph.number_of_edges() == 0:
        raise RuntimeError("The Geofabrik PBF produced an empty Baku driving graph.")
    return graph
