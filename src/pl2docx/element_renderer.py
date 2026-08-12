"""Build per-question Subdoc content and Jinja context for the docx template.

**Phase 3A status**: minimal/fixed formatting, equivalent in substance to Phase 2's
old behavior (`docx_builder.py`'s former `_add_options`/`_add_fill_in`), just
restructured into the three separate subdocs (`question_contents`,
`answer_contents`, `answer_space`) the document-level template now expects instead
of one shared subdoc per document. Phase 3B extends this with config-driven
list-style/display/fill-in-format choices and compound-question support.
"""

from __future__ import annotations

from docxtpl import DocxTemplate

from pl2docx.html_parser import ParsedQuestion, format_points_text, option_letter

ANSWER_SPACE_BLANK_LINES = 2


def build_question_context(tpl: DocxTemplate, question: ParsedQuestion, number: int) -> dict:
    """Build one question's Jinja context dict, including its 3 Subdocs.

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
        (e.g. `answer_panel_text`/`correct_option_indices` are empty for a
        blank-copy parse), same as Phase 2.
    number : int
        This question's 1-based position within the overall document.

    Returns
    -------
    dict
        `{"number", "title", "qid", "points_numeric", "points_text",
        "question_contents", "answer_contents", "answer_space"}` — the
        per-question shape `pl2docx.docx_builder.render_document`'s
        template context expects.
    """
    return {
        "number": number,
        "title": question.title,
        "qid": question.qid,
        "points_numeric": question.points_numeric,
        "points_text": format_points_text(question.points_numeric, question.points),
        "question_contents": _build_question_contents(tpl, question),
        "answer_contents": _build_answer_contents(tpl, question),
        "answer_space": _build_answer_space(tpl),
    }


def _build_question_contents(tpl: DocxTemplate, question: ParsedQuestion):
    subdoc = tpl.new_subdoc()
    if question.prompt_text:
        subdoc.add_paragraph(question.prompt_text)

    if question.kind in ("multiple_choice", "checkbox"):
        for idx, option_text in enumerate(question.options):
            paragraph = subdoc.add_paragraph()
            run = paragraph.add_run(f"({option_letter(idx)}) {option_text}")
            if idx in question.correct_option_indices:
                run.bold = True
    else:
        subdoc.add_paragraph("Answer: " + "_" * 20)

    return subdoc


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
