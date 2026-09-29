#!/usr/bin/env python3
"""Render the Fenestra icon and installer bitmaps from the branding sources.

Run the script from the repository root in an environment of its own, because
the pinned release environment must not gain packages::

    python -m venv build/branding-env
    build/branding-env/Scripts/python -m pip install pillow resvg-py
    build/branding-env/Scripts/python scripts/build_branding.py

The script writes ``icon.ico`` and the four Inno Setup wizard bitmaps under
``branding/``. Icon images up to 32 pixels come from the pixel-snapped
``fenestra-icon-small.svg``; larger images come from ``fenestra-icon.svg``.
Neither the application build nor the installer build runs this script; both
consume the committed outputs.
"""

from __future__ import annotations

import io
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BRANDING_DIR = PROJECT_ROOT / "branding"
ICON_SVG = BRANDING_DIR / "fenestra-icon.svg"
SMALL_ICON_SVG = BRANDING_DIR / "fenestra-icon-small.svg"
ICO_PATH = PROJECT_ROOT / "icon.ico"

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
SMALL_ICON_MAX = 32
SVG_NAMESPACE = "http://www.w3.org/2000/svg"

# The wizard panel matches the icon tile; the header matches the modern
# wizard's white page header.
WIZARD_BACKGROUND = "#15171C"
HEADER_BACKGROUND = "#FFFFFF"

# Each bitmap names its canvas size and the square artwork's size and origin.
BITMAPS = {
    "installer-wizard.bmp": {"canvas": (164, 314), "art": (100, 32, 56), "tile": False},
    "installer-wizard_2x.bmp": {"canvas": (328, 628), "art": (200, 64, 112), "tile": False},
    "installer-header.bmp": {"canvas": (55, 58), "art": (44, 5, 7), "tile": True},
    "installer-header_2x.bmp": {"canvas": (110, 116), "art": (88, 10, 14), "tile": True},
}


def render_png(svg_text: str, size: int):
    """Rasterize SVG markup to a square RGBA image.

    Parameters
    ----------
    svg_text : str
        The SVG document to render.
    size : int
        The output width and height in pixels.

    Returns
    -------
    PIL.Image.Image
        The rendered RGBA image.
    """
    import resvg_py
    from PIL import Image

    png = resvg_py.svg_to_bytes(svg_string=svg_text, width=size, height=size)
    return Image.open(io.BytesIO(bytes(png))).convert("RGBA")


def mark_without_tile(svg_text: str) -> str:
    """Return the icon markup with its background tile removed.

    Parameters
    ----------
    svg_text : str
        The full icon SVG, whose tile carries ``id="tile"``.

    Returns
    -------
    str
        The SVG markup of the window mark alone.

    Raises
    ------
    RuntimeError
        If the source has no element with ``id="tile"``.
    """
    ET.register_namespace("", SVG_NAMESPACE)
    root = ET.fromstring(svg_text)
    for parent in root.iter():
        for child in list(parent):
            if child.get("id") == "tile":
                parent.remove(child)
                return ET.tostring(root, encoding="unicode")
    raise RuntimeError(f"{ICON_SVG.name} has no element with id='tile'.")


def build_icon(icon_svg: str, small_svg: str) -> None:
    """Write ``icon.ico`` with one directly rendered image per size.

    Parameters
    ----------
    icon_svg : str
        The full-size icon markup.
    small_svg : str
        The pixel-snapped icon markup for small sizes.
    """
    images = [
        render_png(small_svg if size <= SMALL_ICON_MAX else icon_svg, size) for size in ICO_SIZES
    ]
    master = images[-1]
    # BMP entries keep every image as uncompressed 32-bit BGRA data.
    master.save(
        ICO_PATH,
        format="ICO",
        sizes=[(size, size) for size in ICO_SIZES],
        append_images=images[:-1],
        bitmap_format="bmp",
    )
    print(f"Wrote {ICO_PATH.relative_to(PROJECT_ROOT)} with sizes {ICO_SIZES}.")


def build_bitmaps(icon_svg: str) -> None:
    """Write the four 24-bit installer bitmaps under ``branding/``.

    Parameters
    ----------
    icon_svg : str
        The full-size icon markup.
    """
    from PIL import Image

    mark_svg = mark_without_tile(icon_svg)
    for name, spec in BITMAPS.items():
        size, left, top = spec["art"]
        background = HEADER_BACKGROUND if spec["tile"] else WIZARD_BACKGROUND
        art = render_png(icon_svg if spec["tile"] else mark_svg, size)
        canvas = Image.new("RGB", spec["canvas"], background)
        canvas.paste(art, (left, top), mask=art.getchannel("A"))
        canvas.save(BRANDING_DIR / name, format="BMP")
        print(f"Wrote branding/{name} at {spec['canvas'][0]}x{spec['canvas'][1]}.")


def main() -> int:
    """Render every branding output and report missing dependencies.

    Returns
    -------
    int
        The process exit code.
    """
    try:
        import PIL  # noqa: F401
        import resvg_py  # noqa: F401
    except ImportError:
        print("Install the helpers with `python -m pip install pillow resvg-py`.", file=sys.stderr)
        return 1
    icon_svg = ICON_SVG.read_text(encoding="utf-8")
    small_svg = SMALL_ICON_SVG.read_text(encoding="utf-8")
    build_icon(icon_svg, small_svg)
    build_bitmaps(icon_svg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
