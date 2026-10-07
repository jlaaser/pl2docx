import re
from pathlib import Path

import pytest

from pl2docx.html_parser import (
    ImageRef,
    MathRef,
    SvgRef,
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


def _option_keys(widget) -> list[str | None]:
    return widget.option_keys


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
    q = parse_instance_question_html(_load("multiple_choice_key.html"), is_answer_key=True)
    widget = q.widgets[0]
    assert widget.kind == "multiple_choice"
    assert _options(widget) == ["Option A text", "Option B text", "Option C text"]
    assert _option_keys(widget) == ["a", "b", "c"]
    assert widget.correct_option_indices == [1]
    assert _answer(q) == "(B) Option B text"
    assert q.points == "1"


def test_multiple_choice_hidden_answer_key():
    """Answer panel with custom explanatory text (pl-hide-in-panel-suppressed default
    list) has no <li> for the old text-matching approach to find at all - but the
    "Variant" panel's JSON is independent of that suppression, so correct_option_indices
    must still resolve correctly via it (this was bug #1 of the two reported: the old
    approach silently gave up here)."""
    q = parse_instance_question_html(_load("multiple_choice_hidden_answer_key.html"), is_answer_key=True)
    widget = q.widgets[0]
    assert widget.kind == "multiple_choice"
    assert widget.correct_option_indices == [1]
    assert _answer(q) == (
        "Option B is correct because it is the only statement consistent with the setup described above."
    )
    assert q.points == "2"


def test_multiple_choice_correct_option_indices_suppressed_without_is_answer_key():
    """Regression: PL's staff "Variant" JSON panel is present even on blank/
    open-instance HTML (confirmed against real fetched HTML, not just this
    fixture) - resolving it unconditionally would leak the correct answer
    (bold_correct) onto the student's own blank copy. Parsing key HTML with
    is_answer_key left at its default (False) must suppress
    correct_option_indices entirely, even though the JSON panel is technically
    present in this fixture too."""
    q = parse_instance_question_html(_load("multiple_choice_key.html"))
    assert q.widgets[0].correct_option_indices == []


def test_math_blank():
    q = parse_instance_question_html(_load("math_blank.html"))
    all_nodes = [n for seg in q.prompt_segments for n in seg]
    math_nodes = [n for n in all_nodes if isinstance(n, MathRef)]
    assert [(n.latex, n.display_mode) for n in math_nodes] == [
        ("\\ce{CH4 + O2 -> CO2 + H2O}", False),
        ("PV = nRT", False),
        ("n", False),
        ("n = \\frac{PV}{RT}", True),
    ]
    assert q.widgets[0].kind == "number_input"


def test_math_key():
    q = parse_instance_question_html(_load("math_key.html"))
    answer_math = [n for n in q.answer_panel_text if isinstance(n, MathRef)]
    assert [(n.latex, n.display_mode) for n in answer_math] == [
        ("\\ce{CH4 + 2O2 -> CO2 + 2H2O}", False),
        ("n = 0.041", False),
    ]


def test_checkbox_blank():
    q = parse_instance_question_html(_load("checkbox_blank.html"))
    widget = q.widgets[0]
    assert widget.kind == "checkbox"
    assert _options(widget) == ["Choice A text", "Choice B text", "Choice C text"]
    assert widget.correct_option_indices == []
    assert q.answer_panel_text is None


def test_checkbox_key():
    q = parse_instance_question_html(_load("checkbox_key.html"), is_answer_key=True)
    widget = q.widgets[0]
    assert widget.kind == "checkbox"
    assert _option_keys(widget) == ["a", "b", "c"]
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


def test_no_widget_on_page_is_supported():
    """Phase 5 subphase 3: a page with no recognized input widget at all - e.g. a
    diagram-only question (SVG-only or a captured-canvas-only interactive element,
    already flattened to a plain <img> before parsing) - must parse successfully
    with an empty widgets list, not raise. Only missing .question-block/
    .question-body containers (a genuinely malformed page) still raise."""
    html = """
    <div class="question-block">
      <div class="card-header"><h1>No supported input</h1></div>
      <div class="card-body question-body"><p>Just text, no widget.</p></div>
    </div>
    """
    q = parse_instance_question_html(html)
    assert q.widgets == []
    assert len(q.prompt_segments) == 1
    assert "Just text, no widget." in plain_text(q.prompt_segments[0])


def test_missing_question_block_still_raises():
    with pytest.raises(UnsupportedElementError):
        parse_instance_question_html("<html><body>Not a real PL page.</body></html>")


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


def _true_answer_panel(json_text: str) -> str:
    return f"""
    <div class="card mb-3 border-warning">
      <div class="card-header bg-warning"><h2>Staff information</h2></div>
      <div class="list-group list-group-flush">
        <div class="list-group-item py-3">
          <h3 class="card-title h5">Variant</h3>
          <div class="d-flex flex-wrap">
            <details class="pe-1">
              <summary>Show/Hide answer</summary>
              <pre class="mt-2 mb-0"><code>{json_text}</code></pre>
            </details>
          </div>
        </div>
      </div>
    </div>
    """


def test_compound_dropdowns_with_overlapping_options_each_get_own_correct_answer():
    """Regression for the real reported bug: `physical-or-chemical` renders 3
    separate `pl-multiple-choice` dropdowns sharing the *identical* two options
    ("chemical property"/"physical property") - the old page-wide `.answer-body`
    text-matching approach could match one widget's answer key against a *different*
    widget's identical option text. Matching via the "Variant" JSON's per-`name` keys
    must keep each widget's correct_option_indices scoped to its own answer only."""
    html = (
        """
    <div class="question-block">
      <div class="card-header"><h1>Physical or chemical</h1></div>
      <div class="card-body question-body">
        <span class="pl-multiple-choice-dropdown">
          <select name="statement-0">
            <option value="a" data-content="(a) chemical property">chemical property</option>
            <option value="b" data-content="(b) physical property">physical property</option>
          </select>
        </span>
        <span class="pl-multiple-choice-dropdown">
          <select name="statement-1">
            <option value="a" data-content="(a) chemical property">chemical property</option>
            <option value="b" data-content="(b) physical property">physical property</option>
          </select>
        </span>
        <span class="pl-multiple-choice-dropdown">
          <select name="statement-2">
            <option value="a" data-content="(a) chemical property">chemical property</option>
            <option value="b" data-content="(b) physical property">physical property</option>
          </select>
        </span>
      </div>
    </div>
    """
        + _true_answer_panel(
            """{
  "statement-0": {"key": "b", "html": "physical property", "score": 1, "feedback": null},
  "statement-1": {"key": "a", "html": "chemical property", "score": 1, "feedback": null},
  "statement-2": {"key": "a", "html": "chemical property", "score": 1, "feedback": null}
}"""
        )
    )
    q = parse_instance_question_html(html, is_answer_key=True)
    by_name = {w.name: w for w in q.widgets}
    assert _options(by_name["statement-0"]) == ["chemical property", "physical property"]
    assert by_name["statement-0"].correct_option_indices == [1]
    assert by_name["statement-1"].correct_option_indices == [0]
    assert by_name["statement-2"].correct_option_indices == [0]


def test_multiple_choice_radio_no_variant_panel_yields_empty_correct_indices():
    """No "Variant" panel at all (e.g. a blank-copy fetch, or a page fetched under
    insufficient staff permissions) must degrade to no highlighting, not raise."""
    html = """
    <div class="question-block">
      <div class="card-header"><h1>No variant panel</h1></div>
      <div class="card-body question-body">
        <div class="form-check">
          <input type="radio" name="statement" value="a" id="a">
          <label class="form-check-label" for="a"><div class="pl-multiple-choice-answer">Option A</div></label>
        </div>
      </div>
    </div>
    """
    q = parse_instance_question_html(html)
    assert q.widgets[0].correct_option_indices == []


def _radio_question_html(extra: str = "") -> str:
    return f"""
    <div class="question-block">
      <div class="card-header"><h1>Variant panel edge cases</h1></div>
      <div class="card-body question-body">
        <div class="form-check">
          <input type="radio" name="statement" value="a" id="a">
          <label class="form-check-label" for="a"><div class="pl-multiple-choice-answer">Option A</div></label>
        </div>
      </div>
    </div>
    {extra}
    """


def test_variant_panel_malformed_json_yields_empty_correct_indices():
    html = _radio_question_html(_true_answer_panel("{not valid json"))
    q = parse_instance_question_html(html)
    assert q.widgets[0].correct_option_indices == []


def test_variant_panel_non_dict_json_yields_empty_correct_indices():
    """`variant.true_answer` can itself be JSON `null` (no named inputs) - must not raise."""
    html = _radio_question_html(_true_answer_panel("null"))
    q = parse_instance_question_html(html)
    assert q.widgets[0].correct_option_indices == []


def test_variant_panel_entry_missing_for_widget_name_yields_empty_correct_indices():
    html = _radio_question_html(_true_answer_panel('{"some-other-name": {"key": "a"}}'))
    q = parse_instance_question_html(html)
    assert q.widgets[0].correct_option_indices == []


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


def test_fill_in_width_chars_extracted_from_size_attribute():
    """PL's own `size` HTML attribute (real per-input character width, e.g.
    35 by default - `SIZE_DEFAULT` in every built-in fill-in element's own
    `.py` source) must be captured as `Widget.width_chars`."""
    html = """
    <div class="question-block">
      <div class="card-header"><h1>Sized fill-in</h1></div>
      <div class="card-body question-body">
        <span class="input-group pl-string-input">
          <input class="form-control pl-string-input-input" name="answer" type="text" size="8">
        </span>
      </div>
    </div>
    """
    q = parse_instance_question_html(html)
    assert q.widgets[0].width_chars == 8


def test_fill_in_width_chars_none_when_size_attribute_absent():
    html = """
    <div class="question-block">
      <div class="card-header"><h1>Unsized fill-in</h1></div>
      <div class="card-body question-body">
        <span class="input-group pl-string-input">
          <input class="form-control pl-string-input-input" name="answer" type="text">
        </span>
      </div>
    </div>
    """
    q = parse_instance_question_html(html)
    assert q.widgets[0].width_chars is None


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
    markup must detect zero widgets - not raise (Phase 5 subphase 3 - a page with
    no recognized widget is now supported), and not silently mis-detect the
    <math-field> as something it isn't."""
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
    q = parse_instance_question_html(html)
    assert q.widgets == []


def test_additional_fill_in_tag_not_detected_without_opt_in():
    """A fill-in-type element that isn't built-in must not be detected unless its
    tag is explicitly passed in additional_fill_in_tags - confirms the mechanism
    is genuinely opt-in, not accidentally always-on. Parses successfully with zero
    widgets (Phase 5 subphase 3) rather than raising."""
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
    q = parse_instance_question_html(html)
    assert q.widgets == []


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


def test_prompt_inline_style_color_and_bold_preserved():
    """Inline `style="color:...; font-weight:bold;"` (e.g.
    `templates/sigfigs-note.mustache`'s real red/blue significant-figures
    note) must resolve to TextRun.color/bold, not be silently dropped."""
    html = """
    <div class="question-block">
      <div class="card-header"><h1>Colored note</h1></div>
      <div class="card-body question-body">
        <p>Significant digits are shown in
          <span style="color:red; font-weight:bold;">red</span> and
          <span style="color:#0000ff">blue</span> (unbolded hex), plain otherwise.
        </p>
        <span class="input-group pl-integer-input">
          <input class="form-control pl-integer-input-input" name="answer" type="text">
        </span>
      </div>
    </div>
    """
    q = parse_instance_question_html(html)
    nodes = q.prompt_segments[0]

    red_nodes = [n for n in nodes if n.text == "red"]
    assert red_nodes and red_nodes[0].color == "FF0000"
    assert red_nodes[0].bold is True

    blue_nodes = [n for n in nodes if n.text == "blue"]
    assert blue_nodes and blue_nodes[0].color == "0000FF"
    assert blue_nodes[0].bold is False

    assert not any(n.color for n in nodes if n.text.strip() in ("Significant digits are shown in", "plain otherwise."))


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


def test_adjacent_p_tags_produce_single_paragraph_break():
    """Regression: whitespace-only text between "</p>" and the next "<p>"
    (ordinary source-formatting indentation) must not defeat the
    consecutive-ParagraphBreak collapse - confirmed by the user against real
    multi-paragraph answer-key content: this used to produce TWO
    ParagraphBreak nodes (an extra blank paragraph once rendered) instead of
    one."""
    from pl2docx.html_parser import ParagraphBreak

    html = """
    <div class="question-block">
      <div class="card-header"><h1>Adjacent paragraphs</h1></div>
      <div class="card-body question-body">
        <p>First paragraph.</p>
        <p>Second paragraph.</p>
        <span class="input-group pl-integer-input">
          <input class="form-control pl-integer-input-input" name="answer" type="text">
        </span>
      </div>
    </div>
    """
    q = parse_instance_question_html(html)
    nodes = q.prompt_segments[0]
    break_count = sum(1 for n in nodes if isinstance(n, ParagraphBreak))
    assert break_count == 1


def test_ordered_list_gets_position_and_ordered_flag():
    """<ol> items must carry ordered=True and their 1-based position, not just
    a flat unordered bullet - real course content (answer-panel explanations)
    uses numbered lists and expects real "1./2./3." numbering."""
    from pl2docx.html_parser import ListItemStart

    html = """
    <div class="question-block">
      <div class="card-header"><h1>Ordered list</h1></div>
      <div class="card-body question-body">
        <ol>
          <li>First</li>
          <li>Second</li>
          <li>Third</li>
        </ol>
        <span class="input-group pl-integer-input">
          <input class="form-control pl-integer-input-input" name="answer" type="text">
        </span>
      </div>
    </div>
    """
    q = parse_instance_question_html(html)
    items = [n for n in q.prompt_segments[0] if isinstance(n, ListItemStart)]
    assert [(n.ordered, n.index) for n in items] == [(True, 1), (True, 2), (True, 3)]


def test_unordered_list_has_no_position():
    from pl2docx.html_parser import ListItemStart

    html = """
    <div class="question-block">
      <div class="card-header"><h1>Unordered list</h1></div>
      <div class="card-body question-body">
        <ul>
          <li>Alpha</li>
          <li>Beta</li>
        </ul>
        <span class="input-group pl-integer-input">
          <input class="form-control pl-integer-input-input" name="answer" type="text">
        </span>
      </div>
    </div>
    """
    q = parse_instance_question_html(html)
    items = [n for n in q.prompt_segments[0] if isinstance(n, ListItemStart)]
    assert [n.ordered for n in items] == [False, False]


def test_list_item_containing_only_a_widget_keeps_its_marker():
    """Regression: a <li> whose sole content is a widget (real content: every
    pl-scinum-input item in TEST/pl-scinum-input) used to lose its list-item
    marker entirely - the ListItemStart landed exactly at a prompt-segment
    boundary and got stripped by the same trimming that (correctly) drops a
    stray leading/trailing ParagraphBreak there."""
    from pl2docx.html_parser import ListItemStart

    html = """
    <div class="question-block">
      <div class="card-header"><h1>List of widgets</h1></div>
      <div class="card-body question-body">
        <ol>
          <li>
            <span class="input-group pl-scinum-input">
              <span class="input-group-text">first:</span>
              <input class="form-control pl-scinum-input-input" name="a" type="text">
            </span>
          </li>
          <li>
            <span class="input-group pl-scinum-input">
              <span class="input-group-text">second:</span>
              <input class="form-control pl-scinum-input-input" name="b" type="text">
            </span>
          </li>
        </ol>
      </div>
    </div>
    """
    q = parse_instance_question_html(html, additional_fill_in_tags=["pl-scinum-input"])
    assert len(q.widgets) == 2
    # Each widget's own segment (the one immediately preceding it) ends with
    # its <li>'s ListItemStart, not empty.
    assert isinstance(q.prompt_segments[0][-1], ListItemStart)
    assert q.prompt_segments[0][-1].index == 1
    assert isinstance(q.prompt_segments[1][-1], ListItemStart)
    assert q.prompt_segments[1][-1].index == 2


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


def test_prompt_image_and_math_recognized():
    """The walker recognizes <img> as an ImageRef and $...$ math text as a
    MathRef (increment 3a: detection only - real OMML/image rendering is a
    later increment, see element_renderer's placeholder MathRef branch)."""
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
    math_nodes = [n for n in nodes if isinstance(n, MathRef)]
    assert len(math_nodes) == 1
    assert math_nodes[0].latex == "x^2"
    assert math_nodes[0].display_mode is False


def test_inline_svg_recognized_as_svg_ref():
    """Phase 5 subphase 1: an inline <svg> block (e.g. pl-lewisstructure's
    print="true" mode) becomes an SvgRef carrying its full outer markup, and
    the walker does not descend into its shape children (<line>/<circle>) as
    stray generic-tag content."""
    q = parse_instance_question_html(_load("svg_inline_blank.html"))
    nodes = q.prompt_segments[0]
    svg_nodes = [n for n in nodes if isinstance(n, SvgRef)]
    assert len(svg_nodes) == 1
    assert "<svg" in svg_nodes[0].svg_markup
    assert "<line" in svg_nodes[0].svg_markup
    assert svg_nodes[0].alt == "Lewis structure diagram"
    # No stray text from inside the <svg> leaked out as a separate node.
    text_only = plain_text([n for n in nodes if not isinstance(n, SvgRef)])
    assert "Draw the Lewis structure shown below:" in text_only


def test_inline_svg_without_aria_label_falls_back_to_generic_alt():
    html = """
    <div class="question-block">
      <div class="card-header"><h1>SVG no label</h1></div>
      <div class="card-body question-body">
        <p><svg viewBox="0 0 10 10" xmlns="http://www.w3.org/2000/svg"><rect width="10" height="10"/></svg></p>
        <span class="input-group pl-integer-input">
          <input class="form-control pl-integer-input-input" name="answer" type="text">
        </span>
      </div>
    </div>
    """
    q = parse_instance_question_html(html)
    svg_nodes = [n for n in q.prompt_segments[0] if isinstance(n, SvgRef)]
    assert len(svg_nodes) == 1
    assert svg_nodes[0].alt == "[diagram]"


def test_svg_ref_plain_text_uses_alt():
    node = SvgRef(svg_markup="<svg></svg>", alt="a diagram")
    assert plain_text([node]) == "a diagram"


def test_img_referencing_svg_file_still_produces_plain_image_ref():
    """<img src=*.svg> (e.g. via <pl-figure>, confirmed to always emit a bare
    <img> regardless of the referenced file's type) needs no parser change -
    the .svg-vs-raster distinction is handled entirely by the renderer, where
    the file actually gets read from disk."""
    q = parse_instance_question_html(_load("svg_img_blank.html"))
    images = [n for n in q.prompt_segments[0] if isinstance(n, ImageRef)]
    assert len(images) == 1
    assert images[0].local_path == "files/1_0_diagram.svg"
    assert not any(isinstance(n, SvgRef) for n in q.prompt_segments[0])


def test_math_delimiter_variants():
    html = """
    <div class="question-block">
      <div class="card-header"><h1>Math delimiters</h1></div>
      <div class="card-body question-body">
        <p>Inline $x^2$, alt-inline \\(y^2\\), display $$a+b$$, alt-display \\[c+d\\], escaped \\$5.</p>
        <span class="input-group pl-integer-input">
          <input class="form-control pl-integer-input-input" name="answer" type="text">
        </span>
      </div>
    </div>
    """
    q = parse_instance_question_html(html)
    nodes = q.prompt_segments[0]
    math_nodes = [n for n in nodes if isinstance(n, MathRef)]
    assert [(n.latex, n.display_mode) for n in math_nodes] == [
        ("x^2", False),
        ("y^2", False),
        ("a+b", True),
        ("c+d", True),
    ]
    # The escaped dollar sign is literal text, not a fifth MathRef.
    assert "$5" in plain_text(nodes)



def test_display_math_spanning_html_comment_is_one_equation():
    """An HTML comment inside `$$...$$` (real course content: a plain-text
    note after a chemical equation) must not split the equation - MathJax
    treats comments as empty text when finding delimiters. Previously each
    lone `$$` became an empty inline equation and the body leaked through as
    raw LaTeX text."""
    html = r"""
    <div class="question-block">
      <div class="card-header"><h1>Comment in math</h1></div>
      <div class="card-body question-body">
        <ol>
          <li>
            Combustion of coke (${\ce{C}}_{ \ce{(s)} }$):
            $$
                2\,{\ce{C}}_{ \ce{(s)} } \ce{ -&gt; } 2\,{\ce{CO}}_{ \ce{(g)} } <!--C(s) + O&#8322;(g) &#8594; CO(g)-->
            $$
          </li>
        </ol>
        <span class="input-group pl-integer-input">
          <input class="form-control pl-integer-input-input" name="answer" type="text">
        </span>
      </div>
    </div>
    """
    q = parse_instance_question_html(html)
    nodes = q.prompt_segments[0]
    math_nodes = [n for n in nodes if isinstance(n, MathRef)]
    assert [(n.latex.strip(), n.display_mode) for n in math_nodes] == [
        (r"{\ce{C}}_{ \ce{(s)} }", False),
        (r"2\,{\ce{C}}_{ \ce{(s)} } \ce{ -> } 2\,{\ce{CO}}_{ \ce{(g)} }", True),
    ]
    # No LaTeX source or stray delimiters leak through as ordinary text, and
    # the comment's own text is dropped.
    text = "".join(n.text for n in nodes if hasattr(n, "text") and not isinstance(n, MathRef))
    assert r"\ce" not in text and "$" not in text
    assert "O\u2082" not in text


def test_unmatched_double_dollar_is_literal_text_not_empty_math():
    html = """
    <div class="question-block">
      <div class="card-header"><h1>Stray $$</h1></div>
      <div class="card-body question-body">
        <p>Costs $$ and $x^2$ here.</p>
        <span class="input-group pl-integer-input">
          <input class="form-control pl-integer-input-input" name="answer" type="text">
        </span>
      </div>
    </div>
    """
    q = parse_instance_question_html(html)
    nodes = q.prompt_segments[0]
    assert [(n.latex, n.display_mode) for n in nodes if isinstance(n, MathRef)] == [("x^2", False)]
    assert "Costs $$ and" in plain_text(nodes)


def test_math_inside_bold_does_not_carry_bold_flag():
    html = """
    <div class="question-block">
      <div class="card-header"><h1>Math in bold</h1></div>
      <div class="card-body question-body">
        <p><strong>Solve $x^2$ now</strong></p>
        <span class="input-group pl-integer-input">
          <input class="form-control pl-integer-input-input" name="answer" type="text">
        </span>
      </div>
    </div>
    """
    q = parse_instance_question_html(html)
    nodes = q.prompt_segments[0]
    text_runs = [n for n in nodes if hasattr(n, "bold") and not isinstance(n, MathRef)]
    assert text_runs and all(t.bold for t in text_runs)
    math_nodes = [n for n in nodes if isinstance(n, MathRef)]
    assert len(math_nodes) == 1
    assert math_nodes[0].latex == "x^2"


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


# --- Phase 7: pl-rich-text-editor, pl-matching, pl-order-blocks, pl-big-o-input ---


def test_rich_text_editor_blank():
    q = parse_instance_question_html(_load("rich_text_editor_blank.html"))
    assert len(q.widgets) == 1
    widget = q.widgets[0]
    assert widget.kind == "rich_text_editor"
    assert widget.name == "essay_answer"
    assert widget.suppress_in_key is False
    assert q.answer_panel_text is None


def test_rich_text_editor_key_suppressed_when_is_answer_key():
    q = parse_instance_question_html(_load("rich_text_editor_key.html"), is_answer_key=True)
    widget = q.widgets[0]
    assert widget.kind == "rich_text_editor"
    assert widget.suppress_in_key is True
    # The page-wide answer panel is unaffected by suppression - an instructor's
    # freestanding model-answer text still comes through.
    assert _answer(q) == "A strong response should mention that the forward and reverse reaction rates become equal."


def test_rich_text_editor_key_not_suppressed_without_is_answer_key_flag():
    """Parsing key HTML without passing is_answer_key=True leaves the widget unsuppressed -
    is_answer_key is an explicit caller signal, not inferred from the HTML itself."""
    q = parse_instance_question_html(_load("rich_text_editor_key.html"))
    assert q.widgets[0].suppress_in_key is False


def test_matching_blank():
    q = parse_instance_question_html(_load("matching_blank.html"))
    assert len(q.widgets) == 1
    widget = q.widgets[0]
    assert widget.kind == "matching"
    assert widget.name == "matching_countries"
    assert [plain_text(s) for s in widget.statements] == ["United States", "France", "Mexico"]
    assert [plain_text(o) for o in widget.match_options] == ["Mexico City", "Paris", "Washington, D.C."]
    assert widget.counter_type == "decimal"
    assert widget.correct_labels == [None, None, None]


def test_matching_key_resolves_correct_labels_from_variant_json():
    q = parse_instance_question_html(_load("matching_key.html"), is_answer_key=True)
    widget = q.widgets[0]
    assert widget.kind == "matching"
    # true_answer["matching_countries"] = [2, 1, 0] (0-based option indices) ->
    # decimal-formatted 1-based labels "3"/"2"/"1", per statement order.
    assert widget.correct_labels == ["3", "2", "1"]


def test_matching_correct_labels_falls_back_to_html_when_json_unavailable():
    """With no Variant JSON at all (only the rendered .pl-matching-answer HTML,
    as if fetched by a viewer without the JSON-first route resolving), the
    HTML-scraping fallback must still resolve the same correct labels."""
    key_html = _load("matching_key.html")
    stripped = re.sub(
        r'<div class="card mb-3 border-warning">.*?</div>\s*$', "", key_html, flags=re.DOTALL
    )
    q = parse_instance_question_html(stripped, is_answer_key=True)
    assert q.widgets[0].correct_labels == ["3", "2", "1"]


def test_matching_correct_labels_suppressed_without_is_answer_key():
    """Same regression as
    test_multiple_choice_correct_option_indices_suppressed_without_is_answer_key,
    for matching's JSON-first correct-label resolution."""
    q = parse_instance_question_html(_load("matching_key.html"))
    assert q.widgets[0].correct_labels == [None, None, None]


def test_order_blocks_blank():
    q = parse_instance_question_html(_load("order_blocks_blank.html"))
    assert len(q.widgets) == 1
    widget = q.widgets[0]
    assert widget.kind == "order_blocks"
    assert [plain_text(b) for b in widget.blocks] == [
        "Add reactants to flask",
        "Heat to reflux",
        "Cool to room temperature",
        "Filter the precipitate",
    ]
    assert widget.correct_order is None


def test_order_blocks_key_resolves_correct_order_excluding_distractor():
    q = parse_instance_question_html(_load("order_blocks_key.html"), is_answer_key=True)
    widget = q.widgets[0]
    assert widget.kind == "order_blocks"
    # "Cool to room temperature" (pool index 2) is a distractor - present in the
    # pool but absent from the correct-answer panel, so it's excluded here.
    assert widget.correct_order == [0, 1, 3]


def test_order_blocks_correct_order_suppressed_without_is_answer_key():
    """Same regression as
    test_matching_correct_labels_suppressed_without_is_answer_key, for
    order_blocks' answer_body-based correct_order resolution."""
    q = parse_instance_question_html(_load("order_blocks_key.html"))
    assert q.widgets[0].correct_order is None


def test_big_o_input_detected_via_class_prefix_override():
    q = parse_instance_question_html(
        _load("big_o_input_blank.html"),
        additional_fill_in_tags=["pl-big-o-input"],
        additional_fill_in_class_prefixes={"pl-big-o-input": "big-o-input"},
    )
    assert len(q.widgets) == 1
    widget = q.widgets[0]
    assert widget.kind == "pl-big-o-input"
    assert widget.name == "answer"
    assert plain_text(widget.label) == "O("
    assert plain_text(widget.suffix) == ")"
    assert widget.width_chars == 20


def test_big_o_input_not_detected_without_class_prefix_override():
    """Without the override, pl-big-o-input's non-conforming class ("big-o-input-input",
    missing the usual "pl-" prefix) simply isn't matched - confirms the override is what
    makes detection work, not some other incidental match."""
    q = parse_instance_question_html(
        _load("big_o_input_blank.html"), additional_fill_in_tags=["pl-big-o-input"]
    )
    assert q.widgets == []


def test_big_o_input_key_answer():
    q = parse_instance_question_html(
        _load("big_o_input_key.html"),
        additional_fill_in_tags=["pl-big-o-input"],
        additional_fill_in_class_prefixes={"pl-big-o-input": "big-o-input"},
    )
    assert q.widgets[0].kind == "pl-big-o-input"
    assert _answer(q) == "The correct answer is O(log n)."
    assert format_points_text(None, None) is None
