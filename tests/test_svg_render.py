"""Tests for `pl2docx.svg_render`.

Needs a real Chromium browser installed via Playwright (`playwright install
chromium`) - self-skips (mirroring `test_latex_math.py`'s pattern for its own
LaTeX-install precondition) when Chromium can't actually be launched, rather
than failing on a machine that hasn't run that one-time install step.
"""

import pytest

import pl2docx.svg_render as svg_render_module
from pl2docx.svg_render import SvgRenderError, render_svg_png
from conftest import chromium_available

pytestmark = pytest.mark.skipif(
    not chromium_available(), reason="requires Playwright's Chromium browser to be installed"
)

_LEWIS_STRUCTURE_SVG = (
    '<svg viewBox="0 0 100 60" width="100" height="60" '
    'xmlns="http://www.w3.org/2000/svg" class="lewis-structure" role="img" '
    'aria-label="Lewis structure diagram">'
    '<line x1="10" y1="10" x2="90" y2="50" stroke="black" stroke-width="2"/>'
    '<circle cx="10" cy="10" r="4" fill="black"/>'
    "</svg>"
)


def _png_header_size(path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return width, height


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Every test starts from a clean cache - module-level state (see svg_render.py's
    own docstring on `_cache`) would otherwise leak between tests within one process."""
    svg_render_module._cache.clear()
    yield
    svg_render_module._cache.clear()


def test_renders_svg_with_explicit_size_to_png():
    rendered = render_svg_png(_LEWIS_STRUCTURE_SVG)
    assert rendered.png_path.is_file()
    width, height = _png_header_size(rendered.png_path)
    assert width > 0 and height > 0
    assert rendered.width_in == pytest.approx(100 / 96)


def test_width_in_independent_of_dpi():
    """dpi controls raster sharpness only, not the physical embed size - see
    RenderedSvg.width_in's docstring, matching latex_math.RenderedMath's own
    dpi/width_in decoupling."""
    low_dpi = render_svg_png(_LEWIS_STRUCTURE_SVG, dpi=150)
    high_dpi = render_svg_png(_LEWIS_STRUCTURE_SVG, dpi=600)
    assert low_dpi.width_in == pytest.approx(high_dpi.width_in)

    low_px, _ = _png_header_size(low_dpi.png_path)
    high_px, _ = _png_header_size(high_dpi.png_path)
    assert high_px > low_px


def test_renders_svg_with_only_viewbox():
    """A viewBox-only SVG (no explicit width/height) has no CSS-guaranteed
    intrinsic size - the browser's own default-object-size algorithm decides
    it (see svg_render.py's render_svg_png for why this module doesn't
    hand-parse viewBox itself). Just confirm this doesn't blow up to the
    full page/viewport width (a real bug this module's shrink-to-fit
    wrapper specifically guards against), not an exact pixel value."""
    svg = (
        '<svg viewBox="0 0 40 20" xmlns="http://www.w3.org/2000/svg">'
        '<rect width="40" height="20" fill="blue"/></svg>'
    )
    rendered = render_svg_png(svg)
    assert rendered.png_path.is_file()
    assert 0 < rendered.width_in <= 5.0


def test_renders_svg_with_no_sizing_info_stays_print_reasonable():
    """An <svg> with no width/height/viewBox has no CSS-guaranteed intrinsic
    size at all - confirms this still renders (doesn't error out) and stays
    bounded to a print-reasonable size (this module's fixed fallback
    viewport, not an arbitrary/huge embed) rather than this module
    hand-rolling its own sizing fallback logic."""
    svg = '<svg xmlns="http://www.w3.org/2000/svg"><rect width="100%" height="100%" fill="red"/></svg>'
    rendered = render_svg_png(svg)
    assert rendered.png_path.is_file()
    assert 0 < rendered.width_in < 6.0


def test_repeated_svg_is_cached():
    first = render_svg_png(_LEWIS_STRUCTURE_SVG)
    second = render_svg_png(_LEWIS_STRUCTURE_SVG)
    assert first.png_path == second.png_path


def test_malformed_markup_raises_svg_render_error():
    with pytest.raises(SvgRenderError):
        render_svg_png("<p>not an svg at all</p>")


def test_close_browser_is_idempotent():
    svg_render_module.close_browser()
    svg_render_module.close_browser()
    # And rendering afterward still works (browser relaunches lazily).
    rendered = render_svg_png(_LEWIS_STRUCTURE_SVG)
    assert rendered.png_path.is_file()
