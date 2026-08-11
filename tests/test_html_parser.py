from pathlib import Path

import pytest

from pl2docx.html_parser import UnsupportedElementError, parse_instance_question_html

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "instance_question"


def _load(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def test_multiple_choice_blank():
    q = parse_instance_question_html(_load("multiple_choice_blank.html"))
    assert q.title == "Sample Multiple Choice Question"
    assert q.kind == "multiple_choice"
    assert "Which of these statements is true?" in q.prompt_text
    assert q.options == ["Option A text", "Option B text", "Option C text"]
    assert q.correct_option_indices == []
    assert q.answer_panel_text is None
    assert q.points == "1"


def test_multiple_choice_key():
    q = parse_instance_question_html(_load("multiple_choice_key.html"))
    assert q.kind == "multiple_choice"
    assert q.options == ["Option A text", "Option B text", "Option C text"]
    assert q.correct_option_indices == [1]
    assert q.answer_panel_text == "(B) Option B text"
    assert q.points == "1"


def test_multiple_choice_hidden_answer_key():
    """Answer panel with custom explanatory text (pl-hide-in-panel-suppressed default list)."""
    q = parse_instance_question_html(_load("multiple_choice_hidden_answer_key.html"))
    assert q.kind == "multiple_choice"
    # No <li> to match against options -> no bolding hint, but this must not raise.
    assert q.correct_option_indices == []
    assert q.answer_panel_text == (
        "Option B is correct because it is the only statement consistent with the setup described above."
    )
    assert q.points == "2"


def test_checkbox_blank():
    q = parse_instance_question_html(_load("checkbox_blank.html"))
    assert q.kind == "checkbox"
    assert q.options == ["Choice A text", "Choice B text", "Choice C text"]
    assert q.correct_option_indices == []
    assert q.answer_panel_text is None


def test_checkbox_key():
    q = parse_instance_question_html(_load("checkbox_key.html"))
    assert q.kind == "checkbox"
    assert q.correct_option_indices == [0, 2]
    assert q.answer_panel_text == "(A) Choice A text (C) Choice C text"


def test_string_input_blank():
    q = parse_instance_question_html(_load("string_input_blank.html"))
    assert q.kind == "string_input"
    assert "Type the name of the sample compound." in q.prompt_text
    assert q.options == []
    assert q.correct_option_indices == []
    assert q.answer_panel_text is None


def test_string_input_key():
    q = parse_instance_question_html(_load("string_input_key.html"))
    assert q.kind == "string_input"
    assert q.answer_panel_text == "sodium chloride"


def test_integer_input_blank():
    q = parse_instance_question_html(_load("integer_input_blank.html"))
    assert q.kind == "integer_input"
    assert "Enter the number of protons." in q.prompt_text
    assert q.answer_panel_text is None


def test_integer_input_key():
    q = parse_instance_question_html(_load("integer_input_key.html"))
    assert q.kind == "integer_input"
    assert q.answer_panel_text == "11"
    assert q.points == "1"


def test_unsupported_element_raises():
    html = """
    <div class="question-block">
      <div class="card-header"><h1>No supported input</h1></div>
      <div class="card-body question-body"><p>Just text, no widget.</p></div>
    </div>
    """
    with pytest.raises(UnsupportedElementError):
        parse_instance_question_html(html)


def test_compound_question_raises():
    html = """
    <div class="question-block">
      <div class="card-header"><h1>Compound question</h1></div>
      <div class="card-body question-body">
        <div class="form-check">
          <input type="radio" name="statement-0" value="a">
          <label><div class="pl-multiple-choice-answer">A0</div></label>
        </div>
        <div class="form-check">
          <input type="radio" name="statement-1" value="a">
          <label><div class="pl-multiple-choice-answer">A1</div></label>
        </div>
      </div>
    </div>
    """
    with pytest.raises(UnsupportedElementError):
        parse_instance_question_html(html)


def test_compound_dropdown_question_raises():
    """Same compound-question rejection, but for display="dropdown" (<select>) MC widgets.

    Regression test: an earlier version compared a fake constant sentinel
    instead of each <select>'s real `name`, so multiple separate dropdown
    widgets on one page (real content: `physical-or-chemical`, 3 statements
    each rendered as its own <select>) silently passed as a single
    non-compound question instead of being rejected.
    """
    html = """
    <div class="question-block">
      <div class="card-header"><h1>Compound dropdown question</h1></div>
      <div class="card-body question-body">
        <select name="statement-0"><option value="a">A0</option></select>
        <select name="statement-1"><option value="a">A1</option></select>
      </div>
    </div>
    """
    with pytest.raises(UnsupportedElementError):
        parse_instance_question_html(html)


def test_no_points_panel_gives_none():
    html = """
    <div class="question-block">
      <div class="card-header"><h1>No score panel</h1></div>
      <div class="card-body question-body">
        <span class="input-group pl-integer-input">
          <input class="form-control pl-integer-input-input" name="answer" type="text">
        </span>
      </div>
    </div>
    """
    q = parse_instance_question_html(html)
    assert q.points is None
