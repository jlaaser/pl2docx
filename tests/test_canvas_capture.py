"""Tests for `pl2docx.canvas_capture`.

Needs a real Chromium browser installed via Playwright (`playwright install
chromium`) - self-skips (mirrors `test_svg_render.py`'s pattern) when
Chromium can't actually be launched. Exercised against a small local static
HTML fixture served over a local HTTP server (not the real PL dev server),
so this stays offline/CI-safe and doesn't need a `PLClient` session against
anything real - a stub with no cookies is enough.
"""

import functools
import http.server
import re
import threading

import pytest

import pl2docx.canvas_capture as canvas_capture_module
from pl2docx.canvas_capture import capture_interactive_elements
from pl2docx.element_config import InteractivePreferences
from conftest import chromium_available

pytestmark = pytest.mark.skipif(
    not chromium_available(), reason="requires Playwright's Chromium browser to be installed"
)

#: Container class matches the (fake) tag name verbatim, same convention confirmed
#: for pl-lewisstructure/pl-orbitaldiagram. Toolbar uses the "-toolbar" suffix, one
#: of canvas_capture._DEFAULT_HIDE_SUFFIXES' candidates.
_FIXTURE_HTML = """<!doctype html>
<html><body>
<div class="fake-widget">
  <div class="fake-widget-toolbar"><button>Hidden button</button></div>
  <div class="fake-widget-canvas-wrap">
    <canvas id="c" width="100" height="60"></canvas>
  </div>
</div>
<script>
  var ctx = document.getElementById('c').getContext('2d');
  ctx.fillStyle = 'red';
  ctx.fillRect(10, 10, 50, 30);
</script>
</body></html>
"""

_EMPTY_HTML = "<!doctype html><html><body><p>nothing here</p></body></html>"


class _StubClient:
    def playwright_cookies(self):
        return []


@pytest.fixture
def start_server(tmp_path):
    """Factory fixture: `start_server(html)` serves `html` over a local HTTP server,
    returning its URL. Multiple distinct pages can be started (each gets its own
    subdirectory); all servers are torn down at test end."""
    servers = []
    counter = [0]

    def _start(html: str) -> str:
        counter[0] += 1
        page_dir = tmp_path / f"page{counter[0]}"
        page_dir.mkdir()
        (page_dir / "index.html").write_text(html, encoding="utf-8")
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(page_dir))
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append((server, thread))
        return f"http://127.0.0.1:{server.server_port}/index.html"

    yield _start

    for server, thread in servers:
        server.shutdown()
        thread.join(timeout=5)


def _png_height(path) -> int:
    data = path.read_bytes()
    return int.from_bytes(data[20:24], "big")


def test_captures_container_and_replaces_with_real_image(start_server, tmp_path):
    url = start_server(_FIXTURE_HTML)
    files_dir = tmp_path / "files"
    interactive_tags = {
        "fake-widget": InteractivePreferences(container_selector=".fake-widget", hide_selectors=[])
    }

    result = capture_interactive_elements(_StubClient(), _FIXTURE_HTML, url, files_dir, 1, interactive_tags)

    assert "<canvas" not in result
    assert "<div" not in result  # the whole container div was replaced, not just the canvas
    assert 'src="files/1_0_fake-widget.png"' in result
    saved = list(files_dir.glob("*.png"))
    assert len(saved) == 1
    assert saved[0].is_file()


def test_explicit_hide_selectors_shrink_the_captured_container(start_server, tmp_path):
    """Hiding the toolbar before screenshotting must actually reduce the container's
    rendered (and thus captured) height, since the toolbar is a sibling block above
    the canvas - a real, checkable proxy for "the toolbar doesn't appear"."""
    url = start_server(_FIXTURE_HTML)
    files_dir = tmp_path / "files"

    with_hide = {
        "fake-widget": InteractivePreferences(
            container_selector=".fake-widget", hide_selectors=[".fake-widget-toolbar"]
        )
    }
    without_hide = {
        "fake-widget": InteractivePreferences(container_selector=".fake-widget", hide_selectors=[])
    }

    capture_interactive_elements(_StubClient(), _FIXTURE_HTML, url, files_dir, 1, with_hide)
    capture_interactive_elements(_StubClient(), _FIXTURE_HTML, url, files_dir, 2, without_hide)

    hidden_png = next(files_dir.glob("1_*.png"))
    visible_png = next(files_dir.glob("2_*.png"))
    assert _png_height(hidden_png) < _png_height(visible_png)


def test_default_container_guess_prefers_canvas_wrap_over_root(start_server, tmp_path):
    """Fully-default preferences (container_selector AND hide_selectors both None)
    must select the tighter `.fake-widget-canvas-wrap`, not the whole `.fake-widget`
    root - excluding the toolbar entirely this way should produce the same
    (shorter) height as explicitly hiding the toolbar within the root container."""
    url = start_server(_FIXTURE_HTML)
    files_dir = tmp_path / "files"

    fully_default = {"fake-widget": InteractivePreferences()}
    root_with_explicit_hide = {
        "fake-widget": InteractivePreferences(
            container_selector=".fake-widget", hide_selectors=[".fake-widget-toolbar"]
        )
    }

    capture_interactive_elements(_StubClient(), _FIXTURE_HTML, url, files_dir, 1, fully_default)
    capture_interactive_elements(_StubClient(), _FIXTURE_HTML, url, files_dir, 2, root_with_explicit_hide)

    default_png = next(files_dir.glob("1_*.png"))
    hidden_root_png = next(files_dir.glob("2_*.png"))
    assert _png_height(default_png) == _png_height(hidden_root_png)


def test_captured_image_gets_upscaled_width_attribute(start_server, tmp_path):
    """The replacement <img> must carry a `width` attribute derived from the
    captured element's own CSS bounding box (times _EMBED_SCALE) - not be left
    unset (which would silently fall back to element_renderer's fixed 3in
    default, too small for a diagram meant to be a question's main content)."""
    url = start_server(_FIXTURE_HTML)
    files_dir = tmp_path / "files"
    interactive_tags = {"fake-widget": InteractivePreferences(container_selector=".fake-widget-canvas-wrap")}

    result = capture_interactive_elements(_StubClient(), _FIXTURE_HTML, url, files_dir, 1, interactive_tags)

    match = re.search(r'width="(\d+)"', result)
    assert match is not None
    width_px = int(match.group(1))
    # The canvas itself is 100px wide; the wrapping div is at least that wide,
    # and _EMBED_SCALE (1.25) must have been applied on top of it.
    assert width_px >= round(100 * canvas_capture_module._EMBED_SCALE)


def test_default_hide_selector_guess_matches_toolbar_suffix(start_server, tmp_path):
    """`hide_selectors=None` (not explicitly configured) must fall back to trying
    `_DEFAULT_HIDE_SUFFIXES` candidates - the fixture's `.fake-widget-toolbar` matches
    the "-toolbar" candidate, so the default guess should shrink the capture exactly
    like an explicit hide_selectors=[".fake-widget-toolbar"] does."""
    url = start_server(_FIXTURE_HTML)
    files_dir = tmp_path / "files"

    default_guess = {"fake-widget": InteractivePreferences(container_selector=".fake-widget")}
    without_hide = {
        "fake-widget": InteractivePreferences(container_selector=".fake-widget", hide_selectors=[])
    }

    capture_interactive_elements(_StubClient(), _FIXTURE_HTML, url, files_dir, 1, default_guess)
    capture_interactive_elements(_StubClient(), _FIXTURE_HTML, url, files_dir, 2, without_hide)

    guessed_png = next(files_dir.glob("1_*.png"))
    visible_png = next(files_dir.glob("2_*.png"))
    assert _png_height(guessed_png) < _png_height(visible_png)


def test_capture_failure_produces_placeholder_not_raw_markup(start_server, tmp_path, monkeypatch):
    """A container present in the static HTML but absent from the live page (e.g. a
    stale/broken selector) must degrade to the empty-src placeholder <img> - never
    leave the raw <div class="fake-widget">...<canvas>...</canvas>...</div> markup
    behind for html_parser.py to walk into and leak toolbar text from."""
    monkeypatch.setattr(canvas_capture_module, "_NAV_TIMEOUT_MS", 2000)
    monkeypatch.setattr(canvas_capture_module, "_SCREENSHOT_TIMEOUT_MS", 2000)

    empty_page_url = start_server(_EMPTY_HTML)
    files_dir = tmp_path / "files"
    interactive_tags = {
        "fake-widget": InteractivePreferences(container_selector=".fake-widget", hide_selectors=[])
    }

    result = capture_interactive_elements(
        _StubClient(), _FIXTURE_HTML, empty_page_url, files_dir, 1, interactive_tags
    )

    assert "<canvas" not in result
    assert "Hidden button" not in result
    assert 'src=""' in result
    assert "interactive content unavailable" in result
    assert not list(files_dir.glob("*.png"))


def test_no_matching_container_returns_html_unchanged(start_server, tmp_path):
    url = start_server(_FIXTURE_HTML)
    files_dir = tmp_path / "files"
    html = "<p>No interactive widget on this page.</p>"
    interactive_tags = {
        "fake-widget": InteractivePreferences(container_selector=".fake-widget", hide_selectors=[])
    }

    result = capture_interactive_elements(_StubClient(), html, url, files_dir, 1, interactive_tags)

    assert result == html
    assert not files_dir.exists()
