# Templating

Instructors control document *layout* (headers, page structure, fonts/colors/spacing)
by editing a `.docx` template in Word. pl2docx supplies the *content* (per-question
text, images, math, answer spaces) by rendering that template with
[docxtpl](https://docxtpl.readthedocs.io/en/latest/), which uses Jinja2-style tags
embedded directly in the document text.

Generate a starting point with:

```bash
pl2docx-starter-template
```

This produces `template.docx` (or a path you specify) with every tag already in place
and a set of named "pl2docx ..." styles you can freely restyle in Word. Everything
outside the loop tags — titles, instructions, prefaces, appendices — is yours to edit
or rearrange freely, as long as the loop/subdoc tags described below stay intact.

## Context available to the template

At the top level:

| Variable | Description |
|---|---|
| `zones` | List of zones in document order. Each has `title` and `questions`. |
| `is_answer_key` | `true` when rendering the answer key, `false` for the blank copy. Use this to branch between a question's `answer_contents` (key) and `answer_space` (blank). |
| `instance_ID` | The current instance's label (an explicit configured label, or `"Instance N"` — see `instance_ids` in [configuration.md](configuration.md)). |

Per question (inside `zone.questions`):

| Field | Description |
|---|---|
| `number` | This question's document-wide number. |
| `title` | Question title. |
| `qid` | The question's PL identifier — useful in the answer key for instructor cross-reference. |
| `points_numeric` | Points as a number. |
| `points_text` | Points formatted as display text (e.g. `"5 points"`). |
| `question_contents` | The question's prompt, with widget content interleaved at its real position. Insert with `{{p question.question_contents }}`. |
| `answer_contents` | The fully-worked answer-key version of the question. Insert with `{{p question.answer_contents }}`. |
| `answer_space` | Blank space (lines) for a student to write an answer in the blank copy. Insert with `{{p question.answer_space }}`. |
| `answer_element` | Only populated when a widget's `display` preference is `template` (see [configuration.md](configuration.md)) — a separate content slot for that widget, for when you want it placed somewhere other than inline with the prompt. Insert with `{{p question.answer_element }}`, guarded by `has_answer_element`. |
| `has_answer_element` | Whether `answer_element` has any content — check this before inserting it, or it may render an unwanted empty section. |

## docxtpl gotchas (confirmed the hard way — read before editing template tags)

**Any tag inserting a `Subdoc` (`question_contents`, `answer_contents`, `answer_space`,
`answer_element`) must use the `{{p ... }}` prefix, not plain `{{ ... }}`.** The `p`
prefix is docxtpl's paragraph-level substitution syntax. Plain `{{ ... }}` silently
produces a broken docx — the subdoc's raw content ends up as literal text, invisible to
normal readers.

- **Mechanism**: before Jinja ever runs, docxtpl finds the *entire paragraph*
  containing a `{%p `/`{{p ` tag and replaces the whole paragraph element with the bare
  tag — that's how the substituted content's own paragraphs end up as real siblings
  instead of nested inside one run.
- **Corollary**: a `{{p ... }}` tag must be **completely alone in its paragraph** — not
  just alone aside from prose, but alone from *any other Jinja tag too*. If another tag
  shares the paragraph, it gets silently deleted as collateral damage — not an error at
  the deletion point, but a confusing error later (e.g. Jinja reporting an unexpected
  `endfor` because an unrelated `{% if %}` two paragraphs earlier lost its matching
  `{% endif %}`).

**Every `for`/`if`/`else`/`endif`/`endfor` control-flow tag should also use the `{%p
... %}` prefix** (not plain `{% %}`, not Jinja's own `{%- -%}` whitespace-trim), so its
own paragraph is cleanly removed:

- A plain, un-prefixed `{% if %}`/`{% for %}` tag's own paragraph survives rendering as
  a real, empty `<w:p>` — Jinja only clears the tag's own text, not its paragraph. This
  shows up as stray blank lines at every zone/question boundary.
- Jinja's `{%- ... -%}` trim syntax *can* eliminate that blank paragraph, but its
  merge runs before docxtpl's own `{{p }}`-paragraph-stripping step and doesn't respect
  paragraph ownership — trimming a control tag directly adjacent to a `{{p ... }}` tag
  corrupts that tag's paragraph boundary, silently breaking its open/close pairing
  (surfacing later as a confusing, unrelated-looking `TemplateSyntaxError`). Only use
  `{%- -%}` trim between two paragraphs of *real content*, never adjacent to a
  `{{p ... }}` tag.
- `{%p if %}`/`{%p endif %}`/`{%p for %}`/`{%p endfor %}` sidestep this entirely —
  confirmed safe directly adjacent to `{{p ... }}` tags on both sides, and inside a
  table.

**Never describe `{{ }}`/`{% %}` tag syntax in plain instructional prose placed inside
the template document itself.** docxtpl parses the *entire* document as Jinja source,
so even instructional text like "edit the `{% for %}` tags below" breaks compilation.

**A paragraph border on a `{{p ... }}` line is silently discarded** — same root cause
as the corollary above (the whole paragraph, including its border, gets replaced by
the substituted content). Use a **table cell border** instead: put the tag inside a 1×1
table and border the cell (`<w:tcBorders>`) rather than the paragraph. A cell's border
isn't paragraph-level, so it survives no matter how many paragraphs the substituted
content expands into. (This is what the starter template's answer-key "SOLUTION:" box
does.)

**A run-level character border is a different, simpler technique** for boxing a
widget's own rendered content (built directly in Python via `draw-border`, not a
template tag) tight to the text, inline with surrounding content. Since it lives on the
run rather than its containing paragraph, it isn't affected by the paragraph-
replacement issue above at all. Word visually merges adjacent runs sharing identical
border formatting into one continuous box.

Note the table-cell-border technique doesn't substitute for this: a table cell always
spans the full page width, which is fine for wrapping a whole subdoc's content but
produces a box far wider than the boxed text if used for a single widget's inline
content.

**If the template file is open in Word (or a cloud-sync tool hasn't finished writing
it) when rendering runs**, you'll get an error that the template file can't be opened
as a valid docx — close it in Word and let syncing finish, then retry.

**Word's autocorrect can silently split a tag across multiple runs**, breaking it, if
you hand-type template tags directly in Word. The starter template is generated via
`python-docx` specifically to guarantee every tag is a single clean run — when adding
new tags to a template, prefer copy-pasting an existing working tag and editing it
in place over typing a brand new one from scratch, to minimize the risk of Word
splitting it.

## Troubleshooting a broken template

If rendering raises a `TemplateSyntaxError` or produces a docx with garbled/missing
content, check in this order:
1. Every `{{p ... }}`/`{%p ... %}` tag is alone on its own paragraph.
2. Every `{%p if %}`/`{%p for %}` has its matching `{%p endif %}`/`{%p endfor %}` (not
   a plain `{% %}` counterpart).
3. No `{%- -%}` trim syntax sits adjacent to a `{{p ... }}` tag.
4. No prose elsewhere in the document accidentally contains `{{`, `}}`, `{%`, or `%}`.
