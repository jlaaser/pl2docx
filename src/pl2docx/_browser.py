"""Shared headless-Chromium browser lifecycle.

Used by both `pl2docx.svg_render` (static SVG rasterization) and
`pl2docx.canvas_capture` (live-page canvas screenshotting) so the two
features share one Chromium process instead of each launching its own -
launching a browser is comparatively expensive, and there's no reason to pay
that cost twice in the same process. Each caller still creates its own
`BrowserContext`(s) from the shared `Browser` (they need different
configurations - `svg_render.py`'s are cookie-less and DPI-keyed;
`canvas_capture.py`'s carry `pl2docx.pl_client.PLClient`'s auth cookies).
"""

from __future__ import annotations

from playwright.sync_api import Browser, sync_playwright

_playwright_ctx = None
_browser: Browser | None = None


class BrowserUnavailableError(Exception):
    """Raised when Playwright's Chromium browser can't be launched.

    Covers Chromium not being installed via `playwright install chromium` (a
    one-time step separate from installing the `playwright` pip package
    itself). Callers should catch this and translate it into their own
    module's error type / fallback behavior rather than let it propagate
    verbatim - see `pl2docx.svg_render.SvgRenderError` for the pattern.
    """


def get_browser() -> Browser:
    """Return the shared Chromium `Browser`, launching it lazily on first use.

    Raises
    ------
    BrowserUnavailableError
        If Chromium isn't installed / can't be launched.
    """
    global _playwright_ctx, _browser
    if _browser is None:
        try:
            _playwright_ctx = sync_playwright().start()
            _browser = _playwright_ctx.chromium.launch()
        except Exception as exc:
            _playwright_ctx = None
            _browser = None
            raise BrowserUnavailableError(
                "Playwright's Chromium browser is not available - after installing "
                "pl2docx's dependencies, run `playwright install chromium` once."
            ) from exc
    return _browser


def close_browser() -> None:
    """Tear down the shared browser/Playwright process, if one was started.

    Safe to call multiple times (e.g. once per feature module that used the
    browser) and safe to call when no browser was ever launched (no-op).
    Callers are responsible for closing any `BrowserContext`/`Page`s they
    created themselves *before* calling this.
    """
    global _playwright_ctx, _browser
    if _browser is not None:
        _browser.close()
        _browser = None
    if _playwright_ctx is not None:
        _playwright_ctx.stop()
        _playwright_ctx = None
