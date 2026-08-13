"""Rasterize SVG markup to a print-resolution PNG via a real headless browser.

Handles **any** SVG reaching a fetched question page, regardless of how it got
there — a raw `<svg>...</svg>` block an instructor embedded directly, a custom
course element's print/static mode injecting inline SVG (confirmed real case:
`pl-lewisstructure`'s `print="true"` mode), or an `<img src="....svg">`
reference (confirmed real case: `<pl-figure>`, a core PL element, always
renders to a bare `<img>` regardless of the referenced file's type — see
`pl2docx.element_renderer`'s `.svg`-suffix dispatch for that second case).
Detection is keyed purely on `<svg>` tag presence / `.svg` file extension,
never on which PL element produced the markup.

Uses a headless Chromium browser (Playwright) rather than a pure-Python SVG
library (e.g. cairosvg) for full CSS/SVG feature fidelity, and because a
later, separate feature (screenshotting canvas-based interactive elements)
will need the same headless-browser technology anyway — one browser-
automation dependency, not two rendering technologies.

**Sizing is measured from the rendered page, not hand-parsed from markup.**
Not every SVG reliably sets both `width`/`height` and `viewBox` (only
confirmed true for `pl-lewisstructure`'s own output) - hand-parsing those
attributes with our own fallback chain would silently misjudge any SVG that
doesn't follow that exact pattern. Instead this lets the browser do what it
already does correctly: load the SVG into a page, then read back the
element's actual rendered box via Playwright's `locator.bounding_box()`. An
SVG with no explicit `width`/`height` has no CSS-guaranteed intrinsic size at
all - its `auto` sizing resolves as a *percentage* of its containing block,
not a fixed spec default (confirmed empirically, see `_FALLBACK_VIEWPORT`'s
docstring) - so this module renders inside a modest, fixed-size viewport
specifically to keep that edge case's output print-reasonable, while real
content with explicit sizing (the confirmed real-world case) measures its
own true size regardless.

Requires a Chromium browser installed via Playwright (`playwright install
chromium`, a one-time step separate from `pip`/`uv` installing the
`playwright` package itself). Callers must catch `SvgRenderError` and degrade
gracefully (as `element_renderer.py` does) rather than assume Chromium is
always present.
"""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Browser, BrowserContext, Error as PlaywrightError, sync_playwright

_DEFAULT_DPI = 600
_RENDER_TIMEOUT_MS = 20_000

#: CSS reference pixel, per the W3C spec (96px/inch) - same constant
#: `element_renderer.py` uses for `<img width>` sizing.
_CSS_PX_PER_INCH = 96

#: Fallback viewport for SVGs with no explicit `width`/`height`. An inline
#: <svg>'s CSS `width`/`height` default to `auto`, which - unlike an <img>'s
#: analogous "default object size" algorithm - resolves as a *percentage* of
#: the containing block (confirmed empirically: the same viewBox-only markup
#: rendered at the full ~1280px default viewport width when the page body
#: had one, and collapsed to zero size in a shrink-to-fit container with no
#: definite width at all). A modest, fixed viewport bounds this fallback to
#: a print-reasonable size instead of an arbitrary/huge embed - real content
#: with explicit width/height/viewBox (the confirmed real-world case, see
#: this module's docstring) measures its own true size regardless of this
#: viewport, so this only affects the "no sizing info at all" edge case.
_FALLBACK_VIEWPORT = {"width": 480, "height": 480}


class SvgRenderError(Exception):
    """Raised when SVG markup could not be rasterized to a PNG.

    Covers: Playwright's Chromium browser not installed, markup with no
    `<svg>` element (or one with no measurable size), or the render timing
    out. Callers should catch this and fall back to alt text, mirroring
    `pl2docx.latex_math.LatexRenderError`.
    """


@dataclass(frozen=True)
class RenderedSvg:
    """An SVG rasterized to a PNG, with what's needed to embed it correctly.

    Parameters
    ----------
    png_path : Path
        Absolute path to the rendered PNG. Valid for the lifetime of this
        process's SVG-rendering cache directory.
    width_in : float
        The image's intended physical width, in inches - the browser's own
        measured rendered width (CSS px, via `locator.bounding_box()`)
        divided by 96 (CSS px/inch). Independent of the `dpi` the PNG was
        rasterized at - `dpi` only controls raster sharpness, not physical
        embed size, matching how `pl2docx.latex_math.RenderedMath.width_in`
        already decouples the two. Callers should pass this explicitly as
        `add_picture`'s `width=`, not rely on auto-sizing.
    """

    png_path: Path
    width_in: float


#: In-process cache, keyed by (sha256 of svg_markup, dpi), so the same
#: diagram (e.g. shared between a blank instance and its answer key) is only
#: rasterized once per run. Module-level and process-lifetime, matching
#: `latex_math.py`'s cache - pl2docx's CLI entry points are one-shot process
#: invocations.
_cache: dict[tuple[str, int], RenderedSvg] = {}
_cache_dir: Path | None = None

_playwright_ctx = None
_browser: Browser | None = None
_contexts: dict[int, BrowserContext] = {}


def _get_cache_dir() -> Path:
    global _cache_dir
    if _cache_dir is None:
        _cache_dir = Path(tempfile.mkdtemp(prefix="pl2docx_svg_"))
    return _cache_dir


def _get_browser() -> Browser:
    global _playwright_ctx, _browser
    if _browser is None:
        try:
            _playwright_ctx = sync_playwright().start()
            _browser = _playwright_ctx.chromium.launch()
        except Exception as exc:
            _playwright_ctx = None
            _browser = None
            raise SvgRenderError(
                "Playwright's Chromium browser is not available - after installing "
                "pl2docx's dependencies, run `playwright install chromium` once."
            ) from exc
    return _browser


def _get_context(dpi: int) -> BrowserContext:
    """Return (creating if needed) the browser context for `dpi`'s device-scale-factor.

    Playwright has no direct "rasterization DPI" knob, only a per-context
    `device_scale_factor` (screenshot px per CSS px) - mapped here as
    `dpi / 96`, so `dpi=600` (matching `latex_math.py`'s default) yields a
    ~6.25x scale factor. Contexts are cached per `dpi` value since that's the
    only thing that varies call to call in this module.
    """
    context = _contexts.get(dpi)
    if context is None:
        context = _get_browser().new_context(
            device_scale_factor=dpi / _CSS_PX_PER_INCH, viewport=_FALLBACK_VIEWPORT
        )
        _contexts[dpi] = context
    return context


def close_browser() -> None:
    """Explicitly tear down the shared browser/Playwright process, if one was started.

    pl2docx's CLI entry points (`fetch.py`/`render.py`) are one-shot process
    invocations, so calling this is optional - Python process teardown would
    eventually reap the underlying subprocess anyway - but it avoids a
    dangling-driver warning / slower exit. Safe to call when no browser was
    ever launched (no-op).
    """
    global _playwright_ctx, _browser, _contexts
    for context in _contexts.values():
        context.close()
    _contexts = {}
    if _browser is not None:
        _browser.close()
        _browser = None
    if _playwright_ctx is not None:
        _playwright_ctx.stop()
        _playwright_ctx = None


def render_svg_png(svg_markup: str, *, dpi: int = _DEFAULT_DPI) -> RenderedSvg:
    """Rasterize `svg_markup` to a tightly-cropped, print-resolution PNG.

    Parameters
    ----------
    svg_markup : str
        A standalone `<svg ...>...</svg>` fragment (as produced by
        `pl2docx.html_parser.SvgRef.svg_markup`, or read verbatim from a
        local `.svg` file referenced by an `<img src>`).
    dpi : int
        Rasterization resolution - controls raster sharpness only, not the
        physical embed size (see `RenderedSvg.width_in`).

    Returns
    -------
    RenderedSvg
        The rendered PNG's path plus its intended physical width. Valid for
        the lifetime of this process's SVG-rendering cache directory - copy
        `png_path` out if it needs to outlive this process.

    Raises
    ------
    SvgRenderError
        If Chromium isn't installed, `svg_markup` contains no `<svg>`
        element (or one with no measurable rendered size), or the render
        times out.
    """
    digest = hashlib.sha256(svg_markup.encode("utf-8")).hexdigest()
    cache_key = (digest, dpi)
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    context = _get_context(dpi)
    page = context.new_page()
    try:
        page.set_content(
            f"<!doctype html><html><body style='margin:0'>{svg_markup}</body></html>",
            timeout=_RENDER_TIMEOUT_MS,
        )
        locator = page.locator("svg").first
        try:
            box = locator.bounding_box(timeout=_RENDER_TIMEOUT_MS)
        except PlaywrightError as exc:
            raise SvgRenderError(f"No <svg> element found in the given markup: {exc}") from exc
        if box is None or box["width"] <= 0 or box["height"] <= 0:
            raise SvgRenderError("SVG has no measurable rendered size (zero width/height, or not found).")

        png_path = _get_cache_dir() / f"{digest[:16]}_{dpi}.png"
        locator.screenshot(path=str(png_path), omit_background=True, timeout=_RENDER_TIMEOUT_MS)
    except PlaywrightError as exc:
        raise SvgRenderError(f"Failed to rasterize SVG: {exc}") from exc
    finally:
        page.close()

    rendered = RenderedSvg(png_path=png_path, width_in=box["width"] / _CSS_PX_PER_INCH)
    _cache[cache_key] = rendered
    return rendered
