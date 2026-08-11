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
  each element's own ``.py``/``.mustache`` source.
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass
from typing import Literal

from bs4 import BeautifulSoup, Tag

QuestionKind = Literal["multiple_choice", "checkbox", "string_input", "integer_input"]


class UnsupportedElementError(RuntimeError):
    """Raised when a question's element type/shape isn't one Phase 2 handles.

    Covers both genuinely unsupported element types and compound questions
    that embed more than one input widget (e.g. multiple named
    ``pl-multiple-choice`` sub-statements in one question) — Phase 2's scope
    is deliberately limited to one simple widget per question; silently
    mis-parsing a compound question would be worse than refusing it.
    """


@dataclass(frozen=True)
class ParsedQuestion:
    """A single `instance_question` page's content, in element-agnostic form.

    Parameters
    ----------
    title : str
        The question's title, from `.question-block h1`.
    kind : QuestionKind
        Which of the 4 Phase-2-supported element types this question uses.
    prompt_text : str
        The question's prompt text, with the input widget markup removed.
        Plain text (HTML stripped) — rich formatting/math is out of scope
        until Phase 4.
    options : list[str]
        For `multiple_choice`/`checkbox`, the answer options in on-page
        order. Empty for `string_input`/`integer_input`.
    correct_option_indices : list[int]
        For `multiple_choice`/`checkbox`, best-effort indices into `options`
        that `.answer-body` could be matched back to (for bolding). Always
        empty for `string_input`/`integer_input`, and may be empty for
        `multiple_choice`/`checkbox` too even when `answer_panel_text` is
        set — matching isn't guaranteed (see `answer_panel_text`).
    answer_panel_text : str or None
        The full text content of `.answer-body`, for *any* question kind.
        `None` if this page has no answer-key data (i.e. parsed from blank/
        open-instance HTML, where `.answer-body` is present but empty). This
        is the authoritative "what's the correct answer" source — always
        render it in full; `correct_option_indices` is only an optional
        enrichment on top.
    points : str or None
        The question's point value, as PL displays it (e.g. `"1"`), from
        `#question-score-panel-content`'s `"Value:"`/`"Available points:"`
        row. `None` if that table/row isn't present in the fetched page.

    Notes
    -----
    Does not assume the source HTML represents a physically consistent
    question+answer pair by itself — `correct_option_indices`/
    `answer_panel_text` are simply whatever `.answer-body` contained, which
    is empty for blank-copy HTML. Pairing a blank parse with a key parse of
    the *same* `instance_question_id` is the caller's responsibility.
    """

    title: str
    kind: QuestionKind
    prompt_text: str
    options: list[str]
    correct_option_indices: list[int]
    answer_panel_text: str | None
    points: str | None


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
        If the question's generic containers can't be found, if none of the
        4 supported element types' input markup is recognized, or if more
        than one distinct input-widget group is present (a compound
        question — out of scope for Phase 2; see CLAUDE.md's open design
        decisions for this known limitation).
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

    answer_body = soup.find(class_="answer-body")
    answer_panel_text = _extract_answer_panel_text(answer_body)
    points = _extract_points(soup)

    kind = _detect_kind(question_body)

    prompt_text = _extract_prompt_text(question_body)

    if kind in ("multiple_choice", "checkbox"):
        options = _extract_options(question_body)
        correct_option_indices = _extract_correct_option_indices(answer_body, options)
        return ParsedQuestion(
            title=title,
            kind=kind,
            prompt_text=prompt_text,
            options=options,
            correct_option_indices=correct_option_indices,
            answer_panel_text=answer_panel_text,
            points=points,
        )

    return ParsedQuestion(
        title=title,
        kind=kind,
        prompt_text=prompt_text,
        options=[],
        correct_option_indices=[],
        answer_panel_text=answer_panel_text,
        points=points,
    )


def _detect_kind(question_body: Tag) -> QuestionKind:
    checkboxes = question_body.find_all("input", attrs={"type": "checkbox"})
    radios = question_body.find_all("input", attrs={"type": "radio"})
    selects = question_body.find_all("select")
    string_inputs = question_body.find_all(
        class_=re.compile(r"pl-string-input-(input|multiline)")
    )
    integer_inputs = question_body.find_all(class_="pl-integer-input-input")

    present = [
        ("checkbox", checkboxes, _widget_group_names(checkboxes)),
        ("multiple_choice", radios or selects, _widget_group_names(radios) or _widget_group_names(selects)),
        ("string_input", string_inputs, {"__string__"}),
        ("integer_input", integer_inputs, {"__integer__"}),
    ]
    matched = [(kind, tags, names) for kind, tags, names in present if tags]

    if len(matched) != 1:
        raise UnsupportedElementError(
            f"Expected exactly one supported input widget type, found: "
            f"{[kind for kind, _, _ in matched]}"
        )
    kind, _tags, names = matched[0]
    if len(names) > 1:
        raise UnsupportedElementError(
            f"Compound question with multiple '{kind}' input groups is not supported in Phase 2."
        )
    return kind  # type: ignore[return-value]


def _widget_group_names(inputs: list[Tag]) -> set[str]:
    return {tag.get("name", "") for tag in inputs}


def _extract_prompt_text(question_body: Tag) -> str:
    body_copy = BeautifulSoup(str(question_body), "html.parser")
    for selector in (
        {"class_": "form-check"},
        {"class_": re.compile(r"^input-group\b")},
    ):
        for tag in body_copy.find_all("div", **selector) + body_copy.find_all("span", **selector):
            tag.decompose()
    text = body_copy.get_text(separator=" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def _extract_options(question_body: Tag) -> list[str]:
    return [
        div.get_text(strip=True)
        for div in question_body.find_all(class_=["pl-multiple-choice-answer", "pl-checkbox-answer"])
    ]


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


def option_letter(index: int) -> str:
    """Map a 0-based option index to its display letter, e.g. 0 -> "A".

    Parameters
    ----------
    index : int
        0-based index into a question's `options` list.

    Returns
    -------
    str
        The letter PL itself would use for this position (`(A)`, `(B)`, …).

    Raises
    ------
    IndexError
        If `index` is outside the 26-letter range this simple scheme covers
        (no Phase 2 fixture/target question has that many options).
    """
    return string.ascii_uppercase[index]
