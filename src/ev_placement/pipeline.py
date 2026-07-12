"""End-to-end reproducible Baku EV accessibility analysis pipeline."""

from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path
from typing import Any

import numpy as np

from .analysis import (
    add_access_bands,
    assign_districts_to_points,
    build_blind_spots,
    build_candidate_pool,
    build_scenario_summary,
    compute_baseline_times,
    compute_candidate_time_matrix,
    district_statistics,
    make_analysis_grid,
    order_selected_sites,
    snap_grid_to_graph,
    validate_results,
)
from .config import ProjectPaths, load_config
from .osm_data import (
    configure_osmnx,
    fetch_candidate_pois,
    fetch_district_boundaries,
    fetch_drive_graph,
    fetch_existing_chargers,
    fetch_natural_earth_land,
    prepare_study_area,
    write_source_manifest,
)
from .outputs import make_figures, make_interactive_maps, write_data_products, write_qa_summary
from .population import attach_population_to_grid, fetch_worldpop
from .report import build_html_report, build_markdown_report, build_pdf_report


def _log(message: str) -> None:
    print(f"[baku-ev] {message}", flush=True)


def _package_versions() -> dict[str, str]:
    packages = [
        "folium",
        "geopandas",
        "matplotlib",
        "networkx",
        "numpy",
        "osmnx",
        "pandas",
        "rasterio",
        "reportlab",
        "scikit-learn",
        "scipy",
        "shapely",
    ]
    versions: dict[str, str] = {}
    for package in packages:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "not installed"
    return versions


def run_pipeline(
    project_root: Path, refresh: bool = False, build_report: bool = True
) -> dict[str, Any]:
    """Run acquisition, routing, optimization, mapping, and reporting."""
    project_root = project_root.resolve()
    paths = ProjectPaths.from_root(project_root)
    paths.create()
    config = load_config(project_root)
    configure_osmnx(paths, config)

    _log("Loading official Baku district boundaries and land mask")
    districts_raw = fetch_district_boundaries(paths, config, refresh=refresh)
    land = fetch_natural_earth_land(paths, config, refresh=refresh)
    districts, study_area = prepare_study_area(
        districts_raw, land, paths, config
    )
    _log(
        f"Study area prepared: {len(districts)} districts, "
        f"{study_area.iloc[0]['land_area_km2']:.1f} km² land"
    )

    _log("Loading OSM charging stations and candidate host POIs")
    chargers = fetch_existing_chargers(
        study_area, paths, config, refresh=refresh
    )
    candidate_pois = fetch_candidate_pois(
        study_area, paths, config, refresh=refresh
    )
    chargers = assign_districts_to_points(chargers, districts)
    _log(f"Retained {len(chargers)} inclusive chargers and {len(candidate_pois)} feasible POIs")

    _log("Loading the directed OSM driving network (first run can take several minutes)")
    graph = fetch_drive_graph(study_area, paths, config, refresh=refresh)
    _log(f"Driving graph ready: {graph.number_of_nodes():,} nodes, {graph.number_of_edges():,} edges")
    manifest_path = write_source_manifest(
        paths, config, districts, chargers, candidate_pois, graph
    )

    _log("Building the land-clipped analysis grid and aggregating WorldPop 2026")
    grid = make_analysis_grid(districts, config)
    population_raster = fetch_worldpop(paths, config, refresh=refresh)
    grid = attach_population_to_grid(grid, population_raster)
    grid = snap_grid_to_graph(grid, graph, config)
    grid, chargers = compute_baseline_times(grid, chargers, graph, config)
    _log(
        f"Routed {len(grid):,} grid cells representing "
        f"{grid['population_est'].sum():,.0f} modeled residents"
    )

    _log("Generating a feasible candidate pool from underserved population clusters")
    candidates = build_candidate_pool(
        grid, candidate_pois, chargers, districts, config
    )
    candidates, candidate_times = compute_candidate_time_matrix(
        grid, candidates, graph, config
    )
    np.savez_compressed(
        paths.processed / "candidate_time_matrix.npz",
        candidate_ids=candidates["candidate_id"].to_numpy(dtype=str),
        cell_ids=grid["cell_id"].to_numpy(dtype=str),
        travel_seconds=candidate_times,
    )
    _log(f"Evaluated {len(candidates)} screened candidate sites on the directed network")

    land_weights = grid["area_km2"].to_numpy(dtype=float)
    population_weights = grid["population_est"].to_numpy(dtype=float)
    if population_weights.sum() <= 0:
        raise RuntimeError("WorldPop aggregation returned zero people in the Baku study area.")
    baseline_seconds = grid["baseline_sec"].to_numpy(dtype=float)
    _log("Solving population-weighted 1–10 site maximum-coverage scenarios")
    scenario_summary, selections, solver_statuses = build_scenario_summary(
        baseline_seconds,
        candidate_times,
        land_weights,
        population_weights,
        population_weights,
        candidates,
        config,
    )
    final_budget = min(int(config["recommendation_count"]), max(selections))
    final_selected = selections[final_budget]
    if not final_selected:
        raise RuntimeError("The final optimization scenario selected no sites.")
    ordered, _ = order_selected_sites(
        final_selected, baseline_seconds, candidate_times, population_weights
    )

    recommended = candidates.iloc[ordered].copy().reset_index(drop=True)
    current = baseline_seconds.copy()
    marginal_population: list[float] = []
    marginal_land: list[float] = []
    marginal_population_10: list[float] = []
    marginal_land_10: list[float] = []
    for candidate_index in ordered:
        candidate = candidate_times[candidate_index]
        marginal_population.append(
            float(population_weights[(current > 900) & (candidate <= 900)].sum())
        )
        marginal_land.append(
            float(land_weights[(current > 900) & (candidate <= 900)].sum())
        )
        marginal_population_10.append(
            float(population_weights[(current > 600) & (candidate <= 600)].sum())
        )
        marginal_land_10.append(
            float(land_weights[(current > 600) & (candidate <= 600)].sum())
        )
        current = np.minimum(current, candidate)

    recommended["deployment_rank"] = np.arange(1, len(recommended) + 1)
    recommended["marginal_population_15_est"] = marginal_population
    recommended["marginal_land_15_km2"] = marginal_land
    recommended["marginal_population_10_est"] = marginal_population_10
    recommended["marginal_land_10_km2"] = marginal_land_10
    recommended["longitude"] = recommended.geometry.x
    recommended["latitude"] = recommended.geometry.y
    recommended["osm_url"] = "https://www.openstreetmap.org/" + recommended["osm_element"]
    recommended["solver_scenario"] = f"optimal_{final_budget}"
    recommended["solver_status"] = solver_statuses[final_budget]

    grid["after_sec"] = current
    grid["after_min"] = current / 60
    grid = add_access_bands(grid)
    scenario_summary.loc[scenario_summary.index[-1], "scenario"] = "after_recommendations"
    city_coverage = scenario_summary.iloc[[0, -1]].copy().reset_index(drop=True)
    city_coverage.loc[0, "scenario"] = "baseline"
    city_coverage.loc[1, "scenario"] = "after"

    district_stats = district_statistics(grid, chargers, recommended)
    blind_spots, blind_stats = build_blind_spots(grid)
    checks = validate_results(grid, city_coverage, recommended, config)
    notes = [
        "Optimization objective is WorldPop-weighted; land coverage is reported separately.",
        "Driving times use static OpenStreetMap speeds and do not include live congestion.",
        "Unknown-access OSM stations are included; explicitly private/no stations are excluded.",
        "Recommended POIs require field, grid-capacity, land-control, safety, and permit review.",
    ]

    _log("Writing GeoJSON, CSV, maps, and report figures")
    write_data_products(
        paths,
        districts,
        chargers,
        grid,
        candidates,
        recommended,
        blind_spots,
        city_coverage,
        district_stats,
        scenario_summary,
        blind_stats,
        config,
    )
    make_figures(
        paths,
        city_coverage,
        district_stats,
        scenario_summary,
        districts,
        chargers,
        recommended,
        blind_spots,
        config,
    )
    make_interactive_maps(
        paths,
        study_area,
        districts,
        chargers,
        grid,
        recommended,
        blind_spots,
        district_stats,
    )
    write_qa_summary(paths, checks, notes)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        {
            "parameters": {
                key: config[key]
                for key in (
                    "grid_size_m",
                    "road_network_buffer_m",
                    "maximum_grid_snap_m",
                    "grid_snap_speed_kph",
                    "threshold_minutes",
                    "recommendation_count",
                    "candidate_clusters",
                    "candidate_search_radius_m",
                    "minimum_distance_from_existing_m",
                    "minimum_selected_spacing_m",
                    "random_seed",
                )
            },
            "population": {
                "source": "WorldPop 2026 constrained 100 m R2025A v1",
                "doi": config["worldpop_doi"],
                "estimated_population_in_grid": float(population_weights.sum()),
                "status": "calculated",
            },
            "optimization": {
                "basis": "WorldPop population; lexicographic 15/10/5-minute maximum coverage",
                "candidate_count": int(len(candidates)),
                "selected_count": int(len(recommended)),
                "solver_status_by_budget": solver_statuses,
                "selected_candidate_ids": recommended["candidate_id"].tolist(),
            },
            "package_versions": _package_versions(),
            "quality_checks": checks,
        }
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    report_paths: list[str] = []
    if build_report:
        _log("Building the PDF, Markdown, and print-HTML technical report")
        markdown_path = build_markdown_report(
            paths,
            config,
            city_coverage,
            district_stats,
            recommended,
            blind_stats,
        )
        html_path = build_html_report(paths, markdown_path)
        pdf_path = build_pdf_report(
            paths,
            config,
            city_coverage,
            district_stats,
            recommended,
            blind_stats,
            manifest,
            checks,
        )
        report_paths = [str(pdf_path), str(markdown_path), str(html_path)]

    _log("Analysis complete")
    return {
        "study_land_km2": float(grid["area_km2"].sum()),
        "population_est": float(population_weights.sum()),
        "existing_chargers": int(len(chargers)),
        "candidate_sites": int(len(candidates)),
        "recommended_sites": int(len(recommended)),
        "baseline_land_15_pct": float(city_coverage.iloc[0]["coverage_15_pct"]),
        "after_land_15_pct": float(city_coverage.iloc[1]["coverage_15_pct"]),
        "baseline_population_15_pct": float(
            city_coverage.iloc[0]["population_coverage_15_pct"]
        ),
        "after_population_15_pct": float(
            city_coverage.iloc[1]["population_coverage_15_pct"]
        ),
        "lowest_accessibility_district": str(district_stats.iloc[0]["district"]),
        "reports": report_paths,
    }

