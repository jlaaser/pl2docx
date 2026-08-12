from docxtpl import DocxTemplate

from pl2docx.element_config import ElementConfig, FillInPreferences, SelectorPreferences
from pl2docx.element_renderer import build_question_context
from pl2docx.html_parser import ParsedQuestion, Widget


def _tpl(starter_template) -> DocxTemplate:
    tpl = DocxTemplate(str(starter_template))
    tpl.init_docx()
    return tpl


def _mc_question(prompt=("Pick one.", "")):
    widget = Widget(
        kind="multiple_choice",
        name="statement",
        options=["Alpha", "Beta"],
        correct_option_indices=[1],
        is_inline=True,
    )
    return ParsedQuestion(
        title="Q",
        prompt_segments=list(prompt),
        widgets=[widget],
        answer_panel_text="(B) Beta",
        points="1",
        points_numeric=1.0,
        qid="q/1",
    )


def _string_question(label=None, suffix=None):
    widget = Widget(kind="string_input", name="answer", label=label, suffix=suffix)
    return ParsedQuestion(
        title="Q",
        prompt_segments=["Fill in:", ""],
        widgets=[widget],
        answer_panel_text=None,
        points="1",
        points_numeric=1.0,
        qid="q/2",
    )


def _text(subdoc) -> str:
    return "\n".join(p.text for p in subdoc.paragraphs)


def _has_run_border(subdoc) -> bool:
    return any("w:bdr" in run._element.xml for p in subdoc.paragraphs for run in p.runs)


def test_selector_inline_display_bold_correct(starter_template):
    tpl = _tpl(starter_template)
    element_config = ElementConfig(
        preferences={"multiple_choice": SelectorPreferences(list_style="letter-labels", display="inline")},
        behavior_class={},
    )
    ctx = build_question_context(tpl, _mc_question(), 1, element_config)
    text = _text(ctx["question_contents"])
    assert "(A) Alpha" in text
    assert "(B) Beta" in text
    assert "Pick one." in text
    # all on one paragraph (inline)
    assert len(ctx["question_contents"].paragraphs) == 1

    beta_run = next(r for p in ctx["question_contents"].paragraphs for r in p.runs if "Beta" in r.text)
    assert beta_run.bold is True


def test_selector_block_display_one_option_per_line(starter_template):
    tpl = _tpl(starter_template)
    element_config = ElementConfig(
        preferences={"multiple_choice": SelectorPreferences(list_style="bubble", display="block")},
        behavior_class={},
    )
    ctx = build_question_context(tpl, _mc_question(), 1, element_config)
    paragraphs = [p.text for p in ctx["question_contents"].paragraphs]
    assert any(p.strip() == "○ Alpha" for p in paragraphs)
    assert any(p.strip() == "○ Beta" for p in paragraphs)


def test_selector_draw_border_inline_uses_run_border(starter_template):
    tpl = _tpl(starter_template)
    element_config = ElementConfig(
        preferences={"multiple_choice": SelectorPreferences(display="inline", draw_border=True)},
        behavior_class={},
    )
    ctx = build_question_context(tpl, _mc_question(), 1, element_config)
    assert _has_run_border(ctx["question_contents"])
    # no table should be created for inline draw-border
    assert len(ctx["question_contents"].tables) == 0


def test_selector_draw_border_block_uses_run_border_not_table(starter_template):
    """draw-border must stay a tight, text-width run border for every display mode
    (including "block", one option per line) — a table-cell border was tried and
    rejected: it always spans the full page width regardless of the boxed text's
    actual width."""
    tpl = _tpl(starter_template)
    element_config = ElementConfig(
        preferences={"multiple_choice": SelectorPreferences(display="block", draw_border=True)},
        behavior_class={},
    )
    ctx = build_question_context(tpl, _mc_question(), 1, element_config)
    assert len(ctx["question_contents"].tables) == 0
    assert _has_run_border(ctx["question_contents"])
    paragraphs = [p.text for p in ctx["question_contents"].paragraphs]
    assert any("Alpha" in p for p in paragraphs)
    assert any("Beta" in p for p in paragraphs)


def test_fill_in_no_label_falls_back_to_answer_prefix(starter_template):
    tpl = _tpl(starter_template)
    element_config = ElementConfig(preferences={}, behavior_class={})
    ctx = build_question_context(tpl, _string_question(), 1, element_config)
    text = _text(ctx["question_contents"])
    assert "Answer: " in text


def test_fill_in_label_and_suffix_used(starter_template):
    tpl = _tpl(starter_template)
    element_config = ElementConfig(preferences={}, behavior_class={})
    ctx = build_question_context(tpl, _string_question(label="pH =", suffix="units"), 1, element_config)
    text = _text(ctx["question_contents"])
    assert "pH =" in text
    assert "units" in text
    assert "Answer:" not in text


def test_fill_in_template_display_routes_to_answer_element(starter_template):
    tpl = _tpl(starter_template)
    element_config = ElementConfig(
        preferences={"string_input": FillInPreferences(display="template")},
        behavior_class={},
    )
    ctx = build_question_context(tpl, _string_question(label="pH =", suffix="units"), 1, element_config)
    assert "pH =" not in _text(ctx["question_contents"])
    assert "pH =" in _text(ctx["answer_element"])


def test_fill_in_none_display_generated_then_discarded(starter_template):
    tpl = _tpl(starter_template)
    element_config = ElementConfig(
        preferences={"string_input": FillInPreferences(display="none")},
        behavior_class={},
    )
    ctx = build_question_context(tpl, _string_question(label="pH ="), 1, element_config)
    assert "pH =" not in _text(ctx["question_contents"])
    assert _text(ctx["answer_element"]).strip() == ""


def test_additional_element_fill_in_renders_end_to_end(starter_template):
    """A widget of a non-built-in kind (as produced for an additional-elements
    tag, e.g. pl-scinum-input) must render exactly like any other fill-in
    widget - build_question_context/element_renderer are kind-agnostic beyond
    the built-in selector-kind check."""
    tpl = _tpl(starter_template)
    widget = Widget(kind="pl-scinum-input", name="first", label="98.0:")
    question = ParsedQuestion(
        title="Q",
        prompt_segments=["Part A:", ""],
        widgets=[widget],
        answer_panel_text=None,
        points="1",
        points_numeric=1.0,
        qid="q/3",
    )
    element_config = ElementConfig(
        preferences={"pl-scinum-input": FillInPreferences(draw_border=True)},
        behavior_class={"pl-scinum-input": "fill-in"},
    )
    ctx = build_question_context(tpl, question, 1, element_config)
    text = _text(ctx["question_contents"])
    assert "98.0:" in text
    assert "Answer:" not in text
    assert _has_run_border(ctx["question_contents"])


def test_widgets_render_at_source_position():
    """Regression: widget content must appear between its own prompt segments,
    not be appended after the whole prompt."""
    from pl2docx.html_parser import parse_instance_question_html

    html = """
    <div class="question-block">
      <div class="card-header"><h1>Ordering</h1></div>
      <div class="card-body question-body">
        <p>Text A</p>
        <span class="input-group pl-string-input">
          <input class="form-control pl-string-input-input" name="answer" type="text">
        </span>
        <p>Text B</p>
      </div>
    </div>
    """
    q = parse_instance_question_html(html)
    assert q.prompt_segments == ["Text A", "Text B"]
