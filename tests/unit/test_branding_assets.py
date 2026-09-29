"""Verify the committed Windows branding assets without optional image libraries."""

from __future__ import annotations

import importlib.util
import struct
import xml.etree.ElementTree as ET
from pathlib import Path
from types import ModuleType

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ICON_SVG = PROJECT_ROOT / "branding" / "fenestra-icon.svg"
SMALL_ICON_SVG = PROJECT_ROOT / "branding" / "fenestra-icon-small.svg"
ICO_PATH = PROJECT_ROOT / "icon.ico"
EXPECTED_ICO_SIZES = {16, 24, 32, 48, 64, 128, 256}
EXPECTED_BMPS = {
    "installer-wizard.bmp": (164, 314),
    "installer-wizard_2x.bmp": (328, 628),
    "installer-header.bmp": (55, 58),
    "installer-header_2x.bmp": (110, 116),
}
SVG = "{http://www.w3.org/2000/svg}"


def _load_branding_generator() -> ModuleType:
    """Load the branding helper without its optional rendering dependencies."""
    script = PROJECT_ROOT / "scripts" / "build_branding.py"
    spec = importlib.util.spec_from_file_location("build_branding", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_icon_sources_share_one_mark_on_their_own_grids() -> None:
    """Both sources draw a tile, a lunette, and three panes on their own grid."""
    for path, view_box in ((ICON_SVG, "0 0 256 256"), (SMALL_ICON_SVG, "0 0 16 16")):
        root = ET.parse(path).getroot()

        assert root.get("viewBox") == view_box
        assert next(root.iter(f"{SVG}rect")).get("id") == "tile"
        assert len(root.findall(f".//{SVG}g/{SVG}path")) == 1
        assert len(root.findall(f".//{SVG}g/{SVG}rect")) == 3


def test_small_icon_edges_lie_on_the_pixel_grid() -> None:
    """Every pane edge of the small icon is a whole pixel at 16 pixels."""
    root = ET.parse(SMALL_ICON_SVG).getroot()
    for pane in root.findall(f".//{SVG}g/{SVG}rect"):
        for attribute in ("x", "y", "width", "height"):
            assert float(pane.get(attribute)).is_integer()


def test_wizard_mark_drops_only_the_tile() -> None:
    """The wizard panel reuses the icon geometry without its background tile."""
    generator = _load_branding_generator()

    mark = ET.fromstring(generator.mark_without_tile(ICON_SVG.read_text(encoding="utf-8")))

    assert all(element.get("id") != "tile" for element in mark.iter())
    assert len(mark.findall(f".//{SVG}g/{SVG}rect")) == 3


def test_icon_contains_all_uncompressed_bgra_resolutions() -> None:
    """The Windows icon contains every documented directly rendered size."""
    payload = ICO_PATH.read_bytes()
    reserved, image_type, count = struct.unpack_from("<HHH", payload)

    assert (reserved, image_type, count) == (0, 1, len(EXPECTED_ICO_SIZES))
    sizes: set[int] = set()
    for index in range(count):
        (
            width_byte,
            height_byte,
            _color_count,
            _reserved,
            _planes,
            bits_per_pixel,
            image_size,
            image_offset,
        ) = struct.unpack_from("<BBBBHHII", payload, 6 + 16 * index)
        width = width_byte or 256
        height = height_byte or 256
        sizes.add(width)
        assert height == width
        assert bits_per_pixel == 32
        assert image_offset + image_size <= len(payload)
        assert struct.unpack_from("<I", payload, image_offset)[0] == 40

    assert sizes == EXPECTED_ICO_SIZES


def test_installer_bitmaps_are_uncompressed_24_bit_windows_assets() -> None:
    """Each committed Inno bitmap has its required canvas and pixel format."""
    for filename, expected_dimensions in EXPECTED_BMPS.items():
        payload = (PROJECT_ROOT / "branding" / filename).read_bytes()
        signature, declared_size, _reserved, pixel_offset = struct.unpack_from(
            "<2sI4sI",
            payload,
        )
        dib_size = struct.unpack_from("<I", payload, 14)[0]
        width, height, planes, bits_per_pixel, compression = struct.unpack_from(
            "<iiHHI",
            payload,
            18,
        )

        assert signature == b"BM"
        assert declared_size == len(payload)
        assert pixel_offset == 54
        assert dib_size == 40
        assert (width, height) == expected_dimensions
        assert planes == 1
        assert bits_per_pixel == 24
        assert compression == 0
