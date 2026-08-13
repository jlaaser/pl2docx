"""Render LaTeX math source to a print-resolution PNG via a real LaTeX install.

Chosen over a pure-Python LaTeX->OMML pipeline specifically because this
course's content uses the `mhchem` package's `\\ce{...}` macro for chemical
formulas (confirmed in the course repo's `chemutils/compounds.py` etc.) -
`mhchem` is a real, fairly elaborate LaTeX package, and general-purpose
LaTeX->MathML converters don't implement it. Compiling through a real
`latex` means any LaTeX construct the course ever uses "just works" with no
subset to maintain, at the cost of non-editable (image, not native
Word-equation) output. See `planning_notes/` for the full comparison.

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

Requires `latex` (any TeX distribution providing it, e.g. MiKTeX/TeX Live,
with `amsmath`/`amssymb`/`mhchem`/`xcolor`/`preview` available) and
`dvipng` on `PATH`. Neither is a Python dependency this package can pin,
unlike the rest of pl2docx's dependency list - callers must catch
`LatexRenderError` and degrade gracefully (as `element_renderer.py` does)
rather than assume these are always present.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_FONT_SIZE_PT = 11
_DEFAULT_DPI = 600
_PREVIEW_BORDER_PT = 1
_LATEX_TIMEOUT_S = 20
_DVIPNG_TIMEOUT_S = 20

#: `\boldmath` matches PrairieLearn's own MathJax rendering, which the user
#: confirmed (2026-08-12 visual review) uses a visibly heavier weight than
#: LaTeX's default math font - without it, rendered math reads noticeably
#: lighter than the surrounding document text. `\PreviewBorder` adds a small
#: margin around the `preview` package's otherwise pixel-tight bounding box,
#: fixing glyph-top clipping the user observed (ascenders with no descender
#: on the same line have essentially zero margin above them without this).
#: `mhchem`'s `version=4` silences its "no version specified" warning
#: (cosmetic - doesn't affect rendering) without changing behavior.
_SOURCE_TEMPLATE = r"""\documentclass[{size}pt]{{article}}
\usepackage[active,tightpage]{{preview}}
\setlength\PreviewBorder{{{border}pt}}
\usepackage{{amsmath}}
\usepackage{{amssymb}}
\usepackage[version=4]{{mhchem}}
\usepackage{{xcolor}}
\pagestyle{{empty}}
\begin{{document}}
\begin{{preview}}
\boldmath {content}
\end{{preview}}
\end{{document}}
"""

_DEPTH_RE = re.compile(r"depth=(\d+)")


class LatexRenderError(Exception):
    """Raised when LaTeX math source could not be rendered to a PNG.

    Covers: `latex`/`dvipng` missing from `PATH`, a LaTeX compile error (bad
    syntax, missing package), a `dvipng` failure, or either step timing out.
    Callers should catch this and fall back to plain-text rendering rather
    than let one bad/unsupported equation fail the whole document.
    """


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
        count, converted using the same DPI the PNG was rasterized at.
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
        Rasterization resolution. The output PNG carries this DPI in its own
        metadata (`dvipng` writes a `pHYs` chunk), so `python-docx`'s
        `add_picture` can auto-size the embedded run from the file itself -
        callers should not pass an explicit `width=`.

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
            "mhchem/xcolor/preview) and dvipng installed."
        )

    content = f"\\[{latex}\\]" if display_mode else f"${latex}$"
    source = _SOURCE_TEMPLATE.format(size=font_size_pt, border=_PREVIEW_BORDER_PT, content=content)

    work_dir = Path(tempfile.mkdtemp(prefix="pl2docx_math_job_"))
    job_name = "eq"
    tex_path = work_dir / f"{job_name}.tex"
    tex_path.write_text(source, encoding="utf-8")

    try:
        compile_result = subprocess.run(
            [
                latex_bin,
                "-interaction=nonstopmode",
                "-halt-on-error",
                "-output-directory",
                str(work_dir),
                str(tex_path),
            ],
            capture_output=True,
            text=True,
            timeout=_LATEX_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        raise LatexRenderError(f"latex timed out rendering {latex!r}") from exc

    dvi_path = work_dir / f"{job_name}.dvi"
    if compile_result.returncode != 0 or not dvi_path.is_file():
        raise LatexRenderError(f"latex failed to compile {latex!r}:\n{compile_result.stdout[-2000:]}")

    png_path = work_dir / f"{job_name}.png"
    try:
        rasterize_result = subprocess.run(
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
            capture_output=True,
            text=True,
            timeout=_DVIPNG_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        raise LatexRenderError(f"dvipng timed out rasterizing {latex!r}") from exc

    if rasterize_result.returncode != 0 or not png_path.is_file():
        raise LatexRenderError(f"dvipng failed to rasterize {latex!r}:\n{rasterize_result.stdout[-2000:]}")

    depth_match = _DEPTH_RE.search(rasterize_result.stdout)
    depth_px = int(depth_match.group(1)) if depth_match else 0
    depth_pt = depth_px / dpi * 72.0
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
