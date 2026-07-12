"""Generate the 6–10 page technical report in PDF, Markdown, and print HTML."""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .config import ProjectPaths


def _register_fonts() -> tuple[str, str]:
    candidates = [
        (
            Path("C:/Windows/Fonts/arial.ttf"),
            Path("C:/Windows/Fonts/arialbd.ttf"),
        ),
        (
            Path("C:/msys64/ucrt64/share/fonts/TTF/DejaVuSans.ttf"),
            Path("C:/msys64/ucrt64/share/fonts/TTF/DejaVuSans-Bold.ttf"),
        ),
    ]
    for regular, bold in candidates:
        if regular.exists() and bold.exists():
            pdfmetrics.registerFont(TTFont("ReportRegular", str(regular)))
            pdfmetrics.registerFont(TTFont("ReportBold", str(bold)))
            return "ReportRegular", "ReportBold"
    return "Helvetica", "Helvetica-Bold"


def _styles():
    regular, bold = _register_fonts()
    base = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle(
            "ReportTitle",
            parent=base["Title"],
            fontName=bold,
            fontSize=20,
            leading=24,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#17365D"),
            spaceAfter=14,
        ),
        "subtitle": ParagraphStyle(
            "ReportSubtitle",
            parent=base["Normal"],
            fontName=regular,
            fontSize=11,
            leading=15,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#4D4D4D"),
            spaceAfter=18,
        ),
        "h1": ParagraphStyle(
            "ReportH1",
            parent=base["Heading1"],
            fontName=bold,
            fontSize=15,
            leading=18,
            textColor=colors.HexColor("#17365D"),
            spaceAfter=8,
        ),
        "h2": ParagraphStyle(
            "ReportH2",
            parent=base["Heading2"],
            fontName=bold,
            fontSize=11,
            leading=14,
            textColor=colors.HexColor("#2B579A"),
            spaceBefore=6,
            spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "ReportBody",
            parent=base["BodyText"],
            fontName=regular,
            fontSize=8.6,
            leading=11.2,
            alignment=TA_LEFT,
            spaceAfter=6,
        ),
        "small": ParagraphStyle(
            "ReportSmall",
            parent=base["BodyText"],
            fontName=regular,
            fontSize=7.3,
            leading=9.3,
            spaceAfter=3,
        ),
        "table": ParagraphStyle(
            "ReportTable",
            parent=base["BodyText"],
            fontName=regular,
            fontSize=7,
            leading=8.4,
        ),
        "table_header": ParagraphStyle(
            "ReportTableHeader",
            parent=base["BodyText"],
            fontName=bold,
            fontSize=7,
            leading=8.4,
            textColor=colors.white,
        ),
    }
    return styles, regular, bold


def _paragraph(text: str, style) -> Paragraph:
    return Paragraph(text, style)


def _table(
    rows: list[list[Any]],
    styles: dict[str, ParagraphStyle],
    widths: list[float] | None = None,
    repeat_rows: int = 1,
) -> Table:
    formatted: list[list[Any]] = []
    for row_index, row in enumerate(rows):
        style = styles["table_header"] if row_index == 0 else styles["table"]
        formatted.append(
            [cell if hasattr(cell, "wrap") else Paragraph(html.escape(str(cell)), style) for cell in row]
        )
    table = Table(formatted, colWidths=widths, repeatRows=repeat_rows, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2B579A")),
                ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#BDBDBD")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F3F6FA")]),
            ]
        )
    )
    return table


def _page_footer(canvas, document, regular_font: str) -> None:
    canvas.saveState()
    canvas.setFont(regular_font, 7)
    canvas.setFillColor(colors.HexColor("#666666"))
    canvas.drawString(1.7 * cm, 1.0 * cm, "Baku public EV charging accessibility study")
    canvas.drawRightString(19.3 * cm, 1.0 * cm, f"Page {document.page}")
    canvas.restoreState()


def _top_district_rows(district_stats: pd.DataFrame, count: int = 6) -> list[list[Any]]:
    rows = [["District", "Existing", "Baseline pop. ≤15", "After pop. ≤15", "Uplift"]]
    for row in district_stats.head(count).itertuples():
        rows.append(
            [
                row.district,
                row.existing_chargers,
                f"{row.baseline_population_coverage_15_pct:.1f}%",
                f"{row.after_population_coverage_15_pct:.1f}%",
                f"{row.population_improvement_15_pp:+.1f} pp",
            ]
        )
    return rows


def _recommended_rows(recommended: pd.DataFrame, count: int = 10) -> list[list[Any]]:
    rows = [["Rank", "Site", "Type", "District", "Lat", "Lon", "New pop. ≤15"]]
    for row in recommended.sort_values("deployment_rank").head(count).itertuples():
        rows.append(
            [
                row.deployment_rank,
                row.name,
                row.site_type,
                row.district,
                f"{row.latitude:.5f}",
                f"{row.longitude:.5f}",
                f"{row.marginal_population_15_est:,.0f}",
            ]
        )
    return rows


def build_pdf_report(
    paths: ProjectPaths,
    config: dict[str, Any],
    city_coverage: pd.DataFrame,
    district_stats: pd.DataFrame,
    recommended: pd.DataFrame,
    blind_stats: pd.DataFrame,
    manifest: dict[str, Any],
    qa_checks: list[str],
) -> Path:
    """Build a deliberately paginated eight-section technical PDF report."""
    destination = paths.reports / "baku_ev_charging_technical_report.pdf"
    styles, regular_font, _ = _styles()
    doc = SimpleDocTemplate(
        str(destination),
        pagesize=A4,
        rightMargin=1.65 * cm,
        leftMargin=1.65 * cm,
        topMargin=1.45 * cm,
        bottomMargin=1.45 * cm,
        title=config["project_title"],
        author="Reproducible geospatial analysis",
    )
    baseline = city_coverage.iloc[0]
    after = city_coverage.iloc[-1]
    added = int(after["chargers_added"])
    worst = district_stats.iloc[0]
    story: list[Any] = []

    # Page 1 — title and executive summary.
    story.extend(
        [
            Spacer(1, 1.1 * cm),
            _paragraph("Optimizing Public EV Charging Infrastructure in Baku", styles["title"]),
            _paragraph(
                "A geospatial coverage and network-based site-selection analysis",
                styles["subtitle"],
            ),
            _paragraph(
                f"Technical report · analysis snapshot {config['analysis_date']} · "
                "OpenStreetMap road and charger data, official district polygons, and WorldPop 2026",
                styles["subtitle"],
            ),
            _paragraph("Executive summary", styles["h1"]),
            _paragraph(
                "This study evaluates how quickly locations across Baku's 12 land-clipped "
                "administrative districts can reach an inclusive set of public or access-unknown "
                "OpenStreetMap EV charging stations by car. Directed shortest paths are calculated "
                "on a static OSM driving graph. A population-weighted maximum-coverage model then "
                f"selects {added} screening-feasible host sites from mapped parking, fuel, retail, "
                "transport, and public-facility points of interest.",
                styles["body"],
            ),
            _table(
                [
                    ["Indicator", "Baseline", f"After {added} sites", "Change"],
                    [
                        "Land within 15 minutes",
                        f"{baseline.coverage_15_pct:.1f}%",
                        f"{after.coverage_15_pct:.1f}%",
                        f"{after.coverage_15_pct - baseline.coverage_15_pct:+.1f} pp",
                    ],
                    [
                        "Population within 15 minutes",
                        f"{baseline.population_coverage_15_pct:.1f}%",
                        f"{after.population_coverage_15_pct:.1f}%",
                        f"{after.population_coverage_15_pct - baseline.population_coverage_15_pct:+.1f} pp",
                    ],
                    [
                        "Population within 10 minutes",
                        f"{baseline.population_coverage_10_pct:.1f}%",
                        f"{after.population_coverage_10_pct:.1f}%",
                        f"{after.population_coverage_10_pct - baseline.population_coverage_10_pct:+.1f} pp",
                    ],
                    [
                        "Population-weighted p90 time (capped 30 min)",
                        f"{baseline.population_p90_min_capped30:.1f} min",
                        f"{after.population_p90_min_capped30:.1f} min",
                        f"{after.population_p90_min_capped30 - baseline.population_p90_min_capped30:+.1f} min",
                    ],
                ],
                styles,
                widths=[6.8 * cm, 3.3 * cm, 3.4 * cm, 3.2 * cm],
            ),
            Spacer(1, 0.25 * cm),
            _paragraph(
                f"The lowest baseline population accessibility is observed in {worst.district} "
                f"({worst.baseline_population_coverage_15_pct:.1f}% within 15 minutes). "
                "The recommended coordinates are planning shortlists, not construction approvals; "
                "grid capacity, land control, traffic access, safety, permits, and charger demand "
                "must be validated in the field.",
                styles["body"],
            ),
            _paragraph(
                "Interpretation note: land percentages answer 'how much territory'; WorldPop "
                "percentages answer 'how much modelled resident population.' Neither measures "
                "charger uptime, queues, connector compatibility, charging power, or live congestion.",
                styles["small"],
            ),
            PageBreak(),
        ]
    )

    # Page 2 — data and study area.
    story.extend(
        [
            _paragraph("1. Study area and data", styles["h1"]),
            _paragraph(
                "The study frame is the union of all 12 Baku district polygons published by "
                "Azerbaijan's official IDDA Open Data Portal, intersected with the Natural Earth "
                "1:10m land layer so Caspian Sea territory is not counted in the denominator. The "
                "fixed OSM relation IDs remain in the manifest as an independent crosswalk.",
                styles["body"],
            ),
            _table(
                [
                    ["Input", "Source and role", "Snapshot / license note"],
                    [
                        "District boundaries",
                        "Azerbaijan IDDA official Regions of Azerbaijan GeoJSON; 12 features with PARENT_ID=10",
                        "Portal updated 2025; catalog declares Creative Commons Attribution",
                    ],
                    [
                        "Existing chargers",
                        "OSM amenity=charging_station and man_made=charge_point via Overpass; private/no excluded",
                        "ODbL 1.0; inclusive unknown-access scenario",
                    ],
                    [
                        "Driving network",
                        "OSM motorcar-accessible directed graph via OSMnx; edge speed from maxspeed/road-class defaults",
                        "Static model, not live traffic",
                    ],
                    [
                        "Population",
                        "WorldPop 2026 constrained 100 m people-per-pixel raster; aggregated to analysis cells",
                        "R2025A alpha; DOI 10.5258/SOTON/WP00839",
                    ],
                    [
                        "Land mask",
                        "Natural Earth 1:10m land polygons",
                        "Public domain",
                    ],
                ],
                styles,
                widths=[3.1 * cm, 8.3 * cm, 5.4 * cm],
            ),
            _paragraph("Station inclusion and data quality", styles["h2"]),
            _paragraph(
                f"The inclusive baseline retains {manifest['input_counts']['chargers_inclusive']} "
                "OSM station sites after spatial clipping, explicit private/no filtering, and "
                "deduplication of standalone charge-point objects near mapped charging-station sites. "
                "Missing access tags are treated as potentially public and identified as unknown, "
                "not asserted to be public. OSM records are incomplete and should be cross-checked "
                "against operators before policy use.",
                styles["body"],
            ),
            Image(str(paths.figures / "blind_spots_and_recommendations.png"), width=16.5 * cm, height=9.5 * cm),
            _paragraph(
                "Figure 1. Baku land-clipped districts, baseline >15-minute zones, existing chargers, and recommended sites.",
                styles["small"],
            ),
            PageBreak(),
        ]
    )

    # Page 3 — methods.
    story.extend(
        [
            _paragraph("2. Analytical method", styles["h1"]),
            _paragraph("Accessibility surface", styles["h2"]),
            _paragraph(
                f"Baku land is partitioned into {config['grid_size_m']} m equal-area squares and "
                "clipped to district land. Each cell's representative point is snapped to the nearest "
                "drivable graph node; points farther than the configured snap tolerance are unreachable. "
                "An access penalty converts the snap distance at 30 km/h. Because a driver travels from "
                "an origin to a charger, shortest paths are computed from all charger nodes on the "
                "reversed directed graph. This respects one-way streets.",
                styles["body"],
            ),
            _paragraph(
                "For cell i and station set S, nearest time is mᵢ=min(tᵢₛ). Land coverage at threshold "
                "h is 100×Σ aᵢ·I(mᵢ≤h)/Σaᵢ. Population coverage replaces cell land aᵢ with WorldPop "
                "population pᵢ. Thresholds are 300, 600, and 900 seconds. Infinite times remain "
                "unreachable; only mean/quantile fields explicitly named capped30 cap values at 30 minutes.",
                styles["body"],
            ),
            _paragraph("Candidate sites and optimization", styles["h2"]),
            _paragraph(
                "Candidate hosts come from OSM parking, fuel, supermarket/mall, transport-station, "
                "university, town-hall, community-centre, and marketplace features. Explicit private/no "
                "sites and POIs too near an existing charger are removed. Weighted K-means summarizes "
                "the >15-minute cells; the nearest feasible POIs to cluster centres form a bounded "
                "candidate pool. Candidate coverage is calculated with reverse cutoff Dijkstra, never "
                "straight-line buffers.",
                styles["body"],
            ),
            _paragraph(
                "A binary maximum-coverage location model selects up to p sites. The objective is "
                "lexicographic: maximize newly covered WorldPop population at 15 minutes, then 10, then "
                "5. Pairwise constraints enforce the minimum site spacing. SciPy's MILP/HiGHS solver is "
                "used; a documented greedy fallback is available. Scenarios p=1…10 are solved independently. "
                "The final p-site set is ordered by marginal 15/10/5-minute population gain to give an "
                "implementable deployment sequence.",
                styles["body"],
            ),
            _paragraph("Quality controls", styles["h2"]),
            _paragraph(
                "Automated checks require C5≤C10≤C15; after-times never exceed baseline times; grid "
                "geometries are valid; recommended coordinates are unique; and minimum spacing is met. "
                "All API responses, inputs, parameters, package versions, selected IDs, and solver status "
                "are retained in the reproducibility package.",
                styles["body"],
            ),
            _table(
                [["Model parameter", "Value"]]
                + [
                    ["Grid", f"{config['grid_size_m']} m, clipped to land"],
                    ["Time thresholds", "5, 10, 15 minutes"],
                    ["Population objective", "WorldPop 2026 constrained 100 m"],
                    ["Candidate clusters", config["candidate_clusters"]],
                    ["Minimum selected spacing", f"{config['minimum_selected_spacing_m']} m"],
                    ["Recommendation budget", config["recommendation_count"]],
                    ["Random seed", config["random_seed"]],
                ],
                styles,
                widths=[6.5 * cm, 10.0 * cm],
            ),
            PageBreak(),
        ]
    )

    # Page 4 — baseline.
    story.extend(
        [
            _paragraph("3. Baseline accessibility", styles["h1"]),
            _paragraph(
                f"At baseline, {baseline.coverage_5_pct:.1f}%, {baseline.coverage_10_pct:.1f}%, "
                f"and {baseline.coverage_15_pct:.1f}% of analyzed land is within 5, 10, and 15 minutes "
                f"of a charger. The corresponding WorldPop-weighted shares are "
                f"{baseline.population_coverage_5_pct:.1f}%, {baseline.population_coverage_10_pct:.1f}%, "
                f"and {baseline.population_coverage_15_pct:.1f}%. The difference between land and "
                "population results shows why a single coverage percentage can mislead: Baku includes "
                "large industrial, coastal, and low-density areas.",
                styles["body"],
            ),
            Image(str(paths.figures / "coverage_before_after.png"), width=16.8 * cm, height=6.7 * cm),
            _paragraph(
                "Figure 2. Land-area and population-weighted coverage at the three policy thresholds.",
                styles["small"],
            ),
            _paragraph("Lowest-accessibility districts", styles["h2"]),
            _table(
                _top_district_rows(district_stats, 7),
                styles,
                widths=[3.3 * cm, 2.1 * cm, 3.8 * cm, 3.8 * cm, 3.0 * cm],
            ),
            _paragraph(
                "Districts are ranked by ascending baseline population coverage within 15 minutes, "
                "with land coverage used as a secondary indicator. Charger counts are site records, not "
                "connector or plug counts; a district with one high-capacity hub and a district with one "
                "single-port charger both show one site.",
                styles["body"],
            ),
            PageBreak(),
        ]
    )

    # Page 5 — equity/blind spots.
    top_blind = blind_stats.head(8)
    blind_rows = [["Zone", "Primary district", "Area km²", "Mean excess min", "Severity score"]]
    for row in top_blind.itertuples():
        blind_rows.append(
            [
                row.blind_zone_id,
                row.primary_district,
                f"{row.area_km2:.1f}",
                f"{row.mean_excess_min_capped30:.1f}",
                f"{row.severity_area_score:.1f}",
            ]
        )
    story.extend(
        [
            _paragraph("4. Spatial equity and blind spots", styles["h1"]),
            Image(str(paths.figures / "district_accessibility.png"), width=16.2 * cm, height=10.1 * cm),
            _paragraph(
                "Figure 3. Population-weighted 15-minute accessibility by district before and after recommendations.",
                styles["small"],
            ),
            _paragraph(
                "A blind spot is a contiguous set of grid cells with a modeled nearest-station time "
                "above 15 minutes or no directed route. Zones are ranked by land area multiplied by "
                "mean excess travel time (capped at 30 minutes). This ranking identifies large, severe "
                "territorial gaps; the optimization itself uses population weights.",
                styles["body"],
            ),
            _table(
                blind_rows,
                styles,
                widths=[2.4 * cm, 4.0 * cm, 2.8 * cm, 3.2 * cm, 3.4 * cm],
            ),
            PageBreak(),
        ]
    )

    # Page 6 — recommendations.
    story.extend(
        [
            _paragraph("5. Recommended new charging locations", styles["h1"]),
            _paragraph(
                f"The final {added}-site solution maximizes population-weighted coverage within the "
                "screened OSM candidate pool and spacing constraints. The ranking below is the recommended "
                "deployment order inside that final optimal set, not proof that every site is buildable. "
                "Marginal population is the additional WorldPop estimate brought within 15 minutes at the "
                "step when the site is added.",
                styles["body"],
            ),
            _table(
                _recommended_rows(recommended, 10),
                styles,
                widths=[1.0 * cm, 4.0 * cm, 2.3 * cm, 2.5 * cm, 2.1 * cm, 2.1 * cm, 2.4 * cm],
            ),
            _paragraph("Implementation screening sequence", styles["h2"]),
            _paragraph(
                "1) Confirm that each mapped POI is genuinely public or contractable and has safe 24/7 "
                "vehicle circulation. 2) Request utility hosting-capacity and connection-cost screening. "
                "3) Verify land ownership, parking-bay control, disability access, drainage, lighting, "
                "fire safety, and permits. 4) Validate connector mix and power against the expected trip "
                "market. 5) Re-run network access with field-confirmed sites and budget/cost constraints.",
                styles["body"],
            ),
            Image(str(paths.figures / "blind_spots_and_recommendations.png"), width=16.3 * cm, height=8.6 * cm),
            _paragraph(
                "Figure 4. Geographic distribution of the final recommendation shortlist.",
                styles["small"],
            ),
            PageBreak(),
        ]
    )

    # Page 7 — impact.
    story.extend(
        [
            _paragraph("6. Expected improvement", styles["h1"]),
            _paragraph(
                f"With all {added} proposed sites, modeled 15-minute population accessibility rises "
                f"from {baseline.population_coverage_15_pct:.1f}% to "
                f"{after.population_coverage_15_pct:.1f}% "
                f"({after.population_coverage_15_pct - baseline.population_coverage_15_pct:+.1f} "
                "percentage points). Land-area coverage rises from "
                f"{baseline.coverage_15_pct:.1f}% to {after.coverage_15_pct:.1f}% "
                f"({after.coverage_15_pct - baseline.coverage_15_pct:+.1f} points). "
                "All figures are recomputed from the minimum network time after adding sites, so "
                "overlapping service areas are not double-counted.",
                styles["body"],
            ),
            Image(str(paths.figures / "scenario_curve.png"), width=15.5 * cm, height=8.7 * cm),
            _paragraph(
                "Figure 5. Independent optimal scenarios for successive site budgets. Sets need not be nested.",
                styles["small"],
            ),
            _table(
                [
                    ["Measure", "Baseline", "After", "Change"],
                    [
                        "Land ≤5 min",
                        f"{baseline.coverage_5_pct:.1f}%",
                        f"{after.coverage_5_pct:.1f}%",
                        f"{after.coverage_5_pct - baseline.coverage_5_pct:+.1f} pp",
                    ],
                    [
                        "Land ≤10 min",
                        f"{baseline.coverage_10_pct:.1f}%",
                        f"{after.coverage_10_pct:.1f}%",
                        f"{after.coverage_10_pct - baseline.coverage_10_pct:+.1f} pp",
                    ],
                    [
                        "Population ≤5 min",
                        f"{baseline.population_coverage_5_pct:.1f}%",
                        f"{after.population_coverage_5_pct:.1f}%",
                        f"{after.population_coverage_5_pct - baseline.population_coverage_5_pct:+.1f} pp",
                    ],
                    [
                        "Population ≤10 min",
                        f"{baseline.population_coverage_10_pct:.1f}%",
                        f"{after.population_coverage_10_pct:.1f}%",
                        f"{after.population_coverage_10_pct - baseline.population_coverage_10_pct:+.1f} pp",
                    ],
                    [
                        "Population mean time (capped 30)",
                        f"{baseline.population_mean_min_capped30:.1f} min",
                        f"{after.population_mean_min_capped30:.1f} min",
                        f"{after.population_mean_min_capped30 - baseline.population_mean_min_capped30:+.1f} min",
                    ],
                ],
                styles,
                widths=[7.0 * cm, 3.1 * cm, 3.1 * cm, 3.1 * cm],
            ),
            PageBreak(),
        ]
    )

    # Page 8 — limitations, conclusions, references.
    story.extend(
        [
            _paragraph("7. Limitations and conclusions", styles["h1"]),
            _paragraph("Key limitations", styles["h2"]),
            _paragraph(
                "• OSM may omit stations, retain closed sites, misclassify device chargers, or lack "
                "access, connector, power, capacity, hours, fee, and operator tags. The inclusive "
                "baseline is a reproducible scenario, not a verified operator inventory.<br/>"
                "• OSM road speeds are static and largely free-flow; Baku congestion, incidents, "
                "turn delays, queues, and parking search are not observed.<br/>"
                "• WorldPop R2025A is a modeled alpha population surface. It does not represent EV "
                "ownership, jobs, commuting, visitors, freight, or destination charging demand.<br/>"
                "• A 750 m grid, road snapping, land-mask resolution, and district boundaries introduce "
                "scale and positional error. Border areas may use chargers outside the study boundary.<br/>"
                "• Maximum coverage does not test grid capacity, capital/operating cost, land tenure, "
                "permitting, reliability, utilization, power, or connector compatibility. Recommended "
                "POIs require technical and commercial feasibility studies.",
                styles["body"],
            ),
            _paragraph("Conclusion", styles["h2"]),
            _paragraph(
                "The analysis establishes a transparent baseline, identifies district and contiguous "
                "network blind spots, and converts those gaps into a field-screening shortlist. Its main "
                "value is comparative: it shows where a fixed number of sites provides the largest "
                "modeled accessibility gain under one consistent road, population, and boundary snapshot. "
                "Before procurement, the inclusive station inventory should be operator-verified, live or "
                "historical congestion should be tested, and the optimization should be repeated with "
                "site costs, electrical hosting capacity, forecast EV demand, and an explicit district "
                "equity constraint.",
                styles["body"],
            ),
            _paragraph("Automated verification", styles["h2"]),
            _paragraph("; ".join(qa_checks) + ".", styles["small"]),
            _paragraph("References and data links", styles["h2"]),
            _paragraph(
                "Azerbaijan IDDA Open Data Portal, Regions of Azerbaijan (official district polygons): "
                "https://opendata.az/en/@azerbaycan-respublikasinin-ekologiya-ve-tebii-servetler-nazirliyi/azerbaycanin-rayonlari<br/>"
                "OpenStreetMap contributors, ODbL 1.0: https://www.openstreetmap.org/copyright<br/>"
                "OpenStreetMap charging-station tagging: https://wiki.openstreetmap.org/wiki/Tag:amenity=charging_station<br/>"
                "WorldPop, Azerbaijan 2026 constrained 100 m population, DOI 10.5258/SOTON/WP00839: "
                "https://hub.worldpop.org/geodata/summary?id=72400<br/>"
                "Natural Earth 1:10m land: https://www.naturalearthdata.com/<br/>"
                "OSMnx: https://osmnx.readthedocs.io/ · SciPy MILP: https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.milp.html",
                styles["small"],
            ),
            _paragraph(
                "Attribution: © OpenStreetMap contributors. OSM-derived data are provided under the "
                "Open Database License. Natural Earth is public domain. Consult the source manifest for "
                "the official-boundary and WorldPop attribution/licensing notes.",
                styles["small"],
            ),
        ]
    )

    doc.build(
        story,
        onFirstPage=lambda canvas, document: _page_footer(canvas, document, regular_font),
        onLaterPages=lambda canvas, document: _page_footer(canvas, document, regular_font),
    )
    return destination


def build_markdown_report(
    paths: ProjectPaths,
    config: dict[str, Any],
    city_coverage: pd.DataFrame,
    district_stats: pd.DataFrame,
    recommended: pd.DataFrame,
    blind_stats: pd.DataFrame,
) -> Path:
    """Write an accessible text version of the technical report."""
    baseline = city_coverage.iloc[0]
    after = city_coverage.iloc[-1]
    added = int(after["chargers_added"])
    district_rows = "\n".join(
        f"| {row.district} | {row.existing_chargers} | "
        f"{row.baseline_population_coverage_15_pct:.1f}% | "
        f"{row.after_population_coverage_15_pct:.1f}% | "
        f"{row.population_improvement_15_pp:+.1f} pp |"
        for row in district_stats.itertuples()
    )
    site_rows = "\n".join(
        f"| {row.deployment_rank} | {row.name} | {row.site_type} | {row.district} | "
        f"{row.latitude:.6f} | {row.longitude:.6f} | {row.marginal_population_15_est:,.0f} |"
        for row in recommended.sort_values("deployment_rank").itertuples()
    )
    blind_rows = "\n".join(
        f"| {row.blind_zone_id} | {row.primary_district} | {row.area_km2:.1f} | "
        f"{row.mean_excess_min_capped30:.1f} |"
        for row in blind_stats.head(10).itertuples()
    )
    content = f"""# Optimizing Public EV Charging Infrastructure in Baku

**Technical report — analysis snapshot {config['analysis_date']}**

## Executive summary

This study measures directed driving-network accessibility from a land-clipped 750 m grid to an inclusive OpenStreetMap EV-charger inventory, then uses a population-weighted maximum-coverage model to select {added} new screening-feasible host sites. The study combines official Azerbaijan district polygons, OpenStreetMap roads/chargers/POIs, Natural Earth land, and WorldPop 2026.

- Baseline land coverage within 5/10/15 minutes: **{baseline.coverage_5_pct:.1f}% / {baseline.coverage_10_pct:.1f}% / {baseline.coverage_15_pct:.1f}%**.
- After land coverage within 5/10/15 minutes: **{after.coverage_5_pct:.1f}% / {after.coverage_10_pct:.1f}% / {after.coverage_15_pct:.1f}%**.
- Baseline population coverage within 5/10/15 minutes: **{baseline.population_coverage_5_pct:.1f}% / {baseline.population_coverage_10_pct:.1f}% / {baseline.population_coverage_15_pct:.1f}%**.
- After population coverage within 5/10/15 minutes: **{after.population_coverage_5_pct:.1f}% / {after.population_coverage_10_pct:.1f}% / {after.population_coverage_15_pct:.1f}%**.

Recommended coordinates are desktop planning shortlists, not construction approvals.

## 1. Study area and data

The study uses the 12 Baku district polygons from Azerbaijan's official IDDA Open Data Portal, clipped to Natural Earth land. Existing chargers and candidate POIs come from dated Overpass responses; the road graph is a directed OSMnx extraction. WorldPop 2026 constrained 100 m population is aggregated to grid cells. OSM station access is inclusive: explicit private/no sites are excluded, while missing access tags remain unknown.

## 2. Method

Each clipped grid fragment is represented by a point snapped to the nearest driveable OSM node. A reverse multi-source Dijkstra gives every origin's shortest directed time to a charger. Coverage is reported at 5, 10, and 15 minutes using both land area and WorldPop population as denominators. Candidate POIs near weighted K-means blind-spot clusters are evaluated with reverse cutoff Dijkstra. A binary SciPy/HiGHS model lexicographically maximizes population coverage at 15, then 10, then 5 minutes, subject to a {config['minimum_selected_spacing_m']} m spacing rule.

## 3. District accessibility

| District | Existing sites | Baseline population ≤15 min | After population ≤15 min | Uplift |
|---|---:|---:|---:|---:|
{district_rows}

![District accessibility](../outputs/figures/district_accessibility.png)

## 4. Blind spots

| Zone | Primary district | Area (km²) | Mean excess over 15 min |
|---|---|---:|---:|
{blind_rows}

![Blind spots and recommendations](../outputs/figures/blind_spots_and_recommendations.png)

## 5. Recommended new charger locations

| Rank | Site | Type | District | Latitude | Longitude | Newly covered population ≤15 min |
|---:|---|---|---|---:|---:|---:|
{site_rows}

## 6. Accessibility improvement

All after-values are recomputed as the minimum time to any existing or selected site, preventing overlap double-counting. With {added} new sites, population coverage within 15 minutes changes by **{after.population_coverage_15_pct - baseline.population_coverage_15_pct:+.1f} percentage points** and land coverage changes by **{after.coverage_15_pct - baseline.coverage_15_pct:+.1f} points**.

![Coverage before and after](../outputs/figures/coverage_before_after.png)

![Scenario curve](../outputs/figures/scenario_curve.png)

## 7. Limitations

OSM stations may be incomplete, stale, duplicated, or missing access/connector/power metadata. Static OSM speeds do not measure Baku congestion. WorldPop is a modelled alpha population surface, not EV demand. Grid size and snapping introduce approximation. The optimization does not include uptime, queues, land tenure, electrical hosting capacity, costs, permits, safety, charging power, or connector compatibility. Each recommendation therefore requires operator verification and field, utility, commercial, and engineering review.

## 8. Sources and attribution

- [Azerbaijan IDDA Open Data Portal — Regions of Azerbaijan](https://opendata.az/en/@azerbaycan-respublikasinin-ekologiya-ve-tebii-servetler-nazirliyi/azerbaycanin-rayonlari)
- [OpenStreetMap copyright and ODbL](https://www.openstreetmap.org/copyright) — © OpenStreetMap contributors
- [WorldPop Azerbaijan 2026](https://hub.worldpop.org/geodata/summary?id=72400), DOI `10.5258/SOTON/WP00839`
- [Natural Earth](https://www.naturalearthdata.com/)
- [OSMnx documentation](https://osmnx.readthedocs.io/)
- [SciPy MILP documentation](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.milp.html)
"""
    destination = paths.reports / "baku_ev_charging_technical_report.md"
    destination.write_text(content, encoding="utf-8")
    return destination


def build_html_report(paths: ProjectPaths, markdown_path: Path) -> Path:
    """Create a print-friendly HTML rendition with the same key report content."""
    # The Markdown remains the editable source. This compact transformation handles
    # the project headings, bullets, tables, emphasis, links, and images predictably.
    import re

    markdown = markdown_path.read_text(encoding="utf-8")
    lines = markdown.splitlines()
    output: list[str] = []
    in_table = False
    for line in lines:
        if line.startswith("|---"):
            continue
        if line.startswith("|"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if not in_table:
                output.append("<table><thead><tr>" + "".join(f"<th>{html.escape(cell)}</th>" for cell in cells) + "</tr></thead><tbody>")
                in_table = True
            else:
                output.append("<tr>" + "".join(f"<td>{html.escape(cell)}</td>" for cell in cells) + "</tr>")
            continue
        if in_table:
            output.append("</tbody></table>")
            in_table = False
        if not line:
            continue
        image_match = re.match(r"!\[(.*?)\]\((.*?)\)", line)
        if image_match:
            output.append(f'<figure><img src="{html.escape(image_match.group(2))}" alt="{html.escape(image_match.group(1))}"><figcaption>{html.escape(image_match.group(1))}</figcaption></figure>')
        elif line.startswith("# "):
            output.append(f"<h1>{html.escape(line[2:])}</h1>")
        elif line.startswith("## "):
            output.append(f"<h2>{html.escape(line[3:])}</h2>")
        elif line.startswith("- "):
            output.append(f"<p>• {html.escape(line[2:])}</p>")
        else:
            escaped = html.escape(line)
            escaped = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", escaped)
            escaped = re.sub(r"\[(.*?)\]\((https?://.*?)\)", r'<a href="\2">\1</a>', escaped)
            output.append(f"<p>{escaped}</p>")
    if in_table:
        output.append("</tbody></table>")
    document = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Baku EV Charging Technical Report</title>
<style>
@page{size:A4;margin:16mm}body{font:10.5pt/1.45 Arial,sans-serif;color:#222;max-width:180mm;margin:0 auto}
h1{color:#17365d;font-size:22pt;text-align:center;margin:20mm 0 8mm}h2{color:#17365d;font-size:15pt;break-before:page;margin-top:0}
p{margin:0 0 3mm}table{border-collapse:collapse;width:100%;font-size:8.5pt;margin:4mm 0 7mm}th{background:#2b579a;color:white;text-align:left}
th,td{border:.3pt solid #aaa;padding:2mm;vertical-align:top}tr:nth-child(even){background:#f3f6fa}figure{margin:5mm 0;text-align:center;break-inside:avoid}
img{max-width:100%;max-height:115mm}figcaption{font-size:8pt;color:#666}a{color:#1f5a96}@media print{a{color:inherit;text-decoration:none}}
</style></head><body>""" + "\n".join(output) + "</body></html>"
    destination = paths.reports / "baku_ev_charging_technical_report.html"
    destination.write_text(document, encoding="utf-8")
    return destination

