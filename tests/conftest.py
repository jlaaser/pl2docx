import functools

import pytest

from pl2docx.starter_template import build_starter_template


@functools.lru_cache(maxsize=1)
def chromium_available() -> bool:
    """Whether Playwright's Chromium browser can actually be launched.

    Used to skip-gate SVG-rendering tests that need a real browser (test_svg_render.py,
    test_element_renderer.py, test_html_parser.py), mirroring test_latex_math.py's
    `shutil.which`-based skip for the LaTeX toolchain. Cached (module-lifetime) since
    actually launching Chromium to check is comparatively expensive to redo per test.
    """
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            browser.close()
        return True
    except Exception:
        return False


@pytest.fixture
def starter_template(tmp_path):
    """A real generated pl2docx starter template (see `pl2docx.starter_template`).

    Generated at test time (docx is a binary format, not something to hand-author
    as a checked-in text fixture) rather than committed to the repo. Using the
    actual generator here (rather than a hand-rolled minimal stand-in) keeps tests
    exercising the same template shape real usage does.
    """
    path = tmp_path / "template.docx"
    build_starter_template(path)
    return path
