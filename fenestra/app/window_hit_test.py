"""Pure size and WM_NCHITTEST rules for the frameless Fenestra window."""

import math

TITLE_BAR_HEIGHT = 34
TITLE_BAR_INTERACTIVE_WIDTH = 320
TITLE_BAR_CONTROLS_WIDTH = 72

HTCAPTION = 2

DEFAULT_WINDOW_SIZE = (1000, 620)
MINIMUM_WINDOW_SIZE = (860, 600)


def fixed_window_size(available) -> tuple[int, int]:
    """Return the window's fixed size for a monitor's available area.

    Parameters
    ----------
    available : QRect or None
        The monitor's work area in device-independent pixels, or None when
        no monitor is known.

    Returns
    -------
    tuple of int
        The default size, reduced to fit the work area but never below the
        minimum that the interface is designed for.
    """
    width, height = DEFAULT_WINDOW_SIZE
    if available is not None:
        width = min(width, int(available.width()))
        height = min(height, int(available.height()))
    return max(width, MINIMUM_WINDOW_SIZE[0]), max(height, MINIMUM_WINDOW_SIZE[1])


def normalize_hit_test_regions(
    interactive_width: int,
    controls_width: int,
    title_bar_height: int,
) -> tuple[int, int, int]:
    """Validate frontend-measured hit-test regions expressed in CSS pixels."""
    values = (
        ("interactive_width", interactive_width, 0, 4096),
        ("controls_width", controls_width, 1, 512),
        ("title_bar_height", title_bar_height, 1, 256),
    )
    normalized = []
    for name, value, minimum, maximum in values:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{name} must be an integer.")
        if not minimum <= value <= maximum:
            raise ValueError(f"{name} must be from {minimum} to {maximum} pixels.")
        normalized.append(value)
    return normalized[0], normalized[1], normalized[2]


def classify_window_hit(
    x: int,
    y: int,
    width: int,
    height: int,
    *,
    title_bar_height: int = TITLE_BAR_HEIGHT,
    interactive_width: int = TITLE_BAR_INTERACTIVE_WIDTH,
    controls_width: int = TITLE_BAR_CONTROLS_WIDTH,
) -> int:
    """Return the Win32 non-client hit code for a client-relative point.

    The frontend reserves the first 320 CSS pixels for the logo, title, and
    search button, and the final 72 pixels for the window controls. Only the
    empty spacer between those regions behaves as a title-bar drag handle.
    The window has a fixed size, so its edges are ordinary client content.
    """
    if width <= 0 or height <= 0:
        return 0
    if 0 <= y < title_bar_height and interactive_width <= x < width - controls_width:
        return HTCAPTION
    return 0


def classify_physical_window_hit(
    screen_x: int,
    screen_y: int,
    window_rect: tuple[int, int, int, int],
    scale: float,
    *,
    interactive_width: int = TITLE_BAR_INTERACTIVE_WIDTH,
    controls_width: int = TITLE_BAR_CONTROLS_WIDTH,
    title_bar_height: int = TITLE_BAR_HEIGHT,
) -> int:
    """Classify a native screen point without mixing physical and Qt coordinates."""
    if not math.isfinite(scale) or scale <= 0:
        scale = 1.0
    left, top, right, bottom = window_rect

    def scaled(value: int) -> int:
        return max(1, math.ceil(value * scale))

    return classify_window_hit(
        screen_x - left,
        screen_y - top,
        right - left,
        bottom - top,
        title_bar_height=scaled(title_bar_height),
        interactive_width=scaled(interactive_width),
        controls_width=scaled(controls_width),
    )
