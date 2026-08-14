"""Regression tests for two formatting fixes:

1. No synthetic space between adjacent, differently-formatted rich-text nodes
   that had no space between them in the source (e.g. "...in <b>red</b>. Numbers...").
2. Configurable left indent for block-display widget content (selector/fill-in
   "block" display, pl-matching, pl-order-blocks).

Kept as a separate module (same reasoning as test_element_renderer_phase7.py)
purely to avoid ambiguous-anchor edits against test_element_renderer.py's
several near-identical trailing assertions.
"""

from docxtpl import DocxTemplate

from pl2docx.element_config import ElementConfig, FillInPreferences, OrderBlocksPreferences, SelectorPreferences
from pl2docx.element_renderer import build_question_context
from pl2docx.html_parser import ImageRef, ParsedQuestion, TextRun, Widget, plain


def _tpl(starter_template) -> DocxTemplate:
    tpl = DocxTemplate(str(starter_template))
    tpl.init_docx()
    return tpl


def _text(subdoc) -> str:
    return "\n".join(p.text for p in subdoc.paragraphs)


# --- No spurious space between adjacent differently-formatted runs ---


def _no_widgets_question(prompt_nodes):
    return ParsedQuestion(
        title="Q",
        prompt_segments=[prompt_nodes],
        widgets=[],
        answer_panel_text=None,
        points="1",
        points_numeric=1.0,
        qid="q/spacing",
    )


def test_no_space_inserted_between_adjacent_differently_colored_runs(starter_template):
    """Source: '...indicated in <span style="color:red">red</span>. Numbers...' -
    no space between the colored "red" run and the following ".", so none should
    be synthesized on render."""
    tpl = _tpl(starter_template)
    nodes = [
        TextRun("indicated in "),
        TextRun("red", color="FF0000"),
        TextRun(". Numbers that do not"),
    ]
    ctx = build_question_context(tpl, _no_widgets_question(nodes), 1, ElementConfig(preferences={}, behavior_class={}))
    assert _text(ctx["question_contents"]) == "indicated in red. Numbers that do not"


def test_space_preserved_when_source_already_has_one(starter_template):
    """Contrast case: when the source *does* have a space between differently-
    formatted runs, exactly one space must survive (not zero, not two)."""
    tpl = _tpl(starter_template)
    nodes = [
        TextRun("a bolded "),
        TextRun("word", bold=True),
        TextRun(" continues"),
    ]
    ctx = build_question_context(tpl, _no_widgets_question(nodes), 1, ElementConfig(preferences={}, behavior_class={}))
    assert _text(ctx["question_contents"]) == "a bolded word continues"


def test_no_space_inserted_around_adjacent_image(starter_template, tmp_path):
    """Source: "(<img>)" with no space on either side (e.g. an inline chemical
    formula image between parentheses) - none should be synthesized."""
    tpl = _tpl(starter_template)
    png_path = tmp_path / "tiny.png"
    # 1x1 transparent PNG.
    png_path.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
            "890000000a49444154789c6360000002000100ffff03000006000557bfabd400"
            "0000004945"
        )
    )
    nodes = [TextRun("("), ImageRef(local_path="tiny.png"), TextRun(") is shown")]
    ctx = build_question_context(
        tpl, _no_widgets_question(nodes), 1, ElementConfig(preferences={}, behavior_class={}),
        image_base_dir=tmp_path,
    )
    subdoc = ctx["question_contents"]
    paragraph = subdoc.paragraphs[0]
    texts = [r.text for r in paragraph.runs]
    assert texts[0] == "("
    assert texts[-1] == ") is shown"


# --- Configurable block-display indentation ---


def _mc_question():
    widget = Widget(
        kind="multiple_choice",
        name="statement",
        options=[plain("Alpha"), plain("Beta")],
        is_inline=False,
    )
    return ParsedQuestion(
        title="Q",
        prompt_segments=[plain("Pick one."), plain("")],
        widgets=[widget],
        answer_panel_text=None,
        points="1",
        points_numeric=1.0,
        qid="q/indent",
    )


def test_block_display_selector_gets_default_indent(starter_template):
    tpl = _tpl(starter_template)
    element_config = ElementConfig(
        preferences={"multiple_choice": SelectorPreferences(list_style="bubble", display="block")},
        behavior_class={},
    )
    ctx = build_question_context(tpl, _mc_question(), 1, element_config)
    option_paragraphs = [p for p in ctx["question_contents"].paragraphs if p.text.strip().endswith(("Alpha", "Beta"))]
    assert len(option_paragraphs) == 2
    for p in option_paragraphs:
        assert p.paragraph_format.left_indent is not None
        assert round(p.paragraph_format.left_indent.inches, 3) == 0.125


def test_block_display_indent_configurable(starter_template):
    tpl = _tpl(starter_template)
    element_config = ElementConfig(
        preferences={"multiple_choice": SelectorPreferences(list_style="bubble", display="block")},
        behavior_class={},
    )
    ctx = build_question_context(
        tpl, _mc_question(), 1, element_config, block_display_indent_inches=0.5
    )
    option_paragraphs = [p for p in ctx["question_contents"].paragraphs if p.text.strip().endswith(("Alpha", "Beta"))]
    for p in option_paragraphs:
        assert round(p.paragraph_format.left_indent.inches, 3) == 0.5


def test_block_display_indent_disabled_with_zero(starter_template):
    tpl = _tpl(starter_template)
    element_config = ElementConfig(
        preferences={"multiple_choice": SelectorPreferences(list_style="bubble", display="block")},
        behavior_class={},
    )
    ctx = build_question_context(
        tpl, _mc_question(), 1, element_config, block_display_indent_inches=0
    )
    option_paragraphs = [p for p in ctx["question_contents"].paragraphs if p.text.strip().endswith(("Alpha", "Beta"))]
    for p in option_paragraphs:
        assert p.paragraph_format.left_indent is None


def test_inline_display_selector_not_indented(starter_template):
    tpl = _tpl(starter_template)
    element_config = ElementConfig(
        preferences={"multiple_choice": SelectorPreferences(list_style="bubble", display="inline")},
        behavior_class={},
    )
    ctx = build_question_context(tpl, _mc_question(), 1, element_config)
    for p in ctx["question_contents"].paragraphs:
        assert p.paragraph_format.left_indent is None


def _fill_in_block_question():
    widget = Widget(kind="string_input", name="answer")
    return ParsedQuestion(
        title="Q",
        prompt_segments=[plain("Fill in:"), plain("")],
        widgets=[widget],
        answer_panel_text=None,
        points="1",
        points_numeric=1.0,
        qid="q/indent2",
    )


def test_fill_in_block_display_gets_default_indent(starter_template):
    tpl = _tpl(starter_template)
    element_config = ElementConfig(
        preferences={"string_input": FillInPreferences(display="block")}, behavior_class={}
    )
    ctx = build_question_context(tpl, _fill_in_block_question(), 1, element_config)
    blank_paragraphs = [p for p in ctx["question_contents"].paragraphs if "_" in p.text]
    assert len(blank_paragraphs) == 1
    assert round(blank_paragraphs[0].paragraph_format.left_indent.inches, 3) == 0.125


def _matching_question():
    widget = Widget(
        kind="matching",
        name="matching_countries",
        statements=[plain("United States")],
        match_options=[plain("Washington, D.C.")],
        counter_type="decimal",
        correct_labels=[None],
    )
    return ParsedQuestion(
        title="Q",
        prompt_segments=[plain("Match."), plain("")],
        widgets=[widget],
        answer_panel_text=None,
        points="1",
        points_numeric=1.0,
        qid="q/indent3",
    )


def test_matching_table_gets_default_indent(starter_template):
    tpl = _tpl(starter_template)
    element_config = ElementConfig(preferences={}, behavior_class={})
    ctx = build_question_context(tpl, _matching_question(), 1, element_config)
    table = ctx["question_contents"].tables[0]
    xml = table._tbl.tblPr.xml
    assert 'w:tblInd' in xml
    assert 'w:w="180"' in xml  # 0.125in * 1440 twips/in


def test_matching_table_indent_disabled_with_zero(starter_template):
    tpl = _tpl(starter_template)
    element_config = ElementConfig(preferences={}, behavior_class={})
    ctx = build_question_context(
        tpl, _matching_question(), 1, element_config, block_display_indent_inches=0
    )
    table = ctx["question_contents"].tables[0]
    assert "w:tblInd" not in table._tbl.tblPr.xml


def _order_blocks_question():
    widget = Widget(kind="order_blocks", name="steps", blocks=[plain("1"), plain("2")])
    return ParsedQuestion(
        title="Q",
        prompt_segments=[plain("Order these."), plain("")],
        widgets=[widget],
        answer_panel_text=None,
        points="1",
        points_numeric=1.0,
        qid="q/indent4",
    )


def test_order_blocks_vertical_table_gets_default_indent(starter_template):
    tpl = _tpl(starter_template)
    element_config = ElementConfig(preferences={}, behavior_class={})
    ctx = build_question_context(tpl, _order_blocks_question(), 1, element_config)
    table = ctx["question_contents"].tables[0]
    assert 'w:tblInd' in table._tbl.tblPr.xml


def test_order_blocks_horizontal_paragraphs_get_default_indent(starter_template):
    tpl = _tpl(starter_template)
    element_config = ElementConfig(
        preferences={"order_blocks": OrderBlocksPreferences(layout="horizontal")}, behavior_class={}
    )
    ctx = build_question_context(tpl, _order_blocks_question(), 1, element_config)
    order_paragraphs = [p for p in ctx["question_contents"].paragraphs if "A." in p.text or "Order:" in p.text]
    assert len(order_paragraphs) == 2
    for p in order_paragraphs:
        assert p.paragraph_format.left_indent is not None
