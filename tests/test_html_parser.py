from pathlib import Path

import pytest

from pl2docx.html_parser import (
    UnsupportedElementError,
    format_points_text,
    parse_instance_question_html,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "instance_question"


def _load(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def test_multiple_choice_blank():
    q = parse_instance_question_html(_load("multiple_choice_blank.html"))
    assert q.title == "Sample Multiple Choice Question"
    assert len(q.widgets) == 1
    widget = q.widgets[0]
    assert widget.kind == "multiple_choice"
    assert "Which of these statements is true?" in q.prompt_segments[0]
    assert widget.options == ["Option A text", "Option B text", "Option C text"]
    assert widget.correct_option_indices == []
    assert q.answer_panel_text is None
    assert q.points == "1"
    assert q.points_numeric == 1.0
    assert q.qid is None  # this fixture's markup has no "Staff information" panel


def test_multiple_choice_key():
    q = parse_instance_question_html(_load("multiple_choice_key.html"))
    widget = q.widgets[0]
    assert widget.kind == "multiple_choice"
    assert widget.options == ["Option A text", "Option B text", "Option C text"]
    assert widget.correct_option_indices == [1]
    assert q.answer_panel_text == "(B) Option B text"
    assert q.points == "1"


def test_multiple_choice_hidden_answer_key():
    """Answer panel with custom explanatory text (pl-hide-in-panel-suppressed default list)."""
    q = parse_instance_question_html(_load("multiple_choice_hidden_answer_key.html"))
    widget = q.widgets[0]
    assert widget.kind == "multiple_choice"
    # No <li> to match against options -> no bolding hint, but this must not raise.
    assert widget.correct_option_indices == []
    assert q.answer_panel_text == (
        "Option B is correct because it is the only statement consistent with the setup described above."
    )
    assert q.points == "2"


def test_checkbox_blank():
    q = parse_instance_question_html(_load("checkbox_blank.html"))
    widget = q.widgets[0]
    assert widget.kind == "checkbox"
    assert widget.options == ["Choice A text", "Choice B text", "Choice C text"]
    assert widget.correct_option_indices == []
    assert q.answer_panel_text is None


def test_checkbox_key():
    q = parse_instance_question_html(_load("checkbox_key.html"))
    widget = q.widgets[0]
    assert widget.kind == "checkbox"
    assert widget.correct_option_indices == [0, 2]
    assert q.answer_panel_text == "(A) Choice A text (C) Choice C text"


def test_string_input_blank():
    q = parse_instance_question_html(_load("string_input_blank.html"))
    widget = q.widgets[0]
    assert widget.kind == "string_input"
    assert "Type the name of the sample compound." in q.prompt_segments[0]
    assert widget.options == []
    assert widget.correct_option_indices == []
    assert q.answer_panel_text is None


def test_string_input_key():
    q = parse_instance_question_html(_load("string_input_key.html"))
    assert q.widgets[0].kind == "string_input"
    assert q.answer_panel_text == "sodium chloride"


def test_integer_input_blank():
    q = parse_instance_question_html(_load("integer_input_blank.html"))
    assert q.widgets[0].kind == "integer_input"
    assert "Enter the number of protons." in q.prompt_segments[0]
    assert q.answer_panel_text is None


def test_integer_input_key():
    q = parse_instance_question_html(_load("integer_input_key.html"))
    assert q.widgets[0].kind == "integer_input"
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


def test_compound_question_is_supported():
    """Phase 3B: a compound question (multiple named widget groups) parses into
    multiple `Widget`s, in source order, rather than raising."""
    html = """
    <div class="question-block">
      <div class="card-header"><h1>Compound question</h1></div>
      <div class="card-body question-body">
        <p>Text A</p>
        <div class="form-check form-check-inline">
          <input type="radio" name="statement-0" value="a">
          <label><div class="pl-multiple-choice-answer">A0</div></label>
        </div>
        <p>Text B</p>
        <div class="form-check form-check-inline">
          <input type="radio" name="statement-1" value="a">
          <label><div class="pl-multiple-choice-answer">A1</div></label>
        </div>
        <p>Text C</p>
      </div>
    </div>
    """
    q = parse_instance_question_html(html)
    assert [w.name for w in q.widgets] == ["statement-0", "statement-1"]
    assert q.widgets[0].options == ["A0"]
    assert q.widgets[1].options == ["A1"]
    assert q.prompt_segments == ["Text A", "Text B", "Text C"]


def test_compound_dropdown_question_is_supported():
    """Same compound-question support, but for display="dropdown" (<select>) MC widgets.

    Regression coverage carried over from the Phase 2 rejection test: multiple
    separate dropdown widgets on one page (real content: `physical-or-chemical`,
    3 statements each rendered as its own <select>) must be recognized as
    distinct widgets (grouped by each <select>'s real `name`), not merged into
    one.
    """
    html = """
    <div class="question-block">
      <div class="card-header"><h1>Compound dropdown question</h1></div>
      <div class="card-body question-body">
        <span class="pl-multiple-choice-dropdown">
          <select name="statement-0"><option value="a" data-content="(a) A0">A0</option></select>
        </span>
        <span class="pl-multiple-choice-dropdown">
          <select name="statement-1"><option value="a" data-content="(a) A1">A1</option></select>
        </span>
      </div>
    </div>
    """
    q = parse_instance_question_html(html)
    assert [w.name for w in q.widgets] == ["statement-0", "statement-1"]
    assert all(w.is_dropdown for w in q.widgets)
    assert q.widgets[0].options == ["A0"]
    assert q.widgets[1].options == ["A1"]


def test_fill_in_label_and_suffix_extracted():
    html = """
    <div class="question-block">
      <div class="card-header"><h1>Labeled fill-in</h1></div>
      <div class="card-body question-body">
        <span class="input-group pl-string-input">
          <span class="input-group-text">pH =</span>
          <input class="form-control pl-string-input-input" name="answer" type="text">
          <span class="input-group-text">units</span>
        </span>
      </div>
    </div>
    """
    q = parse_instance_question_html(html)
    widget = q.widgets[0]
    assert widget.label == "pH ="
    assert widget.suffix == "units"


def test_number_input_detected():
    """pl-number-input is a core PL element sharing string/integer-input's exact
    markup pattern - confirmed against pl-number-input.mustache."""
    html = """
    <div class="question-block">
      <div class="card-header"><h1>Number input</h1></div>
      <div class="card-body question-body">
        <span class="input-group pl-number-input">
          <span class="input-group-text">x =</span>
          <input class="form-control pl-number-input-input" name="answer" type="text">
          <span class="input-group-text">m/s</span>
        </span>
      </div>
    </div>
    """
    q = parse_instance_question_html(html)
    widget = q.widgets[0]
    assert widget.kind == "number_input"
    assert widget.label == "x ="
    assert widget.suffix == "m/s"


def test_units_input_detected():
    """pl-units-input is a core PL element sharing the same markup pattern -
    confirmed against pl-units-input.mustache."""
    html = """
    <div class="question-block">
      <div class="card-header"><h1>Units input</h1></div>
      <div class="card-body question-body">
        <span class="input-group pl-units-input">
          <input class="form-control pl-units-input-input" name="speed" type="text">
        </span>
      </div>
    </div>
    """
    q = parse_instance_question_html(html)
    assert q.widgets[0].kind == "units_input"


def test_symbolic_input_plain_mode_detected():
    """pl-symbolic-input's non-formula-editor branch is a plain <input> following
    the shared pattern - confirmed against pl-symbolic-input.mustache."""
    html = """
    <div class="question-block">
      <div class="card-header"><h1>Symbolic input</h1></div>
      <div class="card-body question-body">
        <span class="input-group pl-symbolic-input">
          <input class="form-control pl-symbolic-input-input" name="expr" type="text">
        </span>
      </div>
    </div>
    """
    q = parse_instance_question_html(html)
    assert q.widgets[0].kind == "symbolic_input"


def test_symbolic_input_formula_editor_mode_not_detected():
    """Regression: pl-symbolic-input's formula_editor mode renders a JS-populated
    <math-field> custom element (carrying the pl-symbolic-input-input class, but a
    different tag name) alongside hidden <input>s that actually carry the name -
    confirmed against pl-symbolic-input.mustache. Restricting detection to real
    <input>/<textarea> tags must exclude the <math-field>, so a page with only this
    markup should fail to detect any widget (UnsupportedElementError), not silently
    produce an empty/unusable one."""
    html = """
    <div class="question-block">
      <div class="card-header"><h1>Formula editor</h1></div>
      <div class="card-body question-body">
        <span class="input-group pl-symbolic-input">
          <input type="hidden" name="expr">
          <input type="hidden" name="expr-latex">
          <math-field class="form-control pl-symbolic-input-input"></math-field>
        </span>
      </div>
    </div>
    """
    with pytest.raises(UnsupportedElementError):
        parse_instance_question_html(html)


def test_additional_fill_in_tag_not_detected_without_opt_in():
    """A fill-in-type element that isn't built-in must not be detected unless its
    tag is explicitly passed in additional_fill_in_tags - confirms the mechanism
    is genuinely opt-in, not accidentally always-on."""
    html = """
    <div class="question-block">
      <div class="card-header"><h1>Scinum</h1></div>
      <div class="card-body question-body">
        <span class="input-group pl-scinum-input">
          <input class="form-control pl-scinum-input-input" name="first" type="text">
        </span>
      </div>
    </div>
    """
    with pytest.raises(UnsupportedElementError):
        parse_instance_question_html(html)


def test_additional_fill_in_tag_compound_question():
    """Modeled on the real course-repo TEST/pl-scinum-input question (a course-
    specific element, not core PL), which has 7 separate named pl-scinum-input
    widgets on one page - exercises both the generic additional-elements
    detection pathway and compound-question grouping together, matching the
    real content this is meant to support."""
    html = """
    <div class="question-block">
      <div class="card-header"><h1>TEST/pl-scinum-input</h1></div>
      <div class="card-body question-body">
        <p>Part A:</p>
        <span class="input-group pl-scinum-input">
          <span class="input-group-text">98.0:</span>
          <input class="form-control pl-scinum-input-input" name="first" type="text">
        </span>
        <p>Part B:</p>
        <span class="input-group pl-scinum-input">
          <span class="input-group-text">N_A:</span>
          <input class="form-control pl-scinum-input-input" name="second" type="text">
        </span>
      </div>
    </div>
    """
    q = parse_instance_question_html(html, additional_fill_in_tags=["pl-scinum-input"])
    assert [w.kind for w in q.widgets] == ["pl-scinum-input", "pl-scinum-input"]
    assert [w.name for w in q.widgets] == ["first", "second"]
    assert q.widgets[0].label == "98.0:"
    assert q.widgets[1].label == "N_A:"
    assert q.prompt_segments == ["Part A:", "Part B:", ""]


def test_checkbox_help_text_and_hidden_legend_stripped():
    """Regression: pl-checkbox injects a `<small class="form-text text-muted">`
    (Python-generated, e.g. "Select all possible options that apply.") and a
    screen-reader-only `<legend class="visually-hidden">Checkbox options</legend>`,
    neither nested inside any option's own .form-check container, so both used to
    leak into prompt_segments as if they were authored question text."""
    html = """
    <div class="question-block">
      <div class="card-header"><h1>Checkbox with help text</h1></div>
      <div class="card-body question-body">
        <p>Pick some.</p>
        <fieldset class="d-block"><legend class="visually-hidden">Checkbox options</legend>
          <div class="form-check">
            <input type="checkbox" name="c" value="a">
            <label><div class="pl-checkbox-answer">Alpha</div></label>
          </div>
        </fieldset>
        <div class="d-flex align-items-center gap-2">
          <span><small class="form-text text-muted">Select all possible options that apply.</small></span>
        </div>
      </div>
    </div>
    """
    q = parse_instance_question_html(html)
    assert q.prompt_segments == ["Pick some.", ""]
    assert q.widgets[0].options == ["Alpha"]


def test_bare_form_text_class_not_stripped():
    """pl-string-input's suffix div uses a bare `form-text` class (no `text-muted`)
    for real question content (e.g. units) - the help-text strip must match on
    `text-muted` specifically, not on `form-text` alone."""
    html = """
    <div class="question-block">
      <div class="card-header"><h1>Bare form-text</h1></div>
      <div class="card-body question-body">
        <p>Report your answer in <span class="form-text">g/mol</span>.</p>
        <span class="input-group pl-integer-input">
          <input class="form-control pl-integer-input-input" name="answer" type="text">
        </span>
      </div>
    </div>
    """
    q = parse_instance_question_html(html)
    assert "g/mol" in " ".join(q.prompt_segments)


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
    assert q.points_numeric is None
    assert q.qid is None


def test_qid_extracted_when_staff_info_panel_present():
    html = """
    <div class="question-block">
      <div class="card-header"><h1>Has a staff info panel</h1></div>
      <div class="card-body question-body">
        <span class="input-group pl-integer-input">
          <input class="form-control pl-integer-input-input" name="answer" type="text">
        </span>
      </div>
    </div>
    <div class="pe-1">QID:</div>
    <div><a href="/pl/course_instance/1/instructor/question/47">TEST/pl-integer-input</a></div>
    """
    q = parse_instance_question_html(html)
    assert q.qid == "TEST/pl-integer-input"


def test_format_points_text():
    assert format_points_text(1.0, "1") == "1 point"
    assert format_points_text(2.0, "2") == "2 points"
    assert format_points_text(1.5, "1.5") == "1.5 points"
    assert format_points_text(None, "up to 2") == "up to 2"
    assert format_points_text(None, None) is None
