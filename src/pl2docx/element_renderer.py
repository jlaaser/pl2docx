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
"""

from __future__ import annotations

from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.text.run import Run
from docxtpl import DocxTemplate

from pl2docx.element_config import (
    ElementConfig,
    ElementPreferences,
    resolve_preferences,
)
from pl2docx.html_parser import ParsedQuestion, Widget, format_points_text, option_letter

ANSWER_SPACE_BLANK_LINES = 2

_SELECTOR_KINDS = ("multiple_choice", "checkbox")
_LIST_MARKERS = {
    "letter-labels": lambda idx: f"({option_letter(idx)})",
    "bubble": lambda _idx: "○",  # ○
    "checkbox": lambda _idx: "☐",  # ☐
}


def build_question_context(
    tpl: DocxTemplate, question: ParsedQuestion, number: int, element_config: ElementConfig
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
    question_contents, answer_element = _build_question_contents(tpl, question, element_config)
    return {
        "number": number,
        "title": question.title,
        "qid": question.qid,
        "points_numeric": question.points_numeric,
        "points_text": format_points_text(question.points_numeric, question.points),
        "question_contents": question_contents,
        "answer_contents": _build_answer_contents(tpl, question),
        "answer_space": _build_answer_space(tpl),
        "answer_element": answer_element,
    }


def _build_question_contents(tpl: DocxTemplate, question: ParsedQuestion, element_config: ElementConfig):
    subdoc = tpl.new_subdoc()
    answer_element = tpl.new_subdoc()

    state = {"paragraph": subdoc.add_paragraph()}

    def append_text(text: str) -> None:
        if not text:
            return
        paragraph = state["paragraph"]
        prefix = " " if paragraph.runs else ""
        paragraph.add_run(prefix + text)

    if not question.widgets:
        append_text(question.prompt_segments[0] if question.prompt_segments else "")
        return subdoc, answer_element

    for i, widget in enumerate(question.widgets):
        append_text(question.prompt_segments[i])

        prefs = resolve_preferences(element_config, widget.kind)
        display = _resolve_display(widget, prefs)
        is_selector = widget.kind in _SELECTOR_KINDS

        if display == "none":
            continue
        elif display == "inline":
            runs = (
                _render_selector_inline(state["paragraph"], widget, prefs)
                if is_selector
                else _render_fill_in_inline(state["paragraph"], widget)
            )
        else:  # "block" or "template"
            target = answer_element if display == "template" else subdoc
            runs = (
                _render_selector_block(target, widget, prefs)
                if is_selector
                else _render_fill_in_block(target, widget)
            )
            if display == "block":
                state["paragraph"] = subdoc.add_paragraph()

        if display != "none" and prefs.draw_border:
            for run in runs:
                _add_run_border(run)

    append_text(question.prompt_segments[-1])
    return subdoc, answer_element


def _resolve_display(widget: Widget, prefs: ElementPreferences) -> str:
    if prefs.display is not None:
        return prefs.display
    if widget.kind in _SELECTOR_KINDS and not widget.is_dropdown:
        return "inline" if widget.is_inline else "block"
    return "block"


def _render_selector_inline(paragraph, widget: Widget, prefs) -> list[Run]:
    runs: list[Run] = []
    for idx, option_text in enumerate(widget.options):
        if idx > 0:
            runs.append(paragraph.add_run("   "))
        marker = _LIST_MARKERS[prefs.list_style](idx)
        run = paragraph.add_run(f"{marker} {option_text}")
        if prefs.bold_correct and idx in widget.correct_option_indices:
            run.bold = True
        runs.append(run)
    return runs


def _render_fill_in_inline(paragraph, widget: Widget) -> list[Run]:
    return [paragraph.add_run(_fill_in_text(widget))]


def _render_selector_block(sink, widget: Widget, prefs) -> list[Run]:
    runs: list[Run] = []
    for idx, option_text in enumerate(widget.options):
        marker = _LIST_MARKERS[prefs.list_style](idx)
        run = sink.add_paragraph().add_run(f"{marker} {option_text}")
        if prefs.bold_correct and idx in widget.correct_option_indices:
            run.bold = True
        runs.append(run)
    return runs


def _render_fill_in_block(sink, widget: Widget) -> list[Run]:
    return [sink.add_paragraph().add_run(_fill_in_text(widget))]


def _fill_in_text(widget: Widget) -> str:
    blank = "_" * 20
    if widget.label is None:
        return f"Answer: {blank}"
    text = f"{widget.label} {blank}"
    if widget.suffix:
        text += f" {widget.suffix}"
    return text


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


def _build_answer_contents(tpl: DocxTemplate, question: ParsedQuestion):
    subdoc = tpl.new_subdoc()
    if question.answer_panel_text is not None:
        subdoc.add_paragraph(question.answer_panel_text)
    return subdoc


def _build_answer_space(tpl: DocxTemplate):
    subdoc = tpl.new_subdoc()
    for _ in range(ANSWER_SPACE_BLANK_LINES):
        subdoc.add_paragraph("")
    return subdoc
