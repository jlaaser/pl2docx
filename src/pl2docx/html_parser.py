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

import re
import string
from collections.abc import Iterable
from dataclasses import dataclass, field
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
        Whether `<strong>`/`<b>`, `<em>`/`<i>`, `<u>` (respectively) wrapped
        this text anywhere in its ancestry within the source HTML.
    """

    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False


@dataclass(frozen=True)
class ParagraphBreak:
    """A paragraph boundary in the source HTML (`<p>`/`<br>`/list-item edges)."""


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


#: One node in a flattened, order-preserving walk of an HTML fragment's content.
#: Produced by `_walk_content`/consumed by `pl2docx.element_renderer` to render
#: formatted runs, paragraph breaks, images, and math into a docx Subdoc instead
#: of the plain strings Phases 2/3 used. `ImageRef`/`MathRef` nodes are recognized
#: by the walker starting Phase 4 increment 1, but only rendered as their real
#: picture/OMML form once increments 2/3 land (element_renderer falls back to
#: alt text / raw LaTeX text until then).
ContentNode = TextRun | ParagraphBreak | ListItemStart | ListItemEnd | ImageRef | MathRef | SvgRef


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
    """Raised when a question's element type/shape isn't one this module handles.

    Compound questions (more than one distinct input-widget group on a page) are
    supported as of Phase 3B — this now only covers questions where none of the
    supported element types' input markup could be recognized at all (built-in, or
    declared via `additional-elements` and passed in as `additional_fill_in_tags`),
    or the page's generic containers (`.question-block`/`.question-body`) couldn't
    be found.
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
    correct_option_indices : list[int]
        For `multiple_choice`/`checkbox`, best-effort indices into `options` that
        `.answer-body` could be matched back to (for bolding). Always empty for
        every fill-in-type kind, and may be empty for `multiple_choice`/
        `checkbox` too even when the page has answer-key data — matching isn't
        guaranteed (see `ParsedQuestion.answer_panel_text`).
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
    """

    kind: str
    name: str
    options: list[list[ContentNode]] = field(default_factory=list)
    correct_option_indices: list[int] = field(default_factory=list)
    is_inline: bool = False
    is_dropdown: bool = False
    label: list[ContentNode] | None = None
    suffix: list[ContentNode] | None = None


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
    html: str, additional_fill_in_tags: Iterable[str] = ()
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
        detected, not a hard error.

    Returns
    -------
    ParsedQuestion

    Raises
    ------
    UnsupportedElementError
        If the question's generic containers can't be found, or if none of
        the supported element types' input markup is recognized anywhere on
        the page.
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
    points = _extract_points(soup)
    points_numeric = _parse_points_numeric(points)
    qid = _extract_qid(soup)

    groups = _find_widget_groups(question_body, additional_fill_in_tags)
    if not groups:
        raise UnsupportedElementError(
            "No supported input widget (pl-multiple-choice/pl-checkbox/"
            + "/".join(_BUILTIN_FILL_IN_TAGS.values())
            + (f"/{'/'.join(additional_fill_in_tags)}" if additional_fill_in_tags else "")
            + ") found in page HTML."
        )

    widgets = [_build_widget(group, answer_body) for group in groups]
    prompt_segments = _extract_prompt_segments(question_body, groups, additional_fill_in_tags)

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


def _split_math_delimiters(text: str, bold: bool, italic: bool, underline: bool) -> list[ContentNode]:
    """Split one text node's raw string into `TextRun`/`MathRef` nodes at LaTeX math delimiters.

    Parameters
    ----------
    text : str
        Raw text content of one HTML text node (a `NavigableString`'s
        `str()`), not yet whitespace-collapsed - that normalization still
        happens later, in `_normalize_nodes`.
    bold, italic, underline : bool
        Character formatting inherited from this text node's tag ancestry,
        applied to the `TextRun` pieces only - a `MathRef` carries no
        character-formatting flags of its own, since its rendered appearance
        is controlled entirely by its LaTeX source instead.

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
            nodes.append(TextRun(text[pos : m.start()], bold, italic, underline))
        if m.group(0) == "\\$":
            nodes.append(TextRun("$", bold, italic, underline))
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
        nodes.append(TextRun(text[pos:], bold, italic, underline))
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
    _walk_into(root, nodes, marker_by_id or {}, bold=False, italic=False, underline=False)
    return nodes


def _walk_into(
    node, out: list[ContentNode | _WidgetMarker], marker_by_id: dict[int, int],
    bold: bool, italic: bool, underline: bool,
) -> None:
    if isinstance(node, Comment):
        return
    if isinstance(node, NavigableString):
        text = str(node)
        if text:
            out.extend(_split_math_delimiters(text, bold, italic, underline))
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

    child_bold = bold or name in _BOLD_TAGS
    child_italic = italic or name in _ITALIC_TAGS
    child_underline = underline or name in _UNDERLINE_TAGS

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
                    name == "ol", index, list_id,
                )
            else:
                _walk_into(child, out, marker_by_id, child_bold, child_italic, child_underline)
        return
    if name == "li":
        # A stray <li> outside any <ol>/<ul> (malformed HTML) - no real list
        # to report an ordered flag/position/list_id from, so this always
        # renders as a plain bullet. Real PL content always wraps <li> in
        # ol/ul, which goes through the branch above instead.
        _walk_li(node, out, marker_by_id, child_bold, child_italic, child_underline, False, None, None)
        return

    is_block = name in _BLOCK_TAGS
    if is_block and out:
        out.append(ParagraphBreak())

    for child in node.children:
        _walk_into(child, out, marker_by_id, child_bold, child_italic, child_underline)

    if is_block:
        out.append(ParagraphBreak())


def _walk_li(
    node: Tag, out: list[ContentNode | _WidgetMarker], marker_by_id: dict[int, int],
    bold: bool, italic: bool, underline: bool, ordered: bool, index: int | None,
    list_id: int | None,
) -> None:
    marker_index = marker_by_id.get(id(node))
    if marker_index is not None:
        out.append(_WidgetMarker(marker_index))
        return
    if out:
        out.append(ParagraphBreak())
    out.append(ListItemStart(ordered=ordered, index=index, list_id=list_id))
    for child in node.children:
        _walk_into(child, out, marker_by_id, bold, italic, underline)
    out.append(ListItemEnd())


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
                and (merged[-1].bold, merged[-1].italic, merged[-1].underline)
                == (node.bold, node.italic, node.underline)
            ):
                prev = merged[-1]
                merged[-1] = TextRun(prev.text + node.text, prev.bold, prev.italic, prev.underline)
            else:
                merged.append(node)
        elif isinstance(node, ParagraphBreak):
            if merged and isinstance(merged[-1], ParagraphBreak):
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
            cleaned.append(TextRun(text, node.bold, node.italic, node.underline))
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

    while cleaned and _is_boundary_junk(cleaned[0]):
        cleaned.pop(0)
    while cleaned and _is_boundary_junk(cleaned[-1]):
        cleaned.pop()

    if cleaned and isinstance(cleaned[0], TextRun):
        first = cleaned[0]
        cleaned[0] = TextRun(first.text.lstrip(), first.bold, first.italic, first.underline)
    if cleaned and isinstance(cleaned[-1], TextRun):
        last = cleaned[-1]
        cleaned[-1] = TextRun(last.text.rstrip(), last.bold, last.italic, last.underline)

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
    question_body: Tag, additional_fill_in_tags: Iterable[str] = ()
) -> list[_WidgetGroup]:
    """Group this page's recognized inputs into one `_WidgetGroup` per (kind, name).

    Groups are returned in true DOM order of first appearance — determined via
    `question_body.descendants`' iteration order, since collecting per-kind with
    `find_all` (as done here for simplicity) interleaves kinds incorrectly on a
    compound page.
    """
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
        container = (
            select.find_parent(class_=re.compile(r"pl-multiple-choice-dropdown")) or select
        )
        add("multiple_choice", select.get("name", ""), container, is_dropdown=True)

    for kind, tag in _BUILTIN_FILL_IN_TAGS.items():
        _add_fill_in_groups(question_body, tag, kind, add)
    for tag in additional_fill_in_tags:
        _add_fill_in_groups(question_body, tag, tag, add)

    order_index = {id(tag): i for i, tag in enumerate(question_body.descendants) if isinstance(tag, Tag)}
    ordered_keys = sorted(
        groups.keys(),
        key=lambda key: order_index.get(id(groups[key].containers[0]), len(order_index)),
    )
    return [groups[key] for key in ordered_keys]


def _add_fill_in_groups(question_body: Tag, tag: str, kind: str, add) -> None:
    """Detect one fill-in-type element's widgets by its tag-name-derived class pattern.

    Confirmed shared convention across every built-in fill-in element (and
    `pl-scinum-input`, a course-specific `additional-elements` element following
    the same pattern): the input's own class is `{tag}-input` or `{tag}-multiline`
    (the element's registered PL tag name, verbatim, plus a fixed suffix) — so this
    needs no per-element knowledge beyond the tag name string itself, which is
    exactly what lets `additional_fill_in_tags` support arbitrary configured
    elements without any course-specific string appearing in this module.

    Restricting the search to `<input>`/`<textarea>` tag names specifically (not
    just any tag carrying a matching class) is deliberate, not incidental: it's
    what correctly excludes `pl-symbolic-input`'s `formula_editor`-mode
    `<math-field>` custom element, which carries the same
    `pl-symbolic-input-input` class but isn't a real, statically-populated input —
    see this module's docstring.
    """
    pattern = re.compile(rf"^{re.escape(tag)}-(input|multiline)$")
    for input_tag in question_body.find_all(["input", "textarea"], class_=pattern):
        container = input_tag.find_parent(class_=re.compile(r"^input-group\b")) or input_tag
        add(kind, input_tag.get("name", ""), container)


def _build_widget(group: _WidgetGroup, answer_body: Tag | None) -> Widget:
    if group.kind in ("multiple_choice", "checkbox"):
        options = _extract_group_options(group)
        return Widget(
            kind=group.kind,
            name=group.name,
            options=options,
            correct_option_indices=_extract_correct_option_indices(answer_body, options),
            is_inline=_extract_group_is_inline(group),
            is_dropdown=group.is_dropdown,
        )
    label, suffix = _extract_group_label_suffix(group)
    return Widget(kind=group.kind, name=group.name, label=label, suffix=suffix)


def _extract_group_options(group: _WidgetGroup) -> list[list[ContentNode]]:
    if group.is_dropdown:
        options = []
        for option in group.containers[0].find_all("option"):
            if not option.get("value"):
                continue  # the blank placeholder option
            content = option.get("data-content", "")
            content = re.sub(r"^\([A-Za-z0-9]+\)\s*", "", content).strip()
            nodes = _rich_from_html_fragment(content) if content else _normalize_nodes(
                _walk_content(option)  # type: ignore[arg-type]
            )
            options.append(nodes)
        return options

    options = []
    for container in group.containers:
        answer = container.find(class_=["pl-multiple-choice-answer", "pl-checkbox-answer"])
        if answer is not None:
            options.append(_normalize_nodes(_walk_content(answer)))  # type: ignore[arg-type]
    return options


def _extract_group_is_inline(group: _WidgetGroup) -> bool:
    if group.is_dropdown:
        return False
    first = group.containers[0]
    classes = first.get("class") or []
    return "form-check-inline" in classes


def _extract_group_label_suffix(
    group: _WidgetGroup,
) -> tuple[list[ContentNode] | None, list[ContentNode] | None]:
    container = group.containers[0]
    input_tag = container.find(attrs={"name": group.name})
    if input_tag is None:
        return None, None

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
    return label, suffix


def _precedes(tag: Tag, other: Tag) -> bool:
    for sibling in tag.find_all_next():
        if sibling is other:
            return True
    return False


def _extract_correct_option_indices(
    answer_body: Tag | None, options: list[list[ContentNode]]
) -> list[int]:
    """Best-effort match of `.answer-body`'s `<li>` items back to `options`.

    Returns an empty list whenever nothing matches — e.g. a question whose
    answer panel holds custom-authored explanatory text instead of PL's
    default `<li>(key) option text</li>` list (see module docstring on
    `pl-hide-in-panel`). Not matching is expected/normal, not an error:
    `answer_panel_text` remains the authoritative answer regardless.

    Matches on `plain_text(option)` (formatting/math stripped) — this is a
    coarse text-containment check, not aiming to preserve rich content, so
    flattening both sides first keeps it simple.
    """
    if answer_body is None:
        return []
    indices: list[int] = []
    for li in answer_body.find_all("li"):
        li_text = li.get_text(strip=True)
        for idx, option in enumerate(options):
            option_text = plain_text(option)
            if option_text and option_text in li_text and idx not in indices:
                indices.append(idx)
                break
    return indices


def _extract_prompt_segments(
    question_body: Tag, groups: list[_WidgetGroup], additional_fill_in_tags: Iterable[str] = ()
) -> list[list[ContentNode]]:
    """Split the prompt's rich content at each widget's source position.

    Re-runs widget detection on a fresh copy of `question_body` (rather than
    mutating the tree used for the rest of parsing) so this can safely walk
    around each widget's first container (replacing it with a `_WidgetMarker`
    node instead of descending into it) and skip the rest. Detection is a
    pure function of the HTML (given the same `additional_fill_in_tags`), so
    `_find_widget_groups` on the copy produces groups in the same order/count as
    `groups` — this is an internal invariant of this module, not something calling
    code needs to reason about.
    """
    if not groups:
        return [_normalize_nodes(_walk_content(question_body))]  # type: ignore[list-item]

    body_copy = BeautifulSoup(str(question_body), "html.parser")
    copy_groups = _find_widget_groups(body_copy, additional_fill_in_tags)

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
