"""Build per-question Subdoc content and Jinja context for the docx template.

**Phase 3B**: config-driven per-widget formatting (list style, bold-correct,
display, draw-border), routed through `pl2docx.element_config`. Widgets render at
their real source position within the prompt (interleaved with `prompt_segments`),
not appended after it — so `question_contents` reads the way the original PL
question did. Compound (multi-widget) questions are supported: each widget is
rendered independently, in source order.

**`draw-border`** draws a box tight around each run of a widget's rendered content
(all options together for selector-type, appearing as a single continuous box, for
`"inline"` display; one box per line for `"block"`/`"template"` display, since each
option/line is its own paragraph) via a run-level character border, regardless of
`display`. An earlier version of this module used a 1x1-table-cell border for
`"block"`/`"template"` content instead — confirmed wrong by the user: a table cell
always spans the full page width, producing a box far wider than the boxed text
itself, unlike the tight, text-width run-level border. See `_add_run_border` below
and CLAUDE.md for the full writeup.

**Phase 4 increment 2**: `ImageRef` nodes render as real embedded pictures
(`Run.add_picture`) when an `image_base_dir` is supplied - see
`build_question_context`'s parameter docs. Sized from the source `<img
width>` attribute (PL's own intended on-page display size, in CSS reference
pixels) when present, else a fixed default width. Falls back to alt text
(same as before this increment) when no `image_base_dir` is given, or the
referenced file isn't found on disk - a missing image shouldn't fail the
whole render.
"""

from __future__ import annotations

from pathlib import Path

from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches
from docx.text.run import Run
from docxtpl import DocxTemplate

from pl2docx.element_config import (
    ElementConfig,
    ElementPreferences,
    resolve_preferences,
)
from pl2docx.html_parser import (
    ContentNode,
    ImageRef,
    ListItemStart,
    MathRef,
    ParagraphBreak,
    ParsedQuestion,
    TextRun,
    Widget,
    format_points_text,
    option_letter,
)

ANSWER_SPACE_BLANK_LINES = 2

#: CSS reference pixel, per the W3C spec PL's own display sizing uses
#: (`<img width>` is in CSS px, at a fixed 96px/inch) - not a guess.
_CSS_PX_PER_INCH = 96
#: Fallback width when a source `<img>` has no usable `width` attribute.
_DEFAULT_IMAGE_WIDTH = Inches(3)

_SELECTOR_KINDS = ("multiple_choice", "checkbox")
_LIST_MARKERS = {
    "letter-labels": lambda idx: f"({option_letter(idx)})",
    "bubble": lambda _idx: "○",  # ○
    "checkbox": lambda _idx: "☐",  # ☐
}


def build_question_context(
    tpl: DocxTemplate,
    question: ParsedQuestion,
    number: int,
    element_config: ElementConfig,
    image_base_dir: Path | None = None,
) -> dict:
    """Build one question's Jinja context dict, including its 4 Subdocs.

    Parameters
    ----------
    tpl : docxtpl.DocxTemplate
        The template instance being rendered. Subdocs must be created from
        this exact instance (`tpl.new_subdoc()`) to insert cleanly into it —
        a `Subdoc` created from a different `DocxTemplate` won't render
        correctly.
    question : ParsedQuestion
        Parsed content for this question — either a blank-copy or
        answer-key parse. This function makes no blank/key distinction
        itself; it simply reflects whatever `question` already contains
        (e.g. `answer_panel_text`/each widget's `correct_option_indices`
        are empty for a blank-copy parse), same as Phase 2/3A.
    number : int
        This question's 1-based position within the overall document.
    element_config : pl2docx.element_config.ElementConfig
        Instructor-configured element/question-level formatting preferences
        for this run (see `pl2docx.element_config`).
    image_base_dir : pathlib.Path or None
        The directory an `ImageRef.local_path` (e.g. `"files/1_0_diagram.png"`)
        is relative to — the `pl2docx.fetch`-written `blank/` or `key/`
        instance directory this `question` was parsed from (each has its own
        sibling `files/` folder; blank/key are parsed from separate HTML
        fetches, so this must match whichever one `question` actually came
        from). `None` (the default) skips real embedding entirely, falling
        back to alt text for every image - same as before this parameter
        existed.

    Returns
    -------
    dict
        `{"number", "title", "qid", "points_numeric", "points_text",
        "question_contents", "answer_contents", "answer_space",
        "answer_element"}` — the per-question shape
        `pl2docx.docx_builder.render_document`'s template context expects.
        `answer_element` holds any widget whose resolved `display` is
        `"template"`, rendered in `"block"` form, in source order; empty
        (but always present) when no widget uses `template` display.
    """
    question_contents, answer_element = _build_question_contents(
        tpl, question, element_config, image_base_dir
    )
    return {
        "number": number,
        "title": question.title,
        "qid": question.qid,
        "points_numeric": question.points_numeric,
        "points_text": format_points_text(question.points_numeric, question.points),
        "question_contents": question_contents,
        "answer_contents": _build_answer_contents(tpl, question, image_base_dir),
        "answer_space": _build_answer_space(tpl),
        "answer_element": answer_element,
    }


def _build_question_contents(
    tpl: DocxTemplate, question: ParsedQuestion, element_config: ElementConfig, image_base_dir: Path | None
):
    subdoc = tpl.new_subdoc()
    answer_element = tpl.new_subdoc()

    state = {"paragraph": subdoc.add_paragraph()}

    def append_nodes(nodes: list[ContentNode]) -> None:
        _render_nodes_into_subdoc(state, subdoc, nodes, image_base_dir)

    if not question.widgets:
        append_nodes(question.prompt_segments[0] if question.prompt_segments else [])
        return subdoc, answer_element

    for i, widget in enumerate(question.widgets):
        append_nodes(question.prompt_segments[i])

        prefs = resolve_preferences(element_config, widget.kind)
        display = _resolve_display(widget, prefs)
        is_selector = widget.kind in _SELECTOR_KINDS

        if display == "none":
            continue
        elif display == "inline":
            runs = (
                _render_selector_inline(state["paragraph"], widget, prefs, image_base_dir)
                if is_selector
                else _render_fill_in_inline(state["paragraph"], widget, image_base_dir)
            )
        else:  # "block" or "template"
            target = answer_element if display == "template" else subdoc
            runs = (
                _render_selector_block(target, widget, prefs, image_base_dir)
                if is_selector
                else _render_fill_in_block(target, widget, image_base_dir)
            )
            if display == "block":
                state["paragraph"] = subdoc.add_paragraph()

        if display != "none" and prefs.draw_border:
            for run in runs:
                _add_run_border(run)

    append_nodes(question.prompt_segments[-1])
    return subdoc, answer_element


def _append_run_text(paragraph, text: str, bold: bool = False, italic: bool = False, underline: bool = False):
    """Add one run of `text` to `paragraph`, inserting a separating space if needed.

    Mirrors Phase 3B's `append_text` prefix behavior (a paragraph that already
    has content gets a leading space before the next chunk, unless either
    side already supplies the whitespace) — now applied uniformly to every
    run added to a shared paragraph (prompt text, widget-rendered content
    alike), not just prompt-segment text as before.
    """
    if not text:
        return None
    if paragraph.runs and not paragraph.runs[-1].text.endswith(" ") and not text.startswith(" "):
        paragraph.add_run(" ")
    run = paragraph.add_run(text)
    run.bold = bold
    run.italic = italic
    run.underline = underline
    return run


def _render_nodes_to_paragraph(
    paragraph, nodes: list[ContentNode], image_base_dir: Path | None = None
) -> list[Run]:
    """Render a node sequence inline into a single existing `paragraph`.

    Used for widget-scoped content (selector options, fill-in label/suffix)
    that's known to be short/inline in practice - `ParagraphBreak`/
    `ListItemStart` nodes are treated as a plain separator rather than
    starting a new paragraph, since splitting the paragraph mid-widget-render
    isn't meaningful here.
    """
    runs: list[Run] = []
    for node in nodes:
        run = _render_one_node(paragraph, node, image_base_dir)
        if run is not None:
            runs.append(run)
    return runs


def _render_nodes_into_subdoc(
    state: dict, sink, nodes: list[ContentNode], image_base_dir: Path | None = None
) -> None:
    """Render a node sequence into `sink`, starting new paragraphs on `ParagraphBreak`.

    `state["paragraph"]` is the shared "current paragraph" also used by widget
    rendering, so prompt text and inline widget content can share one running
    paragraph exactly as Phase 3B's `append_text` did.
    """
    for node in nodes:
        if isinstance(node, ParagraphBreak):
            state["paragraph"] = sink.add_paragraph()
            continue
        _render_one_node(state["paragraph"], node, image_base_dir)


def _render_one_node(paragraph, node: ContentNode, image_base_dir: Path | None = None) -> Run | None:
    if isinstance(node, TextRun):
        return _append_run_text(paragraph, node.text, node.bold, node.italic, node.underline)
    if isinstance(node, ParagraphBreak):
        # Only reached via `_render_nodes_to_paragraph` (inline widget content) -
        # `_render_nodes_into_subdoc` intercepts `ParagraphBreak` itself to start
        # a real new paragraph instead. Here, there's no paragraph to split, so
        # treat it as a plain word-separating space instead of dropping it.
        return _append_run_text(paragraph, " ")
    if isinstance(node, ListItemStart):
        return _append_run_text(paragraph, "• ")
    if isinstance(node, ImageRef):
        return _render_image(paragraph, node, image_base_dir)
    if isinstance(node, MathRef):
        # Real OMML conversion lands in Phase 4 increment 3; interim
        # fallback keeps output sane in the meantime.
        delim = "$$" if node.display_mode else "$"
        return _append_run_text(paragraph, f"{delim}{node.latex}{delim}")
    return None


def _render_image(paragraph, node: ImageRef, image_base_dir: Path | None) -> Run | None:
    """Embed `node` as a real inline picture, falling back to alt text.

    Falls back (rather than raising) whenever the picture can't actually be
    embedded - no `image_base_dir` given, the file isn't on disk (e.g. an
    external image `fetch.py` deliberately didn't download), or python-docx
    itself rejects the file (e.g. corrupt/unsupported format) - a missing or
    bad image shouldn't fail the whole document's render.
    """
    if image_base_dir is not None and node.local_path:
        image_path = image_base_dir / node.local_path
        if image_path.is_file():
            if paragraph.runs and not paragraph.runs[-1].text.endswith(" "):
                paragraph.add_run(" ")
            run = paragraph.add_run()
            width = Inches(node.width_px / _CSS_PX_PER_INCH) if node.width_px else _DEFAULT_IMAGE_WIDTH
            try:
                run.add_picture(str(image_path), width=width)
                return run
            except Exception:
                pass
    return _append_run_text(paragraph, node.alt or "[image]")


def _resolve_display(widget: Widget, prefs: ElementPreferences) -> str:
    if prefs.display is not None:
        return prefs.display
    if widget.kind in _SELECTOR_KINDS and not widget.is_dropdown:
        return "inline" if widget.is_inline else "block"
    return "block"


def _render_selector_inline(
    paragraph, widget: Widget, prefs, image_base_dir: Path | None = None
) -> list[Run]:
    runs: list[Run] = []
    for idx, option_nodes in enumerate(widget.options):
        if idx > 0:
            spacer_start = len(paragraph.runs)
            _append_run_text(paragraph, "   ")
            runs.extend(paragraph.runs[spacer_start:])
        option_start = len(paragraph.runs)
        marker = _LIST_MARKERS[prefs.list_style](idx)
        _append_run_text(paragraph, f"{marker} ")
        _render_nodes_to_paragraph(paragraph, option_nodes, image_base_dir)
        combined = list(paragraph.runs[option_start:])
        if prefs.bold_correct and idx in widget.correct_option_indices:
            for run in combined:
                run.bold = True
        runs.extend(combined)
    return runs


def _render_fill_in_inline(paragraph, widget: Widget, image_base_dir: Path | None = None) -> list[Run]:
    return _fill_in_runs(paragraph, widget, image_base_dir)


def _render_selector_block(
    sink, widget: Widget, prefs, image_base_dir: Path | None = None
) -> list[Run]:
    runs: list[Run] = []
    for idx, option_nodes in enumerate(widget.options):
        paragraph = sink.add_paragraph()
        marker = _LIST_MARKERS[prefs.list_style](idx)
        _append_run_text(paragraph, f"{marker} ")
        _render_nodes_to_paragraph(paragraph, option_nodes, image_base_dir)
        combined = list(paragraph.runs)
        if prefs.bold_correct and idx in widget.correct_option_indices:
            for run in combined:
                run.bold = True
        runs.extend(combined)
    return runs


def _render_fill_in_block(sink, widget: Widget, image_base_dir: Path | None = None) -> list[Run]:
    paragraph = sink.add_paragraph()
    return _fill_in_runs(paragraph, widget, image_base_dir)


def _fill_in_runs(paragraph, widget: Widget, image_base_dir: Path | None = None) -> list[Run]:
    """Render this widget's label/blank/suffix, capturing *every* run added.

    Captures via a paragraph-length snapshot (before/after) rather than
    collecting each helper call's own return value - `_append_run_text` can
    silently insert an extra, unbordered separator-space run between calls
    (e.g. between label and blank) when neither side already has trailing/
    leading whitespace. Missing that run from the returned list broke
    `draw-border`'s single-continuous-box look (regression: it split into
    separate boxes per label/blank/suffix, since Word only merges *adjacent*
    same-bordered runs, and the untracked spacer run in between was never
    bordered). Snapshotting picks up literally everything added, spacers
    included.
    """
    blank = "_" * 20
    start = len(paragraph.runs)
    if widget.label is None:
        _append_run_text(paragraph, f"Answer: {blank}")
        return list(paragraph.runs[start:])
    _render_nodes_to_paragraph(paragraph, widget.label, image_base_dir)
    _append_run_text(paragraph, blank)
    if widget.suffix:
        _render_nodes_to_paragraph(paragraph, widget.suffix, image_base_dir)
    return list(paragraph.runs[start:])


def _add_run_border(run: Run) -> None:
    """Draw a character (run-level) border around `run`'s text.

    Confirmed technique this session: a `<w:bdr>` inside a run's `<w:rPr>` draws
    a border directly around that run's text, inline with surrounding content —
    unlike a paragraph or table-cell border, it lives inside the run itself and
    survives docxtpl's `{{p ... }}` subdoc-splicing intact (that mechanism only
    discards/replaces the *paragraph* container, not run-level formatting).
    Word visually merges adjacent runs that share identical border formatting
    into one continuous box, so applying this to every run of a widget's inline
    content (including inter-option spacer runs) yields a single box around the
    whole widget, matching PL's own on-screen inline-boxed-choice look.
    """
    rPr = run._element.get_or_add_rPr()
    bdr = OxmlElement("w:bdr")
    bdr.set(qn("w:val"), "single")
    bdr.set(qn("w:sz"), "6")
    bdr.set(qn("w:space"), "2")
    bdr.set(qn("w:color"), "000000")
    rPr.append(bdr)


def _build_answer_contents(tpl: DocxTemplate, question: ParsedQuestion, image_base_dir: Path | None = None):
    subdoc = tpl.new_subdoc()
    if question.answer_panel_text is not None:
        state = {"paragraph": subdoc.add_paragraph()}
        _render_nodes_into_subdoc(state, subdoc, question.answer_panel_text, image_base_dir)
    return subdoc


def _build_answer_space(tpl: DocxTemplate):
    subdoc = tpl.new_subdoc()
    for _ in range(ANSWER_SPACE_BLANK_LINES):
        subdoc.add_paragraph("")
    return subdoc
