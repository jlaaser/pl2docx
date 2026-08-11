"""Render parsed questions into a Word document via an instructor template.

Follows CLAUDE.md's "purpose-built conversion layer" direction: all
per-element formatting logic lives here in Python, and the instructor's
template docx only needs a single ``{{p content }}`` placeholder — the
`docxtpl` subdocument this module builds gets inserted there. Formatting is
currently fixed per element kind (lettered options, blank fill-in lines);
run-time-configurable formatting is Phase 3's job, not this one's.
"""

from __future__ import annotations

from pathlib import Path

from docxtpl import DocxTemplate

from pl2docx.html_parser import ParsedQuestion, option_letter


def build_document(template_path: Path, questions: list[ParsedQuestion], output_path: Path) -> None:
    """Render `questions` into a docx built from `template_path`.

    Parameters
    ----------
    template_path : pathlib.Path
        Path to a docx template containing a `{{ content }}` Jinja
        placeholder (an instructor-supplied template with instructions/
        reference material, or a minimal stand-in for testing).
    questions : list[ParsedQuestion]
        Questions to render, in the order they should appear. Each is
        rendered as a heading, a "Value: " line (if `points` is set), its
        prompt text, and a kind-specific answer area: lettered options
        (bolding the correct one(s), if `correct_option_indices` is
        non-empty) for `multiple_choice`/`checkbox`; an "Answer: " line
        (blank, or filled in if `answer_panel_text` is set) for
        `string_input`/`integer_input`. Whenever `answer_panel_text` is set,
        its full text is also always shown as a "Correct answer: " line —
        for `multiple_choice`/`checkbox` this is in *addition* to any
        bolding, never a substitute for it (bolding isn't guaranteed to
        match — see `pl2docx.html_parser`'s module docstring on
        `pl-hide-in-panel`).
    output_path : pathlib.Path
        Where to save the resulting docx. Parent directories are created if
        needed.

    Notes
    -----
    Whether this produces a "blank" or "key" document is entirely a
    function of the input `ParsedQuestion`s' answer data (empty vs.
    populated) — this function itself has no separate blank/key mode.
    """
    doc = DocxTemplate(str(template_path))
    subdoc = doc.new_subdoc()

    for question in questions:
        subdoc.add_heading(question.title, level=2)
        if question.points is not None:
            subdoc.add_paragraph(f"Value: {question.points}").runs[0].italic = True
        if question.prompt_text:
            subdoc.add_paragraph(question.prompt_text)

        if question.kind in ("multiple_choice", "checkbox"):
            _add_options(subdoc, question)
        else:
            _add_fill_in(subdoc, question)

    doc.render({"content": subdoc})
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path))


def _add_options(subdoc, question: ParsedQuestion) -> None:
    for idx, option_text in enumerate(question.options):
        paragraph = subdoc.add_paragraph()
        run = paragraph.add_run(f"({option_letter(idx)}) {option_text}")
        if idx in question.correct_option_indices:
            run.bold = True
    # Always show the real answer-panel text, in addition to (never instead
    # of) any bolding above — bolding is best-effort and can miss (e.g.
    # pl-hide-in-panel-suppressed answer lists with custom explanations).
    if question.answer_panel_text is not None:
        paragraph = subdoc.add_paragraph()
        run = paragraph.add_run(f"Correct answer: {question.answer_panel_text}")
        run.italic = True


def _add_fill_in(subdoc, question: ParsedQuestion) -> None:
    if question.answer_panel_text is not None:
        subdoc.add_paragraph(f"Answer: {question.answer_panel_text}")
    else:
        subdoc.add_paragraph("Answer: " + "_" * 20)
