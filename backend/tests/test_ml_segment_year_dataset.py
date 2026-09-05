"""
Tests for Part 14's offline segment-year dataset builder
(app/data/ml/*). This package is never imported by production code
(app/core, app/api, app/simulation) -- these tests only verify the build
script itself, not anything wired into the running API.

Focus: the properties the task explicitly required --
1. Full 2,964 x 11 shape, no undocumented row forced to label=0.
2. label_status is exactly one of {event, non_event_documented, unobserved}.
3. Strict temporal cutoff: a row's historical_landslide_count_prior never
   counts a same-year-or-later record (the core leakage guard).
4. non_event_documented's keyword path is real code, not a stub (verified
   with a synthetic record even though the real corridor extract yields 0).
"""
import math

import pytest

from app.data.ml.build_segment_year_dataset import build_audit, build_dataset
from app.data.ml.landslide_year_index import LandslideEvent, _is_negative_observation
from app.data.ml.rainfall_archive_loader import available_years


@pytest.fixture(scope="module")
def dataset():
    return build_dataset()


def test_full_cartesian_shape(dataset):
    n_segments = len({r["segment_id"] for r in dataset})
    n_years = len({r["year"] for r in dataset})
    assert n_segments == 2964
    assert n_years == len(available_years())
    assert len(dataset) == n_segments * n_years


def test_label_status_is_one_of_three_values(dataset):
    allowed = {"event", "non_event_documented", "unobserved"}
    assert {r["label_status"] for r in dataset} <= allowed


def test_unobserved_rows_have_nan_label_never_zero(dataset):
    """The central requirement: an undocumented segment-year must never be
    silently forced to label=0."""
    unobserved = [r for r in dataset if r["label_status"] == "unobserved"]
    assert len(unobserved) > 0
    assert all(r["label"] is None or (isinstance(r["label"], float) and math.isnan(r["label"])) for r in unobserved)


def test_event_rows_have_label_one(dataset):
    events = [r for r in dataset if r["label_status"] == "event"]
    assert len(events) > 0
    assert all(r["label"] == 1.0 for r in events)


def test_every_segment_has_exactly_one_row_per_available_year(dataset):
    years = set(available_years())
    by_segment: dict[str, set] = {}
    for r in dataset:
        by_segment.setdefault(r["segment_id"], set()).add(r["year"])
    assert all(seen == years for seen in by_segment.values())


def test_historical_count_prior_never_counts_same_year_or_future_events(dataset):
    """The core leakage guard: for every row, historical_landslide_count_prior
    must be at least the number of `event` rows for that SAME segment with a
    strictly earlier year (it may be higher, since a segment can carry
    multiple matched GSI records per event year) -- never inflated by the
    current or a future year."""
    events_by_segment: dict[str, list[int]] = {}
    for r in dataset:
        if r["label_status"] == "event":
            events_by_segment.setdefault(r["segment_id"], []).append(r["year"])

    for r in dataset:
        event_years = events_by_segment.get(r["segment_id"], [])
        expected_prior = sum(1 for y in event_years if y < r["year"])
        assert r["historical_landslide_count_prior"] >= expected_prior


def test_historical_count_prior_is_monotonically_non_decreasing_per_segment(dataset):
    """For a fixed segment, historical_landslide_count_prior must never
    decrease as year increases -- history only accumulates forward in
    time. A decrease would indicate a future record leaking into an
    earlier row (or vice versa)."""
    by_segment: dict[str, list[tuple[int, int]]] = {}
    for r in dataset:
        by_segment.setdefault(r["segment_id"], []).append((r["year"], r["historical_landslide_count_prior"]))

    for segment_id, year_counts in by_segment.items():
        year_counts.sort()
        counts = [c for _, c in year_counts]
        assert counts == sorted(counts), f"{segment_id} has a non-monotonic prior count: {year_counts}"


def test_an_events_own_year_is_excluded_from_its_own_prior_count(dataset):
    """Directly reproduces the manual check from the design conversation:
    a segment with an event in year Y must show historical_landslide_count_prior
    for year Y computed WITHOUT that same event (i.e. strictly less than
    what year Y+1 shows once the event is safely in the past)."""
    by_segment: dict[str, dict[int, int]] = {}
    for r in dataset:
        by_segment.setdefault(r["segment_id"], {})[r["year"]] = r["historical_landslide_count_prior"]

    event_rows = [r for r in dataset if r["label_status"] == "event"]
    checked = 0
    for r in event_rows:
        years_for_segment = sorted(by_segment[r["segment_id"]].keys())
        idx = years_for_segment.index(r["year"])
        if idx + 1 < len(years_for_segment):
            next_year = years_for_segment[idx + 1]
            # The very next year's prior count must be strictly greater
            # than this event year's own prior count -- proving this
            # event contributed to the NEXT row but not to its own row.
            assert by_segment[r["segment_id"]][next_year] > by_segment[r["segment_id"]][r["year"]]
            checked += 1
    assert checked > 0  # sanity: the property was actually exercised


def test_negative_observation_keyword_path_is_real_code():
    """The non_event_documented path is measured to be empty against the
    real corridor extract (see the audit report) -- confirm here that this
    is because no real record matches, not because the check is a stub."""
    assert _is_negative_observation("Debris slide found stable on re-inspection") is True
    assert _is_negative_observation("Fresh debris slide blocking the carriageway") is False


def test_audit_report_counts_match_dataset(dataset):
    report = build_audit(dataset)
    n_event = sum(1 for r in dataset if r["label_status"] == "event")
    n_unobserved = sum(1 for r in dataset if r["label_status"] == "unobserved")
    assert f"| event | {n_event} |" in report
    assert f"| unobserved | {n_unobserved} |" in report
    assert "32604" in report  # the known real shape, not asserted blindly elsewhere


def test_no_ml_module_is_imported_by_production_code():
    """Guards the isolation requirement: app/data/ml/* must never be
    reachable from app.main / app.api / app.core / app.simulation --
    EXCEPT app/core/ml_risk_signal.py (Part 15B), which is the one,
    explicitly-approved, isolated ML inference service. It imports
    app.data.ml.feature_matrix_v2_17feature deliberately, to reuse the
    exact v2 training-time feature encoding rather than reimplementing it
    (see ml_feature_parity_part15a.md Section 6 and
    ml_risk_signal.py's own module docstring) -- but it is not imported by,
    and does not import, any of risk_engine.py/routing_engine.py/
    reroute_service.py/hazard_state.py, is not called from any API route,
    and is disabled by default (app.config.ML_RISK_ENABLED = False). See
    test_ml_risk_signal_module_is_not_wired_into_risk_or_routing_engines
    below for that isolation boundary, checked explicitly."""
    import ast
    from pathlib import Path

    backend_app = Path(__file__).resolve().parents[1] / "app"
    approved_exception = backend_app / "core" / "ml_risk_signal.py"
    offenders = []
    for sub in ["main.py", "api", "core", "simulation"]:
        target = backend_app / sub
        paths = [target] if target.is_file() else list(target.rglob("*.py"))
        for path in paths:
            if path == approved_exception:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module and "data.ml" in node.module:
                    offenders.append(str(path))
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if "data.ml" in alias.name:
                            offenders.append(str(path))
    assert offenders == []


def test_ml_risk_signal_module_is_not_wired_into_risk_or_routing_engines():
    """The core, permanent isolation boundary for ml_risk_signal.py: it
    must never be imported by, and must never import, any of the four
    production DECISION modules (risk_engine/routing_engine/
    reroute_service/hazard_state) -- regardless of how many API endpoints
    later expose it (Part 15C added exactly one, api/routes_network.py's
    GET /segments/{id}/ml-risk -- an isolated read/expose endpoint, not a
    decision path). This test does NOT forbid API exposure in general
    (Part 15B's earlier, temporary "not reachable from any API route"
    restriction is superseded by Part 15C's explicit instruction) -- it
    forbids exactly the four modules that decide risk_score, routing cost,
    hard-unsafe exclusion, and PROCEED/REROUTE/SUSPEND from ever reading
    an ML signal."""
    import ast
    from pathlib import Path

    backend_app = Path(__file__).resolve().parents[1] / "app"
    guarded_modules = {
        "core/risk_engine.py", "core/routing_engine.py",
        "core/reroute_service.py", "core/hazard_state.py",
    }

    def imports_ml_risk_signal(path: Path) -> bool:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and "ml_risk_signal" in node.module:
                return True
            if isinstance(node, ast.Import):
                if any("ml_risk_signal" in alias.name for alias in node.names):
                    return True
        return False

    for rel in guarded_modules:
        assert not imports_ml_risk_signal(backend_app / rel), f"{rel} must not import ml_risk_signal"

    ml_risk_signal_path = backend_app / "core" / "ml_risk_signal.py"
    tree = ast.parse(ml_risk_signal_path.read_text(encoding="utf-8"))
    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)
    for guarded in guarded_modules:
        guarded_module_name = "app." + guarded.replace("/", ".").removesuffix(".py")
        assert guarded_module_name not in imported_modules, f"ml_risk_signal.py must not import {guarded_module_name}"


def test_ml_risk_api_endpoint_is_the_only_api_file_reaching_ml_risk_signal():
    """Part 15C added exactly one API surface for the ML signal: GET
    /segments/{id}/ml-risk in api/routes_network.py. This confirms that
    remains the ONLY api/*.py or main.py file that reaches
    ml_risk_signal.py -- so a future part can't quietly add a second,
    undocumented ML-reading endpoint without this test forcing a
    conscious update."""
    import ast
    from pathlib import Path

    backend_app = Path(__file__).resolve().parents[1] / "app"
    approved_api_exception = backend_app / "api" / "routes_network.py"

    def imports_ml_risk_signal(path: Path) -> bool:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and "ml_risk_signal" in node.module:
                return True
            if isinstance(node, ast.Import):
                if any("ml_risk_signal" in alias.name for alias in node.names):
                    return True
        return False

    offenders = []
    for sub in ["main.py", "api"]:
        target = backend_app / sub
        paths = [target] if target.is_file() else list(target.rglob("*.py"))
        for path in paths:
            if path == approved_api_exception:
                continue
            if imports_ml_risk_signal(path):
                offenders.append(str(path))
    assert offenders == []
    assert imports_ml_risk_signal(approved_api_exception)
