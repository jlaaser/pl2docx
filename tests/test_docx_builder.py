import pytest
from docx import Document

from pl2docx.docx_builder import TemplateUnreadableError, render_document
from pl2docx.html_parser import ParsedQuestion


def _mc_question(blank: bool) -> ParsedQuestion:
    return ParsedQuestion(
        title="MC Question",
        kind="multiple_choice",
        prompt_text="Pick one.",
        options=["Alpha", "Beta", "Gamma"],
        correct_option_indices=[] if blank else [1],
        answer_panel_text=None if blank else "(B) Beta",
        points="2",
        points_numeric=2.0,
        qid="course/questions/mc-question",
    )


def _integer_question(blank: bool) -> ParsedQuestion:
    return ParsedQuestion(
        title="Integer Question",
        kind="integer_input",
        prompt_text="Enter a number.",
        options=[],
        correct_option_indices=[],
        answer_panel_text=None if blank else "42",
        points="1",
        points_numeric=1.0,
        qid="course/questions/integer-question",
    )


def _zones(blank: bool):
    return [
        {
            "title": "Zone One",
            "questions": [(_mc_question(blank), 1), (_integer_question(blank), 2)],
        },
        {
            "title": None,
            "questions": [],
        },
    ]


def _all_text(path):
    doc = Document(str(path))
    parts = [p.text for p in doc.paragraphs]
    # doc.paragraphs doesn't include paragraphs nested inside tables (e.g. the
    # bordered "SOLUTION:" box the starter template wraps answer_contents in).
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                parts.extend(p.text for p in cell.paragraphs)
    return "\n".join(parts)


def test_render_document_blank(starter_template, tmp_path):
    output_path = tmp_path / "blank.docx"
    zones = _zones(blank=True)
    # drop the empty second zone - not meaningful for a blank/key comparison test
    render_document(starter_template, [zones[0]], is_answer_key=False, output_path=output_path)

    assert output_path.exists()
    text = _all_text(output_path)
    assert "Zone One" in text
    assert "1. MC Question (2 points)" in text
    assert "Pick one." in text
    assert "(A) Alpha" in text
    assert "(B) Beta" in text
    assert "2. Integer Question (1 point)" in text
    assert "course/questions/mc-question" not in text  # qid only shown in the key
    assert "42" not in text


def test_render_document_key(starter_template, tmp_path):
    output_path = tmp_path / "key.docx"
    zones = _zones(blank=False)
    render_document(starter_template, [zones[0]], is_answer_key=True, output_path=output_path)

    text = _all_text(output_path)
    assert "[course/questions/mc-question]" in text
    assert "[course/questions/integer-question]" in text
    assert "42" in text

    doc = Document(str(output_path))
    beta_paragraph = next(p for p in doc.paragraphs if p.text.startswith("(B)"))
    assert any(run.bold for run in beta_paragraph.runs if "Beta" in run.text)


def test_render_document_zone_without_title_has_no_heading(starter_template, tmp_path):
    output_path = tmp_path / "untitled_zone.docx"
    zone = {"title": None, "questions": [(_integer_question(blank=True), 1)]}
    render_document(starter_template, [zone], is_answer_key=False, output_path=output_path)

    doc = Document(str(output_path))
    heading_paragraphs = [p for p in doc.paragraphs if p.style.name == "pl2docx Zone Heading"]
    assert heading_paragraphs == []


def test_render_document_unreadable_template_raises_clear_error(tmp_path):
    """Regression: a template open in Word (or mid-cloud-sync) fails with a cryptic
    docx.opc.exceptions.PackageNotFoundError deep inside docxtpl's subdoc creation.
    Should surface as a clear, actionable pl2docx error instead."""
    not_a_docx = tmp_path / "not_a_docx.docx"
    not_a_docx.write_text("this is not a docx file", encoding="utf-8")

    with pytest.raises(TemplateUnreadableError):
        render_document(not_a_docx, [], is_answer_key=False, output_path=tmp_path / "out.docx")
