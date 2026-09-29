"""Tests for WM_NCHITTEST signed lParam extraction and hit-zone classification.

Tests cover two behaviors added to MainWindow.nativeEvent:
1. Signed 16-bit coordinate extraction from lParam (multi-monitor support)
2. Hit-zone classification including HTCAPTION title bar drag zone

All tests are pure-logic unit tests that do NOT require Qt or a running desktop.
"""

import sys

import pytest

from fenestra.app.window_hit_test import (
    HTCAPTION,
    TITLE_BAR_CONTROLS_WIDTH,
    TITLE_BAR_HEIGHT,
    TITLE_BAR_INTERACTIVE_WIDTH,
    classify_physical_window_hit,
    classify_window_hit,
    fixed_window_size,
    normalize_hit_test_regions,
)

# ---------------------------------------------------------------------------
# Helper: signed 16-bit extraction (mirrors ctypes.c_short(val & 0xFFFF).value)
# ---------------------------------------------------------------------------


def signed_short(val):
    """Extract a signed 16-bit value from an unsigned integer.

    This mirrors the corrected lParam extraction in nativeEvent:
        ctypes.c_short(msg.lParam & 0xFFFF).value
    """
    import ctypes

    return ctypes.c_short(val & 0xFFFF).value


# ---------------------------------------------------------------------------
# Helper: hit-zone classification
# ---------------------------------------------------------------------------


def classify_hit(pos_x, pos_y, width, height):
    """Call the production hit-zone classifier."""
    return classify_window_hit(pos_x, pos_y, width, height)


# ===========================================================================
# Tests: signed_short extraction
# ===========================================================================


@pytest.mark.skipif(sys.platform != "win32", reason="Win32-only (ctypes.c_short)")
class TestSignedShort:
    """Verify signed 16-bit extraction from unsigned lParam values."""

    def test_negative_monitor_x(self):
        """Unsigned 63616 decodes to signed -1920 (monitor at x=-1920)."""
        assert signed_short(63616) == -1920

    def test_zero(self):
        """Zero stays zero."""
        assert signed_short(0) == 0

    def test_positive_value(self):
        """Positive 500 stays positive."""
        assert signed_short(500) == 500

    def test_max_unsigned_is_minus_one(self):
        """65535 (0xFFFF) decodes to -1."""
        assert signed_short(65535) == -1

    def test_min_signed_short(self):
        """32768 (0x8000) decodes to -32768 (minimum signed short)."""
        assert signed_short(32768) == -32768

    def test_max_signed_short(self):
        """32767 (0x7FFF) decodes to 32767 (maximum signed short)."""
        assert signed_short(32767) == 32767


# ===========================================================================
# Tests: classify_hit (hit-zone classification)
# ===========================================================================
# Standard window: 1000x620 with production title-bar constants.


class TestClassifyHit:
    """Verify hit-zone classification for NCHITTEST regions."""

    def test_htcaption_center_title_bar(self):
        """Center of title bar returns HTCAPTION."""
        assert classify_hit(500, 10, 1000, 620) == HTCAPTION

    def test_search_area_is_client_content(self):
        """The logo, title, and search area remain clickable client content."""
        assert classify_hit(100, 10, 1000, 620) == 0
        assert classify_hit(TITLE_BAR_INTERACTIVE_WIDTH - 1, 10, 1000, 620) == 0

    def test_drag_region_starts_after_search_area(self):
        """The empty spacer immediately after the search area drags the window."""
        assert classify_hit(TITLE_BAR_INTERACTIVE_WIDTH, 10, 1000, 620) == HTCAPTION

    @pytest.mark.parametrize(
        ("x", "y"),
        [(2, 300), (998, 300), (2, 2), (998, 2), (2, 618), (998, 618), (500, 618)],
    )
    def test_edges_and_corners_do_not_resize(self, x, y):
        """The fixed-size window treats its edges and corners as client content."""
        assert classify_hit(x, y, 1000, 620) == 0

    def test_top_edge_of_the_spacer_still_drags(self):
        """The spacer drags the window from its first row, with no resize strip."""
        assert classify_hit(500, 0, 1000, 620) == HTCAPTION

    def test_controls_area_not_htcaption(self):
        """The complete 72-pixel controls area remains clickable."""
        boundary = 1000 - TITLE_BAR_CONTROLS_WIDTH
        assert classify_hit(boundary, 10, 1000, 620) == 0
        assert classify_hit(960, 10, 1000, 620) == 0

    def test_top_edge_of_window_controls_remains_clickable(self):
        """The window controls stay clickable up to the window's top edge."""
        assert classify_hit(960, 0, 1000, 620) == 0

    def test_below_title_bar_falls_through(self):
        """Position below the title bar falls through to client handling."""
        assert classify_hit(500, 100, 1000, 620) == 0

    def test_just_before_controls_is_htcaption(self):
        """Position just before the controls area returns HTCAPTION."""
        boundary = 1000 - TITLE_BAR_CONTROLS_WIDTH
        assert classify_hit(boundary - 1, 10, 1000, 620) == HTCAPTION

    def test_controls_boundary_exact(self):
        """The exact controls boundary returns client handling."""
        assert classify_hit(1000 - TITLE_BAR_CONTROLS_WIDTH, 10, 1000, 620) == 0

    def test_title_bar_boundary_exact(self):
        """The exact 34-pixel title boundary falls through to the page."""
        assert classify_hit(500, TITLE_BAR_HEIGHT, 1000, 620) == 0

    def test_measured_regions_replace_the_fallback_contract(self):
        """Measured frontend widths define the drag spacer without code duplication."""
        assert (
            classify_window_hit(
                400,
                10,
                1000,
                620,
                interactive_width=410,
                controls_width=90,
                title_bar_height=40,
            )
            == 0
        )
        assert (
            classify_window_hit(
                410,
                10,
                1000,
                620,
                interactive_width=410,
                controls_width=90,
                title_bar_height=40,
            )
            == HTCAPTION
        )


def test_physical_hit_testing_handles_negative_origin_at_150_percent() -> None:
    """A physical native point maps to the correct CSS-pixel drag region."""
    window_rect = (-1920, 120, -420, 1050)
    assert classify_physical_window_hit(-1170, 135, window_rect, 1.5) == HTCAPTION


def test_physical_hit_testing_scales_the_drag_region() -> None:
    """The 320-DIP search area ends at 480 physical pixels at 150 percent."""
    window_rect = (300, 200, 1800, 1130)
    assert classify_physical_window_hit(779, 210, window_rect, 1.5) == 0
    assert classify_physical_window_hit(780, 210, window_rect, 1.5) == HTCAPTION
    assert classify_physical_window_hit(305, 650, window_rect, 1.5) == 0


@pytest.mark.parametrize(
    ("regions", "error_type"),
    [
        ((True, 72, 34), TypeError),
        ((320, 0, 34), ValueError),
        ((320, 72, 0), ValueError),
        ((5000, 72, 34), ValueError),
    ],
)
def test_measured_region_validation_rejects_untrusted_values(regions, error_type) -> None:
    """The WebChannel measurement slot accepts only bounded integer CSS pixels."""
    with pytest.raises(error_type):
        normalize_hit_test_regions(*regions)


class _Area:
    def __init__(self, width, height):
        self._width, self._height = width, height

    def width(self):
        return self._width

    def height(self):
        return self._height


@pytest.mark.parametrize(
    ("available", "expected"),
    [
        (_Area(2560, 1400), (1000, 620)),
        (_Area(1280, 700), (1000, 620)),
        (_Area(915, 610), (915, 610)),
        (_Area(800, 500), (860, 600)),
        (None, (1000, 620)),
    ],
)
def test_fixed_window_size_fits_the_work_area_within_the_minimum(available, expected) -> None:
    """The default size shrinks to fit a small work area but not below the minimum."""
    assert fixed_window_size(available) == expected
