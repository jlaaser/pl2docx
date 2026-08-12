"""Generate a starter docx template for `pl2docx.docx_builder.render_document`.

Usage
-----
    python -m pl2docx.starter_template [output_path]

`output_path` defaults to `template.docx` in the current working directory.

Built via `python-docx` rather than hand-typed in Word — Word's autocorrect can
silently split a `{{ }}`/`{% %}` tag across multiple runs, breaking it (confirmed
gotcha this project already hit once with docxtpl's `{{p ... }}` subdoc syntax).
Generating it this way guarantees every tag is a single clean run.

The generated file is a *starting point*, not a fixed artifact: open it in Word,
edit the "pl2docx ..." named styles (fonts, colors, spacing, borders) and rearrange
the surrounding boilerplate/instructions text freely. As long as the `{% for %}`/
`{% if %}`/`{{p ... }}` tags stay intact (each on its own paragraph — required for
control-flow tags to be removed cleanly from the rendered output, confirmed this
session), `pl2docx.docx_builder.render_document` will keep working against your
edited copy.
"""

from __future__ import annotations

import sys
from pathlib import Path

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor
from docx.table import _Cell

DEFAULT_OUTPUT_PATH = Path("template.docx")


def build_starter_template(output_path: Path = DEFAULT_OUTPUT_PATH) -> None:
    """Write a starter pl2docx docx template to `output_path`.

    Parameters
    ----------
    output_path : pathlib.Path
        Where to save the generated template. Overwrites any existing file
        at that path.
    """
    doc = Document()
    _define_styles(doc)

    doc.add_heading("Assessment Title", level=1)
    doc.add_paragraph(
        "Instructions: replace this paragraph (and the title above) with your "
        "own instructions/reference material. Everything outside the loop tags "
        "further down is yours to edit or rearrange freely."
    )

    doc.add_paragraph("{% for zone in zones %}")
    doc.add_paragraph("{% if zone.title %}")
    doc.add_paragraph("{{ zone.title }}", style="pl2docx Zone Heading")
    doc.add_paragraph("{% endif %}")

    doc.add_paragraph("{% for question in zone.questions %}")
    doc.add_paragraph(
        "{{ question.number }}. {{ question.title }} ({{ question.points_text }})",
        style="pl2docx Question Title",
    )
    doc.add_paragraph("{%- if is_answer_key -%}")
    doc.add_paragraph(" [{{ question.qid }}]", style="pl2docx QID Reference")
    doc.add_paragraph("{%- endif %}")
    doc.add_paragraph("{{p question.question_contents }}")
    doc.add_paragraph(
        "The paragraph below is only populated when an element's config.yaml "
        "display preference is set to template - move or restyle it freely."
    )
    doc.add_paragraph("{{p question.answer_element }}")
    doc.add_paragraph("{% if is_answer_key %}")
    _add_bordered_solution_box(doc)
    doc.add_paragraph("{% else %}")
    doc.add_paragraph("{{p question.answer_space }}")
    doc.add_paragraph("{% endif %}")
    doc.add_paragraph("{% endfor %}")

    doc.add_paragraph("{% endfor %}")

    doc.add_paragraph("End of assessment.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path))


def _add_bordered_solution_box(doc: Document) -> None:
    """Add a 1x1 table (with a border) holding the "SOLUTION:" label + subdoc tag.

    A *paragraph* border can't be used here — confirmed this session: docxtpl's
    `{{p ... }}` substitution replaces the entire paragraph it's in (that's how it
    splices in real sibling paragraphs), discarding that paragraph's own
    formatting, including any border, along with it. A *table cell* border isn't
    affected by this at all — it lives on the cell (`<w:tcBorders>`), not on the
    paragraphs inside it, so it correctly wraps the substituted content no matter
    how many paragraphs it expands into.
    """
    table = doc.add_table(rows=1, cols=1)
    cell = table.cell(0, 0)
    _add_cell_border(cell)
    label_paragraph = cell.paragraphs[0]
    label_paragraph.add_run("SOLUTION:").bold = True
    cell.add_paragraph("{{p question.answer_contents }}")


def _add_cell_border(cell: _Cell) -> None:
    tcPr = cell._tc.get_or_add_tcPr()
    tcBorders = OxmlElement("w:tcBorders")
    for edge in ("top", "left", "bottom", "right"):
        edge_element = OxmlElement(f"w:{edge}")
        edge_element.set(qn("w:val"), "single")
        edge_element.set(qn("w:sz"), "12")
        edge_element.set(qn("w:space"), "4")
        edge_element.set(qn("w:color"), "000000")
        tcBorders.append(edge_element)
    tcPr.append(tcBorders)


def _define_styles(doc: Document) -> None:
    """Define the named styles the loop tags above reference.

    Placeholder formatting only — replace these in Word's Style Gallery
    ("Manage Styles") with whatever fonts/colors/spacing/borders you want;
    `render_document` only ever references these by name, never by the
    formatting values set here.
    """
    zone_heading = doc.styles.add_style("pl2docx Zone Heading", WD_STYLE_TYPE.PARAGRAPH)
    zone_heading.font.bold = True
    zone_heading.font.size = Pt(16)
    zone_heading.font.color.rgb = RGBColor(0x1A, 0x5C, 0xB0)

    question_title = doc.styles.add_style("pl2docx Question Title", WD_STYLE_TYPE.PARAGRAPH)
    question_title.font.bold = True
    question_title.font.size = Pt(12)

    qid_reference = doc.styles.add_style("pl2docx QID Reference", WD_STYLE_TYPE.PARAGRAPH)
    qid_reference.font.italic = True
    qid_reference.font.size = Pt(9)
    qid_reference.font.color.rgb = RGBColor(0x80, 0x80, 0x80)


def main() -> None:
    output_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT_PATH
    build_starter_template(output_path)
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
