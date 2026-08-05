"""Cross-platform screenshot regression gates at common Windows scale factors."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
PROBE = Path(__file__).with_name("gui_screenshot_probe.py")
BASELINE = Path(__file__).with_name("screenshot_baselines") / "c6_overview.json"
LINUX_VERTICAL_FONT_TOLERANCE = 48


def _assert_platform_rect(actual, expected, tolerance: int) -> None:
    """Keep exact Windows geometry while allowing bounded Linux font metrics."""
    if not sys.platform.startswith("linux"):
        assert actual == pytest.approx(expected, abs=tolerance)
        return

    # The Ubuntu runner uses a different system font from Windows.  Qt keeps
    # horizontal layout stable, but text wrapping changes logical y/height by
    # up to 41 px in the reviewed C6 pages.  Preserve the strict x/width gate
    # and bound only the font-driven vertical axes.
    assert actual[0] == pytest.approx(expected[0], abs=tolerance)
    assert actual[2] == pytest.approx(expected[2], abs=tolerance)
    vertical_tolerance = max(tolerance, LINUX_VERTICAL_FONT_TOLERANCE)
    assert actual[1] == pytest.approx(expected[1], abs=vertical_tolerance)
    assert actual[3] == pytest.approx(expected[3], abs=vertical_tolerance)


def test_linux_font_tolerance_keeps_horizontal_geometry_strict(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")

    _assert_platform_rect([100, 140, 500, 160], [100, 100, 500, 120], 4)
    with pytest.raises(AssertionError):
        _assert_platform_rect([110, 140, 500, 160], [100, 100, 500, 120], 4)
    with pytest.raises(AssertionError):
        _assert_platform_rect([100, 149, 500, 160], [100, 100, 500, 120], 4)


@pytest.mark.parametrize("scale", [1.0, 1.5, 2.0])
def test_overview_screenshot_matches_high_dpi_baseline(scale, tmp_path):
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    output = tmp_path / f"c6-overview-{scale:g}x.png"
    environment = os.environ.copy()
    environment.update(
        {
            "QT_QPA_PLATFORM": "offscreen",
            "QT_SCALE_FACTOR": str(scale),
            "STARCOMPANION_DATA": str(tmp_path / "data"),
            "STARCOMPANION_CACHE": str(tmp_path / "cache"),
            "PYTHONPATH": os.pathsep.join(
                [str(ROOT / "src"), str(ROOT / "tests"), environment.get("PYTHONPATH", "")]
            ),
        }
    )
    completed = subprocess.run(
        [sys.executable, str(PROBE), str(output)],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    metrics = json.loads(completed.stdout.strip().splitlines()[-1])

    assert output.is_file() and output.stat().st_size > 10_000
    assert metrics["logical_size"] == baseline["logical_size"]
    assert metrics["physical_size"] == [round(1280 * scale), round(800 * scale)]
    assert metrics["device_pixel_ratio"] == pytest.approx(scale)
    for name, expected in baseline["rects"].items():
        _assert_platform_rect(metrics["rects"][name], expected, baseline["tolerance"])
    for colour, minimum in baseline["minimum_colour_samples"].items():
        assert metrics["colours"][colour] >= minimum


@pytest.mark.parametrize(
    "page",
    [
        "content",
        "presentation",
        "blueprints",
        "provenance",
        "templates",
        "string-editor",
        "manual-apply",
        "support",
    ],
)
@pytest.mark.parametrize("scale", [1.0, 1.5, 2.0])
def test_modern_page_screenshot_matches_structural_baseline(page, scale, tmp_path):
    baseline_path = Path(__file__).with_name("screenshot_baselines") / f"c6_{page}.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    output = tmp_path / f"c6-{page}-{scale:g}x.png"
    environment = os.environ.copy()
    environment.update(
        {
            "QT_QPA_PLATFORM": "offscreen",
            "QT_SCALE_FACTOR": str(scale),
            "STARCOMPANION_DATA": str(tmp_path / "data"),
            "STARCOMPANION_CACHE": str(tmp_path / "cache"),
            "PYTHONPATH": os.pathsep.join(
                [str(ROOT / "src"), str(ROOT / "tests"), environment.get("PYTHONPATH", "")]
            ),
        }
    )
    completed = subprocess.run(
        [sys.executable, str(PROBE), str(output), page],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    metrics = json.loads(completed.stdout.strip().splitlines()[-1])

    assert output.is_file() and output.stat().st_size > 10_000
    assert metrics["page"] == page
    assert metrics["logical_size"] == baseline["logical_size"]
    assert metrics["physical_size"] == [round(1280 * scale), round(800 * scale)]
    assert metrics["device_pixel_ratio"] == pytest.approx(scale)
    for name, expected in baseline["rects"].items():
        _assert_platform_rect(metrics["rects"][name], expected, baseline["tolerance"])
    for colour, minimum in baseline["minimum_colour_samples"].items():
        assert metrics["colours"][colour] >= minimum


@pytest.mark.parametrize("page", ["templates", "manual-apply"])
def test_workflow_pages_reflow_without_horizontal_clipping_at_minimum_size(page, tmp_path):
    output = tmp_path / f"c6-{page}-minimum.png"
    environment = os.environ.copy()
    environment.update(
        {
            "QT_QPA_PLATFORM": "offscreen",
            "QT_SCALE_FACTOR": "1",
            "STARCOMPANION_DATA": str(tmp_path / "data"),
            "STARCOMPANION_CACHE": str(tmp_path / "cache"),
            "PYTHONPATH": os.pathsep.join(
                [str(ROOT / "src"), str(ROOT / "tests"), environment.get("PYTHONPATH", "")]
            ),
        }
    )
    completed = subprocess.run(
        [sys.executable, str(PROBE), str(output), page, "minimum"],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    metrics = json.loads(completed.stdout.strip().splitlines()[-1])

    assert metrics["logical_size"] == [1040, 680]
    assert metrics["physical_size"] == [1040, 680]
    for name, (x, _y, width, _height) in metrics["rects"].items():
        if name not in {"shell", "sidebar", "header", "stack", "status"}:
            assert x >= 244, name
            assert x + width <= 1040, name
    if page == "templates":
        assert metrics["rects"]["preview_section"][1] > metrics["rects"]["editor_section"][1]
    else:
        assert metrics["rects"]["recovery_section"][1] > metrics["rects"]["plan_view"][1]
