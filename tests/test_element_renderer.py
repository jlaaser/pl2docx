import re
from pathlib import Path

from docx.shared import Inches
from docxtpl import DocxTemplate

from pl2docx.element_config import ElementConfig, FillInPreferences, SelectorPreferences
from pl2docx.element_renderer import build_question_context
from pl2docx.html_parser import ImageRef, ParsedQuestion, Widget, plain

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "instance_question"


def _tpl(starter_template) -> DocxTemplate:
    tpl = DocxTemplate(str(starter_template))
    tpl.init_docx()
    return tpl


def _mc_question(prompt=("Pick one.", "")):
    widget = Widget(
        kind="multiple_choice",
        name="statement",
        options=[plain("Alpha"), plain("Beta")],
        correct_option_indices=[1],
        is_inline=True,
    )
    return ParsedQuestion(
        title="Q",
        prompt_segments=[plain(p) for p in prompt],
        widgets=[widget],
        answer_panel_text=plain("(B) Beta"),
        points="1",
        points_numeric=1.0,
        qid="q/1",
    )


def _string_question(label=None, suffix=None):
    widget = Widget(
        kind="string_input",
        name="answer",
        label=plain(label) if label is not None else None,
        suffix=plain(suffix) if suffix is not None else None,
    )
    return ParsedQuestion(
        title="Q",
        prompt_segments=[plain("Fill in:"), plain("")],
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
    widget = Widget(kind="pl-scinum-input", name="first", label=plain("98.0:"))
    question = ParsedQuestion(
        title="Q",
        prompt_segments=[plain("Part A:"), plain("")],
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
    from pl2docx.html_parser import parse_instance_question_html, plain_text

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
    assert [plain_text(seg) for seg in q.prompt_segments] == ["Text A", "Text B"]


def test_rich_text_fixture_renders_end_to_end(starter_template):
    """The rich_text_key.html fixture's bold/italic answer-panel content must
    survive all the way through build_question_context into real docx runs."""
    from pathlib import Path

    from pl2docx.html_parser import parse_instance_question_html

    fixtures_dir = Path(__file__).parent / "fixtures" / "instance_question"
    html = (fixtures_dir / "rich_text_key.html").read_text(encoding="utf-8")
    question = parse_instance_question_html(html)

    tpl = _tpl(starter_template)
    element_config = ElementConfig(preferences={}, behavior_class={})
    ctx = build_question_context(tpl, question, 1, element_config)

    answer_text = _text(ctx["answer_contents"])
    assert "limiting reagent" in answer_text
    assert "Oxygen: 0 mol remaining" in answer_text

    bold_run = next(
        r
        for p in ctx["answer_contents"].paragraphs
        for r in p.runs
        if "limiting reagent" in r.text
    )
    assert bold_run.bold is True
    italic_run = next(
        r for p in ctx["answer_contents"].paragraphs for r in p.runs if r.text.strip() == "oxygen"
    )
    assert italic_run.italic is True


def test_prompt_bold_run_rendered_with_real_bold_formatting(starter_template):
    """Phase 4 increment 1: a bold TextRun node in a prompt segment must produce
    a real bold run in the rendered subdoc, not just plain concatenated text."""
    from pl2docx.html_parser import TextRun

    tpl = _tpl(starter_template)
    widget = Widget(kind="integer_input", name="answer")
    question = ParsedQuestion(
        title="Q",
        prompt_segments=[
            [TextRun("This is "), TextRun("bold", bold=True), TextRun(" text.")],
            plain(""),
        ],
        widgets=[widget],
        answer_panel_text=None,
        points="1",
        points_numeric=1.0,
        qid="q/4",
    )
    element_config = ElementConfig(preferences={}, behavior_class={})
    ctx = build_question_context(tpl, question, 1, element_config)
    text = _text(ctx["question_contents"])
    assert "This is bold text." in text

    bold_run = next(
        r for p in ctx["question_contents"].paragraphs for r in p.runs if r.text.strip() == "bold"
    )
    assert bold_run.bold is True
    non_bold_runs = [
        r for p in ctx["question_contents"].paragraphs for r in p.runs if "This is" in r.text
    ]
    assert all(not r.bold for r in non_bold_runs)


def _inline_shape_count(subdoc) -> int:
    """Count real embedded pictures in `subdoc`.

    `Subdoc.inline_shapes` (a docxtpl passthrough to the underlying
    python-docx `Document`) doesn't reliably reflect pictures added via
    `Run.add_picture()` on paragraphs docxtpl created - confirmed empty even
    when a `<w:drawing>` element genuinely is present in a run's XML.
    Checking the run XML directly is what actually works.
    """
    return sum(
        1
        for p in subdoc.paragraphs
        for r in p.runs
        if "<w:drawing>" in r._element.xml
    )


def test_image_embeds_as_real_picture_when_base_dir_given(starter_template):
    """Phase 4 increment 2: an ImageRef must become a real embedded picture
    (not the "[image]" fallback text) when image_base_dir resolves to a real
    file - uses the image_blank.html fixture + its sibling files/tiny_dot.png."""
    from pl2docx.html_parser import parse_instance_question_html

    html = (FIXTURES_DIR / "image_blank.html").read_text(encoding="utf-8")
    question = parse_instance_question_html(html)

    tpl = _tpl(starter_template)
    element_config = ElementConfig(preferences={}, behavior_class={})
    ctx = build_question_context(tpl, question, 1, element_config, image_base_dir=FIXTURES_DIR)

    assert _inline_shape_count(ctx["question_contents"]) == 1
    assert "[image]" not in _text(ctx["question_contents"])

    # width="150" in the source <img> -> 150/96 inch, in EMU - checked via the
    # raw <wp:extent cx="..."> XML since Subdoc.inline_shapes isn't reliable
    # here (see _inline_shape_count).
    picture_run = next(
        r
        for p in ctx["question_contents"].paragraphs
        for r in p.runs
        if "<w:drawing>" in r._element.xml
    )
    match = re.search(r'<wp:extent cx="(\d+)"', picture_run._element.xml)
    assert match is not None
    assert int(match.group(1)) == int(Inches(150 / 96))


def test_image_falls_back_to_alt_text_without_image_base_dir(starter_template):
    """No image_base_dir given (the pre-increment-2 default) must keep the
    old alt-text fallback behavior, not attempt to embed anything."""
    from pl2docx.html_parser import parse_instance_question_html

    html = (FIXTURES_DIR / "image_blank.html").read_text(encoding="utf-8")
    question = parse_instance_question_html(html)

    tpl = _tpl(starter_template)
    element_config = ElementConfig(preferences={}, behavior_class={})
    ctx = build_question_context(tpl, question, 1, element_config)

    assert _inline_shape_count(ctx["question_contents"]) == 0
    assert "a red dot" in _text(ctx["question_contents"])


def test_image_falls_back_to_alt_text_when_file_missing(starter_template):
    """A referenced image that isn't actually on disk (e.g. an external image
    fetch.py deliberately didn't download) must fall back gracefully, not
    raise and fail the whole render."""
    tpl = _tpl(starter_template)
    widget = Widget(kind="integer_input", name="answer")
    question = ParsedQuestion(
        title="Q",
        prompt_segments=[
            [ImageRef(local_path="files/does_not_exist.png", alt="missing image")],
            plain(""),
        ],
        widgets=[widget],
        answer_panel_text=None,
        points="1",
        points_numeric=1.0,
        qid="q/5",
    )
    element_config = ElementConfig(preferences={}, behavior_class={})
    ctx = build_question_context(tpl, question, 1, element_config, image_base_dir=FIXTURES_DIR)

    assert _inline_shape_count(ctx["question_contents"]) == 0
    assert "missing image" in _text(ctx["question_contents"])
