"""Write geospatial products, interactive maps, figures, and QA summaries."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import folium
import geopandas as gpd
import matplotlib
import numpy as np
import pandas as pd
from branca.colormap import linear
from folium.plugins import Fullscreen, MeasureControl, MiniMap

from .config import ProjectPaths

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


BAND_COLORS = {
    "le_5": "#1a9850",
    "5_10": "#91cf60",
    "10_15": "#fee08b",
    "gt_15": "#d73027",
    "unreachable": "#542788",
}
BAND_LABELS = {
    "le_5": "≤5 minutes",
    "5_10": ">5–10 minutes",
    "10_15": ">10–15 minutes",
    "gt_15": ">15 minutes",
    "unreachable": "No directed route",
}


def _safe_geojson_frame(frame: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    result = frame.to_crs("EPSG:4326").copy()
    for column in result.columns:
        if column == result.geometry.name:
            continue
        if pd.api.types.is_float_dtype(result[column]):
            result[column] = result[column].replace([np.inf, -np.inf], np.nan)
        elif result[column].map(lambda value: isinstance(value, np.generic)).any():
            result[column] = result[column].map(
                lambda value: value.item() if isinstance(value, np.generic) else value
            )
    return result


def write_data_products(
    paths: ProjectPaths,
    districts: gpd.GeoDataFrame,
    chargers: gpd.GeoDataFrame,
    grid: gpd.GeoDataFrame,
    candidates: gpd.GeoDataFrame,
    recommended: gpd.GeoDataFrame,
    blind_spots: gpd.GeoDataFrame,
    city_coverage: pd.DataFrame,
    district_stats: pd.DataFrame,
    scenario_summary: pd.DataFrame,
    blind_stats: pd.DataFrame,
    config: dict[str, Any],
) -> None:
    """Write the canonical CSV and GeoJSON products documented by the project."""
    districts_out = districts.copy()
    districts_out["district_id"] = (
        districts_out["district"].str.lower().str.replace(r"\W+", "_", regex=True)
    )
    districts_out["source_date"] = config["analysis_date"]
    districts_out["area_km2"] = districts_out.to_crs(config["local_crs"]).geometry.area / 1e6
    _safe_geojson_frame(districts_out).to_file(
        paths.outputs / "districts.geojson", driver="GeoJSON"
    )
    _safe_geojson_frame(chargers).to_file(
        paths.outputs / "existing_chargers.geojson", driver="GeoJSON"
    )

    grid_columns = [
        "cell_id",
        "grid_row",
        "grid_col",
        "district",
        "area_km2",
        "population_est",
        "snap_dist_m",
        "baseline_min",
        "after_min",
        "baseline_band",
        "after_band",
        "geometry",
    ]
    _safe_geojson_frame(grid[grid_columns]).to_file(
        paths.outputs / "grid_accessibility.geojson", driver="GeoJSON"
    )
    _safe_geojson_frame(candidates).to_file(
        paths.outputs / "candidate_sites.geojson", driver="GeoJSON"
    )
    _safe_geojson_frame(recommended).to_file(
        paths.outputs / "recommended_sites.geojson", driver="GeoJSON"
    )
    if not blind_spots.empty:
        _safe_geojson_frame(blind_spots).to_file(
            paths.outputs / "blind_spots.geojson", driver="GeoJSON"
        )
    else:
        (paths.outputs / "blind_spots.geojson").write_text(
            json.dumps({"type": "FeatureCollection", "features": []}),
            encoding="utf-8",
        )

    city_coverage.to_csv(paths.tables / "city_coverage.csv", index=False, encoding="utf-8-sig")
    district_stats.to_csv(
        paths.tables / "district_statistics.csv", index=False, encoding="utf-8-sig"
    )
    scenario_summary.to_csv(
        paths.tables / "scenario_summary.csv", index=False, encoding="utf-8-sig"
    )
    blind_stats.to_csv(
        paths.tables / "blind_spot_statistics.csv", index=False, encoding="utf-8-sig"
    )
    recommended.drop(columns="geometry").to_csv(
        paths.tables / "recommended_sites.csv", index=False, encoding="utf-8-sig"
    )


def _map_center(study_area: gpd.GeoDataFrame) -> list[float]:
    point = study_area.to_crs("EPSG:4326").geometry.union_all().representative_point()
    return [point.y, point.x]


def _base_map(study_area: gpd.GeoDataFrame, zoom_start: int = 9) -> folium.Map:
    map_object = folium.Map(
        location=_map_center(study_area),
        zoom_start=zoom_start,
        tiles="CartoDB positron",
        control_scale=True,
        prefer_canvas=True,
    )
    folium.TileLayer("OpenStreetMap", name="OpenStreetMap").add_to(map_object)
    Fullscreen(position="topright").add_to(map_object)
    MeasureControl(position="bottomleft", primary_length_unit="kilometers").add_to(map_object)
    MiniMap(toggle_display=True).add_to(map_object)
    return map_object


def _add_legend(map_object: folium.Map, title: str = "Nearest charger drive time") -> None:
    items = "".join(
        f'<div><span style="display:inline-block;width:12px;height:12px;'
        f'background:{BAND_COLORS[key]};margin-right:6px"></span>{label}</div>'
        for key, label in BAND_LABELS.items()
    )
    html = f"""
    <div style="position:fixed;bottom:28px;right:12px;z-index:9999;background:white;
      border:1px solid #777;border-radius:4px;padding:8px 10px;font:12px Arial;
      line-height:1.45;box-shadow:0 1px 5px rgba(0,0,0,.25)">
      <strong>{title}</strong>{items}
    </div>
    """
    map_object.get_root().html.add_child(folium.Element(html))


def _grid_style(feature: dict[str, Any], field: str) -> dict[str, Any]:
    band = feature["properties"].get(field, "unreachable")
    return {
        "fillColor": BAND_COLORS.get(band, BAND_COLORS["unreachable"]),
        "color": "#555555",
        "weight": 0.25,
        "fillOpacity": 0.62,
    }


def _add_grid_layer(
    map_object: folium.Map,
    grid: gpd.GeoDataFrame,
    band_field: str,
    name: str,
    show: bool,
) -> None:
    minute_field = "baseline_min" if band_field == "baseline_band" else "after_min"
    data = _safe_geojson_frame(
        grid[
            [
                "cell_id",
                "district",
                "area_km2",
                "population_est",
                minute_field,
                band_field,
                "geometry",
            ]
        ]
    )
    folium.GeoJson(
        data=data.__geo_interface__,
        name=name,
        show=show,
        style_function=lambda feature: _grid_style(feature, band_field),
        highlight_function=lambda _: {"weight": 1.5, "color": "#111111"},
        tooltip=folium.GeoJsonTooltip(
            fields=["cell_id", "district", minute_field, "area_km2", "population_est"],
            aliases=["Cell", "District", "Drive time (min)", "Land (km²)", "Population est."],
            localize=True,
            sticky=False,
        ),
    ).add_to(map_object)


def _add_district_layer(
    map_object: folium.Map,
    districts: gpd.GeoDataFrame,
    district_stats: pd.DataFrame,
) -> None:
    merged = districts.merge(district_stats, on="district", how="left")
    values = merged["baseline_population_coverage_15_pct"].fillna(0)
    colormap = linear.YlOrRd_09.scale(0, max(100, float(values.max())))
    colormap.caption = "Baseline population within 15 minutes (%)"
    folium.GeoJson(
        data=_safe_geojson_frame(merged).__geo_interface__,
        name="District accessibility",
        show=True,
        style_function=lambda feature: {
            "fillColor": colormap(
                feature["properties"].get("baseline_population_coverage_15_pct") or 0
            ),
            "fillOpacity": 0.26,
            "color": "#252525",
            "weight": 1.1,
        },
        tooltip=folium.GeoJsonTooltip(
            fields=[
                "district",
                "existing_chargers",
                "baseline_coverage_15_pct",
                "baseline_population_coverage_15_pct",
                "after_population_coverage_15_pct",
            ],
            aliases=[
                "District",
                "Existing chargers",
                "Baseline land ≤15 min (%)",
                "Baseline population ≤15 min (%)",
                "After population ≤15 min (%)",
            ],
            localize=True,
        ),
    ).add_to(map_object)
    colormap.add_to(map_object)


def _add_site_layers(
    map_object: folium.Map,
    chargers: gpd.GeoDataFrame,
    recommended: gpd.GeoDataFrame,
) -> None:
    existing_layer = folium.FeatureGroup(name="Existing chargers", show=True)
    for row in chargers.to_crs("EPSG:4326").itertuples():
        popup = (
            f"<b>{row.name}</b><br>Access: {row.access_class}<br>"
            f"District: {getattr(row, 'district', '')}<br>OSM: {row.osm_element}"
        )
        folium.CircleMarker(
            location=[row.geometry.y, row.geometry.x],
            radius=5,
            color="#08519c",
            fill=True,
            fill_color="#3182bd",
            fill_opacity=0.9,
            popup=folium.Popup(popup, max_width=320),
            tooltip=f"Existing: {row.name}",
        ).add_to(existing_layer)
    existing_layer.add_to(map_object)

    proposed_layer = folium.FeatureGroup(name="Recommended new sites", show=True)
    for row in recommended.sort_values("deployment_rank").to_crs("EPSG:4326").itertuples():
        popup = (
            f"<b>#{row.deployment_rank}: {row.name}</b><br>Type: {row.site_type}<br>"
            f"District: {row.district}<br>New population ≤15 min: "
            f"{row.marginal_population_15_est:,.0f}<br>Field status: {row.field_status}"
        )
        folium.Marker(
            location=[row.geometry.y, row.geometry.x],
            icon=folium.Icon(color="red", icon="bolt", prefix="fa"),
            popup=folium.Popup(popup, max_width=340),
            tooltip=f"Recommendation #{row.deployment_rank}: {row.name}",
        ).add_to(proposed_layer)
    proposed_layer.add_to(map_object)


def make_interactive_maps(
    paths: ProjectPaths,
    study_area: gpd.GeoDataFrame,
    districts: gpd.GeoDataFrame,
    chargers: gpd.GeoDataFrame,
    grid: gpd.GeoDataFrame,
    recommended: gpd.GeoDataFrame,
    blind_spots: gpd.GeoDataFrame,
    district_stats: pd.DataFrame,
) -> None:
    """Create the requested combined, coverage, and blind-spot HTML maps."""
    combined = _base_map(study_area)
    _add_district_layer(combined, districts, district_stats)
    _add_grid_layer(combined, grid, "baseline_band", "Baseline travel-time grid", True)
    _add_grid_layer(combined, grid, "after_band", "After recommendations", False)
    if not blind_spots.empty:
        folium.GeoJson(
            _safe_geojson_frame(blind_spots).__geo_interface__,
            name="Baseline blind spots (>15 min)",
            show=False,
            style_function=lambda _: {
                "fillColor": "#d73027",
                "color": "#7f0000",
                "weight": 1,
                "fillOpacity": 0.35,
            },
            tooltip=folium.GeoJsonTooltip(
                fields=["blind_zone_id", "primary_district", "area_km2"],
                aliases=["Zone", "Primary district", "Land area (km²)"],
                localize=True,
            ),
        ).add_to(combined)
    _add_site_layers(combined, chargers, recommended)
    _add_legend(combined)
    folium.LayerControl(collapsed=False).add_to(combined)
    combined.save(paths.maps / "interactive_map.html")

    coverage = _base_map(study_area)
    _add_grid_layer(coverage, grid, "baseline_band", "Baseline coverage heatmap", True)
    _add_site_layers(coverage, chargers, recommended.iloc[0:0].copy())
    _add_legend(coverage, "Baseline network drive time")
    folium.LayerControl(collapsed=False).add_to(coverage)
    coverage.save(paths.maps / "coverage_heatmap.html")

    blind_map = _base_map(study_area)
    if not blind_spots.empty:
        folium.GeoJson(
            _safe_geojson_frame(blind_spots).__geo_interface__,
            name="Ranked blind spots",
            style_function=lambda _: {
                "fillColor": "#d73027",
                "color": "#7f0000",
                "weight": 1.2,
                "fillOpacity": 0.48,
            },
            tooltip=folium.GeoJsonTooltip(
                fields=[
                    "blind_zone_id",
                    "primary_district",
                    "area_km2",
                    "mean_excess_min_capped30",
                ],
                aliases=["Zone", "District", "Area (km²)", "Mean excess (min)"],
                localize=True,
            ),
        ).add_to(blind_map)
    _add_site_layers(blind_map, chargers, recommended)
    folium.LayerControl(collapsed=False).add_to(blind_map)
    blind_map.save(paths.maps / "blind_spot_map.html")


def make_figures(
    paths: ProjectPaths,
    city_coverage: pd.DataFrame,
    district_stats: pd.DataFrame,
    scenario_summary: pd.DataFrame,
    districts: gpd.GeoDataFrame,
    chargers: gpd.GeoDataFrame,
    recommended: gpd.GeoDataFrame,
    blind_spots: gpd.GeoDataFrame,
    config: dict[str, Any],
) -> None:
    """Write report-ready static charts and maps."""
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 9,
            "axes.titlesize": 11,
            "axes.labelsize": 9,
        }
    )
    baseline = city_coverage.iloc[0]
    after = city_coverage.iloc[-1]

    fig, axes = plt.subplots(1, 2, figsize=(10, 4), constrained_layout=True)
    x = np.arange(3)
    width = 0.35
    labels = ["5 min", "10 min", "15 min"]
    for axis, prefix, title in (
        (axes[0], "", "Land-area coverage"),
        (axes[1], "population_", "Population-weighted coverage"),
    ):
        before_values = [baseline[f"{prefix}coverage_{m}_pct"] for m in (5, 10, 15)]
        after_values = [after[f"{prefix}coverage_{m}_pct"] for m in (5, 10, 15)]
        axis.bar(x - width / 2, before_values, width, label="Baseline", color="#636363")
        axis.bar(x + width / 2, after_values, width, label="After", color="#2b8cbe")
        axis.set_xticks(x, labels)
        axis.set_ylim(0, 100)
        axis.set_ylabel("Coverage (%)")
        axis.set_title(title)
        axis.grid(axis="y", alpha=0.25)
        for xpos, value in zip(x - width / 2, before_values, strict=True):
            axis.text(xpos, value + 1.2, f"{value:.1f}", ha="center", fontsize=8)
        for xpos, value in zip(x + width / 2, after_values, strict=True):
            axis.text(xpos, value + 1.2, f"{value:.1f}", ha="center", fontsize=8)
    axes[1].legend(loc="lower right")
    fig.savefig(paths.figures / "coverage_before_after.png", dpi=220)
    plt.close(fig)

    ordered = district_stats.sort_values("baseline_population_coverage_15_pct")
    fig, ax = plt.subplots(figsize=(8, 5), constrained_layout=True)
    y = np.arange(len(ordered))
    ax.barh(
        y - 0.18,
        ordered["baseline_population_coverage_15_pct"],
        0.36,
        label="Baseline",
        color="#636363",
    )
    ax.barh(
        y + 0.18,
        ordered["after_population_coverage_15_pct"],
        0.36,
        label="After",
        color="#2b8cbe",
    )
    ax.set_yticks(y, ordered["district"])
    ax.set_xlim(0, 100)
    ax.set_xlabel("Population within 15 minutes (%)")
    ax.set_title("District accessibility before and after recommended sites")
    ax.grid(axis="x", alpha=0.25)
    ax.legend(loc="lower right")
    fig.savefig(paths.figures / "district_accessibility.png", dpi=220)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7.5, 4.2), constrained_layout=True)
    ax.plot(
        scenario_summary["chargers_added"],
        scenario_summary["coverage_15_pct"],
        marker="o",
        label="Land area",
        color="#636363",
    )
    ax.plot(
        scenario_summary["chargers_added"],
        scenario_summary["population_coverage_15_pct"],
        marker="s",
        label="Population",
        color="#2b8cbe",
    )
    ax.set_xlabel("New chargers")
    ax.set_ylabel("Coverage within 15 minutes (%)")
    ax.set_ylim(0, 100)
    ax.set_title("Optimal-site scenario curve")
    ax.grid(alpha=0.25)
    ax.legend()
    fig.savefig(paths.figures / "scenario_curve.png", dpi=220)
    plt.close(fig)

    local_crs = config["local_crs"]
    fig, ax = plt.subplots(figsize=(9, 5.2), constrained_layout=True)
    districts.to_crs(local_crs).plot(ax=ax, facecolor="#f0f0f0", edgecolor="#737373", linewidth=0.6)
    if not blind_spots.empty:
        blind_spots.to_crs(local_crs).plot(
            ax=ax, facecolor="#ef3b2c", edgecolor="#99000d", alpha=0.45, linewidth=0.5
        )
    chargers.to_crs(local_crs).plot(ax=ax, color="#2171b5", markersize=18, label="Existing")
    recommended.to_crs(local_crs).plot(
        ax=ax, color="#cb181d", marker="*", markersize=80, label="Recommended"
    )
    ax.set_axis_off()
    ax.set_title("Baseline blind spots and recommended new charging sites")
    ax.legend(loc="lower left")
    fig.savefig(paths.figures / "blind_spots_and_recommendations.png", dpi=220)
    plt.close(fig)


def write_qa_summary(paths: ProjectPaths, checks: list[str], notes: list[str]) -> Path:
    destination = paths.outputs / "quality_checks.json"
    payload = {"status": "passed", "checks": checks, "notes": notes}
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return destination

