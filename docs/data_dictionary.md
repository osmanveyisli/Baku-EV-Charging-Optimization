# Data Dictionary

## 1. Conventions

All paths below are relative to the project output directory. CSV files are UTF-8 with a header row and comma delimiter. GeoJSON is RFC 7946-style WGS 84 longitude/latitude (EPSG:4326); geometry and field calculations are performed in EPSG:32639 before export.

General conventions:

- IDs are strings. OSM IDs remain strings to avoid integer-precision loss in downstream web mapping.
- Times ending in _min are minutes; distances ending in _m are metres; areas ending in _km2 are square kilometres.
- Percentages ending in _pct range from 0 to 100. A change ending in _pctpt is a percentage-point change.
- Floating-point results are stored at full calculation precision and rounded only for display.
- Missing numeric values are null in GeoJSON and blank in CSV, never zero unless zero is the measured value.
- Dates use ISO 8601 UTC, for example 2026-07-12T09:30:00Z.
- Booleans are true or false in GeoJSON and lowercase true or false in CSV.
- scenario is baseline or after in final comparison tables. The optimization trace may additionally use after_1, after_2, and so on.
- coverage_5_pct, coverage_10_pct, and coverage_15_pct are **land-area-weighted** percentages unless coverage_basis explicitly says otherwise.
- baseline_min and after_min are null only when no valid directed route exists or the sample point is unusable. A time above 30 minutes remains its actual value; it is capped only when computing fields named capped30.

Travel-time bands use these exact values:

| Value | Definition |
|---|---|
| le_5 | Time is less than or equal to 5 minutes |
| 5_10 | Time is greater than 5 and less than or equal to 10 minutes |
| 10_15 | Time is greater than 10 and less than or equal to 15 minutes |
| gt_15 | Time is finite and greater than 15 minutes |
| unreachable | No directed origin-to-charger route exists |

## 2. districts.geojson

One feature per official Azerbaijan IDDA Baku district polygon after Natural Earth land clipping. Geometry is Polygon or MultiPolygon. A frozen OSM relation ID is retained as a crosswalk.

| Field | Type | Nullable | Definition |
|---|---:|:---:|---|
| district_id | string | no | Stable normalized project ID, such as narimanov |
| district | string | no | Canonical district display name |
| official_id | string | no | Feature ID from the official IDDA source |
| osm_id | string | no | Frozen OSM relation crosswalk ID; not the geometry source |
| osm_element | string | no | OSM crosswalk element, such as relation/11827003 |
| source | string | no | Azerbaijan IDDA Open Data Portal |
| source_date | date string | no | Analysis snapshot date |
| area_km2 | number | no | District land area after Natural Earth land clipping |
| geometry | Polygon or MultiPolygon | no | Land-only district geometry |

Required checks: 12 unique osm_id values, 12 unique district_id values, valid non-empty geometries, and no material overlap.

## 3. existing_chargers.geojson

One feature per OSM charging-station element discovered before routing eligibility is applied. This preserves an audit trail for excluded records. Geometry is Point.

| Field | Type | Nullable | Definition |
|---|---:|:---:|---|
| charger_id | string | no | Stable project ID, normally osm_type plus osm_id |
| osm_type | string | no | node, way, or relation |
| osm_id | string | no | OSM element ID |
| name | string | yes | OSM name |
| operator | string | yes | OSM operator |
| access | string | yes | Raw OSM access value |
| public_status | string | no | public, unknown, customer_only, or non_public |
| capacity | integer | yes | OSM capacity or number of charging positions when parseable |
| socket_summary | string | yes | Normalized summary of available OSM socket tags |
| district | string | yes | District containing the source point |
| graph_node | string | yes | Snapped directed driving-graph node ID |
| snap_dist_m | number | yes | Planar distance from source point to graph_node |
| included | boolean | no | Whether the site is used in baseline routing |
| exclude_reason | string | yes | Reason an omitted site is not routed; null when included |
| source_date | datetime string | no | UTC timestamp of the OSM POI snapshot or query |
| geometry | Point | no | Original node coordinate or point on surface for an area feature |

capacity and socket_summary do not weight coverage. included=true sites each act as one destination.

## 4. grid_accessibility.geojson

One feature per non-empty intersection of a 750 m grid square, district land, and the land mask. This is the authoritative source for maps and area aggregation. Geometry is Polygon or MultiPolygon.

| Field | Type | Nullable | Definition |
|---|---:|:---:|---|
| grid_id | string | no | Deterministic ID derived from fixed grid row, column, and district |
| district | string | no | District owning the clipped fragment |
| area_km2 | number | no | Exact land area of the clipped fragment |
| sample_lon | number | no | Longitude of the internal representative point |
| sample_lat | number | no | Latitude of the internal representative point |
| graph_node | string | yes | Nearest eligible driving-graph node |
| snap_dist_m | number | yes | Distance from representative point to graph_node |
| baseline_min | number | yes | Directed drive time to the nearest included existing charger |
| after_min | number | yes | Directed drive time to the nearest existing or selected charger |
| baseline_band | string | no | Baseline band from the controlled band vocabulary |
| after_band | string | no | After-addition band from the controlled band vocabulary |
| baseline_reachable | boolean | no | Whether baseline_min is finite |
| after_reachable | boolean | no | Whether after_min is finite |
| baseline_blind | boolean | no | True when baseline_band is gt_15 or unreachable |
| after_blind | boolean | no | True when after_band is gt_15 or unreachable |
| improvement_min | number | yes | baseline_min minus after_min when both are finite |
| access_change | string | no | unchanged, improved, newly_reachable, or worsened |
| population_est | number | no | WorldPop 2026 constrained 100 m people-per-pixel sum |
| geometry | Polygon or MultiPolygon | no | Land-clipped grid fragment |

worsened is not an expected analytical result and triggers a quality-control failure. `population_est` is sourced only from the dated WorldPop raster and is distinct from land area or raw building count.

## 5. blind_spots.geojson

One feature per contiguous blind-spot component, dissolved separately by district and scenario. A blind component consists of grid fragments with time greater than 15 minutes or no directed route. Geometry is Polygon or MultiPolygon.

| Field | Type | Nullable | Definition |
|---|---:|:---:|---|
| blind_id | string | no | Stable scenario, district, and component ID |
| scenario | string | no | baseline or after |
| chargers_added | integer | no | Number of proposed chargers active in the scenario |
| district | string | no | District containing the component |
| component_no | integer | no | Deterministic component sequence within scenario and district |
| area_km2 | number | no | Total component land area |
| grid_count | integer | no | Number of contributing grid fragments |
| unreachable_km2 | number | no | Component area having no directed route |
| mean_min_capped30 | number | no | Area-weighted mean of min(time, 30), treating unreachable as 30 |
| max_snap_dist_m | number | yes | Maximum grid representative-point snap distance in the component |
| geometry | Polygon or MultiPolygon | no | Dissolved blind-spot component |

The same physical land may appear twice in this file, once for baseline and once for after. scenario must therefore be used when calculating area.

## 6. candidate_sites.geojson

One feature per OSM POI examined by the site-screening stage, including rejected candidates. Optimization uses only feasible=true records. Geometry is Point.

| Field | Type | Nullable | Definition |
|---|---:|:---:|---|
| candidate_id | string | no | Stable project ID, normally osm_type plus osm_id |
| osm_type | string | no | node, way, or relation |
| osm_id | string | no | OSM element ID |
| poi_name | string | yes | OSM name |
| poi_key | string | no | Principal allowlist tag key, such as amenity or shop |
| poi_value | string | no | Principal allowlist tag value |
| access | string | yes | Raw OSM access value |
| district | string | yes | District containing the candidate |
| graph_node | string | yes | Snapped directed driving-graph node ID |
| snap_dist_m | number | yes | Point-to-node snap distance |
| existing_dist_m | number | yes | Straight-line distance to the nearest included existing charger |
| feasible | boolean | no | Whether all manifest screening rules pass |
| rejection_reason | string | yes | Semicolon-delimited stable reason codes; null when feasible |
| objective_min | number | no | Optimization threshold used to calculate coverage sets |
| potential_area_km2 | number | yes | Land area candidate could cover at objective_min without baseline subtraction |
| baseline_gain_km2 | number | yes | Baseline-uncovered land candidate alone could newly cover |
| baseline_gain_pctpt | number | yes | baseline_gain_km2 divided by total city land area, times 100 |
| selected | boolean | no | Whether greedy selection recommends the candidate |
| selected_rank | integer | yes | One-based greedy rank; null when not selected |
| source_date | datetime string | no | UTC timestamp of the OSM POI snapshot or query |
| geometry | Point | no | OSM node coordinate or point on surface |

Recommended rejection_reason codes include outside_land, private_access, excluded_class, duplicate, existing_site, snap_too_far, spacing, and incompatible_use.

## 7. recommended_sites.geojson

Selected subset of candidate_sites.geojson, ordered by greedy rank. These are planning-screening recommendations, not construction-ready sites. Geometry is Point.

| Field | Type | Nullable | Definition |
|---|---:|:---:|---|
| candidate_id | string | no | Foreign key to candidate_sites.geojson |
| rank | integer | no | One-based greedy selection order |
| district | string | no | Candidate district |
| poi_name | string | yes | OSM POI name |
| poi_key | string | no | Principal OSM tag key |
| poi_value | string | no | Principal OSM tag value |
| graph_node | string | no | Snapped graph node used as the proposed destination |
| snap_dist_m | number | no | Point-to-node snap distance |
| objective_min | number | no | Threshold optimized by the greedy procedure |
| marginal_gain_km2 | number | no | Newly covered land at objective_min at this selection step |
| marginal_gain_pctpt | number | no | Corresponding citywide land-coverage percentage-point gain |
| cumulative_added | integer | no | Number of proposed sites active through this rank |
| cumulative_coverage_5_pct | number | no | City land-area coverage within 5 minutes through this rank |
| cumulative_coverage_10_pct | number | no | City land-area coverage within 10 minutes through this rank |
| cumulative_coverage_15_pct | number | no | City land-area coverage within 15 minutes through this rank |
| screening_status | string | no | preliminary; requires field, utility, legal, and engineering review |
| geometry | Point | no | Proposed location represented by the OSM POI point |

## 8. city_coverage.csv

One baseline row and one final after row for citywide results. All coverage fields in this file use land area.

| Field | Type | Nullable | Definition |
|---|---:|:---:|---|
| scenario | string | no | baseline or after |
| chargers_added | integer | no | Zero for baseline; number selected for after |
| analyzed_land_km2 | number | no | Total land-area denominator |
| covered_5_km2 | number | no | Land within 5 minutes |
| covered_10_km2 | number | no | Land within 10 minutes |
| covered_15_km2 | number | no | Land within 15 minutes |
| coverage_5_pct | number | no | Area-weighted 5-minute land coverage |
| coverage_10_pct | number | no | Area-weighted 10-minute land coverage |
| coverage_15_pct | number | no | Area-weighted 15-minute land coverage |
| blind_15_km2 | number | no | Land above 15 minutes or unreachable |
| unreachable_area_km2 | number | no | Land with no directed route to an active charger |
| mean_min_capped30 | number | no | Area-weighted mean of min(time, 30), unreachable set to 30 |
| p90_min_capped30 | number | no | Area-weighted 90th percentile of the same capped time |
| population_est_total | number | no | WorldPop population denominator |
| population_coverage_5_pct | number | no | WorldPop-weighted coverage within 5 minutes |
| population_coverage_10_pct | number | no | WorldPop-weighted coverage within 10 minutes |
| population_coverage_15_pct | number | no | WorldPop-weighted coverage within 15 minutes |
| population_mean_min_capped30 | number | no | Population-weighted capped mean |
| population_p90_min_capped30 | number | no | Population-weighted capped p90 |

The improvement at a threshold is after coverage minus baseline coverage in percentage points. It is not a percent change relative to the baseline value.

## 9. district_statistics.csv

One wide row per district, giving 12 rows with baseline, final after, and improvement fields.

| Field | Type | Nullable | Definition |
|---|---:|:---:|---|
| district | string | no | Canonical district name |
| land_area_km2 | number | no | District land-area denominator |
| population_est | number | no | WorldPop district denominator |
| existing_chargers | integer | no | Existing sites physically located in district |
| recommended_chargers | integer | no | Final proposed sites physically located in district |
| baseline_coverage_{5,10,15}_pct | number | no | Baseline land coverage for each threshold |
| after_coverage_{5,10,15}_pct | number | no | Final land coverage for each threshold |
| improvement_{5,10,15}_pp | number | no | Land percentage-point uplift |
| baseline_population_coverage_{5,10,15}_pct | number | no | Baseline WorldPop coverage |
| after_population_coverage_{5,10,15}_pct | number | no | Final WorldPop coverage |
| population_improvement_{5,10,15}_pp | number | no | Population percentage-point uplift |
| baseline_mean_min_capped30 | number | no | Baseline area-weighted capped mean |
| after_mean_min_capped30 | number | no | Final area-weighted capped mean |

Rows are ranked by ascending baseline population coverage at 15 minutes, then ascending baseline land coverage at 15 minutes.

## 10. blind_spot_statistics.csv

One row per contiguous baseline blind-spot component. It summarizes the polygons in `blind_spots.geojson`.

| Field | Type | Nullable | Definition |
|---|---:|:---:|---|
| scenario | string | no | baseline or after |
| chargers_added | integer | no | Proposed sites active in the scenario |
| scope | string | no | city or district |
| district | string | yes | Canonical district name; null when scope=city |
| total_area_km2 | number | no | Land-area denominator for the scope |
| blind_area_km2 | number | no | Land above 15 minutes or unreachable |
| blind_area_pct | number | no | blind_area_km2 divided by total_area_km2, times 100 |
| unreachable_area_km2 | number | no | Blind-area subset with no directed route |
| component_count | integer | no | Number of contiguous blind components |
| largest_component_km2 | number | no | Area of the largest blind component; zero if none |
| mean_min_capped30 | number | yes | Area-weighted capped mean within blind land; null only if no blind land |
| p90_min_capped30 | number | yes | Area-weighted capped p90 within blind land; null only if no blind land |

When no blind land exists, component_count and largest_component_km2 are zero; mean and p90 are blank rather than zero.

## 11. scenario_summary.csv

Optimization trace with one baseline row followed by one row after each selected site. It makes marginal and cumulative gains auditable.

| Field | Type | Nullable | Definition |
|---|---:|:---:|---|
| step | integer | no | Zero for baseline; otherwise greedy selection rank |
| scenario | string | no | baseline for step 0, then after_1, after_2, and so on |
| added_candidate_id | string | yes | Candidate added at this step; null for baseline |
| added_district | string | yes | District of added candidate; null for baseline |
| marginal_gain_km2 | number | no | New land covered at objective_min during this step |
| marginal_gain_pctpt | number | no | Corresponding city land-coverage percentage-point gain |
| chargers_added | integer | no | Cumulative proposed sites active |
| chargers_total | integer | no | Existing plus cumulative proposed destinations |
| coverage_5_pct | number | no | Cumulative city land-area coverage within 5 minutes |
| coverage_10_pct | number | no | Cumulative city land-area coverage within 10 minutes |
| coverage_15_pct | number | no | Cumulative city land-area coverage within 15 minutes |
| blind_15_km2 | number | no | Remaining city blind land |
| unreachable_area_km2 | number | no | Remaining land with no directed route |
| mean_min_capped30 | number | no | Cumulative city area-weighted capped mean |
| p90_min_capped30 | number | no | Cumulative city area-weighted capped p90 |

For step 0, both marginal-gain fields are zero. The final trace row must reproduce the after row of city_coverage.csv.

## 12. Cross-file integrity rules

- Every district value in output files must match one districts.geojson district value.
- Every recommended_sites.geojson candidate_id must exist exactly once in candidate_sites.geojson with feasible=true and selected=true.
- recommended rank must be unique and contiguous from 1 to chargers_added.
- The final recommended-site rank must match the final scenario_summary.csv step.
- Grid area summed by district must reproduce districts.geojson area_km2 within rounding tolerance.
- For each scenario, 15-minute covered land plus blind land must equal total land.
- City and district coverage must be recomputable from grid_accessibility.geojson using area_km2 as weights.
- baseline/after city values must match city_coverage.csv; per-district values must match district_statistics.csv.
- Blind components and blind_spot_statistics.csv must be derived from the same grid scenario flags.
- Population fields must be reproducible from the WorldPop file named in the run manifest; the manifest must record its URL, DOI, release, timestamp, and raster aggregation method.
