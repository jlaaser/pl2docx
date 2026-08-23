"""Parse a fetched PrairieLearn `instance_question` page into a structured form.

Anchors used here were verified against the PL source (reference clone) and
against real fetched HTML during Phase 1, not guessed:

- ``div.question-block > h1`` / ``div.question-block > div.question-body``:
  generic per-question containers rendered by
  ``components/QuestionContainer.tsx``'s ``QuestionPanel``, applied uniformly
  regardless of element type.
- ``div.answer-body``: generic container for the element's
  ``render(panel='answer')`` output, present (but empty/hidden) in blank HTML
  and populated once ``showCorrectAnswer`` is true. Rendered in full
  regardless of element type — some questions wrap PL's default answer
  markup in ``<pl-hide-in-panel answer="true">`` and supply custom
  explanatory text instead (``elements/pl-hide-in-panel/pl-hide-in-panel.py:16-30``
  renders to nothing when hidden; sibling custom content still renders
  normally, ``components/QuestionContainer.tsx:113-115``), so this module
  never assumes `.answer-body` follows any particular element's default
  shape — matching specific options for bolding is a best-effort bonus on
  top of always showing the raw text, not a precondition for it.
- ``#question-score-panel-content``: generic point-value table, a sibling of
  `.question-block`/`.grading-block` but part of the same full-page fetch
  (``components/QuestionScore.tsx:33-211``). A row labeled `"Value:"`
  (Homework-type assessments) or `"Available points:"` (Exam-type) holds the
  question's worth — not to be confused with the table's always-present
  `"Total points:"` row, which is the student's current score.
- Element-specific input markup (``pl-multiple-choice``, ``pl-checkbox``, and the
  fill-in-type elements below) is documented inline below, from each element's own
  ``.py``/``.mustache`` source. ``form-check-inline`` (on a `.form-check`) signals
  PL's own inline layout choice (``pl-multiple-choice.mustache``/
  ``pl-checkbox.mustache``); a `<select>` instead of `<input type=radio>` signals a
  `display="dropdown"` `pl-multiple-choice`.
- **Fill-in-type elements** (``pl-string-input``, ``pl-integer-input``,
  ``pl-number-input``, ``pl-symbolic-input``, ``pl-units-input`` — confirmed core PL
  elements, all sharing one markup pattern) wrap their `<input>`/`<textarea>` in a
  container whose class starts with `input-group`, with sibling
  `.input-group-text` spans holding the element's `label`/`suffix` text
  (``pl-string-input.mustache`` et al.). The input itself carries a
  `pl-{element}-input` (or `pl-{element}-multiline`, for elements offering a
  multi-line textarea) class alongside its `name` attribute — this is exactly the
  element's own registered tag name plus `-input`/`-multiline`, which is what lets
  `_add_fill_in_groups` below detect any element following this convention purely
  from its tag name string, with no element-specific code. Confirmed **not**
  followed by every fill-in-shaped element: `pl-big-o-input`'s `<input>` carries
  class `big-o-input-input` (missing the `pl-` prefix) — deliberately not
  supported (built-in or via `additional-elements`) until that's worth a special
  case. `pl-symbolic-input` additionally has a `formula_editor` rendering mode
  whose visible widget is a JS-populated `<math-field>` custom element (a
  different tag name, carrying the `pl-symbolic-input-input` class but not
  `<input>`/`<textarea>`) — restricting detection to `<input>`/`<textarea>` tags
  specifically (not just any tag with a matching class) is what correctly excludes
  it, so a `formula_editor`-mode `pl-symbolic-input` safely falls through to
  "unsupported" rather than being mis-detected.
"""

from __future__ import annotations

import json
import re
import string
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from typing import Literal

from bs4 import BeautifulSoup, Comment, NavigableString, Tag


@dataclass(frozen=True)
class TextRun:
    """A run of plain text carrying inline character formatting.

    Parameters
    ----------
    text : str
        The text itself. Already whitespace-collapsed (internal runs of
        whitespace reduced to a single space) and never empty/whitespace-only
        except where a single interior space is needed as a separator between
        differently-formatted neighboring runs.
    bold, italic, underline : bool
        Whether `<strong>`/`<b>`, `<em>`/`<i>`, `<u>` (respectively), or an
        equivalent inline `style` declaration (`font-weight: bold`),
        wrapped this text anywhere in its ancestry within the source HTML.
    color : str or None
        6-digit uppercase hex RGB (e.g. `"FF0000"`, no leading `#`) if any
        ancestor set an inline `style="color: ..."` this text inherits
        (named CSS color, `#rgb`/`#rrggbb` hex, or `rgb(r, g, b)` - see
        `_parse_css_color`), else `None` (render in the template/theme's
        default color). Best-effort, same philosophy as bold/italic/
        underline detection: an unrecognized color value/keyword is simply
        ignored rather than raising.
    """

    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False
    color: str | None = None


@dataclass(frozen=True)
class ParagraphBreak:
    """A paragraph boundary in the source HTML (`<p>`/`<br>`/list-item edges).

    Parameters
    ----------
    hard : bool
        Whether this break marks a real block-tag (`<p>`) boundary, as
        opposed to an incidental one (`<br>`, or one synthesized to separate
        a `<li>` from its neighbor). Only used by the renderer's in-list-item
        soft-line-break handling: a `<p>`-to-`<p>` transition *within one
        `<li>`* needs a full blank line (two soft breaks) to visually read as
        separate paragraphs, since being inside a list item means it can't
        fall back on a real new Word paragraph (with the template's own
        paragraph spacing) the way the same transition would outside a list.
        `_normalize_nodes`'s consecutive-`ParagraphBreak` collapsing
        propagates `hard=True` forward when merging (so it survives being
        deduplicated down to one node), but never fabricates it - a `hard`
        break always traces back to a real `<p>` tag.
    """

    hard: bool = False


@dataclass(frozen=True)
class ListItemStart:
    """Marks the start of an `<li>`'s content, immediately after its `ParagraphBreak`.

    Parameters
    ----------
    ordered : bool
        Whether this `<li>`'s parent list is an `<ol>` (numbered) rather than
        `<ul>` (bulleted).
    index : int or None
        This item's 1-based position among its parent `<ol>`/`<ul>`'s direct
        `<li>` children, when known (i.e. reached via the walker's dedicated
        `<ol>`/`<ul>` handling). `None` for a stray `<li>` encountered outside
        any list container (malformed HTML) - renders as an unordered bullet
        regardless of `ordered`, since there's no real position to number.
    list_id : int or None
        Identifies which distinct `<ol>`/`<ul>` this item belongs to - `id()`
        of that list's own `Tag` object, captured once per list in the
        walker's `<ol>`/`<ul>` handling. Needed because a single list can end
        up split across multiple `ParsedQuestion.prompt_segments` (each
        interleaved widget cuts a new segment - real content:
        `physical-or-chemical`'s whole 3-item `<ol>` is one list, split into
        3 segments by its 3 widgets), so the renderer needs a way to tell
        "still the same list, keep counting" from "a different list, start a
        new Word numbering instance." Safe to use `id()` here specifically
        because it's only ever compared within the `ParsedQuestion` produced
        by *one* `parse_instance_question_html` call (the renderer's
        list-id-to-Word-numId map is freshly built per question and discarded
        after) - not a case where CPython's id-reuse-after-garbage-collection
        could cause two unrelated lists to collide. `None` for a stray `<li>`
        outside any list container, same as `index`.

    Notes
    -----
    Unlike `ParagraphBreak`, a `ListItemStart` is never dropped during
    whitespace/boundary normalization, even when it ends up as the first or
    last node in a segment - it carries real structural meaning (this is a
    numbered/bulleted list item), not merely incidental formatting, so
    dropping it would silently turn a list into unmarked paragraphs.
    """

    ordered: bool = False
    index: int | None = None
    list_id: int | None = None


@dataclass(frozen=True)
class ListItemEnd:
    """Marks the end of a `<li>`'s content - the counterpart to `ListItemStart`.

    Notes
    -----
    Needed so the renderer can tell precisely when "still inside this list
    item, treat further paragraph breaks as soft line breaks within it"
    turns back into ordinary new-paragraph behavior, rather than inferring it
    ambiguously from surrounding content.

    Like `ListItemStart`, this is never dropped during whitespace/boundary
    normalization even at a segment's edge. A `<li>` whose sole content is a
    widget (real content: every item in `TEST/pl-scinum-input`) puts this
    node as the *leading* node of the prompt segment right after that
    widget's marker - if it were trimmed away there (as a first draft of this
    reasoning assumed, before tracing the widget-adjacent case through),
    the renderer would never learn that list item's content had ended, and
    "in list item" render state would leak into whatever comes next.
    """


@dataclass(frozen=True)
class ImageRef:
    """An inline `<img>`, referencing the local file `fetch.py` already downloaded.

    Parameters
    ----------
    local_path : str
        The `<img src>` value as fetched HTML already has it (e.g.
        `"files/1234_0_diagram.png"`, relative to the instance_question HTML's
        own directory — see `fetch.py`'s `_download_images`). Not yet resolved
        to an absolute filesystem path here; that's the renderer's job, since
        only it knows the instance directory.
    alt : str
        The `<img alt>` text, if any. Used as interim fallback display text
        before Phase 4 increment 2, and still the fallback if the real image
        file can't be found on disk at render time.
    width_px : int or None
        The `<img width>` attribute value, if present and parseable — this is
        PL's own intended on-page display size (CSS reference pixels, i.e.
        96px/inch), not necessarily the source image file's native
        resolution. `None` if absent/unparseable, in which case the renderer
        falls back to a fixed default width.
    """

    local_path: str
    alt: str = ""
    width_px: int | None = None


@dataclass(frozen=True)
class MathRef:
    """An inline or display-mode math span, not yet converted to OMML.

    Parameters
    ----------
    latex : str
        The raw LaTeX source, delimiters stripped (e.g. `"x^2"` from
        `"$x^2$"`).
    display_mode : bool
        Whether this was a display-mode span (`$$...$$`/`\\[...\\]`) rather
        than inline (`$...$`/`\\(...\\)`).
    """

    latex: str
    display_mode: bool = False


@dataclass(frozen=True)
class SvgRef:
    """An inline `<svg>...</svg>` block, not yet rasterized to a picture.

    Parameters
    ----------
    svg_markup : str
        The `<svg ...>...</svg>` tag's own outer HTML, verbatim, as captured
        from the source page — handed to `pl2docx.svg_render.render_svg_png`
        unmodified at render time.
    alt : str
        Fallback display text if rasterization fails or Chromium isn't
        available. This module has no way to derive meaningful alt text from
        arbitrary SVG shape markup (unlike `<img alt>`), so this is a fixed
        generic string unless the source `<svg>` carries an `aria-label`
        (confirmed present on real course-element output, e.g.
        `pl-lewisstructure`'s print-mode SVG) — used when available, since
        real SVGs reaching this module are already accessibility-annotated.
    """

    svg_markup: str
    alt: str = "[diagram]"


@dataclass(frozen=True)
class TableRef:
    """A `<table>`'s content, as header/body rows of rich-content cells.

    Parameters
    ----------
    header_rows : list[list[list[ContentNode]]]
        Rows to render as the table's header - each row a list of cells, each
        cell its own rich-content node sequence (so formatting/math/images
        inside a cell still render normally). Empty if the source `<table>`
        had no `<thead>` and no all-`<th>` first row (see Notes).
    body_rows : list[list[list[ContentNode]]]
        Every other row, same cell shape as `header_rows`.

    Notes
    -----
    Header-row detection handles two shapes confirmed against real fetched PL
    pages, not just the well-formed one: a `<thead>` whose header cells are
    wrapped in one or more real `<tr>` elements (every row inside becomes a
    header row), *and* a `<thead>` whose `<th>` cells are direct children of
    `<thead>` itself with no wrapping `<tr>` at all (some of this course's
    authored tables are inconsistent about including it) - treated as a single
    implicit header row. A `<table>` with no `<thead>` at all falls back to
    promoting its first row to the header only if every one of that row's
    cells is a `<th>` (never for a `<td>`-only or mixed first row).

    Does not assume a widget's `<input>`/`<select>` never appears inside a
    cell - if one does, its containing tag is still replaced with the usual
    `_WidgetMarker` internally, but that marker ends up nested inside a cell's
    node list rather than at the top level `_extract_prompt_segments` splits
    on, so it will not actually splice out as a real widget. Not handled
    further since no real course content does this today.
    """

    header_rows: list[list[list["ContentNode"]]]
    body_rows: list[list[list["ContentNode"]]]


#: One node in a flattened, order-preserving walk of an HTML fragment's content.
#: Produced by `_walk_content`/consumed by `pl2docx.element_renderer` to render
#: formatted runs, paragraph breaks, images, and math into a docx Subdoc instead
#: of the plain strings Phases 2/3 used. `ImageRef`/`MathRef` nodes are recognized
#: by the walker starting Phase 4 increment 1, but only rendered as their real
#: picture/OMML form once increments 2/3 land (element_renderer falls back to
#: alt text / raw LaTeX text until then).
ContentNode = TextRun | ParagraphBreak | ListItemStart | ListItemEnd | ImageRef | MathRef | SvgRef | TableRef


def plain(text: str) -> list[ContentNode]:
    """Wrap a plain string as a single-`TextRun` node sequence.

    Parameters
    ----------
    text : str
        Plain text with no formatting.

    Returns
    -------
    list[ContentNode]
        `[]` if `text` is empty, else a single unformatted `TextRun`.

    Notes
    -----
    Convenience for constructing `Widget`/`ParsedQuestion` fixtures/test data
    by hand; `parse_instance_question_html` itself always produces node
    sequences via `_walk_content`, never this function.
    """
    return [TextRun(text)] if text else []


def plain_text(nodes: list[ContentNode]) -> str:
    """Flatten a node sequence back to plain text, discarding all formatting.

    Parameters
    ----------
    nodes : list[ContentNode]
        A node sequence as found in `ParsedQuestion.prompt_segments`,
        `Widget.options`, etc.

    Returns
    -------
    str
        `TextRun` text concatenated in order (paragraph/list breaks become a
        single space); `ImageRef`/`MathRef` nodes contribute their `alt`/
        `latex` text respectively. Whitespace is collapsed and the result
        stripped, matching the old `get_text()`-based flattening's shape —
        useful for callers/tests that only care about the text content.
    """
    parts: list[str] = []
    for node in nodes:
        if isinstance(node, TextRun):
            parts.append(node.text)
        elif isinstance(node, ImageRef):
            parts.append(node.alt)
        elif isinstance(node, MathRef):
            parts.append(node.latex)
        elif isinstance(node, SvgRef):
            parts.append(node.alt)
        elif isinstance(node, TableRef):
            cell_texts = [
                plain_text(cell) for row in (*node.header_rows, *node.body_rows) for cell in row
            ]
            parts.append(" ".join(t for t in cell_texts if t))
        else:
            parts.append(" ")
    # No separator inserted here - each TextRun already carries its own real
    # whitespace from the source HTML (e.g. "bold" then ", " already has the
    # comma+space attached), so direct concatenation is what's faithful; only
    # ParagraphBreak/ListItemStart (mapped to " " above) need a synthetic gap.
    text = re.sub(r"\s+", " ", "".join(parts))
    return text.strip()

#: The built-in (zero-config) widget kinds. `Widget.kind` is `str`, not this `Literal`,
#: since an `additional-elements`-configured widget's `kind` is an arbitrary
#: instructor-declared PL element tag name (e.g. `"pl-scinum-input"`), not a member of
#: this closed set — this alias exists for documentation/reference, not as an
#: exhaustive type constraint.
QuestionKind = Literal[
    "multiple_choice", "checkbox", "string_input", "integer_input", "number_input",
    "symbolic_input", "units_input",
]

#: Built-in fill-in-type kinds, mapped to the PL element tag name whose markup
#: identifies them. All share the exact same detection pattern (see
#: `_add_fill_in_groups`) — confirmed against each element's own PL source, not
#: guessed. `additional-elements`-configured tags (e.g. `pl-scinum-input`, a
#: course-specific element following this same convention) use this identical
#: mechanism but aren't listed here since they come from the caller's config, not a
#: fixed set.
_BUILTIN_FILL_IN_TAGS: dict[str, str] = {
    "string_input": "pl-string-input",
    "integer_input": "pl-integer-input",
    "number_input": "pl-number-input",
    "symbolic_input": "pl-symbolic-input",
    "units_input": "pl-units-input",
}


class UnsupportedElementError(RuntimeError):
    """Raised when a question page's generic containers can't be found at all.

    Compound questions (more than one distinct input-widget group on a page) are
    supported as of Phase 3B. **A question with zero recognized input widgets is
    no longer an error** (Phase 5 subphase 3, 2026-08-13) — a page whose only
    content is a diagram/image/math (e.g. a `print="true"` SVG element, or a
    captured-and-flattened interactive canvas element - see
    `pl2docx.svg_render`/`pl2docx.canvas_capture`) is entirely legitimate;
    `ParsedQuestion.widgets` is simply empty and `prompt_segments` holds the page's
    whole content as one segment (see `_extract_prompt_segments`).
    `pl2docx.element_renderer.build_question_context` already handles
    `widgets == []` explicitly. This now only covers pages whose generic
    containers (`.question-block`/`.question-body`) couldn't be found at all -
    i.e. the fetched HTML doesn't look like a real PL instance_question page.
    """


@dataclass(frozen=True)
class Widget:
    """One distinct, named input-widget group on a question page.

    A "compound" question (e.g. `physical-or-chemical`'s 3 separate
    `pl-multiple-choice` dropdown sub-statements, or `previous-experience`'s radio
    group + text box) has more than one `Widget`, in source (DOM) order.

    Parameters
    ----------
    kind : str
        Which element type this widget is: one of the built-in `QuestionKind`
        values (`"multiple_choice"`/`"checkbox"`/`"string_input"`/`"integer_input"`/
        `"number_input"`/`"symbolic_input"`/`"units_input"`), or — for a fill-in-type
        widget matched via a caller-supplied `additional_fill_in_tags` entry — the
        raw PL element tag name itself (e.g. `"pl-scinum-input"`), matching how
        `pl2docx.element_config` keys `additional-elements` preferences/behavior
        class by that same tag string.
    name : str
        The input `name` attribute shared by this widget's own input tag(s) —
        distinguishes one widget from another on the same page.
    options : list[list[ContentNode]]
        For `multiple_choice`/`checkbox`, one rich-content node sequence per
        answer option, in on-page order — carries any formatting/math the
        option's own source markup had (real course content has LaTeX in
        option text, confirmed by the user; not guessed). Empty for every
        fill-in-type kind.
    option_keys : list[str or None]
        For `multiple_choice`/`checkbox`, each option's own PL-internal answer
        "key" (its rendered `<input>`/`<option>` `value` attribute, e.g.
        `"a"`/`"b"`/... - confirmed identical to the key format used in the
        page's "Variant" JSON, see `_extract_true_answer`), parallel to
        `options` (same length, same order). `None` for any option whose
        `value` couldn't be found. Always empty for every fill-in-type kind.
        Used only to resolve `correct_option_indices`, not consumed by
        rendering.
    correct_option_indices : list[int]
        For `multiple_choice`/`checkbox`, indices into `options` that PL's own
        "Variant" answer-key JSON identifies as correct for this widget's
        `name` (see `_extract_true_answer`) — authoritative, not a text-match
        guess, and immune to another widget's overlapping option text on the
        same page (matched via `option_keys`/`name`, not shared page-wide
        `.answer-body` prose). Always empty for every fill-in-type kind, and
        may be empty for `multiple_choice`/`checkbox` too when the page's
        answer-key JSON is unavailable or has no entry for this widget's
        `name` — `ParsedQuestion.answer_panel_text` remains the authoritative
        "what's the correct answer" source regardless; this is only a
        bolding enrichment on top of it.
    is_inline : bool
        For `multiple_choice`/`checkbox` rendered as radio/checkbox inputs (not a
        dropdown): whether PL's own source HTML used its inline layout
        (`form-check-inline`). Always `False` for every fill-in-type kind and for
        dropdown-rendered `multiple_choice` (no such signal exists there).
    is_dropdown : bool
        Whether this `multiple_choice` widget is rendered as a `<select>`
        (`display="dropdown"` in PL) rather than radio buttons. Always `False` for
        other kinds.
    label : list[ContentNode] or None
        For a fill-in-type kind, the element's `label` content (the
        `.input-group-text` immediately before the `<input>`), if present —
        real course content has LaTeX math here (e.g. `"pH ="`-style labels
        with formulas). Always `None` for `multiple_choice`/`checkbox`.
    suffix : list[ContentNode] or None
        For a fill-in-type kind, the element's `suffix` content (the
        `.input-group-text` immediately after the `<input>`), if present.
        Always `None` for `multiple_choice`/`checkbox`.
    width_chars : int or None
        For a fill-in-type kind, the element's on-page input width in
        characters - PL's own `size` HTML attribute (every built-in fill-in
        element, plus `pl-scinum-input`, always renders a real resolved
        value here, defaulting to 35 in `SIZE_DEFAULT` when the instructor
        didn't set one explicitly - confirmed against each element's own
        `.py`/`.mustache` source), or a multiline element's `cols` attribute
        as a fallback (same unit, different attribute name). `None` if
        neither attribute is present/parseable (e.g. a non-built-in
        `additional-elements` fill-in tag that doesn't follow this
        convention) - callers should fall back to a fixed blank width in
        that case. Always `None` for `multiple_choice`/`checkbox`.
    suppress_in_key : bool
        For `rich_text_editor` only: `True` when this widget was parsed from
        answer-key HTML (`parse_instance_question_html(..., is_answer_key=True)`).
        `pl-rich-text-editor` has no PL concept of a "correct answer" (its own
        `render(panel="answer")` always returns empty), so the renderer skips
        this widget's blank-lines rendering entirely on the key docx rather
        than reproducing the blank student answer space there too - the
        question's page-wide `answer_panel_text` (from `.answer-body`) is
        unaffected and still renders normally, since an instructor may place
        a model answer there even though it isn't tied to this specific
        widget. Always `False` for every other kind.
    statements : list[list[ContentNode]]
        For `matching` only: each statement's rich content, in on-page order
        (one `<select>` dropdown per statement in the live UI, replaced here
        with a blank/filled-in label in print form - see
        `pl2docx.element_renderer`). Always empty for other kinds.
    match_options : list[list[ContentNode]]
        For `matching` only: each answer option's rich content, in on-page
        (counter) order. Always empty for other kinds.
    counter_type : str or None
        For `matching` only: PL's own `counter-type` attribute value
        (`"lower-alpha"`/`"upper-alpha"`/`"decimal"`/`"full-text"`), read off
        the rendered `--pl-matching-counter-type` CSS custom property -
        controls how `match_options` are labeled when printed. `None` for
        other kinds, or if unparseable (callers should default to
        `"lower-alpha"`, PL's own `COUNTER_TYPE_DEFAULT`).
    correct_labels : list[str or None]
        For `matching` only: parallel to `statements` - each statement's
        correct option, as a display label already formatted per
        `counter_type` (e.g. `"1"`/`"a"`/`"A"`), when known. `None` per-entry
        when this widget was parsed from blank HTML (no correct-answer data
        available yet) or a given statement's match couldn't be resolved.
        Always empty for other kinds.
    blocks : list[list[ContentNode]]
        For `order_blocks` only: each pool block's rich content (including
        any distractor blocks - PL shows these in the same pool, per the
        user's confirmed choice to print them like any other block), in
        on-page pool order. Always empty for other kinds.
    correct_order : list[int] or None
        For `order_blocks` only: indices into `blocks`, in the correct
        answer sequence (distractors excluded - this list's length is the
        number of blanks to print). `None` when parsed from blank HTML (no
        correct-answer data available yet) or the correct order couldn't be
        resolved from the key HTML's answer panel. Always `None` for other
        kinds.
    """

    kind: str
    name: str
    options: list[list[ContentNode]] = field(default_factory=list)
    option_keys: list[str | None] = field(default_factory=list)
    correct_option_indices: list[int] = field(default_factory=list)
    is_inline: bool = False
    is_dropdown: bool = False
    label: list[ContentNode] | None = None
    suffix: list[ContentNode] | None = None
    width_chars: int | None = None
    suppress_in_key: bool = False
    statements: list[list[ContentNode]] = field(default_factory=list)
    match_options: list[list[ContentNode]] = field(default_factory=list)
    counter_type: str | None = None
    correct_labels: list[str | None] = field(default_factory=list)
    blocks: list[list[ContentNode]] = field(default_factory=list)
    correct_order: list[int] | None = None


@dataclass(frozen=True)
class ParsedQuestion:
    """A single `instance_question` page's content, in element-agnostic form.

    Parameters
    ----------
    title : str
        The question's title, from `.question-block h1`.
    prompt_segments : list[list[ContentNode]]
        The question's prompt content, with each supported input widget's own
        markup removed, split at each widget's source position. Always has
        exactly `len(widgets) + 1` entries: `prompt_segments[i]` is the
        content immediately before `widgets[i]` (for `i < len(widgets)`), and
        `prompt_segments[-1]` is the trailing content after the last widget
        (or the whole prompt, if `widgets` is empty). Rich-content node
        sequences (formatting/images/math preserved) as of Phase 4 increment
        1 — Phase 2/3 flattened this to plain `str`.
    widgets : list[Widget]
        This question's input-widget groups, in source (DOM) order. Exactly one
        for a simple question; more than one for a compound question.
    answer_panel_text : list[ContentNode] or None
        The full rich content of `.answer-body`, for the whole question (PL's
        combined answer panel doesn't mark widget boundaries, so this isn't split
        per-widget). `None` if this page has no answer-key data (i.e. parsed from
        blank/open-instance HTML, where `.answer-body` is present but empty).
        This is the authoritative "what's the correct answer" source — always
        render it in full; each widget's `correct_option_indices` is only an
        optional enrichment on top. Real answer-panel content in this course
        carries formatting and math (confirmed by the user), which is exactly
        why this needs the rich-content treatment rather than staying plain
        text.
    points : str or None
        The question's point value, as PL displays it (e.g. `"1"`), from
        `#question-score-panel-content`'s `"Value:"`/`"Available points:"`
        row. `None` if that table/row isn't present in the fetched page.
    points_numeric : float or None
        Best-effort `float()` parse of `points`. `None` if `points` is
        `None` or isn't a plain number (e.g. an unusual partial-credit
        display) — callers needing a display string should fall back to
        `points` verbatim in that case, not assume `points_numeric` parses.
    qid : str or None
        The question's real qid/directory path (e.g.
        `"TEST/pl-integer-input"`), from the page's "Staff information"
        panel. This panel is gated only on the viewer having course-staff
        role — which this tool always does — not on assessment type, so
        it's present regardless of whether the question's *title* is shown
        to real students (Exam-type assessments can hide titles; the qid is
        still there). `None` if that panel wasn't found on the page.

    Notes
    -----
    Does not assume the source HTML represents a physically consistent
    question+answer pair by itself — each widget's `correct_option_indices`/
    `answer_panel_text` are simply whatever `.answer-body` contained, which
    is empty for blank-copy HTML. Pairing a blank parse with a key parse of
    the *same* `instance_question_id` is the caller's responsibility.
    """

    title: str
    prompt_segments: list[list[ContentNode]]
    widgets: list[Widget]
    answer_panel_text: list[ContentNode] | None
    points: str | None
    points_numeric: float | None
    qid: str | None


def parse_instance_question_html(
    html: str,
    additional_fill_in_tags: Iterable[str] = (),
    additional_fill_in_class_prefixes: dict[str, str] | None = None,
    is_answer_key: bool = False,
) -> ParsedQuestion:
    """Parse one fetched `instance_question` page into a `ParsedQuestion`.

    Parameters
    ----------
    html : str
        Raw HTML of an `instance_question/:id` page, as fetched by
        `pl2docx.pl_client.PLClient.fetch_instance_questions`. May be either
        the blank (open-instance) or answer-key (closed-instance) render of
        the same variant.
    additional_fill_in_tags : Iterable[str]
        PL element tag names (e.g. `"pl-scinum-input"`) to additionally detect
        as fill-in-type widgets, beyond the built-in set — typically the
        instructor's `additional-elements` config entries whose declared
        `type` is `fill-in` (see `pl2docx.element_config.additional_fill_in_tags`).
        Detected using the exact same tag-name-derived pattern as every built-in
        fill-in element (see this module's docstring); an element not actually
        following that markup convention (e.g. `pl-big-o-input`) simply won't be
        detected unless a `class_prefix` override is also supplied via
        `additional_fill_in_class_prefixes`.
    additional_fill_in_class_prefixes : dict[str, str] or None
        Maps an `additional_fill_in_tags` entry to an override base string used
        in place of the tag itself when building `_add_fill_in_groups`'s
        `{base}-input`/`{base}-multiline` detection pattern — lets a
        non-conforming element (e.g. `pl-big-o-input`, whose real class is
        `big-o-input-input`, missing the usual `pl-` prefix) still be detected
        via the same generic mechanism instead of needing bespoke code (see
        `pl2docx.element_config.additional_fill_in_class_prefixes`). Tags not
        present in this dict fall back to using the tag itself as the base,
        unchanged from before this parameter existed.
    is_answer_key : bool
        Whether `html` is answer-key (closed-instance, `showCorrectAnswer`)
        HTML rather than a blank student copy. Consumed two ways: (1)
        `rich_text_editor` widgets (see `Widget.suppress_in_key`); (2) gates
        whether the page's "Variant" answer JSON (`_extract_true_answer`) is
        used to resolve `multiple_choice`/`checkbox`'s `correct_option_indices`
        or `matching`'s `correct_labels` at all. **Confirmed real bug, fixed
        the same session `is_answer_key` was added**: that JSON panel is
        server-rendered for staff-role viewers *regardless* of
        `showCorrectAnswer` (see `_extract_true_answer`'s own docstring) - so
        without this gate, a blank/open-instance parse would still resolve
        real correct-answer data, and `bold_correct` (on by default) would
        bold the correct MC/checkbox option, or fill in a matching statement's
        correct label, on the *student's own blank copy* - a real
        answer leak, not a hypothetical one (caught rendering real fetched
        HTML against a live PL server, not a unit-test-only finding).
        `answer_panel_text` (the whole-page value) is unaffected either way -
        `.answer-body` is genuinely present-but-empty on a real blank parse,
        so it already resolves to `None` with no gating needed. `matching`'s
        HTML-scrape fallback (`.pl-matching-answer`) and `order_blocks`'
        (`.pl-order-blocks-answer-container`) are *also* gated here, as
        defense-in-depth against a caller mistakenly passing key HTML with
        `is_answer_key` left at its default - not because real blank HTML
        would ever contain that content (it wouldn't, by the same "present
        but empty" logic `answer_panel_text` relies on).

    Returns
    -------
    ParsedQuestion

    Raises
    ------
    UnsupportedElementError
        If the question's generic containers (`.question-block`/
        `.question-body`) can't be found. A page with zero recognized input
        widgets is *not* an error (Phase 5 subphase 3) - `ParsedQuestion.widgets`
        is simply empty, e.g. for a question whose only content is a diagram
        (an SVG-only or captured-canvas-only element).
    """
    soup = BeautifulSoup(html, "html.parser")

    question_block = soup.find(class_="question-block")
    if question_block is None:
        raise UnsupportedElementError("No .question-block found in page HTML.")
    title_tag = question_block.find("h1")
    title = title_tag.get_text(strip=True) if title_tag else ""

    question_body = question_block.find(class_="question-body")
    if question_body is None:
        raise UnsupportedElementError("No .question-body found in page HTML.")
    _strip_help_text(question_body)

    answer_body = soup.find(class_="answer-body")
    answer_panel_text = _extract_answer_panel_text(answer_body)
    # Gated on is_answer_key: the "Variant" JSON panel this feeds is present
    # for staff-role viewers regardless of showCorrectAnswer (see
    # _extract_true_answer's docstring) - resolving it unconditionally would
    # leak real correct-answer data into a blank-copy parse. See this
    # function's own is_answer_key docstring for the full story.
    true_answer = _extract_true_answer(soup) if is_answer_key else None
    points = _extract_points(soup)
    points_numeric = _parse_points_numeric(points)
    qid = _extract_qid(soup)

    class_prefixes = additional_fill_in_class_prefixes or {}
    groups = _find_widget_groups(question_body, additional_fill_in_tags, class_prefixes)
    # See is_answer_key's docstring: widget-level correct-answer resolution
    # (matching/order_blocks' answer_body-based scrape) is gated the same way
    # true_answer already is - defense-in-depth, not because real blank HTML
    # would ever populate .answer-body with this content itself.
    widget_answer_body = answer_body if is_answer_key else None
    widgets = [_build_widget(group, true_answer, widget_answer_body) for group in groups]
    if is_answer_key:
        widgets = [
            replace(w, suppress_in_key=True) if w.kind == "rich_text_editor" else w for w in widgets
        ]
    prompt_segments = _extract_prompt_segments(question_body, groups, additional_fill_in_tags, class_prefixes)

    return ParsedQuestion(
        title=title,
        prompt_segments=prompt_segments,
        widgets=widgets,
        answer_panel_text=answer_panel_text,
        points=points,
        points_numeric=points_numeric,
        qid=qid,
    )


_STRIP_CLASSES = {"text-muted", "visually-hidden"}


def _strip_help_text(question_body: Tag) -> None:
    """Remove PL's own auxiliary/non-visible text from `question_body`, in place.

    Two confirmed sources of leaked-through text, neither nested inside any one
    widget's own container (so not already removed by the widget-container
    stripping `_extract_prompt_segments` does), both siblings of the option
    `.form-check` divs rather than part of the authored prompt:

    - `pl-checkbox.py` (not the mustache template — this is Python-generated
      markup) injects a `<small class="form-text text-muted">Select ...</small>`
      describing selection constraints (e.g. "Select at least 2 options").
      Matched on the `text-muted` class specifically, not the bare `form-text`
      class alone — `pl-string-input` uses plain `form-text` (no `text-muted`)
      for its `suffix` div, which is real question content (e.g. a unit like
      "g/mol"), already extracted separately via `_extract_group_label_suffix`,
      and must not be stripped here.
    - `pl-checkbox.mustache` (and `pl-image-capture`, not in this tool's
      supported-element scope) wraps a screen-reader-only `<legend
      class="visually-hidden">` (a general Bootstrap "hidden but
      screen-reader-accessible" utility class) around descriptive text (e.g.
      "Checkbox options") that's never visually shown in a browser, so it
      shouldn't appear in a printed/Word rendering either.

    No other supported element (`pl-multiple-choice`, `pl-string-input`,
    `pl-integer-input`) generates either of these today, but stripping on the
    class alone (not element-specific selectors) means this generalizes safely
    if a future element uses the same conventions.
    """
    for tag in question_body.find_all(class_=lambda c: c in _STRIP_CLASSES):
        tag.decompose()


@dataclass(frozen=True)
class _WidgetMarker:
    """Internal: a sentinel node marking a widget's position during a content walk.

    Never appears in a `ContentNode` sequence handed to a caller — `_extract_prompt_segments`
    always splits these out before returning.
    """

    index: int


_BOLD_TAGS = {"strong", "b"}
_ITALIC_TAGS = {"em", "i"}
_UNDERLINE_TAGS = {"u"}
_BLOCK_TAGS = {"p"}
_SKIP_TAGS = {"script", "style"}

#: Common CSS named colors (hex, no leading "#") - not the full ~150-keyword
#: CSS spec list, just the set plausible in course-authored `style="color:
#: ..."` prose (e.g. `templates/sigfigs-note.mustache`'s red/blue
#: significant-figures note - confirmed real usage motivating this feature).
#: An unrecognized name (not in this table, and not `#hex`/`rgb(...)`
#: either) is simply not treated as a color - best-effort, matching this
#: module's existing bold/italic detection philosophy.
_CSS_COLOR_NAMES = {
    "black": "000000", "white": "FFFFFF", "red": "FF0000", "green": "008000",
    "blue": "0000FF", "yellow": "FFFF00", "orange": "FFA500", "purple": "800080",
    "gray": "808080", "grey": "808080", "silver": "C0C0C0", "maroon": "800000",
    "olive": "808000", "lime": "00FF00", "aqua": "00FFFF", "cyan": "00FFFF",
    "teal": "008080", "navy": "000080", "fuchsia": "FF00FF", "magenta": "FF00FF",
    "pink": "FFC0CB", "brown": "A52A2A", "gold": "FFD700", "indigo": "4B0082",
    "violet": "EE82EE", "coral": "FF7F50", "salmon": "FA8072", "khaki": "F0E68C",
    "crimson": "DC143C", "turquoise": "40E0D0", "tan": "D2B48C", "beige": "F5F5DC",
    "chocolate": "D2691E", "darkred": "8B0000", "darkblue": "00008B",
    "darkgreen": "006400", "darkorange": "FF8C00", "lightblue": "ADD8E6",
    "lightgreen": "90EE90", "lightgray": "D3D3D3", "lightgrey": "D3D3D3",
    "darkgray": "A9A9A9", "darkgrey": "A9A9A9",
}

_HEX6_RE = re.compile(r"^#([0-9a-f]{6})$")
_HEX3_RE = re.compile(r"^#([0-9a-f])([0-9a-f])([0-9a-f])$")
_RGB_FN_RE = re.compile(r"^rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)$")


def _parse_css_color(value: str) -> str | None:
    """Resolve one CSS color value to 6-digit uppercase hex (no `#`), or `None`.

    Parameters
    ----------
    value : str
        A CSS `color` property value as it appeared in the source HTML's
        `style` attribute (e.g. `"red"`, `"#F00"`, `"rgb(255, 0, 0)"`).

    Returns
    -------
    str or None
        `None` if `value` doesn't match any recognized named color, hex, or
        `rgb()` form - e.g. `rgba()`/`hsl()`/CSS variables, none of which are
        supported.
    """
    value = value.strip().lower()
    m = _HEX6_RE.match(value)
    if m:
        return m.group(1).upper()
    m = _HEX3_RE.match(value)
    if m:
        return "".join(c * 2 for c in m.groups()).upper()
    m = _RGB_FN_RE.match(value)
    if m:
        return "".join(f"{int(c):02X}" for c in m.groups())
    return _CSS_COLOR_NAMES.get(value)


def _parse_style_attr(style: str | None) -> tuple[str | None, bool]:
    """Extract `color`/bold-equivalent `font-weight` from an inline `style` attribute.

    Parameters
    ----------
    style : str or None
        A tag's raw `style` attribute value, e.g.
        `"color:red; font-weight:bold;"`.

    Returns
    -------
    tuple[str or None, bool]
        `(color_hex, is_bold)` - `color_hex` per `_parse_css_color` (`None`
        if absent/unrecognized); `is_bold` is `True` for `font-weight: bold`/
        `bolder`, or a numeric weight >= 600 (matches CSS's own "semi-bold
        and up" convention), else `False`.
    """
    if not style:
        return None, False
    color: str | None = None
    bold = False
    for declaration in style.split(";"):
        if ":" not in declaration:
            continue
        prop, _, raw_value = declaration.partition(":")
        prop = prop.strip().lower()
        raw_value = raw_value.strip()
        if not raw_value:
            continue
        if prop == "color":
            parsed = _parse_css_color(raw_value)
            if parsed is not None:
                color = parsed
        elif prop == "font-weight":
            if raw_value.lower() in ("bold", "bolder"):
                bold = True
            else:
                try:
                    bold = int(raw_value) >= 600
                except ValueError:
                    pass
    return color, bold


def _parse_width_px(width_attr: str | None) -> int | None:
    if not width_attr:
        return None
    try:
        return int(str(width_attr).strip())
    except ValueError:
        return None


#: Matches LaTeX math delimiters in priority order: an escaped `\$` (literal
#: dollar sign, not a delimiter - PL's own markdown preprocessing preserves
#: these specifically so MathJax can still find real delimiters afterward,
#: see `escape_math_delim` in PL's `markdown.ts`), then `$$...$$`/`\[...\]`
#: (display mode), then `\(...\)`/`$...$` (inline). `$$` is listed before the
#: single-`$` alternative so it is never mistaken for two adjacent empty
#: `$...$` spans - `re` tries alternatives left-to-right at a given start
#: position and uses the first that matches, not the longest overall match.
_MATH_SPLIT_RE = re.compile(
    r"\\\$"
    r"|\$\$(?P<disp_dd>.*?)\$\$"
    r"|\\\[(?P<disp_br>.*?)\\\]"
    r"|\\\((?P<inl_br>.*?)\\\)"
    r"|\$(?P<inl_d>[^$]*?)\$",
    re.DOTALL,
)


def _split_math_delimiters(
    text: str, bold: bool, italic: bool, underline: bool, color: str | None = None
) -> list[ContentNode]:
    """Split one text node's raw string into `TextRun`/`MathRef` nodes at LaTeX math delimiters.

    Parameters
    ----------
    text : str
        Raw text content of one HTML text node (a `NavigableString`'s
        `str()`), not yet whitespace-collapsed - that normalization still
        happens later, in `_normalize_nodes`.
    bold, italic, underline, color
        Character formatting inherited from this text node's tag ancestry,
        applied to the `TextRun` pieces only - a `MathRef` carries no
        character-formatting flags of its own, since its rendered appearance
        is controlled entirely by its LaTeX source instead (including any
        `\\textcolor{...}` inside it).

    Returns
    -------
    list[ContentNode]
        `TextRun`/`MathRef` nodes in source order (plain text on both sides
        of each math span, possibly empty at either end). A `MathRef`'s
        `latex` is the delimiter-stripped source, otherwise unmodified - not
        un-escaped or whitespace-trimmed, since interpreting it is the LaTeX
        renderer's job, not this function's.

    Notes
    -----
    Assumes `text` is well-formed enough for delimiters to actually pair up.
    An unbalanced/stray `$` (no matching closer anywhere in this text node)
    is not treated as a delimiter at all and passes through as literal text -
    `_MATH_SPLIT_RE` simply fails to match it, rather than this function
    validating balance up front.
    """
    nodes: list[ContentNode] = []
    pos = 0
    for m in _MATH_SPLIT_RE.finditer(text):
        if m.start() > pos:
            nodes.append(TextRun(text[pos : m.start()], bold, italic, underline, color))
        if m.group(0) == "\\$":
            nodes.append(TextRun("$", bold, italic, underline, color))
        elif m.group("disp_dd") is not None:
            nodes.append(MathRef(m.group("disp_dd"), display_mode=True))
        elif m.group("disp_br") is not None:
            nodes.append(MathRef(m.group("disp_br"), display_mode=True))
        elif m.group("inl_br") is not None:
            nodes.append(MathRef(m.group("inl_br"), display_mode=False))
        elif m.group("inl_d") is not None:
            nodes.append(MathRef(m.group("inl_d"), display_mode=False))
        pos = m.end()
    if pos < len(text):
        nodes.append(TextRun(text[pos:], bold, italic, underline, color))
    return nodes


def _walk_content(
    root: Tag, marker_by_id: dict[int, int] | None = None
) -> list[ContentNode | _WidgetMarker]:
    """Recursively flatten `root`'s children into an ordered node sequence.

    Parameters
    ----------
    root : Tag
        The element to walk (its own tag is not itself considered - only its
        descendants).
    marker_by_id : dict[int, int] or None
        Maps `id(tag)` (identity, not equality) to a widget index. Any
        descendant tag whose identity is a key here is replaced with a
        `_WidgetMarker(index)` leaf instead of being walked into - used by
        `_extract_prompt_segments` to record each widget's source position
        without needing a second, string-marker-based pass.

    Returns
    -------
    list[ContentNode | _WidgetMarker]
        Unnormalized - callers must run this through `_normalize_nodes`
        (and, for prompt splitting, split on `_WidgetMarker`) before use.
    """
    nodes: list[ContentNode | _WidgetMarker] = []
    _walk_into(root, nodes, marker_by_id or {}, bold=False, italic=False, underline=False, color=None)
    return nodes


def _walk_into(
    node, out: list[ContentNode | _WidgetMarker], marker_by_id: dict[int, int],
    bold: bool, italic: bool, underline: bool, color: str | None = None,
) -> None:
    if isinstance(node, Comment):
        return
    if isinstance(node, NavigableString):
        text = str(node)
        if text:
            out.extend(_split_math_delimiters(text, bold, italic, underline, color))
        return
    if not isinstance(node, Tag):
        return

    marker_index = marker_by_id.get(id(node))
    if marker_index is not None:
        out.append(_WidgetMarker(marker_index))
        return

    name = node.name
    if name in _SKIP_TAGS:
        return
    if name == "img":
        out.append(
            ImageRef(
                local_path=node.get("src", ""),
                alt=node.get("alt", ""),
                width_px=_parse_width_px(node.get("width")),
            )
        )
        return
    if name == "svg":
        # Captured whole (str(node) serializes this Tag's own outer HTML,
        # descendants included) and not descended into - an inline <svg>'s
        # shape markup (<path>/<circle>/<text>/...) has no ContentNode
        # equivalent, so descending into it as a generic tag would either
        # contribute nothing (shape elements) or silently leak stray
        # <text> content through as loose TextRuns. See
        # pl2docx.svg_render's module docstring for why this needs a real
        # headless browser to size/rasterize correctly, rather than being
        # handled here.
        out.append(SvgRef(svg_markup=str(node), alt=node.get("aria-label") or "[diagram]"))
        return
    if name == "br":
        out.append(ParagraphBreak())
        return
    if name == "table":
        # Emitted as one whole TableRef node rather than descended into - see
        # TableRef's own docstring for the header-row detection rules. A
        # ParagraphBreak on both sides keeps the table from running into
        # surrounding prose on the same line (tables aren't in _BLOCK_TAGS,
        # which only covers <p>).
        if out:
            out.append(ParagraphBreak())
        out.append(_build_table_ref(node, marker_by_id))
        out.append(ParagraphBreak())
        return

    style_color, style_bold = _parse_style_attr(node.get("style"))
    child_bold = bold or name in _BOLD_TAGS or style_bold
    child_italic = italic or name in _ITALIC_TAGS
    child_underline = underline or name in _UNDERLINE_TAGS
    child_color = style_color if style_color is not None else color

    if name in ("ol", "ul"):
        # Handled here (not via the generic is_block/_BLOCK_TAGS path below) so
        # each direct <li> child can be told its 1-based position and whether
        # the list is ordered - needed for real "1./2./3." numbering instead
        # of a flat bullet for every list, and for <ol> vs <ul> at all.
        list_id = id(node)
        index = 0
        for child in node.children:
            if isinstance(child, Tag) and child.name == "li":
                index += 1
                _walk_li(
                    child, out, marker_by_id, child_bold, child_italic, child_underline,
                    name == "ol", index, list_id, child_color,
                )
            else:
                _walk_into(child, out, marker_by_id, child_bold, child_italic, child_underline, child_color)
        return
    if name == "li":
        # A stray <li> outside any <ol>/<ul> (malformed HTML) - no real list
        # to report an ordered flag/position/list_id from, so this always
        # renders as a plain bullet. Real PL content always wraps <li> in
        # ol/ul, which goes through the branch above instead.
        _walk_li(
            node, out, marker_by_id, child_bold, child_italic, child_underline, False, None, None, child_color
        )
        return

    is_block = name in _BLOCK_TAGS
    if is_block and out:
        out.append(ParagraphBreak(hard=True))

    for child in node.children:
        _walk_into(child, out, marker_by_id, child_bold, child_italic, child_underline, child_color)

    if is_block:
        out.append(ParagraphBreak(hard=True))


def _walk_li(
    node: Tag, out: list[ContentNode | _WidgetMarker], marker_by_id: dict[int, int],
    bold: bool, italic: bool, underline: bool, ordered: bool, index: int | None,
    list_id: int | None, color: str | None = None,
) -> None:
    marker_index = marker_by_id.get(id(node))
    if marker_index is not None:
        out.append(_WidgetMarker(marker_index))
        return
    if out:
        out.append(ParagraphBreak())
    out.append(ListItemStart(ordered=ordered, index=index, list_id=list_id))
    for child in node.children:
        _walk_into(child, out, marker_by_id, bold, italic, underline, color)
    out.append(ListItemEnd())


def _build_table_ref(table: Tag, marker_by_id: dict[int, int]) -> TableRef:
    """Extract `table`'s rows into a `TableRef`. See `TableRef`'s own docstring for the rules.

    Parameters
    ----------
    table : Tag
        The `<table>` element itself.
    marker_by_id : dict[int, int]
        Passed through to each cell's own `_walk_content` call, same as
        `_walk_into`'s own parameter - see `TableRef`'s docstring for why a
        widget nested inside a cell doesn't actually splice out correctly
        even so.

    Returns
    -------
    TableRef
    """

    def cell_nodes(cell: Tag) -> list[ContentNode]:
        return _normalize_nodes(_walk_content(cell, marker_by_id))  # type: ignore[return-value]

    def row_cells(row: Tag) -> list[list[ContentNode]]:
        return [cell_nodes(cell) for cell in row.find_all(["td", "th"], recursive=False)]

    header_rows: list[list[list[ContentNode]]] = []
    thead = table.find("thead", recursive=False)
    if thead is not None:
        header_trs = thead.find_all("tr", recursive=False)
        if header_trs:
            header_rows = [row_cells(tr) for tr in header_trs]
        else:
            # Real, confirmed case: some of this course's authored tables omit
            # the <tr> wrapper inside <thead>, leaving <th> as direct children.
            header_cells = thead.find_all(["td", "th"], recursive=False)
            if header_cells:
                header_rows = [[cell_nodes(cell) for cell in header_cells]]

    tbody = table.find("tbody", recursive=False)
    body_source = tbody if tbody is not None else table
    body_rows = [row_cells(tr) for tr in body_source.find_all("tr", recursive=False)]

    if not header_rows and body_rows:
        # No <thead> at all - promote the first row to the header only if
        # every one of its cells is a <th> (never for a <td>-only/mixed row).
        first_row_tag = body_source.find("tr", recursive=False)
        if first_row_tag is not None and not first_row_tag.find_all("td", recursive=False):
            header_rows = [body_rows[0]]
            body_rows = body_rows[1:]

    return TableRef(header_rows=header_rows, body_rows=body_rows)


def _normalize_nodes(nodes: list[ContentNode | _WidgetMarker]) -> list[ContentNode | _WidgetMarker]:
    """Collapse whitespace and redundant structure in a raw `_walk_content` output.

    - Adjacent `TextRun`s with identical formatting are merged by direct
      concatenation - each already carries its own real whitespace from the
      source HTML, so no separator is synthesized between them.
    - Interior whitespace in each `TextRun`'s text is collapsed to a single
      space each; genuinely empty (not just whitespace-only) runs are dropped.
      Whitespace-only runs are kept as single-space separators at this stage -
      see the trimming pass below for why.
    - Consecutive `ParagraphBreak`s collapse to one - including when only a
      whitespace-only text node (collapsed to a single space above) sits
      between them, e.g. the source-formatting indentation between "</p>"
      and the next "<p>". Without this, sibling block tags produced *two*
      paragraph breaks instead of one (confirmed by the user against real
      multi-paragraph content).
    - Leading/trailing `ParagraphBreak`/whitespace-only-`TextRun` nodes are
      stripped from both ends, and the first/last remaining `TextRun`'s own
      leading/trailing whitespace is stripped - this is what lets
      `_WidgetMarker`-adjacent segments end up with clean boundaries without
      needing a separate `.strip()` step per segment. `ListItemStart`/
      `ListItemEnd` are never stripped, at either end, regardless - see
      their docstrings for why (both carry state-transition signals the
      renderer needs, even when they land exactly at a segment boundary).
    """
    merged: list[ContentNode | _WidgetMarker] = []
    for node in nodes:
        if isinstance(node, TextRun):
            if (
                merged
                and isinstance(merged[-1], TextRun)
                and (merged[-1].bold, merged[-1].italic, merged[-1].underline, merged[-1].color)
                == (node.bold, node.italic, node.underline, node.color)
            ):
                prev = merged[-1]
                merged[-1] = TextRun(prev.text + node.text, prev.bold, prev.italic, prev.underline, prev.color)
            else:
                merged.append(node)
        elif isinstance(node, ParagraphBreak):
            if merged and isinstance(merged[-1], ParagraphBreak):
                # Collapsed to one node, but `hard` (a real <p> boundary, not
                # just incidental formatting) must survive the merge even if
                # only one of the two contributing breaks was hard - see
                # ParagraphBreak's own docstring for why the renderer needs
                # this distinction inside a list item.
                if node.hard and not merged[-1].hard:
                    merged[-1] = ParagraphBreak(hard=True)
                continue
            merged.append(node)
        else:
            merged.append(node)

    cleaned: list[ContentNode | _WidgetMarker] = []
    for node in merged:
        if isinstance(node, TextRun):
            text = re.sub(r"\s+", " ", node.text)
            if text == "":
                continue
            cleaned.append(TextRun(text, node.bold, node.italic, node.underline, node.color))
        else:
            cleaned.append(node)

    # Drop a single-space TextRun (the collapsed form of a whitespace-only
    # text node - e.g. the indentation between "</p>" and the next "<p>")
    # when it sits immediately next to a ParagraphBreak on either side, then
    # re-collapse any ParagraphBreaks that are now adjacent as a result.
    # Without this, "<p>A</p>\n  <p>B</p>" produced ParagraphBreak, " ",
    # ParagraphBreak between "A" and "B" - the lone space run defeated the
    # consecutive-ParagraphBreak merge above (it isn't itself a
    # ParagraphBreak), so both breaks survived and rendered as *two*
    # paragraph breaks (an extra blank paragraph) instead of one - confirmed
    # by the user against real multi-paragraph answer-key content. A space
    # touching a paragraph boundary is never meaningful either way: nothing
    # ever renders on the same visual line across a real paragraph break.
    de_spaced: list[ContentNode | _WidgetMarker] = []
    for i, node in enumerate(cleaned):
        if isinstance(node, TextRun) and node.text == " ":
            prev_is_break = de_spaced and isinstance(de_spaced[-1], ParagraphBreak)
            next_is_break = i + 1 < len(cleaned) and isinstance(cleaned[i + 1], ParagraphBreak)
            if prev_is_break or next_is_break:
                continue
        de_spaced.append(node)
    cleaned = []
    for node in de_spaced:
        if isinstance(node, ParagraphBreak) and cleaned and isinstance(cleaned[-1], ParagraphBreak):
            if node.hard and not cleaned[-1].hard:
                cleaned[-1] = ParagraphBreak(hard=True)
            continue
        cleaned.append(node)

    def _is_boundary_junk(n) -> bool:
        # ListItemStart/ListItemEnd are deliberately excluded - see their
        # docstrings. Trimming only removes incidental formatting (blank
        # paragraph breaks/whitespace-only text), never a real list-item
        # start/end signal, even when one ends up at a segment's edge (e.g.
        # immediately before/after a widget that's itself the sole content
        # of a <li>).
        return isinstance(n, ParagraphBreak) or (isinstance(n, TextRun) and n.text.strip() == "")

    # Same trim, applied *inside* each <li>'s own boundaries too - not just
    # the whole segment's outer edges. A <li> whose content starts (or ends)
    # with a real block tag (<p>) contributes its own leading (or trailing)
    # ParagraphBreak (see _walk_into's is_block handling) - correct at the
    # top level (a real new Word paragraph), but wrong immediately inside a
    # <li>: _render_nodes_into_subdoc renders a ParagraphBreak while inside a
    # list item as a soft line break within the *same* paragraph, so an
    # untrimmed one there renders as a visible blank line before the item's
    # content even starts (or after it ends, before the next item's own
    # marker) - confirmed real bug against this course's
    # mystery-liquid-density-uncertainty answer panel, where every <li> wraps
    # its content in <p>. Done here (post-whitespace-collapse), not inside
    # `_walk_li` itself, because at that point a stray whitespace-only text
    # node between `<li>` and `<p>` (real, confirmed source indentation)
    # still sits between ListItemStart and the block's own ParagraphBreak,
    # defeating a naive "is the very next node a ParagraphBreak" check.
    i = 0
    while i < len(cleaned) - 1:
        if isinstance(cleaned[i], ListItemStart) and _is_boundary_junk(cleaned[i + 1]):
            del cleaned[i + 1]
            continue
        i += 1
    i = 1
    while i < len(cleaned):
        if isinstance(cleaned[i], ListItemEnd) and _is_boundary_junk(cleaned[i - 1]):
            del cleaned[i - 1]
            i -= 1
            continue
        i += 1

    # Same reasoning as the segment-edge lstrip/rstrip below - the block-tag
    # trim above only removes whole junk *nodes*; a real TextRun immediately
    # inside a <li> (e.g. "<p>\n    Density is...") still carries the
    # leading/trailing whitespace from the source's own indentation.
    for i in range(len(cleaned) - 1):
        if isinstance(cleaned[i], ListItemStart) and isinstance(cleaned[i + 1], TextRun):
            t = cleaned[i + 1]
            cleaned[i + 1] = TextRun(t.text.lstrip(), t.bold, t.italic, t.underline, t.color)
    for i in range(1, len(cleaned)):
        if isinstance(cleaned[i], ListItemEnd) and isinstance(cleaned[i - 1], TextRun):
            t = cleaned[i - 1]
            cleaned[i - 1] = TextRun(t.text.rstrip(), t.bold, t.italic, t.underline, t.color)

    while cleaned and _is_boundary_junk(cleaned[0]):
        cleaned.pop(0)
    while cleaned and _is_boundary_junk(cleaned[-1]):
        cleaned.pop()

    if cleaned and isinstance(cleaned[0], TextRun):
        first = cleaned[0]
        cleaned[0] = TextRun(first.text.lstrip(), first.bold, first.italic, first.underline, first.color)
    if cleaned and isinstance(cleaned[-1], TextRun):
        last = cleaned[-1]
        cleaned[-1] = TextRun(last.text.rstrip(), last.bold, last.italic, last.underline, last.color)

    return cleaned


def _rich_from_html_fragment(fragment: str) -> list[ContentNode]:
    """Parse a standalone HTML string (e.g. an attribute value) into node content."""
    frag_soup = BeautifulSoup(fragment, "html.parser")
    return _normalize_nodes(_walk_content(frag_soup))  # type: ignore[return-value]


@dataclass
class _WidgetGroup:
    """Internal: one widget's raw containers, before building its `Widget`."""

    kind: str
    name: str
    containers: list[Tag]  # DOM-order containers to strip/replace for prompt-splitting
    is_dropdown: bool = False


def _find_widget_groups(
    question_body: Tag,
    additional_fill_in_tags: Iterable[str] = (),
    additional_fill_in_class_prefixes: dict[str, str] | None = None,
) -> list[_WidgetGroup]:
    """Group this page's recognized inputs into one `_WidgetGroup` per (kind, name).

    Groups are returned in true DOM order of first appearance — determined via
    `question_body.descendants`' iteration order, since collecting per-kind with
    `find_all` (as done here for simplicity) interleaves kinds incorrectly on a
    compound page.
    """
    class_prefixes = additional_fill_in_class_prefixes or {}
    groups: dict[tuple[str, str], _WidgetGroup] = {}

    def add(kind: str, name: str, container: Tag, is_dropdown: bool = False) -> None:
        key = (kind, name)
        if key not in groups:
            groups[key] = _WidgetGroup(kind=kind, name=name, containers=[], is_dropdown=is_dropdown)
        groups[key].containers.append(container)

    for checkbox in question_body.find_all("input", attrs={"type": "checkbox"}):
        container = checkbox.find_parent("div", class_="form-check") or checkbox
        add("checkbox", checkbox.get("name", ""), container)

    for radio in question_body.find_all("input", attrs={"type": "radio"}):
        container = radio.find_parent("div", class_="form-check") or radio
        add("multiple_choice", radio.get("name", ""), container)

    for select in question_body.find_all("select"):
        if select.find_parent("div", class_=re.compile(r"^pl-matching-container\b")) is not None:
            continue  # a pl-matching statement's own <select> - handled by matching detection below
        container = (
            select.find_parent(class_=re.compile(r"pl-multiple-choice-dropdown")) or select
        )
        add("multiple_choice", select.get("name", ""), container, is_dropdown=True)

    for kind, tag in _BUILTIN_FILL_IN_TAGS.items():
        _add_fill_in_groups(question_body, tag, kind, add)
    for tag in additional_fill_in_tags:
        _add_fill_in_groups(question_body, class_prefixes.get(tag, tag), tag, add)

    for container in question_body.find_all("div", class_="pl-rich-text-editor-container"):
        hidden_input = container.find("input", attrs={"type": "hidden"})
        name = hidden_input.get("name", "") if hidden_input is not None else ""
        add("rich_text_editor", name, container)

    for container in question_body.find_all("div", class_=re.compile(r"^pl-matching-container\b")):
        select = container.find("select")
        name = select.get("name", "") if select is not None else ""
        add("matching", name, container)

    for pool in question_body.find_all("ul", id=re.compile(r"^order-blocks-options-")):
        container = pool
        ancestor = pool.parent
        while ancestor is not None and isinstance(ancestor, Tag):
            if ancestor.find("ul", id=re.compile(r"^order-blocks-dropzone-")) is not None:
                container = ancestor
                break
            ancestor = ancestor.parent
        name = pool.get("id", "")
        add("order_blocks", name, container)

    order_index = {id(tag): i for i, tag in enumerate(question_body.descendants) if isinstance(tag, Tag)}
    ordered_keys = sorted(
        groups.keys(),
        key=lambda key: order_index.get(id(groups[key].containers[0]), len(order_index)),
    )
    return [groups[key] for key in ordered_keys]


def _add_fill_in_groups(question_body: Tag, class_base: str, kind: str, add) -> None:
    """Detect one fill-in-type element's widgets by its class-pattern base string.

    Confirmed shared convention across every built-in fill-in element (and
    `pl-scinum-input`, a course-specific `additional-elements` element following
    the same pattern): the input's own class is `{tag}-input` or `{tag}-multiline`
    (the element's registered PL tag name, verbatim, plus a fixed suffix) — so this
    needs no per-element knowledge beyond the tag name string itself, which is
    exactly what lets `additional_fill_in_tags` support arbitrary configured
    elements without any course-specific string appearing in this module.
    `class_base` is normally just `kind`/the tag itself, but a caller may pass a
    different base string (see `additional_fill_in_class_prefixes` on
    `parse_instance_question_html`) for an element that doesn't follow the
    convention verbatim — confirmed real case: `pl-big-o-input`'s `<input>` class
    is `big-o-input-input`, missing the usual `pl-` prefix, matched by passing
    `class_base="big-o-input"` instead of the tag `"pl-big-o-input"`.

    Restricting the search to `<input>`/`<textarea>` tag names specifically (not
    just any tag carrying a matching class) is deliberate, not incidental: it's
    what correctly excludes `pl-symbolic-input`'s `formula_editor`-mode
    `<math-field>` custom element, which carries the same
    `pl-symbolic-input-input` class but isn't a real, statically-populated input —
    see this module's docstring.
    """
    pattern = re.compile(rf"^{re.escape(class_base)}-(input|multiline)$")
    for input_tag in question_body.find_all(["input", "textarea"], class_=pattern):
        container = input_tag.find_parent(class_=re.compile(r"^input-group\b")) or input_tag
        add(kind, input_tag.get("name", ""), container)


def _build_widget(group: _WidgetGroup, true_answer: dict | None, answer_body: Tag | None) -> Widget:
    if group.kind in ("multiple_choice", "checkbox"):
        options, option_keys = _extract_group_options(group)
        return Widget(
            kind=group.kind,
            name=group.name,
            options=options,
            option_keys=option_keys,
            correct_option_indices=_extract_correct_option_indices(group.name, option_keys, true_answer),
            is_inline=_extract_group_is_inline(group),
            is_dropdown=group.is_dropdown,
        )
    if group.kind == "rich_text_editor":
        return Widget(kind=group.kind, name=group.name)
    if group.kind == "matching":
        return _build_matching_widget(group, true_answer, answer_body)
    if group.kind == "order_blocks":
        return _build_order_blocks_widget(group, answer_body)
    label, suffix, width_chars = _extract_group_label_suffix_width(group)
    return Widget(kind=group.kind, name=group.name, label=label, suffix=suffix, width_chars=width_chars)


_COUNTER_TYPE_DEFAULT = "lower-alpha"


def format_counter(index: int, counter_type: str | None) -> str:
    """Format a 0-based index as PL's own `counter-type` display would, e.g. `"a"`/`"A"`/`"1"`.

    Parameters
    ----------
    index : int
        0-based position among the counted items (a `pl-matching` option, or
        a `pl-order-blocks` pool block).
    counter_type : str or None
        `"lower-alpha"`/`"upper-alpha"`/`"decimal"`/`"full-text"` (PL's own
        `pl-matching` values — reused verbatim for `pl-order-blocks`' pool
        lettering too, which always uses `"upper-alpha"`, PL's element has no
        analogous attribute of its own). `None` treated as
        `_COUNTER_TYPE_DEFAULT` (`"lower-alpha"`, PL's own default).

    Returns
    -------
    str
        The bare counter text, e.g. `"a"`, `"A"`, `"1"` — never includes a
        trailing `"."` or other punctuation; callers append that themselves.
        `""` for `"full-text"` (no counter is shown at all, matching PL's own
        `no_counters` behavior).

    Raises
    ------
    IndexError
        If `index` is outside the 26-letter range for `lower-alpha`/`upper-alpha`
        (no fixture/target question in this project has that many items).
    """
    kind = counter_type or _COUNTER_TYPE_DEFAULT
    if kind == "upper-alpha":
        return string.ascii_uppercase[index]
    if kind == "decimal":
        return str(index + 1)
    if kind == "full-text":
        return ""
    return string.ascii_lowercase[index]


def _build_matching_widget(group: _WidgetGroup, true_answer: dict | None, answer_body: Tag | None) -> Widget:
    """Build a `matching`-kind `Widget` from its `.pl-matching-container`.

    Statement/option content is always extracted from the widget's own
    container (present, disabled, in both blank and key HTML alike).
    `correct_labels` is resolved two ways, JSON-first per the user's
    preference: primarily via PL's own `data["correct_answers"][name]` ->
    `true_answer[name]` pathway (the same mechanism `_extract_correct_option_indices`
    already uses for `pl-multiple-choice`/`pl-checkbox`), falling back to
    scraping PL's own rendered `.pl-matching-answer` answer-panel HTML (only
    present once `showCorrectAnswer` is true) when the JSON route doesn't
    resolve — e.g. if `pl-matching` doesn't expose a `name`-keyed entry in
    that JSON the same way MC/checkbox do (unconfirmed at the time this was
    written; verify against a real fetched Variant JSON payload).
    """
    container = group.containers[0]
    # Confirmed against real fetched HTML: each statement's own <select> is named
    # "{base_name}-dropdown-{index}" (a distinct name per statement, needed since
    # PL submits each dropdown as its own form field), but the Variant JSON's
    # top-level key for the whole pl-matching element is just "{base_name}" (no
    # suffix) - group.name (the *first* statement's select name, from
    # _find_widget_groups) must have that suffix stripped before it's usable as
    # both this Widget's own display name and the true_answer lookup key, or the
    # JSON route silently finds nothing and always falls through to the HTML
    # scrape fallback below (confirmed the hard way against a real question).
    name = re.sub(r"-dropdown-\d+$", "", group.name)
    statements: list[list[ContentNode]] = []
    for statement_text in container.find_all(class_="pl-matching-statement-text"):
        statements.append(_normalize_nodes(_walk_content(statement_text)))  # type: ignore[arg-type]

    match_options: list[list[ContentNode]] = []
    for option in container.find_all("li", class_="pl-matching-option"):
        content = option.find(id=re.compile(r"-content$")) or option
        match_options.append(_normalize_nodes(_walk_content(content)))  # type: ignore[arg-type]

    counter_type = None
    options_block = container.find(class_="pl-matching-options")
    if options_block is not None:
        li = options_block.find("li")
        if li is not None:
            style = li.get("style", "")
            m = re.search(r"--pl-matching-counter-type:\s*([\w-]+)", style)
            if m:
                counter_type = m.group(1)

    correct_labels = _extract_matching_correct_labels_from_json(name, true_answer, counter_type)
    if correct_labels is None:
        correct_labels = _extract_matching_correct_labels_from_html(answer_body, len(statements))
    if correct_labels is None:
        correct_labels = [None] * len(statements)

    return Widget(
        kind="matching",
        name=name,
        statements=statements,
        match_options=match_options,
        counter_type=counter_type,
        correct_labels=correct_labels,
    )


def _extract_matching_correct_labels_from_json(
    name: str, true_answer: dict | None, counter_type: str | None
) -> list[str | None] | None:
    """Attempt to resolve `pl-matching`'s correct answers from the Variant JSON.

    Returns
    -------
    list[str or None] or None
        One formatted counter label per statement (matching `format_counter`'s
        output), or `None` (not `[]`) if `true_answer` has no usable entry for
        `name` at all — signals the caller to fall back to HTML scraping,
        distinct from "resolved, but this particular statement's match is
        unknown" (which would be a per-entry `None` inside the returned list).

    Notes
    -----
    PL's `pl-matching.py` stores `data["correct_answers"][name] = correct_matches`,
    a list of per-statement correct option indices (one entry per statement, in
    statement order) — the same `data["correct_answers"][name]` pathway
    `_extract_true_answer`'s JSON already exposes for `pl-multiple-choice`/
    `pl-checkbox`. This has not been confirmed against a real fetched Variant
    JSON payload for `pl-matching` specifically (unlike the MC/checkbox case,
    which was confirmed against real fetched HTML) — implemented defensively,
    returning `None` for any shape that doesn't parse as a plain list of
    integers, so the HTML-scraping fallback always has a chance to run.
    """
    if not true_answer or name not in true_answer:
        return None
    entry = true_answer[name]
    if not isinstance(entry, list) or not all(isinstance(x, int) for x in entry):
        return None
    return [format_counter(idx, counter_type) if idx is not None else None for idx in entry]


def _extract_matching_correct_labels_from_html(
    answer_body: Tag | None, statement_count: int
) -> list[str | None] | None:
    """Fallback: scrape correct matches directly from PL's rendered `.pl-matching-answer` HTML.

    Parameters
    ----------
    answer_body : Tag or None
        The page's `.answer-body` container (present but empty in blank HTML,
        populated once `showCorrectAnswer` is true).
    statement_count : int
        This widget's own statement count, used only to decide whether a
        `.pl-matching-answer` scrape found a plausible one-per-statement match
        (a sanity check, not a hard requirement).

    Returns
    -------
    list[str or None] or None
        `None` if no `.pl-matching-answer` blocks were found at all (blank
        HTML, or the JSON route already resolved this and this fallback
        wasn't needed). Page-wide, not per-widget-name-scoped — see this
        module's `Widget.correct_labels` docstring for the known limit this
        creates for multiple same-page `pl-matching` widgets.
    """
    if answer_body is None:
        return None
    answers = answer_body.find_all(class_="pl-matching-answer")
    if not answers:
        return None
    labels: list[str | None] = []
    for answer in answers:
        strong = answer.find("strong")
        if strong is None:
            labels.append(None)
            continue
        # PL's own rendered <strong> text already includes the trailing "."
        # (confirmed against real fetched HTML: "<strong>b.</strong>"), unlike
        # the JSON route's bare format_counter() output ("b") - strip it here
        # so both routes return the same bare-label shape, since the renderer
        # always appends its own "." after whichever label it's given.
        labels.append(strong.get_text(strip=True).rstrip("."))
    return labels


def _build_order_blocks_widget(group: _WidgetGroup, answer_body: Tag | None) -> Widget:
    """Build an `order_blocks`-kind `Widget` from its pool `<ul>` + wrapping container.

    Pool block content (including distractors, per the user's confirmed
    choice) is always extracted from the widget's own pool `<ul>` (present in
    both blank and key HTML). `correct_order` is only resolvable from key
    HTML's `.pl-order-blocks-answer-container` (a plain, static, correctly-ordered
    `<li>` list PL renders once `showCorrectAnswer` is true) — the correct
    order is computed inside the element's own Python controller and is never
    present in the Variant JSON at all, so (unlike `pl-matching`) this
    intentionally never attempts a JSON-based route. Matched to pool blocks by
    content equality (best-effort — falls back to `None` if a match can't be
    resolved for every answer-panel block).
    """
    pool = group.containers[0].find("ul", id=re.compile(r"^order-blocks-options-"))
    if pool is None:
        pool = group.containers[0]
    blocks: list[list[ContentNode]] = []
    for li in pool.find_all("li", class_="pl-order-block", recursive=False) or pool.find_all(
        "li", class_="pl-order-block"
    ):
        content = li.find(class_="pl-order-block-content") or li
        blocks.append(_normalize_nodes(_walk_content(content)))  # type: ignore[arg-type]

    correct_order = _extract_order_blocks_correct_order(answer_body, blocks)

    return Widget(kind="order_blocks", name=group.name, blocks=blocks, correct_order=correct_order)


def _extract_order_blocks_correct_order(
    answer_body: Tag | None, blocks: list[list[ContentNode]]
) -> list[int] | None:
    if answer_body is None:
        return None
    answer_container = answer_body.find(class_="pl-order-blocks-answer-container")
    if answer_container is None:
        return None
    block_texts = [plain_text(b) for b in blocks]
    order: list[int] = []
    for li in answer_container.find_all("li", class_="pl-order-block"):
        content = li.find(class_="pl-order-block-content") or li
        text = plain_text(_normalize_nodes(_walk_content(content)))  # type: ignore[arg-type]
        try:
            order.append(block_texts.index(text))
        except ValueError:
            return None
    return order or None


def _extract_group_options(
    group: _WidgetGroup,
) -> tuple[list[list[ContentNode]], list[str | None]]:
    """Extract each option's rich content, alongside its own PL answer "key".

    Returns
    -------
    tuple[list[list[ContentNode]], list[str or None]]
        `(options, option_keys)`, parallel lists (same length, same order).
        Each key is the option's own rendered `value` attribute — confirmed
        against `pl-multiple-choice.mustache`/`pl-checkbox.mustache`:
        `value="{{key}}"` on every `<input>`/`<option>`, so this is exactly
        the same key format `_extract_true_answer`'s JSON uses, letting
        `_extract_correct_option_indices` match the two directly.
    """
    if group.is_dropdown:
        options: list[list[ContentNode]] = []
        option_keys: list[str | None] = []
        for option in group.containers[0].find_all("option"):
            value = option.get("value")
            if not value:
                continue  # the blank placeholder option
            content = option.get("data-content", "")
            content = re.sub(r"^\([A-Za-z0-9]+\)\s*", "", content).strip()
            nodes = _rich_from_html_fragment(content) if content else _normalize_nodes(
                _walk_content(option)  # type: ignore[arg-type]
            )
            options.append(nodes)
            option_keys.append(value)
        return options, option_keys

    options = []
    option_keys = []
    for container in group.containers:
        answer = container.find(class_=["pl-multiple-choice-answer", "pl-checkbox-answer"])
        if answer is not None:
            options.append(_normalize_nodes(_walk_content(answer)))  # type: ignore[arg-type]
            input_tag = _find_named_input(container, group.name)
            option_keys.append(input_tag.get("value") if input_tag is not None else None)
    return options, option_keys


def _find_named_input(container: Tag, name: str) -> Tag | None:
    """Find the `name`-matching `<input>`/`<select>` within (or as) `container`.

    `group.containers[i]` may be the input itself (the bare-element fallback
    in `_find_widget_groups`, when no `.form-check` parent exists), not just
    a wrapping container — `Tag.find()` only searches descendants, never the
    tag itself, so that case needs an explicit self-check first.
    """
    if container.name in ("input", "select") and container.get("name") == name:
        return container
    return container.find(attrs={"name": name})


def _extract_group_is_inline(group: _WidgetGroup) -> bool:
    if group.is_dropdown:
        return False
    first = group.containers[0]
    classes = first.get("class") or []
    return "form-check-inline" in classes


def _extract_group_label_suffix_width(
    group: _WidgetGroup,
) -> tuple[list[ContentNode] | None, list[ContentNode] | None, int | None]:
    container = group.containers[0]
    input_tag = container.find(attrs={"name": group.name})
    if input_tag is None:
        return None, None, None

    texts = container.find_all(class_="input-group-text")
    label: list[ContentNode] | None = None
    suffix: list[ContentNode] | None = None
    for text_tag in texts:
        nodes = _normalize_nodes(_walk_content(text_tag))  # type: ignore[arg-type]
        if not nodes:
            continue
        if _precedes(text_tag, input_tag):
            if label is None:
                label = nodes
        else:
            suffix = nodes  # last trailing one wins

    width_chars = _parse_width_chars(input_tag.get("size")) or _parse_width_chars(input_tag.get("cols"))
    return label, suffix, width_chars


def _parse_width_chars(value: str | None) -> int | None:
    if not value:
        return None
    try:
        parsed = int(str(value).strip())
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _precedes(tag: Tag, other: Tag) -> bool:
    for sibling in tag.find_all_next():
        if sibling is other:
            return True
    return False


def _extract_correct_option_indices(
    name: str, option_keys: list[str | None], true_answer: dict | None
) -> list[int]:
    """Resolve which of this widget's own options PL's "Variant" JSON marks correct.

    Replaces an earlier text-matching approach (search the page-wide
    `.answer-body` for an `<li>` whose text contained an option's text) that
    was confirmed unreliable two ways: (1) a question that wraps its default
    answer markup in `<pl-hide-in-panel answer="true">` and supplies custom
    explanatory prose instead has no matching `<li>` at all; (2) `.answer-body`
    is one combined, unmarked panel for the *whole page*, so a compound
    question with multiple widgets sharing overlapping option text (real
    content: `intro/practice/physical-or-chemical`'s 3 `pl-multiple-choice`
    dropdowns, all offering "chemical property"/"physical property") could
    match another widget's `<li>` instead of its own, mis-highlighting every
    such widget. This is scoped by `name` from the start instead, so neither
    failure mode is possible.

    Parameters
    ----------
    name : str
        This widget's own `name` attribute — the `true_answer` dict's
        top-level key for this widget specifically.
    option_keys : list[str or None]
        This widget's own `Widget.option_keys`, parallel to `options`.
    true_answer : dict or None
        The page's parsed "Variant" answer JSON (`_extract_true_answer`), or
        `None` if unavailable.

    Returns
    -------
    list[int]
        Indices into this widget's own `options`/`option_keys` whose key
        appears in `true_answer[name]`'s correct-answer key set. Empty
        whenever `true_answer` is `None`, has no entry for `name`, or that
        entry's shape is unrecognized — never raises; `answer_panel_text`
        remains the authoritative "what's the correct answer" source
        regardless (see its own docstring), this is only a bolding
        enrichment on top of it. Handles both `pl-multiple-choice`'s
        single-dict shape (`{"key": ..., ...}`) and `pl-checkbox`'s
        list-of-dicts shape (multi-answer) generically — confirmed against
        each element's own Python source (`data["correct_answers"][name]`),
        not guessed.
    """
    if not true_answer or name not in true_answer:
        return []
    entry = true_answer[name]
    entries = entry if isinstance(entry, list) else [entry]
    correct_keys = {e.get("key") for e in entries if isinstance(e, dict) and e.get("key") is not None}
    if not correct_keys:
        return []
    return [idx for idx, key in enumerate(option_keys) if key in correct_keys]


def _extract_prompt_segments(
    question_body: Tag,
    groups: list[_WidgetGroup],
    additional_fill_in_tags: Iterable[str] = (),
    additional_fill_in_class_prefixes: dict[str, str] | None = None,
) -> list[list[ContentNode]]:
    """Split the prompt's rich content at each widget's source position.

    Re-runs widget detection on a fresh copy of `question_body` (rather than
    mutating the tree used for the rest of parsing) so this can safely walk
    around each widget's first container (replacing it with a `_WidgetMarker`
    node instead of descending into it) and skip the rest. Detection is a
    pure function of the HTML (given the same `additional_fill_in_tags`/
    `additional_fill_in_class_prefixes`), so `_find_widget_groups` on the copy
    produces groups in the same order/count as `groups` — this is an internal
    invariant of this module, not something calling code needs to reason about.
    """
    if not groups:
        return [_normalize_nodes(_walk_content(question_body))]  # type: ignore[list-item]

    body_copy = BeautifulSoup(str(question_body), "html.parser")
    copy_groups = _find_widget_groups(body_copy, additional_fill_in_tags, additional_fill_in_class_prefixes)

    marker_by_id: dict[int, int] = {}
    for idx, copy_group in enumerate(copy_groups):
        marker_by_id[id(copy_group.containers[0])] = idx
        for extra in copy_group.containers[1:]:
            extra.decompose()

    nodes = _normalize_nodes(_walk_content(body_copy, marker_by_id))

    segments: list[list[ContentNode]] = [[] for _ in range(len(groups) + 1)]
    seg_idx = 0
    for node in nodes:
        if isinstance(node, _WidgetMarker):
            seg_idx = node.index + 1
            continue
        segments[seg_idx].append(node)  # type: ignore[arg-type]
    return [_normalize_nodes(seg) for seg in segments]  # type: ignore[misc]


def _extract_answer_panel_text(answer_body: Tag | None) -> list[ContentNode] | None:
    if answer_body is None:
        return None
    nodes = _normalize_nodes(_walk_content(answer_body))  # type: ignore[arg-type]
    return nodes or None


_POINTS_ROW_LABELS = {"Value:", "Available points:"}


def _extract_points(soup: BeautifulSoup) -> str | None:
    score_panel = soup.find(id="question-score-panel-content")
    if score_panel is None:
        return None
    for row in score_panel.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) >= 2 and cells[0].get_text(strip=True) in _POINTS_ROW_LABELS:
            text = cells[1].get_text(separator=" ", strip=True)
            text = re.sub(r"\s+", " ", text).strip()
            return text or None
    return None


def _parse_points_numeric(points: str | None) -> float | None:
    if points is None:
        return None
    try:
        return float(points)
    except ValueError:
        return None


def _extract_qid(soup: BeautifulSoup) -> str | None:
    """Extract the real qid from the page's "Staff information" panel.

    Confirmed real markup: a `<div>QID:</div>` immediately followed by a
    sibling `<div>` containing the qid (usually as an `<a>` link's text,
    e.g. `intro/practice/matter-classification`). This panel is present
    whenever the viewer has course-staff role, independent of the
    assessment's title-display settings.
    """
    label = soup.find(lambda tag: tag.name is not None and tag.get_text(strip=True) == "QID:")
    if label is None:
        return None
    value_tag = label.find_next_sibling()
    if value_tag is None:
        return None
    text = value_tag.get_text(strip=True)
    return text or None


def _extract_true_answer(soup: BeautifulSoup) -> dict | None:
    """Extract PL's own `variant.true_answer` JSON from the "Variant" staff-info panel.

    Confirmed against the real PrairieLearn source
    (`InstructorInfoPanel.tsx`'s `VariantInfo`): staff-role pages (Previewer+,
    same role this tool already authenticates and fetches as - see
    `pl_client.py`'s module docstring) server-render
    `<details><summary>Show/Hide answer</summary><pre><code>{JSON.stringify(
    variant.true_answer, null, 2)}</code></pre></details>` regardless of
    `showCorrectAnswer`/`<pl-hide-in-panel>` suppression - this is the
    variant's real stored correct-answer data, keyed by each named input's
    own `name` - unlike `.answer-body`'s free-form `<li>` prose, it's not
    subject to `<pl-hide-in-panel>` suppression or cross-widget ambiguity.
    Confirmed present in already-fetched real HTML
    (`output/125/blank/1838.html`) and matches each rendered
    `<input>`/`<option>`'s own `value` attribute exactly (both set to the
    same PL-internal answer "key" - see `pl-multiple-choice.mustache`/
    `pl-checkbox.mustache`'s `value="{{key}}"`).

    Returns
    -------
    dict or None
        The parsed JSON object, or `None` if the panel isn't present at all
        (a fetch under insufficient staff permissions - shouldn't happen
        given this tool's own auth model, but handled defensively), its
        content isn't valid JSON, or the parsed value isn't a non-empty
        dict (e.g. a question with no named inputs at all) - callers should
        treat any of these the same way (no per-widget answer-key data
        available), never raise.
    """
    summary = soup.find("summary", string=lambda s: s is not None and s.strip() == "Show/Hide answer")
    if summary is None:
        return None
    pre = summary.find_next_sibling("pre")
    if pre is None:
        return None
    code = pre.find("code")
    if code is None:
        return None
    try:
        data = json.loads(code.get_text())
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) and data else None


def format_points_text(points_numeric: float | None, points_raw: str | None) -> str | None:
    """Format a question's point value as display text, e.g. "1 point"/"2 points".

    Parameters
    ----------
    points_numeric : float or None
        A `ParsedQuestion.points_numeric` value.
    points_raw : str or None
        The corresponding `ParsedQuestion.points` (raw scraped text), used
        as a fallback when `points_numeric` couldn't be parsed.

    Returns
    -------
    str or None
        `None` if both inputs are `None`. If `points_numeric` parsed
        cleanly, a pluralized `"N point(s)"` string (integer values render
        without a decimal, e.g. `"2 points"` not `"2.0 points"`). Otherwise
        `points_raw` verbatim (no "points" suffix appended, since its shape
        isn't known).
    """
    if points_numeric is None:
        return points_raw
    value = int(points_numeric) if points_numeric == int(points_numeric) else points_numeric
    unit = "point" if value == 1 else "points"
    return f"{value} {unit}"


def option_letter(index: int) -> str:
    """Map a 0-based option index to its display letter, e.g. 0 -> "A".

    Parameters
    ----------
    index : int
        0-based index into a widget's `options` list.

    Returns
    -------
    str
        The letter PL itself would use for this position (`(A)`, `(B)`, …).

    Raises
    ------
    IndexError
        If `index` is outside the 26-letter range this simple scheme covers
        (no fixture/target question in this project has that many options).
    """
    return string.ascii_uppercase[index]
