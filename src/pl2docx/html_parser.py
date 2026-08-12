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
- Element-specific input markup (``pl-multiple-choice``, ``pl-checkbox``,
  ``pl-string-input``, ``pl-integer-input``) is documented inline below, from
  each element's own ``.py``/``.mustache`` source. ``form-check-inline`` (on
  a `.form-check`) signals PL's own inline layout choice
  (``pl-multiple-choice.mustache``/``pl-checkbox.mustache``); a `<select>`
  instead of `<input type=radio>` signals a `display="dropdown"`
  `pl-multiple-choice`; both `pl-string-input` and `pl-integer-input` wrap
  their `<input>` in `.input-group`, with sibling `.input-group-text` spans
  holding the element's `label`/`suffix` text
  (``pl-string-input.mustache``/``pl-integer-input.mustache``).
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass, field
from typing import Literal

from bs4 import BeautifulSoup, NavigableString, Tag

QuestionKind = Literal["multiple_choice", "checkbox", "string_input", "integer_input"]


class UnsupportedElementError(RuntimeError):
    """Raised when a question's element type/shape isn't one this module handles.

    Compound questions (more than one distinct input-widget group on a page) are
    supported as of Phase 3B — this now only covers questions where none of the 4
    supported element types' input markup could be recognized at all, or the page's
    generic containers (`.question-block`/`.question-body`) couldn't be found.
    """


@dataclass(frozen=True)
class Widget:
    """One distinct, named input-widget group on a question page.

    A "compound" question (e.g. `physical-or-chemical`'s 3 separate
    `pl-multiple-choice` dropdown sub-statements, or `previous-experience`'s radio
    group + text box) has more than one `Widget`, in source (DOM) order.

    Parameters
    ----------
    kind : QuestionKind
        Which of the 4 supported element types this widget is.
    name : str
        The input `name` attribute shared by this widget's own input tag(s) —
        distinguishes one widget from another on the same page.
    options : list[str]
        For `multiple_choice`/`checkbox`, the answer options in on-page order.
        Empty for `string_input`/`integer_input`.
    correct_option_indices : list[int]
        For `multiple_choice`/`checkbox`, best-effort indices into `options` that
        `.answer-body` could be matched back to (for bolding). Always empty for
        `string_input`/`integer_input`, and may be empty for `multiple_choice`/
        `checkbox` too even when the page has answer-key data — matching isn't
        guaranteed (see `ParsedQuestion.answer_panel_text`).
    is_inline : bool
        For `multiple_choice`/`checkbox` rendered as radio/checkbox inputs (not a
        dropdown): whether PL's own source HTML used its inline layout
        (`form-check-inline`). Always `False` for `string_input`/`integer_input`
        and for dropdown-rendered `multiple_choice` (no such signal exists there).
    is_dropdown : bool
        Whether this `multiple_choice` widget is rendered as a `<select>`
        (`display="dropdown"` in PL) rather than radio buttons. Always `False` for
        other kinds.
    label : str or None
        For `string_input`/`integer_input`, the element's `label` text (the
        `.input-group-text` immediately before the `<input>`), if present. Always
        `None` for `multiple_choice`/`checkbox`.
    suffix : str or None
        For `string_input`/`integer_input`, the element's `suffix` text (the
        `.input-group-text` immediately after the `<input>`), if present. Always
        `None` for `multiple_choice`/`checkbox`.
    """

    kind: QuestionKind
    name: str
    options: list[str] = field(default_factory=list)
    correct_option_indices: list[int] = field(default_factory=list)
    is_inline: bool = False
    is_dropdown: bool = False
    label: str | None = None
    suffix: str | None = None


@dataclass(frozen=True)
class ParsedQuestion:
    """A single `instance_question` page's content, in element-agnostic form.

    Parameters
    ----------
    title : str
        The question's title, from `.question-block h1`.
    prompt_segments : list[str]
        The question's prompt text, with each supported input widget's own markup
        removed, split at each widget's source position. Always has exactly
        `len(widgets) + 1` entries: `prompt_segments[i]` is the text immediately
        before `widgets[i]` (for `i < len(widgets)`), and `prompt_segments[-1]` is
        the trailing text after the last widget (or the whole prompt, if
        `widgets` is empty). Plain text (HTML stripped) — rich formatting/math is
        out of scope until Phase 4.
    widgets : list[Widget]
        This question's input-widget groups, in source (DOM) order. Exactly one
        for a simple question; more than one for a compound question.
    answer_panel_text : str or None
        The full text content of `.answer-body`, for the whole question (PL's
        combined answer panel doesn't mark widget boundaries, so this isn't split
        per-widget). `None` if this page has no answer-key data (i.e. parsed from
        blank/open-instance HTML, where `.answer-body` is present but empty).
        This is the authoritative "what's the correct answer" source — always
        render it in full; each widget's `correct_option_indices` is only an
        optional enrichment on top.
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
    prompt_segments: list[str]
    widgets: list[Widget]
    answer_panel_text: str | None
    points: str | None
    points_numeric: float | None
    qid: str | None


def parse_instance_question_html(html: str) -> ParsedQuestion:
    """Parse one fetched `instance_question` page into a `ParsedQuestion`.

    Parameters
    ----------
    html : str
        Raw HTML of an `instance_question/:id` page, as fetched by
        `pl2docx.pl_client.PLClient.fetch_instance_questions`. May be either
        the blank (open-instance) or answer-key (closed-instance) render of
        the same variant.

    Returns
    -------
    ParsedQuestion

    Raises
    ------
    UnsupportedElementError
        If the question's generic containers can't be found, or if none of
        the 4 supported element types' input markup is recognized anywhere
        on the page.
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

    groups = _find_widget_groups(question_body)
    if not groups:
        raise UnsupportedElementError(
            "No supported input widget (pl-multiple-choice/pl-checkbox/"
            "pl-string-input/pl-integer-input) found in page HTML."
        )

    widgets = [_build_widget(group, answer_body) for group in groups]
    prompt_segments = _extract_prompt_segments(question_body, groups)

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


@dataclass
class _WidgetGroup:
    """Internal: one widget's raw containers, before building its `Widget`."""

    kind: QuestionKind
    name: str
    containers: list[Tag]  # DOM-order containers to strip/replace for prompt-splitting
    is_dropdown: bool = False


def _find_widget_groups(question_body: Tag) -> list[_WidgetGroup]:
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

    for string_input in question_body.find_all(class_=re.compile(r"pl-string-input-(input|multiline)")):
        container = string_input.find_parent(class_=re.compile(r"^input-group\b")) or string_input
        add("string_input", string_input.get("name", ""), container)

    for integer_input in question_body.find_all(class_="pl-integer-input-input"):
        container = integer_input.find_parent(class_=re.compile(r"^input-group\b")) or integer_input
        add("integer_input", integer_input.get("name", ""), container)

    order_index = {id(tag): i for i, tag in enumerate(question_body.descendants) if isinstance(tag, Tag)}
    ordered_keys = sorted(
        groups.keys(),
        key=lambda key: order_index.get(id(groups[key].containers[0]), len(order_index)),
    )
    return [groups[key] for key in ordered_keys]


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


def _extract_group_options(group: _WidgetGroup) -> list[str]:
    if group.is_dropdown:
        options = []
        for option in group.containers[0].find_all("option"):
            if not option.get("value"):
                continue  # the blank placeholder option
            content = option.get("data-content", "")
            content = re.sub(r"^\([A-Za-z0-9]+\)\s*", "", content).strip()
            options.append(content or option.get_text(strip=True))
        return options

    options = []
    for container in group.containers:
        answer = container.find(class_=["pl-multiple-choice-answer", "pl-checkbox-answer"])
        if answer is not None:
            options.append(answer.get_text(strip=True))
    return options


def _extract_group_is_inline(group: _WidgetGroup) -> bool:
    if group.is_dropdown:
        return False
    first = group.containers[0]
    classes = first.get("class") or []
    return "form-check-inline" in classes


def _extract_group_label_suffix(group: _WidgetGroup) -> tuple[str | None, str | None]:
    container = group.containers[0]
    input_tag = container.find(attrs={"name": group.name})
    if input_tag is None:
        return None, None

    texts = container.find_all(class_="input-group-text")
    label = None
    suffix = None
    for text_tag in texts:
        text = text_tag.get_text(strip=True)
        if not text:
            continue
        if _precedes(text_tag, input_tag):
            if label is None:
                label = text
        else:
            suffix = text  # last trailing one wins
    return label, suffix


def _precedes(tag: Tag, other: Tag) -> bool:
    for sibling in tag.find_all_next():
        if sibling is other:
            return True
    return False


def _extract_correct_option_indices(answer_body: Tag | None, options: list[str]) -> list[int]:
    """Best-effort match of `.answer-body`'s `<li>` items back to `options`.

    Returns an empty list whenever nothing matches — e.g. a question whose
    answer panel holds custom-authored explanatory text instead of PL's
    default `<li>(key) option text</li>` list (see module docstring on
    `pl-hide-in-panel`). Not matching is expected/normal, not an error:
    `answer_panel_text` remains the authoritative answer regardless.
    """
    if answer_body is None:
        return []
    indices: list[int] = []
    for li in answer_body.find_all("li"):
        li_text = li.get_text(strip=True)
        for idx, option in enumerate(options):
            if option and option in li_text and idx not in indices:
                indices.append(idx)
                break
    return indices


def _extract_prompt_segments(question_body: Tag, groups: list[_WidgetGroup]) -> list[str]:
    """Split the prompt's flattened text at each widget's source position.

    Re-runs widget detection on a fresh copy of `question_body` (rather than
    mutating the tree used for the rest of parsing) so this can safely replace
    each widget's first container with a placeholder and remove the rest, then
    split the resulting flattened text on those placeholders. Detection is a pure
    function of the HTML, so `_find_widget_groups` on the copy produces groups in
    the same order/count as `groups` — this is an internal invariant of this
    module, not something calling code needs to reason about.
    """
    if not groups:
        text = question_body.get_text(separator=" ", strip=False)
        return [re.sub(r"\s+", " ", text).strip()]

    body_copy = BeautifulSoup(str(question_body), "html.parser")
    copy_groups = _find_widget_groups(body_copy)

    marker = "\x00"
    for copy_group in copy_groups:
        copy_group.containers[0].replace_with(NavigableString(marker))
        for extra in copy_group.containers[1:]:
            extra.decompose()

    text = body_copy.get_text(separator=" ", strip=False)
    parts = text.split(marker)
    return [re.sub(r"\s+", " ", part).strip() for part in parts]


def _extract_answer_panel_text(answer_body: Tag | None) -> str | None:
    if answer_body is None:
        return None
    text = answer_body.get_text(separator=" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


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
