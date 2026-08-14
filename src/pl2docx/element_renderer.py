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

**SVG embedding**: `SvgRef` nodes (raw inline `<svg>` markup) and `.svg`-suffixed
`ImageRef` nodes (a downloaded `.svg` file, e.g. from `<pl-figure>`) both render as
real embedded pictures via `pl2docx.svg_render.render_svg_png` (a headless-browser
rasterization pipeline, since python-docx/docxtpl cannot embed SVG directly) - see
`_render_svg`/`_render_svg_image_ref` below and `pl2docx.svg_render`'s module
docstring. Falls back to alt text (same philosophy as `_render_math`/`_render_image`)
when Chromium isn't available or a given SVG fails to rasterize.

**Phase 4 increment 1 follow-up**: `ListItemStart`/`ListItemEnd` nodes render
as *real* Word list items (`<w:numPr>` XML, `_apply_list_numbering`) when a
`list_formats` is supplied - see `build_question_context`'s parameter docs -
instead of plain prepended `"1. "`/`"• "` text. Confirmed this session:
`tpl.new_subdoc()` is always called without a `docpath` in this codebase, so
every subdoc shares the *same* in-memory document part/package as `tpl`
itself (`docxtpl/subdoc.py`'s no-`docpath` branch does
`self.subdocx._part = self.docx._part`) - meaning a `numId` only needs to
exist in `tpl`'s own `numbering.xml` by the time a subdoc paragraph
references it, no cross-package merge step needed. A whole `<li>`'s content
(however many lines/images it has) renders into *one* Word paragraph, using
soft line breaks (`Run.add_break()`, i.e. Word's own Shift+Enter convention)
between its internal lines instead of real paragraph breaks - simpler than
matching each internal line's own indent to the list's indent definition,
for an equivalent visual result. Falls back to the old flat marker-text
behavior (same as before this follow-up) when no `list_formats` is given.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
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
    ListItemEnd,
    ListItemStart,
    MathRef,
    ParagraphBreak,
    ParsedQuestion,
    SvgRef,
    TextRun,
    Widget,
    format_points_text,
    option_letter,
)
from pl2docx.latex_math import LatexRenderError, render_math_png
from pl2docx.svg_render import SvgRenderError, render_svg_png

logger = logging.getLogger(__name__)

ANSWER_SPACE_BLANK_LINES = 2

#: CSS reference pixel, per the W3C spec PL's own display sizing uses
#: (`<img width>` is in CSS px, at a fixed 96px/inch) - not a guess.
_CSS_PX_PER_INCH = 96
#: Fallback width when a source `<img>` has no usable `width` attribute.
_DEFAULT_IMAGE_WIDTH = Inches(3)

#: Standard Word list indent (0.5in left, 0.25in hanging) - matches Word's
#: own default numbered/bulleted list styles' own indent.
_LIST_INDENT_TWIPS = 720
_LIST_HANGING_TWIPS = 360

@dataclass(frozen=True)
class ListFormats:
    """Document-wide real-list format definitions, created once per `render_document()` call.

    Two `<w:abstractNum>` definitions (decimal-numbered, bulleted) that every
    per-question list instance mints a fresh `<w:num>` against (see
    `_ListNumIds`) - sharing the *format* while each distinct source list
    gets its own independently-restarting Word numbering instance.
    """

    numbering_elm: object  # docx.oxml.numbering.CT_Numbering
    decimal_abstract_id: int
    bullet_abstract_id: int


def create_list_formats(tpl: DocxTemplate) -> ListFormats:
    """Create this render's shared real-list format definitions.

    Parameters
    ----------
    tpl : docxtpl.DocxTemplate
        The template instance being rendered. List numbering is written
        into its own `numbering.xml` part - since every subdoc shares the
        same in-memory document part as `tpl` (see this module's docstring),
        this only needs calling once per `render_document()` call, not once
        per question/subdoc.

    Returns
    -------
    ListFormats

    Raises
    ------
    RuntimeError
        If the template has no numbering-definitions part and python-docx
        can't create one from scratch (`NumberingPart.new()` is
        unimplemented there) - real templates virtually always have one
        (any plain `Document()`, including what `starter_template.py`
        generates, ships a full `numbering.xml`), so this should only fire
        for an unusual instructor-supplied template built by some other
        tool. Fixable by opening the template in Word, adding then removing
        a numbered/bulleted list anywhere, and re-saving.
    """
    try:
        numbering_part = tpl.get_docx().part.numbering_part
    except NotImplementedError as exc:
        raise RuntimeError(
            "This template has no numbering-definitions part (numbering.xml), and "
            "python-docx can't create one from scratch. Open the template in Word, "
            "add a numbered or bulleted list anywhere (even briefly, then delete it "
            "again), save, and retry."
        ) from exc
    numbering_elm = numbering_part.element
    decimal_id = _add_list_abstract_num(numbering_elm, ordered=True)
    bullet_id = _add_list_abstract_num(numbering_elm, ordered=False)
    return ListFormats(
        numbering_elm=numbering_elm, decimal_abstract_id=decimal_id, bullet_abstract_id=bullet_id
    )


def _next_abstract_num_id(numbering_elm) -> int:
    existing = [int(v) for v in numbering_elm.xpath("./w:abstractNum/@w:abstractNumId")]
    return (max(existing) + 1) if existing else 0


def _add_list_abstract_num(numbering_elm, ordered: bool) -> int:
    """Create a new `<w:abstractNum>` (decimal or bullet format), return its id.

    Raw OOXML construction (`OxmlElement`/`qn()`) - python-docx's object
    model has no `CT_AbstractNum` support at all (confirmed:
    `docx/oxml/numbering.py` defines `CT_Num`/`CT_NumPr`/`CT_Numbering` but
    nothing for `<w:abstractNum>` itself), so this follows the same
    low-level pattern as `_add_run_border`.
    """
    abstract_num_id = _next_abstract_num_id(numbering_elm)

    abstract_num = OxmlElement("w:abstractNum")
    abstract_num.set(qn("w:abstractNumId"), str(abstract_num_id))

    multi_level = OxmlElement("w:multiLevelType")
    multi_level.set(qn("w:val"), "hybridMultilevel")
    abstract_num.append(multi_level)

    lvl = OxmlElement("w:lvl")
    lvl.set(qn("w:ilvl"), "0")

    start = OxmlElement("w:start")
    start.set(qn("w:val"), "1")
    lvl.append(start)

    num_fmt = OxmlElement("w:numFmt")
    num_fmt.set(qn("w:val"), "decimal" if ordered else "bullet")
    lvl.append(num_fmt)

    lvl_text = OxmlElement("w:lvlText")
    lvl_text.set(qn("w:val"), "%1." if ordered else "•")
    lvl.append(lvl_text)

    lvl_jc = OxmlElement("w:lvlJc")
    lvl_jc.set(qn("w:val"), "left")
    lvl.append(lvl_jc)

    p_pr = OxmlElement("w:pPr")
    ind = OxmlElement("w:ind")
    ind.set(qn("w:left"), str(_LIST_INDENT_TWIPS))
    ind.set(qn("w:hanging"), str(_LIST_HANGING_TWIPS))
    p_pr.append(ind)
    lvl.append(p_pr)

    abstract_num.append(lvl)

    # <w:abstractNum> elements must precede any <w:num> elements per the
    # schema's declared child order - insert right after any existing
    # <w:abstractNum> siblings, ahead of everything else.
    existing_abstract_nums = numbering_elm.findall(qn("w:abstractNum"))
    numbering_elm.insert(len(existing_abstract_nums), abstract_num)

    return abstract_num_id


class _ListNumIds:
    """Per-subdoc `list_id -> Word numId` map, scoped to one question/answer render.

    Mints a fresh `<w:num>` instance (`CT_Numbering.add_num`) the first time
    each source `list_id` is seen, so multiple independent lists - even ones
    split across several `prompt_segments` by an interleaved widget - each
    get their own real, independently-restarting Word numbering sequence,
    sharing one `ListFormats` abstractNum. Deliberately scoped narrower than
    `ListFormats` (fresh instance per `_build_question_contents`/
    `_build_answer_contents` call, not shared document-wide): `list_id` is
    only unique within one `ParsedQuestion` (see `ListItemStart.list_id`'s
    docstring on why reusing it across different questions/parses would risk
    an `id()`-reuse collision).
    """

    def __init__(self, formats: ListFormats):
        self._formats = formats
        self._num_id_by_list_id: dict[int, int] = {}

    def num_id_for(self, list_id: int, ordered: bool) -> int:
        if list_id not in self._num_id_by_list_id:
            abstract_id = self._formats.decimal_abstract_id if ordered else self._formats.bullet_abstract_id
            num_elm = self._formats.numbering_elm.add_num(abstract_id)
            # Confirmed necessary this session (not optional, contrary to the
            # original plan): without an explicit startOverride, Word treats
            # multiple <w:num> instances sharing one <w:abstractNum> as one
            # continuing logical list (e.g. a question's own list at 1/2/3,
            # then its answer-key list picking up at 4/5/6) rather than each
            # independently restarting at the abstractNum's own <w:start>.
            num_elm.add_lvlOverride(ilvl=0).add_startOverride(1)
            self._num_id_by_list_id[list_id] = num_elm.numId
        return self._num_id_by_list_id[list_id]


def _apply_list_numbering(paragraph, num_id: int, ilvl: int = 0) -> None:
    """Attach `<w:numPr>` (real Word list membership) to `paragraph`.

    Word derives the paragraph's own indent/marker from the numbering
    definition (`_add_list_abstract_num`) once this is attached - no
    separate indent needs setting on the paragraph itself.
    """
    p_pr = paragraph._p.get_or_add_pPr()
    num_pr = OxmlElement("w:numPr")
    ilvl_elm = OxmlElement("w:ilvl")
    ilvl_elm.set(qn("w:val"), str(ilvl))
    num_pr.append(ilvl_elm)
    num_id_elm = OxmlElement("w:numId")
    num_id_elm.set(qn("w:val"), str(num_id))
    num_pr.append(num_id_elm)
    p_pr.insert(0, num_pr)


_SELECTOR_KINDS = ("multiple_choice", "checkbox")
_LIST_MARKERS = {
    "letter-labels": lambda idx: f"({option_letter(idx)})",
    "bubble": lambda _idx: "◯",  # ◯ LARGE CIRCLE
    "checkbox": lambda _idx: "▢",  # ▢ WHITE SQUARE WITH ROUNDED CORNERS
}

#: Marker glyph size, as a multiple of the paragraph's own effective font
#: size - `letter-labels` is plain text and deliberately excluded (not in
#: this dict), left at normal size. `bubble` (◯, U+25EF) is also excluded:
#: at its normal nominal size it already reads plenty large - confirmed by
#: the user an earlier 1.6x bump made it look too big. Only `checkbox`
#: (▢, U+25A2) renders visually small at normal text size and needs a
#: modest boost to read at a comparable size to the bubble glyph.
_MARKER_SIZE_SCALE = {
    "checkbox": 1.3,
}


def _resolve_paragraph_font_size_pt(paragraph) -> float:
    """Best-effort resolution of `paragraph`'s effective font size, in points.

    Walks the paragraph's own style, then its `base_style` chain (python-docx
    doesn't resolve inherited style properties itself) - this module never
    sets an explicit run-level font size elsewhere, so a style's own `size`
    is the only place it could legitimately come from. Falls back to 11.0
    (Word's own stock "Normal" style default) if no style in the chain
    defines one at all - an unusual instructor template with no explicit
    size anywhere, not the common case.
    """
    style = paragraph.style
    while style is not None:
        if style.font.size is not None:
            return style.font.size.pt
        style = style.base_style
    return 11.0


def _apply_marker_font_size(run: Run | None, paragraph, list_style: str) -> None:
    """Scale up `run` (a bubble/checkbox marker glyph) relative to `paragraph`'s own text size."""
    if run is None:
        return
    scale = _MARKER_SIZE_SCALE.get(list_style)
    if scale is None:
        return
    run.font.size = Pt(_resolve_paragraph_font_size_pt(paragraph) * scale)


def build_question_context(
    tpl: DocxTemplate,
    question: ParsedQuestion,
    number: int,
    element_config: ElementConfig,
    image_base_dir: Path | None = None,
    list_formats: ListFormats | None = None,
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
    list_formats : ListFormats or None
        This render's shared list-format definitions (see
        `create_list_formats`, called once per `render_document()` call, not
        per question). `None` (the default) falls back to plain prepended
        `"1. "`/`"• "` marker text for lists - not a real Word list - same
        as before this parameter existed.

    Returns
    -------
    dict
        `{"number", "title", "qid", "points_numeric", "points_text",
        "question_contents", "answer_contents", "answer_space",
        "answer_element", "has_answer_element"}` — the per-question shape
        `pl2docx.docx_builder.render_document`'s template context expects.
        `answer_element` holds any widget whose resolved `display` is
        `"template"`, rendered in `"block"` form, in source order; empty
        (but always present) when no widget uses `template` display.
        `has_answer_element` is `True` only when at least one widget actually
        used `template` display — lets the template guard its
        `{{p question.answer_element }}` tag behind
        `{% if question.has_answer_element %}` so an unused `answer_element`
        (the common case) doesn't leave a stray empty paragraph behind, since
        an empty `Subdoc` still splices in as one blank paragraph rather than
        vanishing entirely.
    """
    question_contents, answer_element, has_answer_element = _build_question_contents(
        tpl, question, element_config, image_base_dir, list_formats
    )
    return {
        "number": number,
        "title": question.title,
        "qid": question.qid,
        "points_numeric": question.points_numeric,
        "points_text": format_points_text(question.points_numeric, question.points),
        "question_contents": question_contents,
        "answer_contents": _build_answer_contents(tpl, question, image_base_dir, list_formats),
        "answer_space": _build_answer_space(tpl, _resolve_answer_space_lines(question, element_config)),
        "answer_element": answer_element,
        "has_answer_element": has_answer_element,
    }


def _build_question_contents(
    tpl: DocxTemplate,
    question: ParsedQuestion,
    element_config: ElementConfig,
    image_base_dir: Path | None,
    list_formats: ListFormats | None = None,
):
    subdoc = tpl.new_subdoc()
    answer_element = tpl.new_subdoc()
    list_num_ids = _ListNumIds(list_formats) if list_formats is not None else None

    state = {"paragraph": subdoc.add_paragraph(), "in_list_item": False}
    has_answer_element = False

    def append_nodes(nodes: list[ContentNode]) -> None:
        _render_nodes_into_subdoc(state, subdoc, nodes, image_base_dir, list_num_ids)

    if not question.widgets:
        append_nodes(question.prompt_segments[0] if question.prompt_segments else [])
        _trim_trailing_empty_paragraph(subdoc)
        return subdoc, answer_element, has_answer_element

    for i, widget in enumerate(question.widgets):
        # Any ListItemStart/ListItemEnd trailing/leading this segment is
        # processed normally here (never trimmed away - see their
        # docstrings), which already applies real list numbering to
        # state["paragraph"] and tracks state["in_list_item"] before the
        # widget's own render below runs - no separate peeling step needed.
        # (Earlier draft of this logic *did* peel a trailing ListItemStart
        # out and hand it to the widget render as a "pending marker" - that
        # was solving a problem specific to the old flat-text-marker
        # rendering, where leaving it in the segment would've put the
        # marker text in the wrong paragraph. It doesn't apply anymore now
        # that block-display widgets themselves reuse state["paragraph"]
        # when already inside a list item, below - and that peeling missed
        # real content entirely when a widget wasn't a <li>'s *sole*
        # content, e.g. physical-or-chemical's images+arrow+colon before
        # each widget, confirmed by tracing through real fetched HTML.)
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
                else _render_fill_in_inline(state["paragraph"], widget, prefs, image_base_dir)
            )
        else:  # "block" or "template"
            if display == "template":
                has_answer_element = True
            target = answer_element if display == "template" else subdoc
            # Reuse the current (already list-numbered) paragraph as this
            # widget's first line/option when we're mid-list-item - a real
            # *new* paragraph here would have no numPr/indent of its own at
            # all. Only when `target` is `subdoc` (plain "block" display):
            # "template" display routes to answer_element, a wholly
            # different Subdoc state["paragraph"] doesn't belong to.
            first_paragraph = state["paragraph"] if (state["in_list_item"] and target is subdoc) else None
            # General fix, not selector-specific: "block" display means "this
            # widget's content starts its own line" - if the reused paragraph
            # already has content on its current line (e.g.
            # physical-or-chemical's image/arrow/colon text preceding the
            # widget within the same <li>), that requires an explicit soft
            # break here, for any block-display widget kind, not just
            # multiple-choice/checkbox options.
            if first_paragraph is not None and first_paragraph.runs:
                first_paragraph.add_run().add_break()
            runs = (
                _render_selector_block(target, widget, prefs, image_base_dir, first_paragraph)
                if is_selector
                else _render_fill_in_block(target, widget, prefs, image_base_dir, first_paragraph)
            )
            # Only start a fresh paragraph when this widget wasn't reusing an
            # already-list-numbered paragraph (first_paragraph is None) - when
            # it was (a block-display widget inside a <li>), the upcoming
            # ListItemEnd/ParagraphBreak handling in the next prompt_segment
            # already starts the correct next paragraph; unconditionally
            # starting one here left a spurious empty paragraph behind between
            # every list item (confirmed: question_contents only, since
            # _build_answer_contents never goes through this widget-render
            # branch at all - it walks answer_panel_text through the generic
            # node renderer, which is why the extra blank line only appeared
            # in the question, not the answer key).
            if display == "block" and first_paragraph is None:
                state["paragraph"] = subdoc.add_paragraph()

        if display != "none" and prefs.draw_border:
            for run in runs:
                _add_run_border(run)

    append_nodes(question.prompt_segments[-1])
    _trim_trailing_empty_paragraph(subdoc)
    return subdoc, answer_element, has_answer_element


def _trim_trailing_empty_paragraph(subdoc) -> None:
    """Drop a genuinely-empty trailing paragraph from `subdoc`, if any.

    A block-display widget always ends its own line by starting a *fresh*
    paragraph for whatever comes next (the `state["paragraph"] =
    subdoc.add_paragraph()` call above) - when that widget is the *last*
    thing in the question (nothing left to fill that fresh paragraph), it's
    pure artifact, not real content, and would otherwise splice into
    `question_contents` as a stray blank paragraph (confirmed: this, not a
    template/Jinja issue, was the source of a residual blank line the
    starter template's own `{%p %}` control-flow fix didn't - and
    shouldn't have - touched). Never removes `subdoc`'s only paragraph, even
    if it's empty - only a genuinely superfluous *trailing extra* one.
    """
    paragraphs = subdoc.paragraphs
    if len(paragraphs) <= 1:
        return
    last = paragraphs[-1]
    if not last.runs:
        last._p.getparent().remove(last._p)


def _append_run_text(
    paragraph, text: str, bold: bool = False, italic: bool = False, underline: bool = False,
    color: str | None = None,
):
    """Add one run of `text` to `paragraph`, inserting a separating space if needed.

    Mirrors Phase 3B's `append_text` prefix behavior (a paragraph that already
    has content gets a leading space before the next chunk, unless either
    side already supplies the whitespace) — now applied uniformly to every
    run added to a shared paragraph (prompt text, widget-rendered content
    alike), not just prompt-segment text as before.

    Parameters
    ----------
    color : str or None
        6-digit hex RGB (`TextRun.color`'s format, e.g. `"FF0000"`), or
        `None` to leave the run's color at the template's default.
    """
    if not text:
        return None
    if paragraph.runs and not paragraph.runs[-1].text.endswith(" ") and not text.startswith(" "):
        paragraph.add_run(" ")
    run = paragraph.add_run(text)
    run.bold = bold
    run.italic = italic
    run.underline = underline
    if color:
        run.font.color.rgb = RGBColor.from_string(color)
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
    state: dict,
    sink,
    nodes: list[ContentNode],
    image_base_dir: Path | None = None,
    list_num_ids: _ListNumIds | None = None,
) -> None:
    """Render a node sequence into `sink`, starting new paragraphs on `ParagraphBreak`.

    `state["paragraph"]` is the shared "current paragraph" also used by widget
    rendering, so prompt text and inline widget content can share one running
    paragraph exactly as Phase 3B's `append_text` did. `state["in_list_item"]`
    tracks whether we're currently inside a `<li>`'s content - while `True`, a
    `ParagraphBreak` becomes a soft line break within the *same* (already
    list-numbered) paragraph instead of starting a real new one, so a whole
    multi-line `<li>` renders as one real Word list item, not just its first
    line.

    A display-mode `MathRef` (`$$...$$`/`\\[...\\]`) is separated from
    surrounding content by a soft line break (`Run.add_break()`) rather than
    a real new paragraph, on both sides - deliberately the *same* mechanism
    already used for line breaks inside a list item (see `in_list_item`
    above), rather than a second, paragraph-based mechanism reserved for
    display math specifically. The user chose this over isolating display
    math into its own (centerable) paragraph specifically because real
    course content has display math *inside* `<ol>`/`<ul>` list items -
    one soft-break rule that behaves identically whether or not it's
    currently inside a list item means that case needs no separate handling
    when it's implemented, rather than resurfacing this same design question
    a second time. Trade-off accepted: display math can't be centered this
    way (`w:jc`/alignment is paragraph-level in OOXML, so centering only one
    line sharing a paragraph with left-aligned prose isn't achievable
    without tab-stop tricks) - skipped, not attempted, per that same
    decision. No break is added before/after when this is already the
    first/last thing in its segment.
    """
    for node in nodes:
        if isinstance(node, ListItemStart):
            _start_list_item_paragraph(state, sink, node, list_num_ids)
            state["needs_break_before_next"] = False
            continue
        if isinstance(node, ListItemEnd):
            state["in_list_item"] = False
            continue
        if isinstance(node, ParagraphBreak):
            if state.get("in_list_item"):
                state["paragraph"].add_run().add_break()
            else:
                state["paragraph"] = sink.add_paragraph()
            state["needs_break_before_next"] = False
            continue
        if isinstance(node, MathRef) and node.display_mode:
            if state["paragraph"].runs:
                state["paragraph"].add_run().add_break()
            _render_one_node(state["paragraph"], node, image_base_dir)
            state["needs_break_before_next"] = True
            continue
        if state.pop("needs_break_before_next", False):
            state["paragraph"].add_run().add_break()
        _render_one_node(state["paragraph"], node, image_base_dir)


def _start_list_item_paragraph(
    state: dict, sink, item: ListItemStart, list_num_ids: _ListNumIds | None
) -> None:
    """Start (or reuse) `state["paragraph"]` as one list item's own paragraph.

    Only starts a *fresh* paragraph if the current one already has content -
    a `ListItemStart` immediately following a real `ParagraphBreak` (the
    common case) finds an already-empty paragraph and reuses it directly,
    avoiding a redundant blank one. Can't rely on that preceding
    `ParagraphBreak` always being there, though: it's plain formatting and
    can legitimately have been trimmed away (e.g. this segment starts
    exactly at a `<li>` boundary right after a widget), while `ListItemStart`
    itself never is - see its docstring.
    """
    if state["paragraph"].runs:
        state["paragraph"] = sink.add_paragraph()
    _apply_pending_list_item(state["paragraph"], item, list_num_ids)
    state["in_list_item"] = True


def _apply_pending_list_item(paragraph, item: ListItemStart, list_num_ids: _ListNumIds | None) -> None:
    """Mark `paragraph` as list item `item`'s own paragraph.

    Real `<w:numPr>` numbering when `list_num_ids` is available and `item`
    has a real `list_id` (i.e. reached via the walker's dedicated
    `<ol>`/`<ul>` handling, not a stray `<li>`) - otherwise falls back to
    plain prepended marker text, same as before this follow-up.
    """
    if list_num_ids is not None and item.list_id is not None:
        num_id = list_num_ids.num_id_for(item.list_id, item.ordered)
        _apply_list_numbering(paragraph, num_id)
    else:
        _append_run_text(paragraph, _list_marker_text(item))


def _list_marker_text(node: ListItemStart) -> str:
    if node.ordered and node.index is not None:
        return f"{node.index}. "
    return "• "


def _render_one_node(paragraph, node: ContentNode, image_base_dir: Path | None = None) -> Run | None:
    if isinstance(node, TextRun):
        return _append_run_text(paragraph, node.text, node.bold, node.italic, node.underline, node.color)
    if isinstance(node, ParagraphBreak):
        # Only reached via `_render_nodes_to_paragraph` (inline widget content) -
        # `_render_nodes_into_subdoc` intercepts `ParagraphBreak` itself to start
        # a real new paragraph (or a soft break) instead. Here, there's no
        # paragraph to split, so treat it as a plain word-separating space
        # instead of dropping it.
        return _append_run_text(paragraph, " ")
    if isinstance(node, ListItemStart):
        # Only reached via `_render_nodes_to_paragraph` (inline widget-scoped
        # content, e.g. option/label text) - real list numbering needs a
        # `sink` to create/track paragraphs against, which that context
        # doesn't have (and realistically never needs to - PL option/label
        # text doesn't itself contain nested lists), so this always falls
        # back to plain marker text regardless of `list_num_ids`.
        return _append_run_text(paragraph, _list_marker_text(node))
    if isinstance(node, ListItemEnd):
        return None
    if isinstance(node, ImageRef):
        if node.local_path.lower().endswith(".svg") and image_base_dir is not None:
            return _render_svg_image_ref(paragraph, node, image_base_dir)
        return _render_image(paragraph, node, image_base_dir)
    if isinstance(node, MathRef):
        return _render_math(paragraph, node)
    if isinstance(node, SvgRef):
        return _render_svg(paragraph, node)
    return None


def _render_math(paragraph, node: MathRef) -> Run | None:
    """Render `node` as a real LaTeX-compiled image, falling back to raw-text math.

    Falls back (rather than raising) whenever `latex`/`dvipng` aren't
    available or the LaTeX source fails to compile - see
    `pl2docx.latex_math`'s module docstring for why a real LaTeX install is
    used instead of a pure-Python converter, and why this can fail on a
    machine without one. A bad/unsupported equation should degrade one
    question's math, not fail the whole document's render.
    """
    try:
        rendered = render_math_png(node.latex, node.display_mode)
    except LatexRenderError as exc:
        logger.warning("Falling back to plain-text math for %r: %s", node.latex, exc)
        delim = "$$" if node.display_mode else "$"
        return _append_run_text(paragraph, f"{delim}{node.latex}{delim}")
    image_ref = ImageRef(local_path=rendered.png_path.name, alt=node.latex)
    run = _render_image(
        paragraph,
        image_ref,
        image_base_dir=rendered.png_path.parent,
        default_width=Inches(rendered.width_in),
    )
    if run is not None:
        _set_run_baseline_offset(run, rendered.depth_pt)
    return run


def _render_svg(paragraph, node: SvgRef) -> Run | None:
    """Render `node` as a real rasterized (PNG) picture, falling back to alt text.

    Falls back (rather than raising) whenever Chromium isn't available or
    the SVG markup fails to rasterize - see `pl2docx.svg_render`'s module
    docstring for why headless-browser rasterization is used instead of a
    pure-Python SVG renderer. A bad/unsupported diagram should degrade one
    question's content, not fail the whole document's render.
    """
    try:
        rendered = render_svg_png(node.svg_markup)
    except SvgRenderError as exc:
        logger.warning("Falling back to alt text for inline SVG: %s", exc)
        return _append_run_text(paragraph, node.alt)
    image_ref = ImageRef(local_path=rendered.png_path.name, alt=node.alt)
    return _render_image(
        paragraph,
        image_ref,
        image_base_dir=rendered.png_path.parent,
        default_width=Inches(rendered.width_in),
    )


def _render_svg_image_ref(paragraph, node: ImageRef, image_base_dir: Path) -> Run | None:
    """Rasterize an `<img src=*.svg>`-referenced local file, then embed as a picture.

    `fetch.py` downloads every same-origin `<img>` (`.svg` included, e.g. an
    SVG file used with `<pl-figure>`) with no content-type filtering - by
    render time the referenced file already exists locally as real SVG
    markup, but `run.add_picture()` (what plain `_render_image` does) can't
    embed raw SVG directly, hence routing through the same
    `pl2docx.svg_render` rasterization helper `SvgRef` nodes use.
    """
    image_path = image_base_dir / node.local_path
    if not image_path.is_file():
        return _append_run_text(paragraph, node.alt or "[image]")
    try:
        svg_markup = image_path.read_text(encoding="utf-8")
    except OSError:
        return _append_run_text(paragraph, node.alt or "[image]")
    try:
        rendered = render_svg_png(svg_markup)
    except SvgRenderError as exc:
        logger.warning("Falling back to alt text for %s: %s", node.local_path, exc)
        return _append_run_text(paragraph, node.alt or "[image]")
    # Prefer the source <img width> (PL's own intended on-page size) over the
    # SVG's own intrinsic size when both are available, matching how every
    # other ImageRef already prioritizes node.width_px over default_width.
    width = Inches(node.width_px / _CSS_PX_PER_INCH) if node.width_px else Inches(rendered.width_in)
    png_ref = ImageRef(local_path=rendered.png_path.name, alt=node.alt)
    return _render_image(paragraph, png_ref, image_base_dir=rendered.png_path.parent, default_width=width)


def _set_run_baseline_offset(run: Run, depth_pt: float) -> None:
    """Lower `run` by `depth_pt` points via OOXML's run-level `<w:position>`.

    Word anchors an inline picture's *bottom* edge to the surrounding text's
    baseline, treating the whole image as if it had no descender - correct
    only when the image itself has none. A rendered-math PNG's bottom edge
    is the bottom of whatever descends furthest below the LaTeX baseline
    (a fraction's denominator, a subscript), so without this offset such
    content visibly floats too high (confirmed by the user against
    fraction/subscript-heavy real course content). `w:position`'s value is
    in half-points, negative to lower (ECMA-376 17.3.2.36) - `depth_pt=0.0`
    (no descender) is a no-op, left unset rather than writing a redundant
    zero.
    """
    if depth_pt <= 0:
        return
    half_points = round(depth_pt * 2)
    if half_points == 0:
        return
    r_pr = run._element.get_or_add_rPr()
    position = OxmlElement("w:position")
    position.set(qn("w:val"), str(-half_points))
    r_pr.append(position)


def _render_image(
    paragraph, node: ImageRef, image_base_dir: Path | None, default_width=_DEFAULT_IMAGE_WIDTH
) -> Run | None:
    """Embed `node` as a real inline picture, falling back to alt text.

    Falls back (rather than raising) whenever the picture can't actually be
    embedded - no `image_base_dir` given, the file isn't on disk (e.g. an
    external image `fetch.py` deliberately didn't download), or python-docx
    itself rejects the file (e.g. corrupt/unsupported format) - a missing or
    bad image shouldn't fail the whole document's render.

    Parameters
    ----------
    default_width : docx.shared.Length
        Width to use when `node.width_px` isn't set - `_DEFAULT_IMAGE_WIDTH`
        for fetched question images, or a real computed width for rendered
        math PNGs (`_render_math` passes `RenderedMath.width_in` explicitly -
        deliberately *not* left to `add_picture`'s own DPI-metadata
        auto-sizing, confirmed unreliable for `dvipng` output: it writes a
        fixed ~96 DPI `pHYs` chunk regardless of the DPI actually used to
        rasterize, which silently inflated every embedded equation ~6x).
    """
    if image_base_dir is not None and node.local_path:
        image_path = image_base_dir / node.local_path
        if image_path.is_file():
            if paragraph.runs and not paragraph.runs[-1].text.endswith(" "):
                paragraph.add_run(" ")
            run = paragraph.add_run()
            width = Inches(node.width_px / _CSS_PX_PER_INCH) if node.width_px else default_width
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
        marker_run = _append_run_text(paragraph, f"{marker} ")
        _apply_marker_font_size(marker_run, paragraph, prefs.list_style)
        _render_nodes_to_paragraph(paragraph, option_nodes, image_base_dir)
        combined = list(paragraph.runs[option_start:])
        if prefs.bold_correct and idx in widget.correct_option_indices:
            for run in combined:
                run.bold = True
        runs.extend(combined)
    return runs


def _render_fill_in_inline(paragraph, widget: Widget, prefs, image_base_dir: Path | None = None) -> list[Run]:
    return _fill_in_runs(paragraph, widget, prefs, image_base_dir)


def _render_selector_block(
    sink, widget: Widget, prefs, image_base_dir: Path | None = None, first_paragraph=None
) -> list[Run]:
    """Render each option as its own paragraph - or, given `first_paragraph`
    (this widget is a list item's own content), *all* options into that one
    paragraph instead, joined by soft line breaks - real content:
    `physical-or-chemical`'s dropdown-rendered options are each a `<li>`'s
    sole content. Keeps every option under the list item's own indent/number
    (a real second paragraph per option would have no numPr/indent of its
    own at all), matching the same "whole `<li>` = one paragraph" policy used
    everywhere else in this module.

    Every option starts on its own soft-broken line within that shared
    paragraph - block display inherently means "one option per line". (The
    leading break before the *first* option, needed when `first_paragraph`
    already carries other content on its current line - e.g.
    `physical-or-chemical`'s image/arrow/colon text preceding the options
    within the same `<li>` - is handled by the caller, `_build_question_contents`,
    once, before this function is even called - not selector-specific, so it
    applies uniformly to any block-display widget kind.)
    """
    runs: list[Run] = []
    single_paragraph_mode = first_paragraph is not None
    paragraph = first_paragraph
    for idx, option_nodes in enumerate(widget.options):
        if single_paragraph_mode:
            if idx > 0:
                paragraph.add_run().add_break()
        else:
            paragraph = sink.add_paragraph()
        marker_start = len(paragraph.runs)
        marker = _LIST_MARKERS[prefs.list_style](idx)
        marker_run = _append_run_text(paragraph, f"{marker} ")
        _apply_marker_font_size(marker_run, paragraph, prefs.list_style)
        _render_nodes_to_paragraph(paragraph, option_nodes, image_base_dir)
        combined = list(paragraph.runs[marker_start:])
        if prefs.bold_correct and idx in widget.correct_option_indices:
            for run in combined:
                run.bold = True
        runs.extend(combined)
    return runs


def _render_fill_in_block(
    sink, widget: Widget, prefs, image_base_dir: Path | None = None, first_paragraph=None
) -> list[Run]:
    paragraph = first_paragraph if first_paragraph is not None else sink.add_paragraph()
    return _fill_in_runs(paragraph, widget, prefs, image_base_dir)


#: Fallback blank-line width (underscore count) when a widget has no resolved
#: `width_chars` at all (e.g. a non-built-in `additional-elements` fill-in
#: tag that doesn't follow PL's `size`/`cols` convention) - this project's
#: previous fixed width, kept as the no-signal fallback.
_DEFAULT_BLANK_CHARS = 20
#: Clamp range for a widget's own `width_chars` (PL's real `size`/`cols`
#: attribute value, verbatim) - guards against an instructor-set value that's
#: impractically small/large for a printed blank line, not a guess at PL's
#: own bounds (PL itself doesn't clamp `size`).
_MIN_BLANK_CHARS = 5
_MAX_BLANK_CHARS = 80
#: Enlarged font size (points) for the blank line specifically, when
#: `draw-border` is on - a run-level border sits tight against its text (see
#: `_add_run_border`), which reads as cramped for handwritten answers at
#: normal text size (confirmed by the user); bumping just the blank run's
#: size gives real breathing room without resizing the widget's label/suffix
#: text too.
_BORDERED_BLANK_FONT_SIZE_PT = 20


def _blank_text(widget: Widget) -> str:
    """Choose the blank line's underscore run, sized from the widget's own input width."""
    chars = widget.width_chars if widget.width_chars is not None else _DEFAULT_BLANK_CHARS
    chars = max(_MIN_BLANK_CHARS, min(_MAX_BLANK_CHARS, chars))
    return "_" * chars


def _fill_in_runs(paragraph, widget: Widget, prefs, image_base_dir: Path | None = None) -> list[Run]:
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

    `prefs.default_label` supplies fallback label text only when the widget's
    own source HTML had none (`widget.label is None`) - never overrides a
    real label. The blank's own run is tracked separately (`blank_run`, not
    folded into the snapshot-only `combined` list) so `draw-border` can size
    just that run larger, without touching label/suffix text size.
    """
    start = len(paragraph.runs)
    if widget.label is None:
        if prefs.default_label:
            _append_run_text(paragraph, prefs.default_label)
    else:
        _render_nodes_to_paragraph(paragraph, widget.label, image_base_dir)
    blank_run = _append_run_text(paragraph, _blank_text(widget))
    if prefs.draw_border and blank_run is not None:
        blank_run.font.size = Pt(_BORDERED_BLANK_FONT_SIZE_PT)
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


def _build_answer_contents(
    tpl: DocxTemplate,
    question: ParsedQuestion,
    image_base_dir: Path | None = None,
    list_formats: ListFormats | None = None,
):
    subdoc = tpl.new_subdoc()
    if question.answer_panel_text is not None:
        list_num_ids = _ListNumIds(list_formats) if list_formats is not None else None
        state = {"paragraph": subdoc.add_paragraph(), "in_list_item": False}
        _render_nodes_into_subdoc(state, subdoc, question.answer_panel_text, image_base_dir, list_num_ids)
    return subdoc


def _build_answer_space(tpl: DocxTemplate, blank_lines: int = ANSWER_SPACE_BLANK_LINES):
    subdoc = tpl.new_subdoc()
    for _ in range(blank_lines):
        subdoc.add_paragraph("")
    return subdoc


def _resolve_answer_space_lines(question: ParsedQuestion, element_config: ElementConfig) -> int:
    """Resolve how many blank lines `question`'s answer_space should give students.

    Per-kind override via `SelectorPreferences`/`FillInPreferences.blank_answer_lines`
    (see their docstrings) - a compound question with multiple widgets of different
    kinds, each configuring a different value, uses the *largest* one (confirmed
    with the user: "the max value across all widgets on the question" - a question
    needs room for whichever of its parts needs the most space, not the least).
    Falls back to `ANSWER_SPACE_BLANK_LINES` when the question has no widgets at all,
    or none of its widgets' kinds configure this preference.
    """
    configured = [
        prefs.blank_answer_lines
        for widget in question.widgets
        if (prefs := resolve_preferences(element_config, widget.kind)).blank_answer_lines is not None
    ]
    return max(configured) if configured else ANSWER_SPACE_BLANK_LINES
