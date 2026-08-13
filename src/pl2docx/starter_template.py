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
the surrounding boilerplate/instructions text freely. As long as the `{%p for %}`/
`{%p if %}`/`{{p ... }}` tags stay intact (each on its own paragraph), and each
`{%p %}` control tag keeps its matching `{%p %}` counterpart (not a plain
`{% %}`), `pl2docx.docx_builder.render_document` will keep working against your
edited copy.

**Default formatting** (confirmed with the user, targeting something reasonably
close to `planning_notes/reference_examples/MT1_A_KEY.pdf`'s look, not an exact
match): black Times New Roman 12pt, single line spacing, no bold/italic/underline
anywhere by default. The only exceptions are the assessment title/zone heading
(16pt) and the "SOLUTION:" label (bold, a run-level override — see
`_add_bordered_solution_box`) — deliberately not a general-purpose "make it
pretty" theme, just this one specific, plain, print-friendly look.

**`{%p %}` control-flow tags, not plain `{% %}` or whitespace-trim**: every
`for`/`if`/`else`/`endif`/`endfor` control-tag paragraph below uses docxtpl's
`{%p ... %}` prefix (the same paragraph-consuming mechanism `{{p ... }}` uses
for subdoc insertion — confirmed via `docxtpl/template.py`'s `patch_xml`: the
`for y in ["tr", "tc", "p", "r"]` loop applies to *any* `{%y ... %}`/`{{y ... }}`
tag, control-flow included, not just subdoc/table tags), rather than a plain
`{% if %}`/`{% for %}` or Jinja's own `{%- ... -%}` whitespace-trim syntax.
Confirmed necessary this session, the hard way:
- A *plain*, un-prefixed `{% if %}`/`{% for %}` tag's own paragraph survives
  rendering as a real, empty `<w:p>` (Jinja only clears the tag's own text) —
  one stray blank paragraph per un-trimmed control tag. Direct before/after
  paragraph-dump testing against real fetched content showed a rendered
  assessment accumulating 2-5 of these at every zone/question boundary.
- Jinja's `{%- ... -%}` trim (already used for the `is_answer_key`/QID pair,
  which predates this note and is deliberately left as-is — it's real content
  on both sides, not `{{p ... }}` tags, so it's safe) fixes that by having
  `patch_xml` delete the XML between a trim marker and the nearest `</w:t>`/
  `<w:t>` boundary — but that deletion runs *before* `patch_xml`'s own
  `{{p ... }}`-paragraph-stripping step, and doesn't respect paragraph
  ownership. Trimming a control tag adjacent to a `{{p ... }}` subdoc tag
  (e.g. wrapping `{{p question.answer_element }}` in a trimmed `{%- if -%}`)
  merges the trim tag's text into the *same* flattened region as the `{{p }}`
  tag, corrupting the paragraph boundary that tag's own stripping regex
  depends on — silently breaking its `if`/`endif` pairing (confirmed by
  reproducing a `TemplateSyntaxError: Unexpected end of template ... looking
  for endfor` this way, then bisecting a minimal repro down to exactly this).
  This is the same class of failure CLAUDE.md's docxtpl gotchas list already
  documents for two tags sharing one paragraph — here it's two tags merged
  into one paragraph's worth of *flattened text* by trimming, not authored
  that way, but the underlying collision is identical.
- `{%p if %}`/`{%p endif %}`/`{%p for %}`/`{%p endfor %}` sidesteps this
  entirely: each tag's own paragraph is stripped by the *same* mechanism
  `{{p ... }}` uses, independently and cleanly, regardless of what's adjacent
  to it — verified safe directly against a `{{p ... }}` tag on both sides
  (`{%p if question.has_answer_element %}{{p question.answer_element
  }}{%p endif %}`), against a table in one branch (the "SOLUTION:" box), and
  confirmed it leaves a real content paragraph's own named style (e.g. "pl2docx
  Zone Heading") completely untouched, since — unlike trim — it never merges
  two *real* paragraphs together, only ever removes a control tag's own
  (otherwise-empty) one.
"""

from __future__ import annotations

import sys
from pathlib import Path

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
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

    doc.add_paragraph("Assessment Title", style="pl2docx Assessment Title")
    doc.add_paragraph(
        "Instructions: replace this paragraph (and the title above) with your "
        "own instructions/reference material. Everything outside the loop tags "
        "further down is yours to edit or rearrange freely. The line inserting "
        "each question's answer_element content, further down, is only "
        "populated when an element's config.yaml display preference is set to "
        "template - safe to leave in place even if you never use that feature."
    )

    doc.add_paragraph("{%p for zone in zones %}")
    doc.add_paragraph("{%p if zone.title %}")
    doc.add_paragraph("{{ zone.title }}", style="pl2docx Zone Heading")
    doc.add_paragraph("{%p endif %}")

    doc.add_paragraph("{%p for question in zone.questions %}")
    doc.add_paragraph(
        "{{ question.number }}. {{ question.title }} ({{ question.points_text }})",
        style="pl2docx Question Title",
    )
    doc.add_paragraph("{%- if is_answer_key -%}")
    doc.add_paragraph(" [{{ question.qid }}]", style="pl2docx QID Reference")
    doc.add_paragraph("{%- endif %}")
    doc.add_paragraph("{{p question.question_contents }}")
    doc.add_paragraph("{%p if question.has_answer_element %}")
    doc.add_paragraph("{{p question.answer_element }}")
    doc.add_paragraph("{%p endif %}")
    doc.add_paragraph("{%p if is_answer_key %}")
    _add_bordered_solution_box(doc)
    doc.add_paragraph("{%p else %}")
    doc.add_paragraph("{{p question.answer_space }}")
    doc.add_paragraph("{%p endif %}")
    doc.add_paragraph("{%p endfor %}")

    doc.add_paragraph("{%p endfor %}")

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

    "SOLUTION:" stays bold via a run-level override, not a paragraph style — the
    label paragraph itself is otherwise plain "Normal" (Times New Roman 12pt,
    inherited), matching the reference example's look.
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
    """Override "Normal" and define the named styles the loop tags above reference.

    Deliberately plain and uniform by default (confirmed with the user, targeting
    something reasonably similar to
    `planning_notes/reference_examples/MT1_A_KEY.pdf`, not an exact match): every
    style here is based on "Normal" (black, Times New Roman, 12pt, single line
    spacing, no bold/italic/underline) — "pl2docx Zone Heading"/
    "pl2docx Assessment Title" only override size (16pt, the zone heading also
    bold, matching the reference's module headings); "pl2docx Question Title"/
    "pl2docx QID Reference" get no overrides at all by default, existing purely as
    named customization hooks a user can restyle in Word later without touching
    body text. This is a starting point, not a fixed artifact — replace any of
    these in Word's Style Gallery ("Manage Styles") with whatever fonts/colors/
    spacing/borders you want; `render_document` only ever references these by
    name, never by the formatting values set here.
    """
    normal = doc.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(12)
    normal.font.color.rgb = RGBColor(0x00, 0x00, 0x00)
    normal.font.bold = False
    normal.font.italic = False
    normal.font.underline = False
    normal.paragraph_format.line_spacing = 1.0
    normal.paragraph_format.space_before = Pt(0)
    normal.paragraph_format.space_after = Pt(6)

    assessment_title = doc.styles.add_style("pl2docx Assessment Title", WD_STYLE_TYPE.PARAGRAPH)
    assessment_title.base_style = normal
    assessment_title.font.size = Pt(16)
    assessment_title.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER

    zone_heading = doc.styles.add_style("pl2docx Zone Heading", WD_STYLE_TYPE.PARAGRAPH)
    zone_heading.base_style = normal
    zone_heading.font.bold = True
    zone_heading.font.size = Pt(16)

    question_title = doc.styles.add_style("pl2docx Question Title", WD_STYLE_TYPE.PARAGRAPH)
    question_title.base_style = normal

    qid_reference = doc.styles.add_style("pl2docx QID Reference", WD_STYLE_TYPE.PARAGRAPH)
    qid_reference.base_style = normal


def main() -> None:
    output_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT_PATH
    build_starter_template(output_path)
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
