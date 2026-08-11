from docx import Document

from pl2docx.docx_builder import build_document
from pl2docx.html_parser import ParsedQuestion


def _questions(blank: bool) -> list[ParsedQuestion]:
    return [
        ParsedQuestion(
            title="MC Question",
            kind="multiple_choice",
            prompt_text="Pick one.",
            options=["Alpha", "Beta", "Gamma"],
            correct_option_indices=[] if blank else [1],
            answer_panel_text=None if blank else "(B) Beta",
            points="2",
        ),
        ParsedQuestion(
            title="Integer Question",
            kind="integer_input",
            prompt_text="Enter a number.",
            options=[],
            correct_option_indices=[],
            answer_panel_text=None if blank else "42",
            points=None,
        ),
    ]


def _all_text(path):
    doc = Document(str(path))
    return "\n".join(p.text for p in doc.paragraphs)


def test_build_document_blank(minimal_template, tmp_path):
    output_path = tmp_path / "blank.docx"
    build_document(minimal_template, _questions(blank=True), output_path)

    assert output_path.exists()
    text = _all_text(output_path)
    assert "MC Question" in text
    assert "Value: 2" in text
    assert "Pick one." in text
    assert "(A) Alpha" in text
    assert "(B) Beta" in text
    assert "(C) Gamma" in text
    assert "Correct answer:" not in text
    assert "Answer: 42" not in text
    assert "Answer: " in text  # blank fill-in line for integer_input


def test_build_document_key(minimal_template, tmp_path):
    output_path = tmp_path / "key.docx"
    build_document(minimal_template, _questions(blank=False), output_path)

    text = _all_text(output_path)
    assert "Answer: 42" in text
    assert "Correct answer: (B) Beta" in text

    doc = Document(str(output_path))
    beta_paragraph = next(p for p in doc.paragraphs if p.text.startswith("(B)"))
    assert any(run.bold for run in beta_paragraph.runs if "Beta" in run.text)


def test_build_document_key_shows_answer_even_without_bolding(minimal_template, tmp_path):
    """answer_panel_text must render even when correct_option_indices can't identify it."""
    questions = [
        ParsedQuestion(
            title="MC With Custom Explanation",
            kind="multiple_choice",
            prompt_text="Pick one.",
            options=["Alpha", "Beta"],
            correct_option_indices=[],  # couldn't be matched (e.g. pl-hide-in-panel)
            answer_panel_text="Beta is correct because of reasons.",
            points="1",
        ),
    ]
    output_path = tmp_path / "key_no_bold.docx"
    build_document(minimal_template, questions, output_path)

    text = _all_text(output_path)
    assert "Correct answer: Beta is correct because of reasons." in text

    doc = Document(str(output_path))
    for paragraph in doc.paragraphs:
        for run in paragraph.runs:
            if run.text.strip() in ("(A) Alpha", "(B) Beta"):
                assert not run.bold
