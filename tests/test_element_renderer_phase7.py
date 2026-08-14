"""Phase 7: rich_text_editor / matching / order_blocks rendering.

Kept as a separate module (rather than appended to test_element_renderer.py)
purely to avoid an ambiguous-anchor edit against that file's several
identical trailing assertions - no functional reason for the split.
"""

from docxtpl import DocxTemplate

from pl2docx.element_config import ElementConfig, FillInPreferences, OrderBlocksPreferences
from pl2docx.element_renderer import build_question_context
from pl2docx.html_parser import ParsedQuestion, Widget, plain


def _tpl(starter_template) -> DocxTemplate:
    tpl = DocxTemplate(str(starter_template))
    tpl.init_docx()
    return tpl


def _text(subdoc) -> str:
    return "\n".join(p.text for p in subdoc.paragraphs)


def _rich_text_question(suppress_in_key=False):
    widget = Widget(kind="rich_text_editor", name="essay", suppress_in_key=suppress_in_key)
    return ParsedQuestion(
        title="Q",
        prompt_segments=[plain("Explain your reasoning."), plain("")],
        widgets=[widget],
        answer_panel_text=plain("Model answer text.") if suppress_in_key else None,
        points="1",
        points_numeric=1.0,
        qid="q/rte",
    )


def _matching_question(correct_labels=None):
    widget = Widget(
        kind="matching",
        name="matching_countries",
        statements=[plain("United States"), plain("France"), plain("Mexico")],
        match_options=[plain("Mexico City"), plain("Paris"), plain("Washington, D.C.")],
        counter_type="decimal",
        correct_labels=correct_labels if correct_labels is not None else [None, None, None],
    )
    return ParsedQuestion(
        title="Q",
        prompt_segments=[plain("Match each country to its capital."), plain("")],
        widgets=[widget],
        answer_panel_text=None,
        points="1",
        points_numeric=1.0,
        qid="q/matching",
    )


def _order_blocks_question(correct_order=None):
    widget = Widget(
        kind="order_blocks",
        name="steps",
        blocks=[plain("Add reactants"), plain("Heat to reflux"), plain("Cool"), plain("Filter")],
        correct_order=correct_order,
    )
    return ParsedQuestion(
        title="Q",
        prompt_segments=[plain("Put these steps in order."), plain("")],
        widgets=[widget],
        answer_panel_text=None,
        points="1",
        points_numeric=1.0,
        qid="q/order",
    )


def _table_cell_texts(subdoc, col: int) -> list[str]:
    table = subdoc.tables[0]
    return [table.cell(i, col).text for i in range(len(table.rows))]


def test_rich_text_editor_default_blank_lines(starter_template):
    tpl = _tpl(starter_template)
    element_config = ElementConfig(preferences={}, behavior_class={})
    ctx = build_question_context(tpl, _rich_text_question(), 1, element_config)
    subdoc = ctx["question_contents"]
    blank_paragraphs = [p for p in subdoc.paragraphs if p.text == ""]
    assert len(blank_paragraphs) == 8


def test_rich_text_editor_blank_lines_configurable(starter_template):
    tpl = _tpl(starter_template)
    element_config = ElementConfig(
        preferences={"rich_text_editor": FillInPreferences(blank_answer_lines=3)},
        behavior_class={},
    )
    ctx = build_question_context(tpl, _rich_text_question(), 1, element_config)
    subdoc = ctx["question_contents"]
    blank_paragraphs = [p for p in subdoc.paragraphs if p.text == ""]
    assert len(blank_paragraphs) == 3


def test_rich_text_editor_suppressed_in_key_but_answer_contents_unaffected(starter_template):
    tpl = _tpl(starter_template)
    element_config = ElementConfig(preferences={}, behavior_class={})
    ctx = build_question_context(tpl, _rich_text_question(suppress_in_key=True), 1, element_config)
    blank_paragraphs = [p for p in ctx["question_contents"].paragraphs if p.text == ""]
    assert len(blank_paragraphs) == 0
    assert "Model answer text." in _text(ctx["answer_contents"])


def test_matching_blank_shows_blank_line_and_options(starter_template):
    tpl = _tpl(starter_template)
    element_config = ElementConfig(preferences={}, behavior_class={})
    ctx = build_question_context(tpl, _matching_question(), 1, element_config)
    subdoc = ctx["question_contents"]
    assert len(subdoc.tables) == 1
    left = _table_cell_texts(subdoc, 0)
    right = _table_cell_texts(subdoc, 1)
    assert left[0].startswith("_") and "United States" in left[0]
    assert right == ["1. Mexico City", "2. Paris", "3. Washington, D.C."]


def test_matching_key_fills_bold_correct_label(starter_template):
    tpl = _tpl(starter_template)
    element_config = ElementConfig(preferences={}, behavior_class={})
    ctx = build_question_context(
        tpl, _matching_question(correct_labels=["3", "2", "1"]), 1, element_config
    )
    subdoc = ctx["question_contents"]
    left = _table_cell_texts(subdoc, 0)
    assert left[0] == "3. United States"
    assert left[1] == "2. France"
    assert left[2] == "1. Mexico"
    first_row_runs = subdoc.tables[0].rows[0].cells[0].paragraphs[0].runs
    assert any(r.bold and r.text == "3." for r in first_row_runs)


def test_order_blocks_vertical_blank(starter_template):
    tpl = _tpl(starter_template)
    element_config = ElementConfig(preferences={}, behavior_class={})
    ctx = build_question_context(tpl, _order_blocks_question(), 1, element_config)
    subdoc = ctx["question_contents"]
    table = subdoc.tables[0]
    assert len(table.rows) == 5  # 4 pool blocks + 1 header row
    assert table.cell(0, 1).text == "Order:"
    assert table.cell(1, 0).text == "A. Add reactants"
    assert table.cell(1, 1).text == "____"


def test_order_blocks_vertical_key_fills_correct_letters_excluding_distractor(starter_template):
    tpl = _tpl(starter_template)
    element_config = ElementConfig(preferences={}, behavior_class={})
    ctx = build_question_context(
        tpl, _order_blocks_question(correct_order=[0, 1, 3]), 1, element_config
    )
    subdoc = ctx["question_contents"]
    table = subdoc.tables[0]
    assert len(table.rows) == 5
    assert [table.cell(i, 1).text for i in range(1, 4)] == ["A", "B", "D"]
    assert table.cell(4, 1).text == ""  # distractor's row gets no order blank at all


def test_order_blocks_horizontal_layout(starter_template):
    tpl = _tpl(starter_template)
    element_config = ElementConfig(
        preferences={"order_blocks": OrderBlocksPreferences(layout="horizontal")},
        behavior_class={},
    )
    ctx = build_question_context(tpl, _order_blocks_question(), 1, element_config)
    subdoc = ctx["question_contents"]
    assert subdoc.tables == []
    text = _text(subdoc)
    assert "A. Add reactants" in text
    assert "Order:" in text
    assert "____" in text
