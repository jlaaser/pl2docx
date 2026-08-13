"""Screenshot canvas-based interactive PL elements at fetch time (Phase 5 subphase 2).

Handles elements like `pl-orbitaldiagram`/`pl-lewisstructure` (non-`print` mode) that
render a fabric.js `<canvas>` a student draws on directly - `python-docx` can't embed a
`<canvas>` at all, and unlike `pl2docx.svg_render`'s static SVG markup, a canvas's
content only exists after real JS execution against the current variant. That JS needs
a **live, authenticated** page load (this module drives Chromium via Playwright to the
real `instance_question` URL, with `pl2docx.pl_client.PLClient`'s session cookies
attached), not something that can be replayed from already-saved static HTML.

Confirmed real DOM shape (`pl-lewisstructure`/`pl-orbitaldiagram`, both course-authored
fabric.js elements in `pl-pitt-chem0110`): each wraps itself in a root `<div>` whose
class is exactly its own tag name (`.pl-lewisstructure`/`.pl-orbitaldiagram`), with a
sibling toolbar/controls block only present on the interactive question panel. Core PL's
`pl-drawing` does **not** follow the "container class == tag name" convention
(`.pl-drawing-container`, not `.pl-drawing`) - confirming this default guess (used by
`pl2docx.element_config.additional_interactive_tags` when `container_selector` isn't
explicitly configured) isn't universal, hence the explicit-override escape hatch.
Canvas init in both confirmed elements is a synchronous inline `<script>` immediately
after the element's markup (no async gap, no ready-event) - a plain `page.goto(url)`
(default `"load"` wait) is sufficient.

**Guaranteed replacement, success or failure**: every matched container is *always*
replaced with an `<img>` tag before this module returns - a real one
(`src="files/....png"`) on success, or a placeholder (`src=""`,
`alt="[interactive content unavailable]"`) on any failure (Chromium unavailable,
selector not found on the live page, screenshot timeout, etc.). This is deliberate, not
an oversight: leaving raw canvas/toolbar markup in place would let
`pl2docx.html_parser`'s generic-tag walk recurse into it and leak stray button-label
text into the rendered prompt. The empty-`src` placeholder needs no
`pl2docx.html_parser`/`pl2docx.element_renderer` changes at all -
`element_renderer._render_image`'s existing fallback already treats a falsy
`local_path` as "no file, use alt text".

**Toolbar default guess**: no single derived selector string covers every confirmed
real element (`.pl-lewisstructure-toolbars`, `.pl-orbitaldiagram-controls`/
`.pl-orbitaldiagram-toolbar`, core `pl-drawing`'s `.pl-drawing-sidebar` all differ) - see
`_DEFAULT_HIDE_SUFFIXES`. When `InteractivePreferences.hide_selectors` isn't explicitly
configured, this module tries a fixed candidate list of `f".{tag}{suffix}"` selectors
covering all three known real conventions; a selector matching nothing is a harmless
no-op (e.g. the answer panel, which has no toolbar at all). An explicit
`hide_selectors` list fully replaces this guessing, not merges with it.
"""

from __future__ import annotations

import logging
from pathlib import Path

from bs4 import BeautifulSoup

from pl2docx._browser import close_browser as _close_shared_browser
from pl2docx._browser import get_browser
from pl2docx.element_config import InteractivePreferences
from pl2docx.pl_client import PLClient

logger = logging.getLogger(__name__)

_NAV_TIMEOUT_MS = 20_000
_SCREENSHOT_TIMEOUT_MS = 20_000

#: Candidate toolbar/controls selector suffixes tried, in derivation order, when
#: `InteractivePreferences.hide_selectors` isn't explicitly configured - covers every
#: confirmed real naming convention (`pl-lewisstructure`'s `-toolbars`,
#: `pl-orbitaldiagram`'s `-controls`/`-toolbar`, core `pl-drawing`'s `-sidebar`). A
#: non-matching candidate is a harmless no-op.
_DEFAULT_HIDE_SUFFIXES = ["-toolbar", "-toolbars", "-controls", "-sidebar"]

_PLACEHOLDER_ALT = "[interactive content unavailable]"

_HIDE_JS = "els => els.forEach(el => el.style.display = 'none')"


def close_browser() -> None:
    """Tear down the shared Chromium browser/Playwright process, if one was started.

    Thin passthrough to `pl2docx._browser.close_browser` - safe to call alongside
    `pl2docx.svg_render.close_browser()` (both ultimately close the same shared
    browser, which is itself idempotent). This module doesn't cache its own
    `BrowserContext`s (unlike `svg_render.py`) - each `capture_interactive_elements`
    call opens and closes its own - so there's nothing else for this function to do.
    """
    _close_shared_browser()


def _resolve_hide_selectors(tag: str, prefs: InteractivePreferences) -> list[str]:
    if prefs.hide_selectors is not None:
        return prefs.hide_selectors
    return [f".{tag}{suffix}" for suffix in _DEFAULT_HIDE_SUFFIXES]


def capture_interactive_elements(
    client: PLClient,
    html: str,
    page_url: str,
    files_dir: Path,
    instance_question_id: int,
    interactive_tags: dict[str, InteractivePreferences],
) -> str:
    """Screenshot each configured interactive element's container, replacing it with an `<img>`.

    Parameters
    ----------
    client : PLClient
        Used for its authenticated session cookies (`playwright_cookies()`) - the
        live page must be loaded as the same authenticated user the HTML was
        originally fetched as.
    html : str
        Raw page HTML, as fetched (same HTML `pl2docx.fetch._download_images`
        operates on).
    page_url : str
        The live URL to navigate a headless browser to for this page - typically
        `client.instance_question_url(course_instance_id, instance_question_id)`.
    files_dir : pathlib.Path
        Directory (created if needed) to save captured screenshots into, alongside
        downloaded images.
    instance_question_id : int
        The page's `instance_question_id`, used as a filename prefix so captures
        from different questions never collide (same convention as
        `pl2docx.fetch._download_images`).
    interactive_tags : dict[str, InteractivePreferences]
        PL element tag name -> resolved capture preferences (typically
        `pl2docx.element_config.additional_interactive_tags`'s return value, which
        has already defaulted `container_selector`).

    Returns
    -------
    str
        `html` unchanged if no configured tag's container was found on the page.
        Otherwise, `html` with every matched container replaced by an `<img>` tag -
        see this module's docstring for why replacement always happens, success or
        failure, and never leaves raw markup behind.
    """
    soup = BeautifulSoup(html, "html.parser")
    per_tag_matches = {
        tag: soup.select(prefs.container_selector or f".{tag}")
        for tag, prefs in interactive_tags.items()
    }
    per_tag_matches = {tag: containers for tag, containers in per_tag_matches.items() if containers}
    if not per_tag_matches:
        return html

    files_dir.mkdir(parents=True, exist_ok=True)

    try:
        browser = get_browser()
        context = browser.new_context()
        context.add_cookies(client.playwright_cookies())
        page = context.new_page()
        page.goto(page_url, timeout=_NAV_TIMEOUT_MS)
    except Exception as exc:
        logger.warning("Failed to load %s for interactive-element capture: %s", page_url, exc)
        for containers in per_tag_matches.values():
            for container in containers:
                container.replace_with(_placeholder_img(soup))
        return str(soup)

    counter = 0
    try:
        for tag, prefs in interactive_tags.items():
            containers = per_tag_matches.get(tag)
            if not containers:
                continue
            selector = prefs.container_selector or f".{tag}"
            for hide_selector in _resolve_hide_selectors(tag, prefs):
                try:
                    page.locator(hide_selector).evaluate_all(_HIDE_JS)
                except Exception as exc:  # pragma: no cover - defensive, hiding is best-effort
                    logger.warning("Failed to hide %r before capturing %r: %s", hide_selector, tag, exc)
            for i, container in enumerate(containers):
                local_name = f"{instance_question_id}_{counter}_{tag}.png"
                counter += 1
                try:
                    page.locator(selector).nth(i).screenshot(
                        path=str(files_dir / local_name),
                        omit_background=True,
                        timeout=_SCREENSHOT_TIMEOUT_MS,
                    )
                    img_tag = soup.new_tag("img", src=f"files/{local_name}", alt=f"{tag} diagram")
                except Exception as exc:
                    logger.warning(
                        "Failed to capture interactive element %r (instance_question %d): %s",
                        tag,
                        instance_question_id,
                        exc,
                    )
                    img_tag = _placeholder_img(soup)
                container.replace_with(img_tag)
    finally:
        page.close()
        context.close()

    return str(soup)


def _placeholder_img(soup: BeautifulSoup):
    return soup.new_tag("img", src="", alt=_PLACEHOLDER_ALT)
