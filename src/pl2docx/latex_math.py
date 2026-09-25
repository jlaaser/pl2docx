"""Render LaTeX math source to a print-resolution PNG via a real LaTeX install.

Chosen over a pure-Python LaTeX->OMML pipeline specifically because real
course content can use specialty LaTeX packages (e.g. `mhchem`'s `\\ce{...}`
macro for chemical formulas, confirmed in one course's `chemutils/
compounds.py` etc.) that general-purpose LaTeX->MathML converters don't
implement. Compiling through a real `latex` means any LaTeX construct a
course uses "just works" with no subset to maintain, at the cost of
non-editable (image, not native Word-equation) output. See `planning_notes/`
for the full comparison.

Only genuinely universal packages (`amsmath`/`amssymb`/`xcolor` - see
`_SOURCE_TEMPLATE`) are built in. Anything else a course's content needs
(`mhchem`, `siunitx`, ...) is **not** auto-detected from equation content -
that doesn't generalize (it would need a growing, hardcoded macro->package
table, and still wouldn't guarantee the package is actually installed) -
instead it's declared explicitly via `config.yaml`'s `latex-packages` and
wired in once per process via `configure_extra_packages`, mirroring
`pl2docx.element_config`'s `additional-elements` pattern: one config line,
no code change, for a package this module doesn't know about.

Pipeline: `latex` (not `pdflatex` - see below) compiles a `preview`-wrapped
snippet to a DVI, then `dvipng` rasterizes it. `dvipng --depth` is the reason
for going through DVI rather than PDF+`pdftoppm` (the increment 3b
approach): it reports exactly how far the rendered glyphs extend below the
LaTeX baseline (e.g. a fraction's denominator, a subscript), in pixels, at
the chosen DPI - `pdftoppm` has no equivalent, so increment 3b's PNGs had no
way to align with surrounding text baseline for anything beyond simple
same-line content (confirmed by the user: fractions/subscripts visibly
floated wrong). `pl2docx.element_renderer` uses `RenderedMath.depth_pt` to
apply a compensating `<w:position>` (OOXML's run-level baseline shift) to
the embedded picture's run.

Requires `latex` (any TeX distribution providing it, e.g. MiKTeX/TeX Live)
and `dvipng` on `PATH`. Neither is a Python dependency this package can pin,
unlike the rest of pl2docx's dependency list - callers must catch
`LatexRenderError` and degrade gracefully (as `element_renderer.py` does)
rather than assume these are always present.
"""

from __future__ import annotations

import hashlib
import os
import re
import signal
import shutil
import subprocess
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_FONT_SIZE_PT = 11
_DEFAULT_DPI = 600
_PREVIEW_BORDER_PT = 1
#: Total desired vertical whitespace above/below a display-mode equation's own
#: ink, in points - confirmed by the user (2026-08-22) that display-mode
#: equations were bumping right up against surrounding text, making them
#: harder to read. `_PREVIEW_BORDER_PT` already contributes 1pt of this
#: uniformly (all 4 sides, both display and inline); `_DISPLAY_EXTRA_VPAD_PT`
#: below makes up the rest, added only above/below (never left/right) and
#: only for display-mode content - inline math (`$...$`) is deliberately left
#: untouched, per the user's explicit ask.
_DISPLAY_VERTICAL_MARGIN_PT = 6
_DISPLAY_EXTRA_VPAD_PT = max(_DISPLAY_VERTICAL_MARGIN_PT - _PREVIEW_BORDER_PT, 0)
_LATEX_TIMEOUT_S = 20
_DVIPNG_TIMEOUT_S = 20
_KPSEWHICH_TIMEOUT_S = 10
#: How long to wait, after killing a timed-out tool's process tree, for its
#: output pipes to close before giving up on them - see `_run_tool`.
_KILL_GRACE_S = 5

#: `\boldmath` matches PrairieLearn's own MathJax rendering, which the user
#: confirmed (2026-08-12 visual review) uses a visibly heavier weight than
#: LaTeX's default math font - without it, rendered math reads noticeably
#: lighter than the surrounding document text. `\PreviewBorder` adds a small
#: margin around the `preview` package's otherwise pixel-tight bounding box,
#: fixing glyph-top clipping the user observed (ascenders with no descender
#: on the same line have essentially zero margin above them without this).
#: `amsmath`/`amssymb`/`xcolor` are the only packages loaded unconditionally
#: - genuinely universal, virtually every TeX install has them. Anything
#: else goes through `{extra_packages}`, filled in by `render_math_png` from
#: `configure_extra_packages`'s module-level state - see module docstring.
_SOURCE_TEMPLATE = r"""\documentclass[{size}pt]{{article}}
\usepackage[active,tightpage]{{preview}}
\setlength\PreviewBorder{{{border}pt}}
\usepackage{{amsmath}}
\usepackage{{amssymb}}
\usepackage{{xcolor}}
{extra_packages}
\pagestyle{{empty}}
\begin{{document}}
\begin{{preview}}
\boldmath {content}
\end{{preview}}
\end{{document}}
"""

_DEPTH_RE = re.compile(r"depth=(\d+)")

#: Two-or-more consecutive newlines (a blank line), possibly with trailing
#: horizontal whitespace on the blank line itself.
_BLANK_LINE_RE = re.compile(r"\n[ \t]*\n+")

#: Start of an mhchem `\ce{...}`/`\pu{...}` (chemical-formula/physical-unit
#: macros) call - see `_brace_mhchem_macros`.
_MHCHEM_MACRO_RE = re.compile(r"\\(?:ce|pu)\{")

#: A `_`/`^` (after optional whitespace) at the start of the remaining text.
_SCRIPT_MARKER_RE = re.compile(r"\s*[_^]")


def _collapse_blank_lines(latex: str) -> str:
    """Collapse blank lines in math source into a single interword space.

    Parameters
    ----------
    latex : str
        Raw LaTeX math source, as extracted verbatim (including whitespace)
        from a PrairieLearn question's HTML.

    Returns
    -------
    str
        `latex` with every run of 2+ consecutive newlines replaced by a
        single space.

    Notes
    -----
    Question authors sometimes hand-format a long equation across several
    indented lines with a blank line for visual separation (e.g. before a
    final "= result" line) - MathJax renders this without complaint, but a
    literal blank line inside real LaTeX math mode is a paragraph break and
    raises `! Missing $ inserted.`. A *single* newline is left untouched: TeX
    already treats it as an ordinary interword space, so it's not the
    problem case. This can turn a blank line inside a `\\text{...}` argument
    into a single space too, but a blank line there would already be
    unusual/meaningless content, not a case worth preserving exactly.
    """
    return _BLANK_LINE_RE.sub(" ", latex)


def _brace_mhchem_macros(latex: str) -> str:
    r"""Wrap `\ce{...}`/`\pu{...}` in braces where it's directly adjacent to `_`/`^`.

    Parameters
    ----------
    latex : str
        Raw LaTeX math source, as extracted verbatim from a PrairieLearn
        question's HTML.

    Returns
    -------
    str
        `latex` with each `\ce{...}`/`\pu{...}` call that is either the
        argument of a `_`/`^` (`V_\ce{CO2}`) or itself takes a `_`/`^`
        (`\ce{CaCl2}_{(aq)}`) wrapped in its own brace group
        (`V_{\ce{CO2}}`, `{\ce{CaCl2}}_{(aq)}`). Each macro's own closing
        brace is matched by depth-counting, so nested braces inside the
        argument (e.g. `\ce{CO2^{2+}}`) aren't cut short.

    Notes
    -----
    Two distinct real-LaTeX failures that MathJax's mhchem extension
    tolerates, both confirmed by minimal reproduction:

    - *Script argument*: mhchem's `\ce`/`\pu` are `xparse`-based and can't
      be grabbed as the single token `_`/`^` take when not in braces
      (`! Missing { inserted.` on `V_\ce{CO2}`).
    - *Script on `\ce`*: `\ce{...}` already ends in a script slot in real
      LaTeX, so a following `_`/`^` raises `! Double subscript.`
      (`\ce{CaCl2}_{(aq)}`); a brace group makes it an ordinary nucleus.

    A `\ce` that is both preceded and followed by a script marker is only
    wrapped once (a genuine double subscript in TeX, not something this can
    or should repair). Applies unconditionally (not gated on `mhchem` being
    in `_extra_packages`): if mhchem isn't loaded, `\ce`/`\pu` are undefined
    regardless of bracing, so this only changes which (still correct) error
    occurs.
    """
    out = []
    pos = 0
    for m in _MHCHEM_MACRO_RE.finditer(latex):
        if m.start() < pos:
            continue
        brace_open = m.end() - 1
        depth = 0
        close = None
        for i in range(brace_open, len(latex)):
            if latex[i] == "{":
                depth += 1
            elif latex[i] == "}":
                depth -= 1
                if depth == 0:
                    close = i
                    break
        if close is None:
            continue
        preceded = latex[pos : m.start()].rstrip().endswith(("_", "^"))
        followed = _SCRIPT_MARKER_RE.match(latex, close + 1) is not None
        out.append(latex[pos : m.start()])
        call = latex[m.start() : close + 1]
        out.append(f"{{{call}}}" if preceded or followed else call)
        pos = close + 1
    out.append(latex[pos:])
    return "".join(out)


class LatexRenderError(Exception):
    """Raised when LaTeX math source could not be rendered to a PNG.

    Covers: `latex`/`dvipng` missing from `PATH`, a LaTeX compile error (bad
    syntax, missing package), a `dvipng` failure, or either step timing out.
    Callers should catch this and fall back to plain-text rendering rather
    than let one bad/unsupported equation fail the whole document.
    """


class LatexPackageNotFoundError(Exception):
    """Raised by `configure_extra_packages` when a declared package isn't installed.

    Deliberately *not* a subclass of `LatexRenderError` - that exception is
    meant to be caught per-equation, mid-render, to degrade one bad/
    unsupported snippet to placeholder text without failing the whole
    document. A missing package declared in `config.yaml` is a startup
    configuration mistake instead: it affects every equation that would use
    it, and should fail the whole run loudly and immediately (a typo'd
    package name should never be caught by `element_renderer.py`'s
    equation-level `except LatexRenderError`).
    """


#: Extra `\usepackage{...}` names (config.yaml's `latex-packages`, beyond
#: this module's small built-in default set), set once per process by
#: `configure_extra_packages` - see module docstring for why this is
#: config-driven rather than auto-detected from equation content.
_extra_packages: list[str] = []


def configure_extra_packages(packages: Iterable[str]) -> None:
    """Register extra LaTeX packages to load in every subsequently-rendered equation.

    Parameters
    ----------
    packages : Iterable[str]
        Package names (no `.sty` extension, e.g. `["mhchem"]`) to
        `\\usepackage` in addition to this module's built-in
        `amsmath`/`amssymb`/`xcolor` set - typically `config.yaml`'s
        `latex-packages`, loaded once at CLI startup (`render.py`'s
        `main()`).

    Raises
    ------
    LatexPackageNotFoundError
        If `kpsewhich` is on `PATH` (i.e. a LaTeX install is present) and
        one or more of `packages` doesn't resolve via it - fails fast with
        every missing package name, rather than each affected equation
        silently degrading to placeholder text mid-render. Not raised at all
        when `kpsewhich` itself isn't found on `PATH` - that's the "no LaTeX
        install" case already handled gracefully by every `render_math_png`
        call's own fallback, so there's nothing to validate against.

    Notes
    -----
    Clears this module's render cache (see `_cache`) - a previously-cached
    PNG may have been rendered under a different package configuration
    (matters most for tests, which call this repeatedly with different
    package sets within one process).
    """
    packages = list(packages)
    kpsewhich_bin = shutil.which("kpsewhich")
    if kpsewhich_bin is not None:
        missing = [pkg for pkg in packages if not _package_resolves(kpsewhich_bin, pkg)]
        if missing:
            raise LatexPackageNotFoundError(
                f"Configured latex-packages not found in this LaTeX install: "
                f"{', '.join(missing)}. Check the package name(s) in config.yaml, or "
                "install them (e.g. via the MiKTeX/TeX Live package manager)."
            )

    global _extra_packages
    _extra_packages = packages
    _cache.clear()


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """Forcibly terminate `proc` and every process it spawned.

    Parameters
    ----------
    proc : subprocess.Popen
        A process started by `_run_tool` (on POSIX, as the leader of its own
        session/process group - see there).

    Notes
    -----
    Best-effort: never raises if the process (or its tree) has already
    exited. Falls back to killing just `proc` itself if the tree kill fails.
    """
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=_KILL_GRACE_S,
            )
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        proc.kill()
    except OSError:
        pass


def _run_tool(args: list[str], timeout_s: float) -> subprocess.CompletedProcess[str]:
    """Run an external TeX tool to completion, enforcing `timeout_s` on its whole process tree.

    Parameters
    ----------
    args : list[str]
        Command line: executable path followed by its arguments.
    timeout_s : float
        Maximum wall-clock time to let the tool run, in seconds.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The finished process's return code and captured stdout/stderr
        (decoded as text; undecodable bytes replaced rather than raising).

    Raises
    ------
    subprocess.TimeoutExpired
        If the tool hasn't exited within `timeout_s`. Its whole process tree
        has been killed by the time this propagates.

    Notes
    -----
    Replaces a plain `subprocess.run(..., capture_output=True, timeout=...)`,
    which does not reliably enforce its timeout on Windows: on timeout it
    kills only the direct child, then waits - with no timeout - for the
    output pipes to close. MiKTeX's `latex.exe`/`dvipng.exe` are launchers
    that spawn the real engine as a grandchild holding those same pipes, so
    a stuck engine (e.g. waiting on MiKTeX's install-missing-package prompt)
    hung the whole pl2docx run indefinitely instead of failing after
    `timeout_s`. Here the entire tree is killed (`taskkill /T` on Windows,
    a process-group kill elsewhere), and stdin is `DEVNULL` so the tool can
    never block waiting on console input.
    """
    popen_kwargs = {} if os.name == "nt" else {"start_new_session": True}
    proc = subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        **popen_kwargs,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc)
        try:
            proc.communicate(timeout=_KILL_GRACE_S)
        except subprocess.TimeoutExpired:
            # Something outside the tree still holds the pipes; abandon them
            # rather than hang - the caller only needs the timeout reported.
            pass
        raise
    return subprocess.CompletedProcess(args, proc.returncode, stdout, stderr)


def _package_resolves(kpsewhich_bin: str, package: str) -> bool:
    try:
        result = _run_tool([kpsewhich_bin, f"{package}.sty"], _KPSEWHICH_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


@dataclass(frozen=True)
class RenderedMath:
    """A math snippet rendered to a PNG, with what's needed to embed it correctly.

    Parameters
    ----------
    png_path : Path
        Absolute path to the rendered PNG. Valid for the lifetime of this
        process's math-rendering cache directory.
    width_in : float
        The image's intended physical width, in inches, computed from its
        real pixel width and the DPI it was actually rasterized at (not from
        the PNG's own `pHYs` metadata - confirmed `dvipng` writes a fixed
        ~96 DPI `pHYs` chunk regardless of the `-D` value used to rasterize,
        so trusting that metadata for auto-sizing inflated every embedded
        equation ~6x at this module's 600 DPI default). Callers should pass
        this explicitly as `add_picture`'s `width=`, not rely on
        auto-sizing.
    depth_pt : float
        How far the rendered content extends below the LaTeX baseline, in
        points (e.g. a fraction's denominator or a subscript) - `0.0` for
        content with no descenders. From `dvipng --depth`'s reported pixel
        count, converted using the same DPI the PNG was rasterized at, plus
        `_DISPLAY_EXTRA_VPAD_PT` for `display_mode` content (the deliberate
        extra vertical margin below the equation - dvipng's own reported
        depth doesn't grow to reflect it, see `render_math_png`'s body).
        Callers should apply this as a downward run-level baseline shift
        (OOXML `<w:position>`, negative = lower) - Word otherwise anchors an
        inline picture's bottom edge to the text baseline, which is wrong
        whenever the image's own bottom is below its LaTeX baseline.
    """

    png_path: Path
    width_in: float
    depth_pt: float


def _png_pixel_width(path: Path) -> int:
    """Read a PNG's pixel width straight from its `IHDR` chunk.

    Not `pHYs` - see `RenderedMath.width_in`'s docstring for why that
    metadata can't be trusted for `dvipng` output. `IHDR` is always the
    first chunk in a well-formed PNG (PNG spec 11.2.2), at a fixed offset,
    so no general chunk-walking is needed here.
    """
    with path.open("rb") as f:
        header = f.read(24)
    return int.from_bytes(header[16:20], "big")


#: In-process cache, keyed by every input that affects the rendered pixels,
#: so the same formula (e.g. shared between a blank instance and its answer
#: key, or repeated across a question) is only compiled once per run -
#: `latex`+`dvipng` is slow (two real process spawns) per snippet.
#: Deliberately module-level and process-lifetime, not threaded through as
#: an explicit cache object: pl2docx's CLI entry points (`fetch.py`/
#: `render.py`) are one-shot process invocations, so a process-lifetime
#: cache already matches "one render run" without needing to plumb a cache
#: handle through `element_renderer.py`'s whole call chain.
_cache: dict[tuple[str, bool, int, int], RenderedMath] = {}
_cache_dir: Path | None = None


def _get_cache_dir() -> Path:
    global _cache_dir
    if _cache_dir is None:
        _cache_dir = Path(tempfile.mkdtemp(prefix="pl2docx_math_"))
    return _cache_dir


def render_math_png(
    latex: str,
    display_mode: bool,
    *,
    font_size_pt: int = _DEFAULT_FONT_SIZE_PT,
    dpi: int = _DEFAULT_DPI,
) -> RenderedMath:
    """Compile `latex` to a tightly-cropped, print-resolution, depth-annotated PNG.

    Parameters
    ----------
    latex : str
        Raw LaTeX math source, delimiters already stripped (as produced by
        `pl2docx.html_parser.MathRef.latex`).
    display_mode : bool
        Whether to typeset in LaTeX's display style (`\\[...\\]` - larger
        operators, centered) rather than inline/text style (`$...$`).
    font_size_pt : int
        The LaTeX document's base font size, in points - controls how large
        the rendered glyphs come out relative to surrounding document text.
        Should match (or approximate) the instructor template's body text
        size for visually consistent inline math; exact per-template
        introspection is not attempted here (see module-level docstring).
    dpi : int
        Rasterization resolution. `RenderedMath.width_in` is computed from
        this and the PNG's real pixel width - not from the PNG's own `pHYs`
        metadata, which `dvipng` writes inaccurately (see `RenderedMath`'s
        docstring).

    Returns
    -------
    RenderedMath
        The rendered PNG's path plus its baseline depth. Valid for the
        lifetime of this process's math-rendering cache directory - copy
        `png_path` out if it needs to outlive this process.

    Raises
    ------
    LatexRenderError
        If `latex`/`dvipng` aren't on `PATH`, the LaTeX source fails to
        compile (bad syntax, unsupported/missing package), `dvipng` fails,
        or either step times out.
    """
    cache_key = (latex, display_mode, font_size_pt, dpi)
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    latex_bin = shutil.which("latex")
    dvipng_bin = shutil.which("dvipng")
    if latex_bin is None or dvipng_bin is None:
        raise LatexRenderError(
            "latex/dvipng not found on PATH - math rendering needs a LaTeX "
            "distribution (e.g. MiKTeX or TeX Live, providing amsmath/amssymb/"
            "xcolor/preview) and dvipng installed."
        )

    # display_mode deliberately does NOT use \[...\]/displaymath: that
    # environment always typesets as an \hbox *to* \linewidth (LaTeX's normal
    # display-math centering mechanism), and when the equation's actual ink
    # is wider than \linewidth, the box is "overfull" - its glyphs visibly
    # protrude past \linewidth, but its own *reported* dimension (what
    # preview's tightpage bounding box and dvipng's -T tight both trust) is
    # still exactly \linewidth. The overflowing portion is silently dropped
    # from the rendered PNG, not just visually cut off in a way cropping
    # could recover - confirmed by reproducing the user's exact cut-off
    # equation from millstone-NPs and inspecting the raw dvipng output before
    # any docx involvement. `\hbox{$\displaystyle ...$}` sidesteps this
    # entirely: an hbox with no explicit "to <width>" is always natural-width
    # (never overfull, regardless of how wide), so the full equation is
    # always captured - "falls off the edge" only in the sense that the
    # resulting image is simply wider than the page once embedded, which the
    # user confirmed is fine (unlike silently losing content). `\displaystyle`
    # preserves display-style sizing (large operators/fractions, real
    # `\left`/`\right` sizing) that plain inline math wouldn't have.
    #
    # The `\vbox{\kern ... \hbox{...} \kern ...}` wrapper is `_DISPLAY_EXTRA_VPAD_PT`'s
    # delivery mechanism: a `\kern` above/below the equation's own hbox adds genuine
    # additive vertical whitespace to the vbox's total height/depth (confirmed
    # empirically - preview's tightpage bounding box does track it). Left/right stay
    # untouched (no horizontal kern), matching the user's ask for vertical-only
    # margin. dvipng's reported `depth=` figure does NOT track the added bottom kern
    # (stays fixed at the inner hbox's own natural depth) - `depth_pt` is adjusted
    # manually below instead of trusting that measurement for this piece.
    normalized_latex = _brace_mhchem_macros(_collapse_blank_lines(latex))
    if display_mode:
        content = (
            f"\\vbox{{\\kern {_DISPLAY_EXTRA_VPAD_PT}pt"
            f"\\hbox{{$\\displaystyle {normalized_latex}$}}"
            f"\\kern {_DISPLAY_EXTRA_VPAD_PT}pt}}"
        )
    else:
        content = f"${normalized_latex}$"
    extra_packages = "\n".join(f"\\usepackage{{{pkg}}}" for pkg in _extra_packages)
    source = _SOURCE_TEMPLATE.format(
        size=font_size_pt, border=_PREVIEW_BORDER_PT, extra_packages=extra_packages, content=content
    )

    work_dir = Path(tempfile.mkdtemp(prefix="pl2docx_math_job_"))
    job_name = "eq"
    tex_path = work_dir / f"{job_name}.tex"
    tex_path.write_text(source, encoding="utf-8")

    try:
        compile_result = _run_tool(
            [
                latex_bin,
                "-interaction=nonstopmode",
                "-halt-on-error",
                "-output-directory",
                str(work_dir),
                str(tex_path),
            ],
            _LATEX_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        raise LatexRenderError(f"latex timed out rendering {latex!r}") from exc

    dvi_path = work_dir / f"{job_name}.dvi"
    if compile_result.returncode != 0 or not dvi_path.is_file():
        raise LatexRenderError(f"latex failed to compile {latex!r}:\n{compile_result.stdout[-2000:]}")

    png_path = work_dir / f"{job_name}.png"
    try:
        rasterize_result = _run_tool(
            [
                dvipng_bin,
                "-D",
                str(dpi),
                "-T",
                "tight",
                "--depth",
                "-bg",
                "Transparent",
                "-o",
                str(png_path),
                str(dvi_path),
            ],
            _DVIPNG_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        raise LatexRenderError(f"dvipng timed out rasterizing {latex!r}") from exc

    if rasterize_result.returncode != 0 or not png_path.is_file():
        raise LatexRenderError(f"dvipng failed to rasterize {latex!r}:\n{rasterize_result.stdout[-2000:]}")

    depth_match = _DEPTH_RE.search(rasterize_result.stdout)
    depth_px = int(depth_match.group(1)) if depth_match else 0
    depth_pt = depth_px / dpi * 72.0
    if display_mode:
        # dvipng's own `depth=` figure reflects only the inner hbox's natural
        # depth - it does not grow when the outer `\vbox`'s trailing `\kern`
        # pushes the image's bottom edge further below the real baseline (see
        # the comment above `content`'s construction). Added deterministically
        # here instead, since we know exactly how much kern we inserted.
        depth_pt += _DISPLAY_EXTRA_VPAD_PT
    width_in = _png_pixel_width(png_path) / dpi

    # Moved into the shared cache dir under a content-hashed name so repeated
    # calls for the same formula (see `_cache` above) resolve to one file,
    # and so each job's own `work_dir` (with the .tex/.log/.aux/.dvi
    # clutter) can be discarded now rather than accumulating one temp dir
    # per equation.
    digest = hashlib.sha256(repr(cache_key).encode("utf-8")).hexdigest()[:16]
    final_path = _get_cache_dir() / f"{digest}.png"
    shutil.move(str(png_path), str(final_path))
    shutil.rmtree(work_dir, ignore_errors=True)

    rendered = RenderedMath(png_path=final_path, width_in=width_in, depth_pt=depth_pt)
    _cache[cache_key] = rendered
    return rendered
