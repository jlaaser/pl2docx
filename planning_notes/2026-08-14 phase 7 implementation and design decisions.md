# Phase 7: pl-rich-text-editor, pl-matching, pl-order-blocks, pl-big-o-input, plus two cross-cutting formatting fixes

Session date: 2026-08-14. No architecture changes — this phase extends the existing
`Widget`/`ParsedQuestion`/`ElementConfig`/`element_renderer` pipeline (Phase 3B's
config-driven per-widget formatting, Phase 4's rich-content nodes) to four more PL
element types, then fixes two formatting bugs found while verifying the new elements
against a real PL server. Full plan (as approved before implementation) is preserved in
the session transcript, not duplicated here — this note captures the parts worth
knowing when *resuming* work, especially the things that diverged from the plan once
real fetched HTML was in hand.

## New element types

### pl-rich-text-editor
`Widget.kind = "rich_text_editor"`. Renders as `blank_answer_lines` (default 8, via a
new `_RICH_TEXT_DEFAULT_BLANK_LINES` constant — reuses `FillInPreferences.blank_answer_lines`
as its config knob, no new field needed) empty paragraphs at the widget's position.

**Blank/key distinction needed a new mechanism**, unlike every other kind: PL's
`pl-rich-text-editor.py` `render(panel="answer")` always returns `""` (no correct-answer
concept at all), and the user wanted the key docx to suppress this widget's own blank
lines while still rendering the question's freestanding `answer_contents` (an instructor
may put a model answer there unrelated to this specific widget). Since `build_question_context`
makes no blank/key distinction on principle (reflects whatever `ParsedQuestion` already
contains), and this widget's *own* container HTML is otherwise identical between blank
and key fetches, a new `is_answer_key: bool` parameter was threaded through
`parse_instance_question_html()` (and down through `render.py`'s `_build_zones`/`_load_question`,
which already know which directory they're parsing) specifically to set
`Widget.suppress_in_key = True` for this kind. See below — this same plumbing turned
out to be needed for a real bug fix, not just this one kind.

### pl-matching
`Widget.kind = "matching"`. Renders as a borderless 2-column table (statement+blank |
counter-labeled options), reusing `docxtpl.Subdoc.add_table()` the same way
`starter_template.py`'s `_add_bordered_solution_box` already did. Key docx replaces the
blank with the bold correct label (confirmed judgment call).

**Correct-answer resolution, JSON-first per the user's preference — but with a real gotcha
found against live content**: PL's `data["correct_answers"][name]` feeds the same Variant
JSON pathway MC/checkbox already use. The catch: each statement's own `<select>` is named
`"{base_name}-dropdown-{index}"`, but the JSON's top-level key is just `"{base_name}"`
(no suffix) — confirmed against a real fetched page (`pl-tutorial/other-inputs`'s matching
widget, `<select name="string_value-dropdown-0">` vs. JSON key `"string_value"`). Fixed by
stripping the `-dropdown-\d+$` suffix before both the JSON lookup and the widget's own
`name`. **If this file is being read because matching's correct answers aren't resolving on
a new course's content, check this first** — a different PL version/element revision could
plausibly use a different suffix convention.

Falls back to scraping PL's own rendered `.pl-matching-answer` HTML (`<strong>b.</strong> Mexico City`)
when the JSON route doesn't resolve. **Also found against real content**: that `<strong>`
text already includes the trailing "." — the fallback strips it (`_extract_matching_correct_labels_from_html`),
since the renderer always appends its own "." after whichever label it's given, regardless
of source.

### pl-order-blocks
`Widget.kind = "order_blocks"`. Two layouts, **config-only, not derived from the fetched
page's own DOM/CSS state** — the user's explicit call: "default to vertical - that is how
prairielearn always lays out this element, as far as I can tell." New `OrderBlocksPreferences.layout`
(`"vertical"` default / `"horizontal"`), a new `BehaviorClass` value (`"order-blocks"`)
since it needs its own preferences dataclass, not `SelectorPreferences`/`FillInPreferences`.

Vertical: same table shape as matching (shared `_style_two_column_table` helper), header
row (`"" | "Order:"`), one row per pool block (distractors included, lettered like any
other block — confirmed choice), order blanks limited to the correct-sequence length.
Horizontal: same content as inline paragraph runs instead of table cells.

**Correct order is *never* attempted via the Variant JSON** — confirmed with the user
this is a deliberate exception to the JSON-first preference: the correct order is
*computed* inside `pl-order-blocks.py`'s controller at grade/render time, not stored
verbatim in the variant. Resolved entirely by scraping PL's static
`.pl-order-blocks-answer-container` (present only once `showCorrectAnswer` is true),
matching pool blocks back to their index by content-text equality
(`_extract_order_blocks_correct_order`).

### pl-big-o-input
Not a new `Widget.kind` at all — reuses the existing generic fill-in detection
(`_add_fill_in_groups`) via a new `FillInPreferences.class_prefix` /
`additional_fill_in_class_prefixes()` config override. `pl-big-o-input`'s real class is
`big-o-input-input` (missing the `pl-` prefix every other fill-in element's class has),
so `config.yaml`'s `additional-elements: pl-big-o-input: {type: fill-in, class-prefix: big-o-input}`
substitutes `"big-o-input"` for the tag itself when building `_add_fill_in_groups`'s
`{base}-input` regex. No other code needed — label/suffix/width extraction already work
generically once the input is found.

## Real bug found verifying against a live server: answer leak on blank copies

**This is the one finding from this phase most worth knowing about if working on
`html_parser.py`/`bold_correct` again.** PL's staff "Variant" JSON panel
(`_extract_true_answer`) is server-rendered for staff-role viewers *regardless of
`showCorrectAnswer`* — confirmed present on blank/open-instance fetched HTML, not just
key HTML. Before this phase, `correct_option_indices` (MC/checkbox) was resolved
unconditionally from it, meaning `bold_correct` (on by default) was **already bolding
the correct MC/checkbox answer on the student's own blank copy** — confirmed directly in
rendered output (`output/12/12_blank.docx` showed bolded "1" and "closer to", the real
correct answers, before the fix). This predates Phase 7 but had gone unnoticed because
no existing test/fixture exercised a blank-parsed page against a *populated* Variant JSON
panel (the existing blank fixtures happen not to carry one, or the assertions never
checked for absence of bolding).

Fixed by gating `_extract_true_answer`'s result — and, for matching/order-blocks, the
`answer_body` handed to their widget builders — behind the same `is_answer_key` parameter
added for `pl-rich-text-editor` above:
```python
true_answer = _extract_true_answer(soup) if is_answer_key else None
widget_answer_body = answer_body if is_answer_key else None
```
`answer_panel_text` (the whole-page value) was already safe (`.answer-body` is genuinely
empty on real blank HTML) and needed no gating.

**Caller obligation this creates**: any code parsing HTML that's actually the answer key
must now pass `is_answer_key=True` explicitly, or correct-answer data silently resolves to
nothing. `render.py`'s two `_build_zones`/`_load_question` calls already do this correctly
(blank dir → `False`, key dir → `True`). If a new caller of `parse_instance_question_html`
is added, check this.

## Two cross-cutting formatting fixes (requested after Phase 7's element work, same session)

### Spurious space between adjacent rich-text runs
`_append_run_text`'s "insert a space if the previous run doesn't already end in one"
heuristic was applied uniformly to *every* run added to a paragraph, including two
genuinely-adjacent `TextRun`/`MathRef`-fallback/image-alt nodes from the *same* continuous
source flow — which already encode whatever real whitespace (or lack of it) existed
between them. Confirmed visible in real output: `"...in <b>red</b>. Numbers..."` (no
space in source) rendered as `"red ."`; an inline LaTeX span between parens rendered as
`"( C_2H_6 )"`.

Fixed by splitting into two functions:
- `_add_formatted_run` (new) — plain concatenation, no synthetic space. Used for every
  `ContentNode` rendered in sequence via `_render_one_node` (TextRun, math/image/svg
  fallback text) — i.e., content walked from one continuous source flow.
- `_append_run_text` (existing, unchanged) — kept for genuinely separate chunks *this
  module* assembles with no inherent source adjacency: fill-in label/blank/suffix,
  prompt text before/after a widget, MC's inter-option spacer, list marker prefixes.

`_render_image`'s successful-picture-embed path also had its own unconditional
leading-space insertion before the picture run — removed outright (same reasoning).

### Configurable block-display indent
New `config.yaml` top-level key `block_display_indent_inches` (default `0.125`, `0`
disables) — a **single global value, not per-element** (explicit user request). Applies
a left indent to: selector/fill-in widgets in `"block"` display (whether explicit or the
auto-detected default for a non-inline, non-dropdown selector), plus `pl-matching`/
`pl-order-blocks` (always block-shaped, since neither exposes a `display` setting).
Threaded `Config.block_display_indent_inches` → `render_instance` → `render_document` →
`build_question_context` → the block-render functions. Table indent uses a raw
`<w:tblInd>` OOXML element (`_apply_table_indent`, since `python-docx`'s `Table` has no
high-level indent property, unlike `Paragraph.paragraph_format.left_indent`).
Deliberately **not** applied to a block-display widget reusing an already-list-numbered
paragraph (mid-`<li>` content) — that paragraph already has its own list-level indent;
stacking this on top would look inconsistent with sibling list items. Also **not**
applied to `pl-rich-text-editor`'s blank lines (not a `"block"`-*display* widget in the
`Display` vocabulary sense — the user's request was scoped to `display`-participating
kinds plus matching/order-blocks specifically).

`pl2docx.config` deliberately does **not** import `pl2docx.element_renderer` for this
(would pull in `python-docx`/`docxtpl`/`playwright`, dependencies `config.py` stays free
of) — the `0.125` default is duplicated by hand in both
`Config.block_display_indent_inches` and `element_renderer.DEFAULT_BLOCK_DISPLAY_INDENT_INCHES`,
noted in both docstrings so a future change to one doesn't silently drift from the other.

## Verification

All new/changed behavior confirmed against the user's own `pl2docx-phase1-test`
assessment on a real local PL server (not just synthetic fixtures) — the user added three
real test questions (`misc/TA-questions` for rich-text, `pl-tutorial/other-inputs` for
matching+order-blocks, `TEST/big-o-input`) specifically for this. 233 tests pass
(`py -3.14 -m pytest`, up from 193 at the start of this phase).
