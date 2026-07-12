"""Network accessibility, candidate generation, optimization, and statistics."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Iterable

import geopandas as gpd
import networkx as nx
import numpy as np
import osmnx as ox
import pandas as pd
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import lil_matrix
from scipy.spatial import cKDTree
from scipy.spatial.distance import cdist
from shapely.geometry import box
from sklearn.cluster import KMeans


def make_analysis_grid(
    districts: gpd.GeoDataFrame, config: dict[str, Any]
) -> gpd.GeoDataFrame:
    """Create a clipped equal-area square grid and assign each cell to a district."""
    local_crs = config["local_crs"]
    cell_size = float(config["grid_size_m"])
    minimum_area = float(config["minimum_cell_area_m2"])
    districts_local = districts.to_crs(local_crs)
    city = districts_local.geometry.union_all()
    minx, miny, maxx, maxy = city.bounds
    x0 = np.floor(minx / cell_size) * cell_size
    y0 = np.floor(miny / cell_size) * cell_size

    rows: list[dict[str, Any]] = []
    row_id = 0
    y = y0
    while y < maxy:
        col_id = 0
        x = x0
        while x < maxx:
            clipped = box(x, y, x + cell_size, y + cell_size).intersection(city)
            if not clipped.is_empty and clipped.area >= minimum_area:
                point = clipped.representative_point()
                rows.append(
                    {
                        "grid_row": row_id,
                        "grid_col": col_id,
                        "area_km2": clipped.area / 1_000_000,
                        "origin_x": point.x,
                        "origin_y": point.y,
                        "geometry": clipped,
                    }
                )
            x += cell_size
            col_id += 1
        y += cell_size
        row_id += 1

    grid = gpd.GeoDataFrame(rows, geometry="geometry", crs=local_crs)
    if grid.empty:
        raise RuntimeError("The configured grid produced no cells.")
    origins = gpd.GeoDataFrame(
        grid.drop(columns="geometry"),
        geometry=gpd.points_from_xy(grid["origin_x"], grid["origin_y"]),
        crs=local_crs,
    )
    joined = gpd.sjoin(
        origins,
        districts_local[["district", "geometry"]],
        how="left",
        predicate="intersects",
    )
    joined = joined[~joined.index.duplicated(keep="first")]
    grid["district"] = joined["district"].reindex(grid.index).to_numpy()
    if grid["district"].isna().any():
        missing = grid[grid["district"].isna()].index
        for idx in missing:
            point = origins.loc[idx, "geometry"]
            nearest_idx = districts_local.geometry.distance(point).idxmin()
            grid.loc[idx, "district"] = districts_local.loc[nearest_idx, "district"]
    grid = grid.reset_index(drop=True)
    grid["cell_id"] = [f"CELL-{i + 1:05d}" for i in range(len(grid))]
    return grid


def assign_districts_to_points(
    points: gpd.GeoDataFrame, districts: gpd.GeoDataFrame
) -> gpd.GeoDataFrame:
    """Add a district name to point features without duplicating boundary points."""
    result = points.copy()
    if result.empty:
        result["district"] = pd.Series(dtype="object")
        return result
    joined = gpd.sjoin(
        result,
        districts[["district", "geometry"]].to_crs(result.crs),
        how="left",
        predicate="intersects",
    )
    joined = joined[~joined.index.duplicated(keep="first")]
    result["district"] = joined["district"].reindex(result.index).to_numpy()
    return result


def snap_grid_to_graph(
    grid: gpd.GeoDataFrame, graph, config: dict[str, Any]
) -> gpd.GeoDataFrame:
    """Snap grid origins to the directed road graph and add an access-time penalty."""
    result = grid.copy()
    origins = gpd.GeoSeries(
        gpd.points_from_xy(result["origin_x"], result["origin_y"]),
        crs=result.crs,
    ).to_crs("EPSG:4326")
    nodes, distances = ox.distance.nearest_nodes(
        graph,
        X=origins.x.to_numpy(),
        Y=origins.y.to_numpy(),
        return_dist=True,
    )
    result["road_node"] = np.asarray(nodes, dtype=np.int64)
    result["snap_dist_m"] = np.asarray(distances, dtype=float)
    metres_per_second = float(config["grid_snap_speed_kph"]) / 3.6
    result["snap_penalty_sec"] = result["snap_dist_m"] / metres_per_second
    return result


def compute_baseline_times(
    grid: gpd.GeoDataFrame,
    chargers: gpd.GeoDataFrame,
    graph,
    config: dict[str, Any],
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Compute origin-to-nearest-charger times using reverse multi-source Dijkstra."""
    if chargers.empty:
        raise RuntimeError("At least one existing charger is required for baseline routing.")
    charger_result = chargers.copy()
    coords = charger_result.to_crs("EPSG:4326").geometry
    nodes, snap_distances = ox.distance.nearest_nodes(
        graph,
        X=coords.x.to_numpy(),
        Y=coords.y.to_numpy(),
        return_dist=True,
    )
    charger_result["road_node"] = np.asarray(nodes, dtype=np.int64)
    charger_result["snap_dist_m"] = np.asarray(snap_distances, dtype=float)
    usable = charger_result["snap_dist_m"] <= float(config["maximum_grid_snap_m"])
    source_nodes = sorted(set(charger_result.loc[usable, "road_node"].astype(int)))
    if not source_nodes:
        raise RuntimeError("No chargers could be snapped to the Baku driving graph.")

    reverse_graph = graph.reverse(copy=False)
    shortest = nx.multi_source_dijkstra_path_length(
        reverse_graph, source_nodes, weight="travel_time"
    )
    result = grid.copy()
    route_seconds = np.array(
        [shortest.get(int(node), np.inf) for node in result["road_node"]],
        dtype=float,
    )
    baseline = route_seconds + result["snap_penalty_sec"].to_numpy(dtype=float)
    baseline[result["snap_dist_m"].to_numpy() > float(config["maximum_grid_snap_m"])] = np.inf
    result["baseline_sec"] = baseline
    result["baseline_min"] = baseline / 60
    return result, charger_result


def _band(minutes: float) -> str:
    if not np.isfinite(minutes):
        return "unreachable"
    if minutes <= 5:
        return "le_5"
    if minutes <= 10:
        return "5_10"
    if minutes <= 15:
        return "10_15"
    return "gt_15"


def add_access_bands(grid: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    result = grid.copy()
    result["baseline_band"] = result["baseline_min"].map(_band)
    if "after_min" in result:
        result["after_band"] = result["after_min"].map(_band)
    return result


def build_candidate_pool(
    grid: gpd.GeoDataFrame,
    candidate_pois: gpd.GeoDataFrame,
    chargers: gpd.GeoDataFrame,
    districts: gpd.GeoDataFrame,
    config: dict[str, Any],
) -> gpd.GeoDataFrame:
    """Use weighted K-means to shortlist real OSM POIs near blind-spot clusters."""
    blind = grid[grid["baseline_sec"] > 15 * 60].copy()
    if blind.empty:
        raise RuntimeError("No 15-minute blind spots exist; new-site optimization is unnecessary.")

    local_crs = config["local_crs"]
    pois = candidate_pois.to_crs(local_crs).copy()
    existing = chargers.to_crs(local_crs)
    if not existing.empty:
        existing_union = existing.geometry.union_all()
        pois["existing_distance_m"] = pois.geometry.distance(existing_union)
        pois = pois[
            pois["existing_distance_m"]
            >= float(config["minimum_distance_from_existing_m"])
        ].copy()
    else:
        pois["existing_distance_m"] = np.inf
    if pois.empty:
        raise RuntimeError("All candidate POIs were removed by feasibility screening.")

    cluster_count = min(
        int(config["candidate_clusters"]), len(blind), len(pois)
    )
    model = KMeans(
        n_clusters=cluster_count,
        random_state=int(config["random_seed"]),
        n_init=20,
    )
    model.fit(
        blind[["origin_x", "origin_y"]].to_numpy(),
        sample_weight=blind["area_km2"].to_numpy(),
    )

    poi_xy = np.column_stack((pois.geometry.x.to_numpy(), pois.geometry.y.to_numpy()))
    tree = cKDTree(poi_xy)
    k = min(4, len(pois))
    distances, indices = tree.query(model.cluster_centers_, k=k)
    if k == 1:
        distances = np.asarray(distances)[:, None]
        indices = np.asarray(indices)[:, None]
    else:
        distances = np.asarray(distances)
        indices = np.asarray(indices)
    selected_indices: list[int] = []
    for distance_row, index_row in zip(distances, indices, strict=True):
        for distance, index in zip(
            np.atleast_1d(distance_row), np.atleast_1d(index_row), strict=True
        ):
            if float(distance) <= float(config["candidate_search_radius_m"]):
                selected_indices.append(int(index))
                break

    # Add feasible POIs nearest the most severe cells if cluster matches repeat.
    severity = np.minimum(blind["baseline_min"].to_numpy(), 30) - 15
    severe_order = np.argsort(-severity)
    desired = min(int(config["maximum_candidate_pool"]), len(pois))
    for grid_pos in severe_order:
        if len(set(selected_indices)) >= desired:
            break
        point = blind.iloc[int(grid_pos)][["origin_x", "origin_y"]].to_numpy(dtype=float)
        distance, index = tree.query(point, k=1)
        if float(distance) <= float(config["candidate_search_radius_m"]):
            selected_indices.append(int(index))

    unique_indices = list(dict.fromkeys(selected_indices))[:desired]
    if not unique_indices:
        raise RuntimeError("No candidate POIs are close enough to underserved demand cells.")
    candidates = pois.iloc[unique_indices].copy().reset_index(drop=True)
    candidates = candidates.to_crs("EPSG:4326")
    candidates["candidate_id"] = [f"CAND-{i + 1:03d}" for i in range(len(candidates))]
    candidates = assign_districts_to_points(candidates, districts)
    return candidates


def compute_candidate_time_matrix(
    grid: gpd.GeoDataFrame,
    candidates: gpd.GeoDataFrame,
    graph,
    config: dict[str, Any],
) -> tuple[gpd.GeoDataFrame, np.ndarray]:
    """Compute origin-to-candidate times up to the 15-minute policy threshold."""
    result = candidates.copy()
    coords = result.to_crs("EPSG:4326").geometry
    nodes, snap_distances = ox.distance.nearest_nodes(
        graph,
        X=coords.x.to_numpy(),
        Y=coords.y.to_numpy(),
        return_dist=True,
    )
    result["road_node"] = np.asarray(nodes, dtype=np.int64)
    result["snap_dist_m"] = np.asarray(snap_distances, dtype=float)

    node_to_rows: dict[int, list[int]] = defaultdict(list)
    for row_index, node in enumerate(grid["road_node"].astype(int)):
        node_to_rows[node].append(row_index)
    matrix = np.full((len(result), len(grid)), np.inf, dtype=np.float32)
    reverse_graph = graph.reverse(copy=False)
    max_route_seconds = 15 * 60
    access_penalty = grid["snap_penalty_sec"].to_numpy(dtype=float)
    max_snap = float(config["maximum_grid_snap_m"])

    for candidate_index, row in enumerate(result.itertuples()):
        if float(row.snap_dist_m) > max_snap:
            continue
        shortest = nx.single_source_dijkstra_path_length(
            reverse_graph,
            int(row.road_node),
            cutoff=max_route_seconds,
            weight="travel_time",
        )
        for road_node, route_seconds in shortest.items():
            for grid_index in node_to_rows.get(int(road_node), []):
                value = float(route_seconds) + float(access_penalty[grid_index])
                if value <= max_route_seconds:
                    matrix[candidate_index, grid_index] = value
    return result, matrix


def _spacing_pairs(candidates: gpd.GeoDataFrame, config: dict[str, Any]) -> list[tuple[int, int]]:
    local = candidates.to_crs(config["local_crs"])
    coords = np.column_stack((local.geometry.x, local.geometry.y))
    distances = cdist(coords, coords)
    spacing = float(config["minimum_selected_spacing_m"])
    return [
        (i, j)
        for i in range(len(candidates))
        for j in range(i + 1, len(candidates))
        if distances[i, j] < spacing
    ]


def solve_maximum_coverage(
    baseline_seconds: np.ndarray,
    candidate_times: np.ndarray,
    weights: np.ndarray,
    candidates: gpd.GeoDataFrame,
    site_count: int,
    config: dict[str, Any],
) -> tuple[list[int], str]:
    """Solve a lexicographic 15/10/5-minute maximum-coverage MILP."""
    candidate_count, cell_count = candidate_times.shape
    if candidate_count == 0 or site_count <= 0:
        return [], "not_run"
    site_count = min(site_count, candidate_count)
    thresholds = [15 * 60, 10 * 60, 5 * 60]
    total_weight_raw = float(weights.sum())
    if total_weight_raw <= 0:
        raise ValueError("Optimization weights must sum to a positive value.")
    # Normalization preserves the optimum and avoids poorly scaled MILP coefficients
    # when the objective weights are people rather than square kilometres.
    weights = weights / total_weight_raw * 1_000.0
    total_weight = float(weights.sum())
    multiplier = total_weight + 1.0
    objective_factors = [multiplier**2, multiplier, 1.0]

    uncovered_groups: list[tuple[int, np.ndarray, float]] = []
    z_count = 0
    for threshold, factor in zip(thresholds, objective_factors, strict=True):
        indices = np.flatnonzero(baseline_seconds > threshold)
        uncovered_groups.append((threshold, indices, factor))
        z_count += len(indices)

    variable_count = candidate_count + z_count
    spacing_pairs = _spacing_pairs(candidates, config)
    constraint_count = z_count + 1 + len(spacing_pairs)
    matrix = lil_matrix((constraint_count, variable_count), dtype=float)
    lower = np.full(constraint_count, -np.inf, dtype=float)
    upper = np.zeros(constraint_count, dtype=float)
    objective = np.zeros(variable_count, dtype=float)

    row_cursor = 0
    z_cursor = candidate_count
    for threshold, grid_indices, factor in uncovered_groups:
        for grid_index in grid_indices:
            matrix[row_cursor, z_cursor] = 1.0
            covering = np.flatnonzero(candidate_times[:, grid_index] <= threshold)
            if len(covering):
                matrix[row_cursor, covering] = -1.0
            objective[z_cursor] = -float(weights[grid_index]) * factor
            row_cursor += 1
            z_cursor += 1

    matrix[row_cursor, :candidate_count] = 1.0
    upper[row_cursor] = float(site_count)
    row_cursor += 1
    for first, second in spacing_pairs:
        matrix[row_cursor, first] = 1.0
        matrix[row_cursor, second] = 1.0
        upper[row_cursor] = 1.0
        row_cursor += 1

    result = milp(
        c=objective,
        integrality=np.ones(variable_count, dtype=np.int8),
        bounds=Bounds(np.zeros(variable_count), np.ones(variable_count)),
        constraints=LinearConstraint(matrix.tocsr(), lower, upper),
        options={"time_limit": 180, "mip_rel_gap": 0.0001},
    )
    if result.x is None:
        selected = _greedy_fallback(
            baseline_seconds, candidate_times, weights, site_count, candidates, config
        )
        return selected, f"greedy_fallback_after_milp_status_{result.status}"
    selected = np.flatnonzero(result.x[:candidate_count] > 0.5).tolist()
    return selected, f"scipy_milp_status_{result.status}"


def _greedy_fallback(
    baseline_seconds: np.ndarray,
    candidate_times: np.ndarray,
    weights: np.ndarray,
    site_count: int,
    candidates: gpd.GeoDataFrame,
    config: dict[str, Any],
) -> list[int]:
    current = baseline_seconds.copy()
    remaining = set(range(len(candidates)))
    selected: list[int] = []
    spacing_pairs = set(_spacing_pairs(candidates, config))
    for _ in range(site_count):
        best = None
        best_score = (-1.0, -1.0, -1.0)
        for index in remaining:
            if any(
                (min(index, chosen), max(index, chosen)) in spacing_pairs
                for chosen in selected
            ):
                continue
            candidate = candidate_times[index]
            score = tuple(
                float(weights[(current > threshold) & (candidate <= threshold)].sum())
                for threshold in (900, 600, 300)
            )
            if score > best_score:
                best, best_score = index, score
        if best is None or best_score[0] <= 0:
            break
        selected.append(best)
        remaining.remove(best)
        current = np.minimum(current, candidate_times[best])
    return selected


def order_selected_sites(
    selected: Iterable[int],
    baseline_seconds: np.ndarray,
    candidate_times: np.ndarray,
    weights: np.ndarray,
) -> tuple[list[int], list[dict[str, float]]]:
    """Create a nested deployment order within the optimal final site set."""
    current = baseline_seconds.copy()
    remaining = set(int(index) for index in selected)
    ordered: list[int] = []
    marginal: list[dict[str, float]] = []
    while remaining:
        scored: list[tuple[tuple[float, float, float], int]] = []
        for index in remaining:
            candidate = candidate_times[index]
            score = tuple(
                float(weights[(current > threshold) & (candidate <= threshold)].sum())
                for threshold in (900, 600, 300)
            )
            scored.append((score, index))
        score, chosen = max(scored)
        ordered.append(chosen)
        marginal.append(
            {
                "marginal_15min_weight": score[0],
                "marginal_10min_weight": score[1],
                "marginal_5min_weight": score[2],
            }
        )
        current = np.minimum(current, candidate_times[chosen])
        remaining.remove(chosen)
    return ordered, marginal


def weighted_quantile(values: np.ndarray, weights: np.ndarray, quantile: float) -> float:
    order = np.argsort(values)
    sorted_values = values[order]
    sorted_weights = weights[order]
    cumulative = np.cumsum(sorted_weights)
    if cumulative[-1] <= 0:
        return float("nan")
    return float(sorted_values[np.searchsorted(cumulative, quantile * cumulative[-1])])


def coverage_metrics(
    seconds: np.ndarray,
    weights: np.ndarray,
    scenario: str,
    chargers_added: int,
) -> dict[str, Any]:
    total = float(weights.sum())
    capped_minutes = np.minimum(seconds / 60, 30)
    capped_minutes[~np.isfinite(capped_minutes)] = 30
    record: dict[str, Any] = {
        "scenario": scenario,
        "chargers_added": int(chargers_added),
        "analyzed_land_km2": total,
    }
    for minutes in (5, 10, 15):
        covered = float(weights[seconds <= minutes * 60].sum())
        record[f"coverage_{minutes}_pct"] = 100 * covered / total if total else 0.0
        record[f"covered_{minutes}_km2"] = covered
    record["mean_min_capped30"] = float(np.average(capped_minutes, weights=weights))
    record["median_min_capped30"] = weighted_quantile(capped_minutes, weights, 0.5)
    record["p90_min_capped30"] = weighted_quantile(capped_minutes, weights, 0.9)
    record["unreachable_pct"] = (
        100 * float(weights[~np.isfinite(seconds)].sum()) / total if total else 0.0
    )
    return record


def dual_coverage_metrics(
    seconds: np.ndarray,
    land_weights: np.ndarray,
    population_weights: np.ndarray,
    scenario: str,
    chargers_added: int,
) -> dict[str, Any]:
    """Report both land-area and WorldPop-weighted accessibility."""
    record = coverage_metrics(
        seconds, land_weights, scenario=scenario, chargers_added=chargers_added
    )
    total_population = float(population_weights.sum())
    record["population_est_total"] = total_population
    if total_population <= 0:
        for minutes in (5, 10, 15):
            record[f"population_coverage_{minutes}_pct"] = np.nan
            record[f"covered_population_{minutes}_est"] = np.nan
        record["population_mean_min_capped30"] = np.nan
        record["population_median_min_capped30"] = np.nan
        record["population_p90_min_capped30"] = np.nan
        return record
    for minutes in (5, 10, 15):
        covered = float(population_weights[seconds <= minutes * 60].sum())
        record[f"population_coverage_{minutes}_pct"] = 100 * covered / total_population
        record[f"covered_population_{minutes}_est"] = covered
    capped_minutes = np.minimum(seconds / 60, 30)
    capped_minutes[~np.isfinite(capped_minutes)] = 30
    record["population_mean_min_capped30"] = float(
        np.average(capped_minutes, weights=population_weights)
    )
    record["population_median_min_capped30"] = weighted_quantile(
        capped_minutes, population_weights, 0.5
    )
    record["population_p90_min_capped30"] = weighted_quantile(
        capped_minutes, population_weights, 0.9
    )
    return record


def build_scenario_summary(
    baseline_seconds: np.ndarray,
    candidate_times: np.ndarray,
    land_weights: np.ndarray,
    population_weights: np.ndarray,
    objective_weights: np.ndarray,
    candidates: gpd.GeoDataFrame,
    config: dict[str, Any],
) -> tuple[pd.DataFrame, dict[int, list[int]], dict[int, str]]:
    """Solve independent optimum scenarios from 1 through the configured budget."""
    records = [
        dual_coverage_metrics(
            baseline_seconds, land_weights, population_weights, "baseline", 0
        )
    ]
    selections: dict[int, list[int]] = {0: []}
    statuses: dict[int, str] = {0: "baseline"}
    maximum = min(int(config["recommendation_count"]), len(candidates))
    for count in range(1, maximum + 1):
        selected, status = solve_maximum_coverage(
            baseline_seconds,
            candidate_times,
            objective_weights,
            candidates,
            count,
            config,
        )
        after = baseline_seconds.copy()
        if selected:
            after = np.minimum(after, np.min(candidate_times[selected], axis=0))
        record = dual_coverage_metrics(
            after,
            land_weights,
            population_weights,
            f"optimal_{count}",
            len(selected),
        )
        record["selected_candidate_ids"] = ";".join(
            candidates.iloc[selected]["candidate_id"].astype(str)
        )
        record["solver_status"] = status
        records.append(record)
        selections[count] = selected
        statuses[count] = status
    return pd.DataFrame(records), selections, statuses


def district_statistics(
    grid: gpd.GeoDataFrame,
    chargers: gpd.GeoDataFrame,
    recommended: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """Create the district baseline/after accessibility and uplift table."""
    records: list[dict[str, Any]] = []
    charger_counts = chargers["district"].value_counts(dropna=True)
    recommended_counts = recommended["district"].value_counts(dropna=True)
    for district, subset in grid.groupby("district", dropna=False):
        weights = subset["area_km2"].to_numpy(dtype=float)
        population = subset["population_est"].to_numpy(dtype=float)
        baseline = coverage_metrics(
            subset["baseline_sec"].to_numpy(dtype=float), weights, "baseline", 0
        )
        after = coverage_metrics(
            subset["after_sec"].to_numpy(dtype=float),
            weights,
            "after_recommendations",
            len(recommended),
        )
        record: dict[str, Any] = {
            "district": district,
            "land_area_km2": float(weights.sum()),
            "population_est": float(population.sum()),
            "existing_chargers": int(charger_counts.get(district, 0)),
            "recommended_chargers": int(recommended_counts.get(district, 0)),
        }
        for minutes in (5, 10, 15):
            before_value = baseline[f"coverage_{minutes}_pct"]
            after_value = after[f"coverage_{minutes}_pct"]
            record[f"baseline_coverage_{minutes}_pct"] = before_value
            record[f"after_coverage_{minutes}_pct"] = after_value
            record[f"improvement_{minutes}_pp"] = after_value - before_value
            population_total = float(population.sum())
            if population_total > 0:
                before_population = (
                    100
                    * float(population[subset["baseline_sec"].to_numpy() <= minutes * 60].sum())
                    / population_total
                )
                after_population = (
                    100
                    * float(population[subset["after_sec"].to_numpy() <= minutes * 60].sum())
                    / population_total
                )
            else:
                before_population = np.nan
                after_population = np.nan
            record[f"baseline_population_coverage_{minutes}_pct"] = before_population
            record[f"after_population_coverage_{minutes}_pct"] = after_population
            record[f"population_improvement_{minutes}_pp"] = (
                after_population - before_population
            )
        for field in (
            "mean_min_capped30",
            "median_min_capped30",
            "p90_min_capped30",
            "unreachable_pct",
        ):
            record[f"baseline_{field}"] = baseline[field]
            record[f"after_{field}"] = after[field]
        records.append(record)
    frame = pd.DataFrame(records)
    return frame.sort_values(
        ["baseline_population_coverage_15_pct", "baseline_coverage_15_pct"],
        ascending=[True, True],
    ).reset_index(drop=True)


def build_blind_spots(grid: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, pd.DataFrame]:
    """Dissolve contiguous baseline >15-minute cells into ranked blind-spot zones."""
    blind = grid[grid["baseline_sec"] > 15 * 60].copy()
    if blind.empty:
        empty = gpd.GeoDataFrame(
            columns=["blind_zone_id", "geometry"], geometry="geometry", crs=grid.crs
        )
        return empty, pd.DataFrame()
    positions = {
        (int(row.grid_row), int(row.grid_col)): index
        for index, row in blind.iterrows()
    }
    zone_by_index: dict[int, int] = {}
    zone = 0
    for index, row in blind.iterrows():
        if index in zone_by_index:
            continue
        zone += 1
        queue = deque([(int(row.grid_row), int(row.grid_col))])
        while queue:
            position = queue.popleft()
            cell_index = positions.get(position)
            if cell_index is None or cell_index in zone_by_index:
                continue
            zone_by_index[cell_index] = zone
            r, c = position
            queue.extend(((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)))
    blind["blind_zone_num"] = pd.Series(zone_by_index)
    blind["excess_min_capped30"] = np.minimum(blind["baseline_min"], 30) - 15

    records: list[dict[str, Any]] = []
    geometries = []
    for zone_num, subset in blind.groupby("blind_zone_num"):
        district_area = subset.groupby("district")["area_km2"].sum()
        primary_district = district_area.idxmax() if not district_area.empty else None
        area = float(subset["area_km2"].sum())
        severity = float(
            np.average(
                subset["excess_min_capped30"], weights=subset["area_km2"]
            )
        )
        records.append(
            {
                "blind_zone_num": int(zone_num),
                "primary_district": primary_district,
                "area_km2": area,
                "mean_excess_min_capped30": severity,
                "unreachable_area_km2": float(
                    subset.loc[~np.isfinite(subset["baseline_sec"]), "area_km2"].sum()
                ),
                "severity_area_score": area * severity,
            }
        )
        geometries.append(subset.geometry.union_all())
    zones = gpd.GeoDataFrame(records, geometry=geometries, crs=grid.crs)
    zones = zones.sort_values("severity_area_score", ascending=False).reset_index(drop=True)
    zones["blind_zone_id"] = [f"BLIND-{i + 1:03d}" for i in range(len(zones))]
    statistics = pd.DataFrame(zones.drop(columns="geometry"))
    return zones, statistics


def validate_results(
    grid: gpd.GeoDataFrame,
    city_coverage: pd.DataFrame,
    recommended: gpd.GeoDataFrame,
    config: dict[str, Any],
) -> list[str]:
    """Run invariant checks that protect the interpretation of results."""
    checks: list[str] = []
    for row in city_coverage.itertuples():
        if not (row.coverage_5_pct <= row.coverage_10_pct <= row.coverage_15_pct):
            raise AssertionError(f"Coverage thresholds are not monotonic for {row.scenario}.")
    checks.append("5/10/15-minute coverage is monotonic in every scenario")
    if np.any(grid["after_sec"].to_numpy() > grid["baseline_sec"].to_numpy()):
        raise AssertionError("Adding recommended sites reduced accessibility for a grid cell.")
    checks.append("recommended sites never increase a cell's nearest-station time")
    if recommended.geometry.duplicated().any():
        raise AssertionError("Recommended-site geometries contain duplicates.")
    checks.append("recommended-site geometries are unique")
    if len(recommended) > 1:
        coords = recommended.to_crs(config["local_crs"]).geometry
        xy = np.column_stack((coords.x, coords.y))
        pairwise = cdist(xy, xy)
        pairwise[pairwise == 0] = np.inf
        if pairwise.min() + 1e-6 < float(config["minimum_selected_spacing_m"]):
            raise AssertionError("Recommended sites violate the configured spacing rule.")
    checks.append("recommended sites satisfy the minimum spacing rule")
    if grid.geometry.is_empty.any() or (~grid.geometry.is_valid).any():
        raise AssertionError("Analysis-grid geometry is empty or invalid.")
    checks.append("analysis-grid geometries are non-empty and valid")
    return checks
