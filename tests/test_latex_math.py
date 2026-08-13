"""Tests for `pl2docx.latex_math`.

Needs a real LaTeX install (`latex`, `dvipng`) - self-skips (mirroring
`test_pl_client_integration.py`'s pattern for its own live-server
precondition) when they aren't found on `PATH`, rather than failing on a
machine without MiKTeX/TeX Live + dvipng.
"""

import shutil

import pytest

from pl2docx.latex_math import LatexRenderError, render_math_png

pytestmark = pytest.mark.skipif(
    shutil.which("latex") is None or shutil.which("dvipng") is None,
    reason="requires a LaTeX install (latex) and dvipng on PATH",
)


def _png_header_size(path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return width, height


def test_renders_plain_algebra_to_png():
    rendered = render_math_png("x^2 + y^2 = z^2", display_mode=False)
    assert rendered.png_path.is_file()
    width, height = _png_header_size(rendered.png_path)
    assert width > 0 and height > 0
    assert rendered.depth_pt >= 0.0


def test_width_in_uses_real_dpi_not_png_metadata():
    """Regression: dvipng writes a fixed ~96 DPI pHYs chunk regardless of the
    -D value actually used to rasterize, so trusting that PNG metadata for
    sizing (rather than computing width_in from real pixel width / the DPI
    we requested) silently inflated every embedded equation ~6x - confirmed
    by the user against real rendered output."""
    dpi = 600
    rendered = render_math_png("x^2 + y^2 = z^2", display_mode=False, dpi=dpi)
    px_width, _ = _png_header_size(rendered.png_path)
    assert rendered.width_in == pytest.approx(px_width / dpi)
    # A small inline snippet at a normal document font size must come out on
    # the order of an inch or two wide, not several times that - a coarse
    # guard against a similar unit-conversion regression in the future.
    assert rendered.width_in < 2.0


def test_renders_display_mode_larger_than_inline_for_same_content():
    inline = render_math_png("\\sum_{i=1}^n i", display_mode=False)
    display = render_math_png("\\sum_{i=1}^n i", display_mode=True)
    _, inline_h = _png_header_size(inline.png_path)
    _, display_h = _png_header_size(display.png_path)
    # Display style draws \sum larger (and centered) than inline/text style.
    assert display_h > inline_h


def test_renders_mhchem_chemistry_formula():
    """This is the whole reason for choosing a real LaTeX install over a
    pure-Python converter - \\ce{} is a real mhchem macro, not core LaTeX math."""
    rendered = render_math_png("\\ce{H2O}", display_mode=False)
    assert rendered.png_path.is_file()
    width, height = _png_header_size(rendered.png_path)
    assert width > 0 and height > 0


def test_fraction_reports_nonzero_depth():
    """A fraction's denominator extends below the LaTeX baseline - depth_pt
    is what lets element_renderer.py correct the embedded image's vertical
    position (confirmed necessary by the user against real fraction/
    subscript-heavy course content, which floated wrong without it)."""
    rendered = render_math_png("\\frac{1}{2}", display_mode=False)
    assert rendered.depth_pt > 0.0


def test_repeated_formula_is_cached():
    first = render_math_png("a+b", display_mode=False)
    second = render_math_png("a+b", display_mode=False)
    assert first.png_path == second.png_path


def test_malformed_latex_raises_latex_render_error():
    with pytest.raises(LatexRenderError):
        render_math_png("\\frac{1}{", display_mode=False)


def test_missing_latex_binary_raises_latex_render_error(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _name: None)
    with pytest.raises(LatexRenderError):
        render_math_png("z^2", display_mode=False, font_size_pt=12, dpi=200)
