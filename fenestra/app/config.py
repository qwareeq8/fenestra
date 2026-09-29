"""Application identity, setting defaults, and setting normalizers.

This module holds constants only, so packaging tools can read ``APP_VERSION``
without importing Qt.
"""

APP_NAME = "Fenestra"
ORGANIZATION = "Yusuf Qwareeq"
APP_VERSION = "0.0.1"
LOG_DIR = "Fenestra"
LOG_FILE = "fenestra.log"
SETTINGS_GROUP = "Settings"
INSTANCE_WINDOW_PROPERTY = "Fenestra.InstanceWindow.v1"
# A second launch posts this registered message to the running instance's
# window, which then shows itself.
INSTANCE_ACTIVATE_MESSAGE = "Fenestra.ActivateInstance.v1"

DEFAULTS = {
    "snap_key": "shift",
    "restore_key": "ctrl",
    "enable_snap": True,
    "snap_presses": 3,
    "snap_interval": 1050,
    "width_pct": 76,
    "height_pct": 76,
    "ex_auto_size": False,
    "game_mode_enabled": True,
    "run_at_startup": False,
    "theme": "system",
    "accent": "amber",
    "density": "cozy",
    "minimize_to_tray": True,
}

VALID_ACCENTS = ("amber", "green", "teal", "blue", "violet")
VALID_DENSITIES = ("compact", "cozy", "comfortable")


def normalize_snap_presses(value):
    """Coerce a press count to an integer from 1 to 10."""
    try:
        val = int(value)
    except Exception:
        return DEFAULTS["snap_presses"]
    return max(1, min(10, val))


def normalize_accent(value):
    """Return a supported accent name, falling back to the default."""
    accent = str(value or "").strip().lower()
    return accent if accent in VALID_ACCENTS else DEFAULTS["accent"]


def normalize_density(value):
    """Return a supported density name, falling back to the default."""
    density = str(value or "").strip().lower()
    return density if density in VALID_DENSITIES else DEFAULTS["density"]
