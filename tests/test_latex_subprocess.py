"""Tests for `pl2docx.latex_math`'s external-tool runner (`_run_tool`).

Unlike `test_latex_math.py`, these don't need a LaTeX install: they drive
`_run_tool` with small Python child processes standing in for `latex`/
`dvipng`, so they run everywhere.
"""

import subprocess
import sys
import time

import pytest

import pl2docx.latex_math as latex_math_module
from pl2docx.latex_math import LatexRenderError, _run_tool, render_math_png


def test_run_tool_captures_output_and_return_code():
    result = _run_tool([sys.executable, "-c", "print('depth=7'); raise SystemExit(3)"], 30)
    assert result.returncode == 3
    assert "depth=7" in result.stdout


def test_run_tool_gives_child_empty_stdin():
    # A tool that tries to read console input must see EOF immediately, not
    # block waiting on the terminal (e.g. a TeX error/install prompt).
    result = _run_tool([sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"], 30)
    assert result.stdout.strip() == "''"


def test_run_tool_timeout_kills_grandchild_holding_pipes():
    # Mimics MiKTeX's launcher -> engine structure: the direct child spawns a
    # grandchild that inherits stdout/stderr and hangs. A plain
    # `subprocess.run(capture_output=True, timeout=...)` blocks until the
    # grandchild exits (60s here) on Windows; `_run_tool` must not.
    script = (
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "time.sleep(60)\n"
    )
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        _run_tool([sys.executable, "-c", script], 2)
    assert time.monotonic() - start < 20


def test_render_math_png_reports_tool_timeout_as_latex_render_error(monkeypatch):
    def _timeout(args, timeout_s):
        raise subprocess.TimeoutExpired(args, timeout_s)

    monkeypatch.setattr("shutil.which", lambda name: name)
    monkeypatch.setattr(latex_math_module, "_run_tool", _timeout)
    with pytest.raises(LatexRenderError, match="timed out"):
        render_math_png("x_{timeout-test}", display_mode=False)
