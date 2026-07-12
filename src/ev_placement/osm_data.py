"""Acquire and prepare OpenStreetMap and land-mask inputs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import geopandas as gpd
import osmnx as ox
import pandas as pd
import requests
from shapely import make_valid
from shapely.geometry import Point

from .config import ProjectPaths
from .pbf_graph import build_drive_graph_from_pbf, download_geofabrik_pbf


WGS84 = "EPSG:4326"


def configure_osmnx(paths: ProjectPaths, config: dict[str, Any]) -> None:
    """Configure OSMnx to cache all web responses inside the project."""
    cache_dir = paths.raw / "osmnx_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    ox.settings.use_cache = True
    ox.settings.cache_folder = cache_dir
    ox.settings.log_console = True
    ox.settings.requests_timeout = 600
    ox.settings.nominatim_user_agent = config["nominatim_user_agent"]
    ox.settings.overpass_url = config["overpass_endpoints"][0].removesuffix("/interpreter")
    # Public status endpoints are often less reliable than the interpreter.
    # We already issue a bounded, cached research extraction, so skip status polling.
    ox.settings.overpass_rate_limit = False


def fetch_district_boundaries(
    paths: ProjectPaths, config: dict[str, Any], refresh: bool = False
) -> gpd.GeoDataFrame:
    """Fetch the official district polygons and attach frozen OSM crosswalk IDs."""
    destination = paths.raw / "districts_raw.geojson"
    if destination.exists() and not refresh:
        return gpd.read_file(destination).to_crs(WGS84)

    official_download = paths.raw / "azerbaijan_regions_official.geojson"
    if refresh or not official_download.exists():
        response = requests.get(
            config["official_boundaries_url"],
            timeout=300,
            headers={"User-Agent": config["nominatim_user_agent"]},
        )
        response.raise_for_status()
        official_download.write_bytes(response.content)
    source = gpd.read_file(official_download)
    if "PARENT_ID" not in source or "Name_AZ" not in source:
        raise RuntimeError(
            "The official boundary schema changed: PARENT_ID or Name_AZ is missing."
        )
    districts = source[source["PARENT_ID"].astype(str).eq("10")].copy()
    if len(districts) != 12:
        raise RuntimeError(
            f"Expected 12 Baku districts in the official dataset; found {len(districts)}."
        )

    districts["district"] = (
        districts["Name_AZ"]
        .astype(str)
        .str.strip()
        .str.replace(r"\s+(rayonu|r\.)$", "", regex=True)
    )
    relation_by_name = {
        record["name"]: int(record["osm_relation_id"])
        for record in config["districts"]
    }
    unexpected = sorted(set(districts["district"]) - set(relation_by_name))
    missing = sorted(set(relation_by_name) - set(districts["district"]))
    if unexpected or missing:
        raise RuntimeError(
            f"Official/OSM district crosswalk mismatch; unexpected={unexpected}, missing={missing}."
        )
    districts["osm_id"] = districts["district"].map(relation_by_name)
    districts["osm_element"] = "relation/" + districts["osm_id"].astype(str)
    districts["official_id"] = districts["ID"].astype(str) if "ID" in districts else None
    districts["source"] = "Azerbaijan IDDA Open Data Portal"
    districts["geometry"] = districts.geometry.map(make_valid)
    districts = districts[
        [
            "district",
            "official_id",
            "osm_id",
            "osm_element",
            "source",
            "geometry",
        ]
    ].to_crs(WGS84)
    districts.to_file(destination, driver="GeoJSON")
    return districts


def fetch_natural_earth_land(
    paths: ProjectPaths, config: dict[str, Any], refresh: bool = False
) -> gpd.GeoDataFrame:
    """Download the Natural Earth 1:10m land layer used to remove sea area."""
    archive = paths.raw / "ne_10m_land.zip"
    if refresh or not archive.exists():
        response = requests.get(
            config["natural_earth_land_url"],
            timeout=300,
            headers={"User-Agent": config["nominatim_user_agent"]},
        )
        response.raise_for_status()
        archive.write_bytes(response.content)
    land = gpd.read_file(f"zip://{archive}")
    return land.to_crs(WGS84)


def prepare_study_area(
    districts_raw: gpd.GeoDataFrame,
    land: gpd.GeoDataFrame,
    paths: ProjectPaths,
    config: dict[str, Any],
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Clip administrative districts to land and create the study-area union."""
    local_crs = config["local_crs"]
    raw_local = districts_raw.to_crs(local_crs).copy()
    minx, miny, maxx, maxy = districts_raw.total_bounds
    land_subset = land.cx[minx - 0.2 : maxx + 0.2, miny - 0.2 : maxy + 0.2]
    if land_subset.empty:
        raise RuntimeError("Natural Earth land mask does not overlap the Baku districts.")
    land_union = make_valid(land_subset.to_crs(local_crs).geometry.union_all())

    raw_local["geometry"] = raw_local.geometry.map(
        lambda geom: make_valid(geom).intersection(land_union)
    )
    raw_local = raw_local[~raw_local.geometry.is_empty].copy()
    raw_local["land_area_km2"] = raw_local.geometry.area / 1_000_000
    districts_land = raw_local.to_crs(WGS84)
    districts_land.to_file(paths.processed / "districts.geojson", driver="GeoJSON")

    study_geometry = make_valid(raw_local.geometry.union_all())
    study_area = gpd.GeoDataFrame(
        {
            "name": ["Baku study area (12 districts, land only)"],
            "analysis_date": [config["analysis_date"]],
            "land_area_km2": [study_geometry.area / 1_000_000],
        },
        geometry=[study_geometry],
        crs=local_crs,
    ).to_crs(WGS84)
    study_area.to_file(paths.processed / "study_area.geojson", driver="GeoJSON")
    return districts_land, study_area


def _study_bbox(study_area: gpd.GeoDataFrame, padding_degrees: float = 0.02) -> str:
    west, south, east, north = study_area.to_crs(WGS84).total_bounds
    return (
        f"{south - padding_degrees:.7f},{west - padding_degrees:.7f},"
        f"{north + padding_degrees:.7f},{east + padding_degrees:.7f}"
    )


def _overpass_request(
    query: str,
    destination: Path,
    config: dict[str, Any],
    refresh: bool,
) -> dict[str, Any]:
    if destination.exists() and not refresh:
        return json.loads(destination.read_text(encoding="utf-8"))

    errors: list[str] = []
    for endpoint in config["overpass_endpoints"]:
        try:
            response = requests.post(
                endpoint,
                data={"data": query},
                timeout=600,
                headers={"User-Agent": config["nominatim_user_agent"]},
            )
            response.raise_for_status()
            payload = response.json()
            payload["_download_metadata"] = {
                "endpoint": endpoint,
                "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
            }
            destination.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
            return payload
        except (requests.RequestException, ValueError) as exc:
            errors.append(f"{endpoint}: {exc}")
    raise RuntimeError("All Overpass endpoints failed: " + " | ".join(errors))


def _elements_to_points(elements: Iterable[dict[str, Any]]) -> gpd.GeoDataFrame:
    rows: list[dict[str, Any]] = []
    for element in elements:
        if element.get("type") == "node":
            lon, lat = element.get("lon"), element.get("lat")
        else:
            center = element.get("center", {})
            lon, lat = center.get("lon"), center.get("lat")
        if lon is None or lat is None:
            continue
        tags = element.get("tags", {})
        rows.append(
            {
                "osm_type": element.get("type"),
                "osm_id": int(element["id"]),
                "osm_element": f"{element.get('type')}/{element['id']}",
                "name": tags.get("name") or tags.get("brand") or tags.get("operator"),
                "amenity": tags.get("amenity"),
                "man_made": tags.get("man_made"),
                "shop": tags.get("shop"),
                "railway": tags.get("railway"),
                "public_transport": tags.get("public_transport"),
                "parking": tags.get("parking"),
                "access": tags.get("access"),
                "motorcar": tags.get("motorcar"),
                "operator": tags.get("operator"),
                "brand": tags.get("brand"),
                "capacity": tags.get("capacity"),
                "opening_hours": tags.get("opening_hours"),
                "fee": tags.get("fee"),
                "socket_type2": tags.get("socket:type2"),
                "socket_ccs": tags.get("socket:type2_combo"),
                "socket_chademo": tags.get("socket:chademo"),
                "raw_tags": json.dumps(tags, ensure_ascii=False, sort_keys=True),
                "geometry": Point(float(lon), float(lat)),
            }
        )
    if not rows:
        return gpd.GeoDataFrame(
            columns=["osm_type", "osm_id", "osm_element", "geometry"],
            geometry="geometry",
            crs=WGS84,
        )
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=WGS84)


def _filter_to_land(
    points: gpd.GeoDataFrame, study_area: gpd.GeoDataFrame
) -> gpd.GeoDataFrame:
    if points.empty:
        return points
    land_geometry = study_area.to_crs(WGS84).geometry.union_all()
    mask = points.geometry.map(land_geometry.covers)
    return points.loc[mask].copy()


def fetch_existing_chargers(
    study_area: gpd.GeoDataFrame,
    paths: ProjectPaths,
    config: dict[str, Any],
    refresh: bool = False,
) -> gpd.GeoDataFrame:
    """Fetch inclusive public/unknown EV charging sites and remove private sites."""
    bbox = _study_bbox(study_area)
    query = f"""
[out:json][timeout:300];
(
  nwr[\"amenity\"=\"charging_station\"]({bbox});
  nwr[\"man_made\"=\"charge_point\"]({bbox});
);
out center tags;
""".strip()
    payload = _overpass_request(
        query, paths.raw / "chargers_overpass.json", config, refresh
    )
    chargers = _filter_to_land(
        _elements_to_points(payload.get("elements", [])), study_area
    )
    if chargers.empty:
        raise RuntimeError("The Overpass query returned no EV chargers in the study area.")

    excluded_access = {"private", "no"}
    chargers = chargers[
        ~chargers["access"].fillna("").str.lower().isin(excluded_access)
        & ~chargers["motorcar"].fillna("").str.lower().isin(excluded_access)
    ].copy()
    chargers["access_class"] = "unknown"
    chargers.loc[
        chargers["access"].fillna("").str.lower().isin({"yes", "permissive"}),
        "access_class",
    ] = "confirmed_public"
    chargers.loc[
        chargers["access"].fillna("").str.lower().isin({"customers", "destination"}),
        "access_class",
    ] = "conditional"

    projected = chargers.to_crs(config["local_crs"])
    station_mask = chargers["amenity"].eq("charging_station").to_numpy()
    if station_mask.any():
        station_union = projected.loc[station_mask].geometry.buffer(75).union_all()
        duplicate_charge_points = (
            chargers["man_made"].eq("charge_point").to_numpy()
            & projected.geometry.map(station_union.covers).to_numpy()
        )
        chargers = chargers.loc[~duplicate_charge_points].copy()

    chargers["charger_id"] = [f"CHG-{i + 1:03d}" for i in range(len(chargers))]
    chargers["name"] = chargers["name"].fillna("Unnamed OSM charging station")
    chargers.to_file(paths.processed / "existing_chargers.geojson", driver="GeoJSON")
    return chargers


def fetch_candidate_pois(
    study_area: gpd.GeoDataFrame,
    paths: ProjectPaths,
    config: dict[str, Any],
    refresh: bool = False,
) -> gpd.GeoDataFrame:
    """Fetch plausible public vehicle-accessible candidate host sites."""
    bbox = _study_bbox(study_area)
    query = f"""
[out:json][timeout:300];
(
  nwr[\"amenity\"~\"^(parking|fuel|marketplace|community_centre|townhall|university)$\"]({bbox});
  nwr[\"shop\"~\"^(supermarket|mall|department_store)$\"]({bbox});
  nwr[\"railway\"=\"station\"]({bbox});
  nwr[\"public_transport\"=\"station\"]({bbox});
);
out center tags;
""".strip()
    payload = _overpass_request(
        query, paths.raw / "candidate_pois_overpass.json", config, refresh
    )
    candidates = _filter_to_land(
        _elements_to_points(payload.get("elements", [])), study_area
    )
    if candidates.empty:
        raise RuntimeError("The Overpass query returned no feasible-site POIs.")
    excluded_access = {"private", "no"}
    candidates = candidates[
        ~candidates["access"].fillna("").str.lower().isin(excluded_access)
        & ~candidates["motorcar"].fillna("").str.lower().isin(excluded_access)
    ].copy()

    def classify(row: pd.Series) -> str:
        if row.get("amenity") == "parking":
            return "parking"
        if row.get("amenity") == "fuel":
            return "fuel_station"
        if pd.notna(row.get("shop")):
            return str(row["shop"])
        if row.get("railway") == "station" or row.get("public_transport") == "station":
            return "transport_station"
        return str(row.get("amenity") or "public_facility")

    candidates["site_type"] = candidates.apply(classify, axis=1)
    candidates["name"] = candidates["name"].fillna(
        candidates["site_type"].str.replace("_", " ").str.title()
    )
    candidates["field_status"] = "desktop shortlist; access/grid capacity unverified"
    return candidates


def fetch_drive_graph(
    study_area: gpd.GeoDataFrame,
    paths: ProjectPaths,
    config: dict[str, Any],
    refresh: bool = False,
):
    """Build or load a directed drivable graph from the Geofabrik OSM extract."""
    destination = paths.raw / "baku_drive.graphml"
    if destination.exists() and not refresh:
        return ox.load_graphml(destination)
    pbf_path = download_geofabrik_pbf(paths, config, refresh=refresh)
    graph = build_drive_graph_from_pbf(pbf_path, study_area, config)
    ox.save_graphml(graph, destination)
    return graph


def write_source_manifest(
    paths: ProjectPaths,
    config: dict[str, Any],
    districts: gpd.GeoDataFrame,
    chargers: gpd.GeoDataFrame,
    candidate_pois: gpd.GeoDataFrame,
    graph,
) -> Path:
    """Record source identity, snapshot date, and core input counts."""
    manifest = {
        "analysis_date": config["analysis_date"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "license_and_attribution": {
            "openstreetmap": "© OpenStreetMap contributors; ODbL 1.0",
            "natural_earth": "Natural Earth; public domain",
            "official_boundaries": "Azerbaijan IDDA Open Data Portal; catalog declares Creative Commons Attribution",
            "worldpop": "WorldPop 2026 R2025A; DOI 10.5258/SOTON/WP00839",
        },
        "sources": {
            "districts": "Azerbaijan IDDA Open Data Portal; OSM relation IDs retained as crosswalk",
            "chargers": "OpenStreetMap via Overpass API",
            "candidate_pois": "OpenStreetMap via Overpass API",
            "road_network": "OpenStreetMap Azerbaijan PBF distributed by Geofabrik; local PyOsmium extraction",
            "land_mask": "Natural Earth 1:10m land",
        },
        "district_relations": [
            {"district": row.district, "osm_id": int(row.osm_id)}
            for row in districts.itertuples()
        ],
        "input_counts": {
            "districts": int(len(districts)),
            "chargers_inclusive": int(len(chargers)),
            "candidate_pois": int(len(candidate_pois)),
            "road_nodes": int(graph.number_of_nodes()),
            "road_edges": int(graph.number_of_edges()),
        },
        "method_scope": (
            "Static modeled driving times from OSM road classes/maxspeed; "
            "not live or congested travel times."
        ),
    }
    destination = paths.outputs / "run_manifest.json"
    destination.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return destination
