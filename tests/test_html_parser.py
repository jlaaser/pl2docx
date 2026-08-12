from pathlib import Path

import pytest

from pl2docx.html_parser import (
    ImageRef,
    MathRef,
    UnsupportedElementError,
    format_points_text,
    parse_instance_question_html,
    plain_text,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "instance_question"


def _load(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def _segments(q) -> list[str]:
    return [plain_text(seg) for seg in q.prompt_segments]


def _options(widget) -> list[str]:
    return [plain_text(opt) for opt in widget.options]


def _label(widget) -> str | None:
    return plain_text(widget.label) if widget.label is not None else None


def _suffix(widget) -> str | None:
    return plain_text(widget.suffix) if widget.suffix is not None else None


def _answer(q) -> str | None:
    return plain_text(q.answer_panel_text) if q.answer_panel_text is not None else None


def test_multiple_choice_blank():
    q = parse_instance_question_html(_load("multiple_choice_blank.html"))
    assert q.title == "Sample Multiple Choice Question"
    assert len(q.widgets) == 1
    widget = q.widgets[0]
    assert widget.kind == "multiple_choice"
    assert "Which of these statements is true?" in plain_text(q.prompt_segments[0])
    assert _options(widget) == ["Option A text", "Option B text", "Option C text"]
    assert widget.correct_option_indices == []
    assert q.answer_panel_text is None
    assert q.points == "1"
    assert q.points_numeric == 1.0
    assert q.qid is None  # this fixture's markup has no "Staff information" panel


def test_multiple_choice_key():
    q = parse_instance_question_html(_load("multiple_choice_key.html"))
    widget = q.widgets[0]
    assert widget.kind == "multiple_choice"
    assert _options(widget) == ["Option A text", "Option B text", "Option C text"]
    assert widget.correct_option_indices == [1]
    assert _answer(q) == "(B) Option B text"
    assert q.points == "1"


def test_multiple_choice_hidden_answer_key():
    """Answer panel with custom explanatory text (pl-hide-in-panel-suppressed default list)."""
    q = parse_instance_question_html(_load("multiple_choice_hidden_answer_key.html"))
    widget = q.widgets[0]
    assert widget.kind == "multiple_choice"
    # No <li> to match against options -> no bolding hint, but this must not raise.
    assert widget.correct_option_indices == []
    assert _answer(q) == (
        "Option B is correct because it is the only statement consistent with the setup described above."
    )
    assert q.points == "2"


def test_checkbox_blank():
    q = parse_instance_question_html(_load("checkbox_blank.html"))
    widget = q.widgets[0]
    assert widget.kind == "checkbox"
    assert _options(widget) == ["Choice A text", "Choice B text", "Choice C text"]
    assert widget.correct_option_indices == []
    assert q.answer_panel_text is None


def test_checkbox_key():
    q = parse_instance_question_html(_load("checkbox_key.html"))
    widget = q.widgets[0]
    assert widget.kind == "checkbox"
    assert widget.correct_option_indices == [0, 2]
    assert _answer(q) == "(A) Choice A text (C) Choice C text"


def test_string_input_blank():
    q = parse_instance_question_html(_load("string_input_blank.html"))
    widget = q.widgets[0]
    assert widget.kind == "string_input"
    assert "Type the name of the sample compound." in plain_text(q.prompt_segments[0])
    assert widget.options == []
    assert widget.correct_option_indices == []
    assert q.answer_panel_text is None


def test_string_input_key():
    q = parse_instance_question_html(_load("string_input_key.html"))
    assert q.widgets[0].kind == "string_input"
    assert _answer(q) == "sodium chloride"


def test_integer_input_blank():
    q = parse_instance_question_html(_load("integer_input_blank.html"))
    assert q.widgets[0].kind == "integer_input"
    assert "Enter the number of protons." in plain_text(q.prompt_segments[0])
    assert q.answer_panel_text is None


def test_integer_input_key():
    q = parse_instance_question_html(_load("integer_input_key.html"))
    assert q.widgets[0].kind == "integer_input"
    assert _answer(q) == "11"
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
    assert _options(q.widgets[0]) == ["A0"]
    assert _options(q.widgets[1]) == ["A1"]
    assert _segments(q) == ["Text A", "Text B", "Text C"]


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
    assert _options(q.widgets[0]) == ["A0"]
    assert _options(q.widgets[1]) == ["A1"]


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
    assert _label(widget) == "pH ="
    assert _suffix(widget) == "units"


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
    assert _label(widget) == "x ="
    assert _suffix(widget) == "m/s"


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
    assert _label(q.widgets[0]) == "98.0:"
    assert _label(q.widgets[1]) == "N_A:"
    assert _segments(q) == ["Part A:", "Part B:", ""]


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
    assert _segments(q) == ["Pick some.", ""]
    assert _options(q.widgets[0]) == ["Alpha"]


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
    assert "g/mol" in " ".join(_segments(q))


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


def test_rich_text_fixture_blank():
    q = parse_instance_question_html(_load("rich_text_blank.html"))
    nodes = q.prompt_segments[0]
    assert "limiting reagent" in plain_text(nodes)
    assert any(getattr(n, "bold", False) and "limiting reagent" in n.text for n in nodes)
    assert any(getattr(n, "italic", False) and "fully consumed" in n.text for n in nodes)
    assert any(getattr(n, "underline", False) and "in excess" in n.text for n in nodes)
    assert q.answer_panel_text is None


def test_rich_text_fixture_key():
    from pl2docx.html_parser import ListItemStart

    q = parse_instance_question_html(_load("rich_text_key.html"))
    answer_nodes = q.answer_panel_text
    assert answer_nodes is not None
    assert "Oxygen: 0 mol remaining" in plain_text(answer_nodes)
    assert any(getattr(n, "bold", False) and "limiting reagent" in n.text for n in answer_nodes)
    assert any(getattr(n, "italic", False) and n.text == "oxygen" for n in answer_nodes)
    assert any(isinstance(n, ListItemStart) for n in answer_nodes)


def test_prompt_bold_italic_underline_preserved():
    """Phase 4 increment 1: <strong>/<em>/<u> formatting must survive as node
    attributes, not be flattened away like Phase 2/3's get_text()-based parsing."""
    html = """
    <div class="question-block">
      <div class="card-header"><h1>Rich text</h1></div>
      <div class="card-body question-body">
        <p>This is <strong>bold</strong>, <em>italic</em>, and <u>underlined</u> text.</p>
        <span class="input-group pl-integer-input">
          <input class="form-control pl-integer-input-input" name="answer" type="text">
        </span>
      </div>
    </div>
    """
    q = parse_instance_question_html(html)
    nodes = q.prompt_segments[0]
    assert plain_text(nodes) == "This is bold, italic, and underlined text."

    bold_nodes = [n for n in nodes if getattr(n, "bold", False)]
    italic_nodes = [n for n in nodes if getattr(n, "italic", False)]
    underline_nodes = [n for n in nodes if getattr(n, "underline", False)]
    assert any(n.text == "bold" for n in bold_nodes)
    assert any(n.text == "italic" for n in italic_nodes)
    assert any(n.text == "underlined" for n in underline_nodes)
    # plain surrounding text must not pick up the formatting
    assert not any(getattr(n, "bold", False) for n in nodes if n.text.strip() == "This is")


def test_prompt_paragraphs_and_list_preserved():
    """Multiple <p> blocks and a <ul><li> list should produce ParagraphBreak/
    ListItemStart nodes at the right positions, not silently vanish."""
    from pl2docx.html_parser import ListItemStart, ParagraphBreak

    html = """
    <div class="question-block">
      <div class="card-header"><h1>Paragraphs and lists</h1></div>
      <div class="card-body question-body">
        <p>First paragraph.</p>
        <p>Second paragraph.</p>
        <ul>
          <li>Item one</li>
          <li>Item two</li>
        </ul>
        <span class="input-group pl-integer-input">
          <input class="form-control pl-integer-input-input" name="answer" type="text">
        </span>
      </div>
    </div>
    """
    q = parse_instance_question_html(html)
    nodes = q.prompt_segments[0]
    assert plain_text(nodes) == "First paragraph. Second paragraph. Item one Item two"
    assert any(isinstance(n, ParagraphBreak) for n in nodes)
    assert any(isinstance(n, ListItemStart) for n in nodes)


def test_image_width_px_parsed_from_width_attribute():
    """Phase 4 increment 2: the walker must capture <img width> so the renderer
    can size the embedded picture to PL's own intended display size."""
    html = """
    <div class="question-block">
      <div class="card-header"><h1>Image with width</h1></div>
      <div class="card-body question-body">
        <p><img src="files/1_0_diagram.png" alt="a diagram" width="150"></p>
        <span class="input-group pl-integer-input">
          <input class="form-control pl-integer-input-input" name="answer" type="text">
        </span>
      </div>
    </div>
    """
    q = parse_instance_question_html(html)
    images = [n for n in q.prompt_segments[0] if isinstance(n, ImageRef)]
    assert len(images) == 1
    assert images[0].width_px == 150


def test_image_missing_or_unparseable_width_gives_none():
    html = """
    <div class="question-block">
      <div class="card-header"><h1>Image without width</h1></div>
      <div class="card-body question-body">
        <p><img src="files/1_0_diagram.png" alt="a diagram"></p>
        <span class="input-group pl-integer-input">
          <input class="form-control pl-integer-input-input" name="answer" type="text">
        </span>
      </div>
    </div>
    """
    q = parse_instance_question_html(html)
    images = [n for n in q.prompt_segments[0] if isinstance(n, ImageRef)]
    assert images[0].width_px is None


def test_prompt_image_and_math_recognized_but_not_yet_rendered():
    """Phase 4 increment 1 scope: the walker recognizes <img> as an ImageRef and
    keeps raw $...$/$$...$$ math text as plain text (increments 2/3 handle real
    embedding/OMML conversion) - both must be recognized, not silently dropped."""
    html = """
    <div class="question-block">
      <div class="card-header"><h1>Image and math</h1></div>
      <div class="card-body question-body">
        <p>See <img src="files/1_0_diagram.png" alt="a diagram"> and $x^2$.</p>
        <span class="input-group pl-integer-input">
          <input class="form-control pl-integer-input-input" name="answer" type="text">
        </span>
      </div>
    </div>
    """
    q = parse_instance_question_html(html)
    nodes = q.prompt_segments[0]
    images = [n for n in nodes if isinstance(n, ImageRef)]
    assert len(images) == 1
    assert images[0].local_path == "files/1_0_diagram.png"
    assert images[0].alt == "a diagram"
    # Math detection itself is increment 3 scope - raw delimiters stay literal text for now.
    assert "$x^2$" in plain_text(nodes)
    assert not any(isinstance(n, MathRef) for n in nodes)


def test_option_and_label_rich_content_preserved():
    """Confirmed by the user: real course content has LaTeX in MC option text and
    fill-in label/suffix text, so these must carry rich nodes too, not plain str."""
    html = """
    <div class="question-block">
      <div class="card-header"><h1>Rich option and label</h1></div>
      <div class="card-body question-body">
        <div class="form-check">
          <input type="radio" name="answer" value="a">
          <label><div class="pl-multiple-choice-answer">The <strong>correct</strong> answer</div></label>
        </div>
        <span class="input-group pl-string-input">
          <span class="input-group-text"><em>pH</em> =</span>
          <input class="form-control pl-string-input-input" name="ph" type="text">
        </span>
      </div>
    </div>
    """
    q = parse_instance_question_html(html)
    mc_widget = next(w for w in q.widgets if w.kind == "multiple_choice")
    fill_in_widget = next(w for w in q.widgets if w.kind == "string_input")

    option_nodes = mc_widget.options[0]
    assert plain_text(option_nodes) == "The correct answer"
    assert any(getattr(n, "bold", False) and n.text == "correct" for n in option_nodes)

    label_nodes = fill_in_widget.label
    assert plain_text(label_nodes) == "pH ="
    assert any(getattr(n, "italic", False) and n.text == "pH" for n in label_nodes)


def test_format_points_text():
    assert format_points_text(1.0, "1") == "1 point"
    assert format_points_text(2.0, "2") == "2 points"
    assert format_points_text(1.5, "1.5") == "1.5 points"
    assert format_points_text(None, "up to 2") == "up to 2"
    assert format_points_text(None, None) is None
