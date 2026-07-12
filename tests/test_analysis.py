from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point, box


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ev_placement.analysis import (  # noqa: E402
    build_blind_spots,
    coverage_metrics,
    solve_maximum_coverage,
    weighted_quantile,
)


class CoreAnalysisTests(unittest.TestCase):
    def test_weighted_quantile(self) -> None:
        values = np.array([1.0, 2.0, 10.0])
        weights = np.array([1.0, 8.0, 1.0])
        self.assertEqual(weighted_quantile(values, weights, 0.5), 2.0)
        self.assertEqual(weighted_quantile(values, weights, 0.9), 2.0)

    def test_coverage_is_monotonic(self) -> None:
        seconds = np.array([100.0, 400.0, 800.0, np.inf])
        weights = np.ones(4)
        result = coverage_metrics(seconds, weights, "test", 0)
        self.assertLessEqual(result["coverage_5_pct"], result["coverage_10_pct"])
        self.assertLessEqual(result["coverage_10_pct"], result["coverage_15_pct"])
        self.assertEqual(result["unreachable_pct"], 25.0)

    def test_milp_selects_largest_population_gain(self) -> None:
        baseline = np.array([1_000.0, 1_000.0, 1_000.0])
        candidate_times = np.array(
            [[800.0, 800.0, np.inf], [np.inf, np.inf, 800.0]], dtype=float
        )
        population = np.array([100.0, 1.0, 50.0])
        candidates = gpd.GeoDataFrame(
            {"candidate_id": ["A", "B"]},
            geometry=[Point(49.8, 40.4), Point(50.2, 40.5)],
            crs="EPSG:4326",
        )
        selected, status = solve_maximum_coverage(
            baseline,
            candidate_times,
            population,
            candidates,
            1,
            {"local_crs": "EPSG:32639", "minimum_selected_spacing_m": 0},
        )
        self.assertEqual(selected, [0])
        self.assertIn("milp", status)

    def test_blind_spot_components(self) -> None:
        grid = gpd.GeoDataFrame(
            {
                "grid_row": [0, 0, 3],
                "grid_col": [0, 1, 3],
                "baseline_sec": [1_000.0, 1_100.0, np.inf],
                "baseline_min": [1_000 / 60, 1_100 / 60, np.inf],
                "area_km2": [1.0, 1.0, 1.0],
                "district": ["A", "A", "B"],
            },
            geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1), box(3, 3, 4, 4)],
            crs="EPSG:32639",
        )
        zones, statistics = build_blind_spots(grid)
        self.assertEqual(len(zones), 2)
        self.assertAlmostEqual(statistics["area_km2"].sum(), 3.0)


class ProductionArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.output_dir = PROJECT_ROOT / "outputs"
        cls.available = (cls.output_dir / "tables" / "city_coverage.csv").exists()

    def test_expected_artifacts_exist(self) -> None:
        if not self.available:
            self.skipTest("Production outputs have not been generated.")
        expected = [
            "maps/interactive_map.html",
            "maps/coverage_heatmap.html",
            "maps/blind_spot_map.html",
            "tables/city_coverage.csv",
            "tables/district_statistics.csv",
            "tables/recommended_sites.csv",
            "recommended_sites.geojson",
            "grid_accessibility.geojson",
        ]
        for relative in expected:
            self.assertTrue((self.output_dir / relative).exists(), relative)

    def test_production_metrics_are_consistent(self) -> None:
        if not self.available:
            self.skipTest("Production outputs have not been generated.")
        coverage = pd.read_csv(self.output_dir / "tables" / "city_coverage.csv")
        self.assertEqual(coverage["scenario"].tolist(), ["baseline", "after"])
        for _, row in coverage.iterrows():
            self.assertLessEqual(row.coverage_5_pct, row.coverage_10_pct)
            self.assertLessEqual(row.coverage_10_pct, row.coverage_15_pct)
            self.assertLessEqual(
                row.population_coverage_5_pct, row.population_coverage_10_pct
            )
            self.assertLessEqual(
                row.population_coverage_10_pct, row.population_coverage_15_pct
            )
        self.assertGreaterEqual(
            coverage.iloc[1].population_coverage_15_pct,
            coverage.iloc[0].population_coverage_15_pct,
        )

    def test_report_is_eight_pages(self) -> None:
        pdf = PROJECT_ROOT / "reports" / "baku_ev_charging_technical_report.pdf"
        if not pdf.exists():
            self.skipTest("Production PDF has not been generated.")
        page_count = len(re.findall(rb"/Type\s*/Page(?!s)", pdf.read_bytes()))
        self.assertEqual(page_count, 8)


if __name__ == "__main__":
    unittest.main()

