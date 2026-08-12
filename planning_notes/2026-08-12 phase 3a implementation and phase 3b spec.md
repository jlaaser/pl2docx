# pl2docx: Phase 3A Implementation Summary + Phase 3B Full Specification

This note is a session handoff: what was decided and built (Phase 3A, document-level
formatting), what's confirmed-but-tricky about the tooling (`docxtpl` gotchas), and the
full specification for Phase 3B (element/question-level formatting) as given by the user
— captured here in detail so a future session can start implementing without needing the
user to re-explain it.

## Status at end of session

- **Phase 3A (document-level, template-driven layout): implemented, tested, and
  live-verified.** Commit pending (see repo status below).
- **Phase 3B (element/question-level, config-driven formatting): specified in full below,
  not yet implemented.** This is the next work item.
- All 33 tests pass except one pre-existing live-server integration test currently
  failing with a 403 — the user believes this is because the local PL Docker container
  needed a "reload from disk" after a Windows Update forced a restart overnight. Not
  chased down this session (explicitly deferred by the user); check this first if
  `test_pl_client_integration.py` still fails when work resumes, before assuming a code
  regression.
- `output/` currently has instances `47`, `48`, `49` (fetched + rendered against the
  Phase 3A pipeline) for reference.

## Phase 3A: what was built

Architecture decision (see also `planning_notes/2026-08-10 phased implementation
roadmap.md`'s original Phase 3 note, and CLAUDE.md's Architecture section item 5): a
hybrid of template-driven document *layout* and Python-driven *content*, replacing
Phase 2's single-subdoc-per-document approach entirely.

- Document layout (zone/question loop, page structure, headers) lives in the
  instructor's own docx template, using real Jinja2 `{% for %}`/`{% if %}` tags and
  named Word styles (`"pl2docx Zone Heading"`, `"pl2docx Question Title"`, `"pl2docx QID
  Reference"`) the instructor edits directly in Word.
- All content (per-element formatting, matching/bolding) is built in Python
  (`element_renderer.py`) and handed to the template as `docxtpl.Subdoc`s, three per
  question: `question_contents`, `answer_contents`, `answer_space`.
- Full Jinja context shape, confirmed working:
  ```
  is_answer_key: bool   # explicit, set once per render pass, not inferred from content
  zones: [
    { title: str | None,
      questions: [
        { number: int,             # 1-based, continuous across the whole document
          title: str,
          qid: str | None,         # from the page's "Staff information" panel
          points_numeric: float | None,
          points_text: str | None, # e.g. "2 points" / "1 point"
          question_contents: Subdoc,
          answer_contents: Subdoc,
          answer_space: Subdoc,
        }, ...
      ] }, ...
  ]
  ```
- New/changed modules: `config.py` (+ `template_path`), `html_parser.py` (+ `qid`,
  `points_numeric`, `format_points_text()`), `element_renderer.py` (new —
  `build_question_context()`, currently fixed/Phase-2-equivalent formatting only),
  `docx_builder.py` (rewritten — `render_document()`, assembles the `zones` context and
  calls `docxtpl`, no per-element logic of its own; added `TemplateUnreadableError`),
  `starter_template.py` (new — `build_starter_template()` / `pl2docx-starter-template`
  CLI, generates an editable example template), `render.py` (rewritten orchestration,
  reads `structure.json` for zone/question order, assigns continuous numbering).

### `docxtpl` gotchas discovered this session (all confirmed against source, not guessed — see CLAUDE.md for the full writeup)

1. `{{p content }}`'s mechanism: `docxtpl`'s `patch_xml` regex finds the *entire*
   `<w:p>...</w:p>` containing a `{%p `/`{{p ` tag and replaces the whole paragraph
   element with the bare tag — that's how substituted subdoc content becomes real
   sibling paragraphs. Corollary: `{{p ... }}` must be **completely alone** in its
   paragraph, or any other Jinja tag sharing that paragraph gets silently deleted
   (surfacing later as a confusing unrelated Jinja syntax error).
2. Jinja whitespace-trim syntax (`{%- if x -%}`/`{%- endif %}`) is explicitly supported
   by `docxtpl` and merges a control tag's paragraph into its neighbor, eliminating the
   blank-paragraph artifact that plain `{% if %}`/`{% endif %}` leaves behind. Caveat:
   the merge keeps only one paragraph's style — content that was on its own
   differently-styled paragraph loses that distinct styling once merged in.
3. A **paragraph border** on a `{{p ... }}` line is discarded (same root cause as #1). A
   **table cell border** (1x1 table) survives, since it's not paragraph-level.
   `starter_template.py`'s answer-key "SOLUTION:" box uses this.
4. Template open in Word (or mid-cloud-sync) when `render_document()` runs causes a
   cryptic `PackageNotFoundError`. Now caught and re-raised as `TemplateUnreadableError`
   with actionable guidance.
5. Never describe `{{ }}`/`{% %}` syntax in plain instructional prose *inside* a
   template docx — `docxtpl` parses the whole document as Jinja source.

The user's reaction, verbatim, worth remembering: *"These docxtpl templates seem
frustratingly EXTREMELY fragile, but that works well enough for now, I guess."* — i.e.
tolerated, not loved. Worth keeping template-authoring guidance/examples thorough and
precise in whatever ships to instructors, and not assuming template edits are safe by
default.

## Phase 3B: full specification (user-provided, verbatim structure preserved)

Not yet implemented. Tackle after Phase 3A is considered fully settled by the user.

### Scope

In scope this phase: `pl-checkbox`, `pl-multiple-choice`, `pl-string-input`,
`pl-integer-input`. More complex inputs (`pl-drawing`, etc.) stay deferred.

These fall into two behavior classes:
- **Selector-type** (`pl-checkbox`, `pl-multiple-choice`): student marks one or more
  provided answers.
- **Fill-in-type** (`pl-string-input`, `pl-integer-input`, and — not yet detected by
  `html_parser.py`, but should slot into the same fill-in behavior set when added later
  — `pl-chemformula-input`, `pl-symbolic-input`, `pl-scinum-input`, `pl-number-input`).

### Config structure (user's sketch, `config.yaml`)

For built-in/standard PL elements, hardcode which behavior class (selector vs. fill-in)
each belongs to in code. For elements not yet natively supported, let the config
declare which behavior class they extend:

```yaml
global-element-preferences:
  pl-multiple-choice:
    # (selector-type preferences, see below)
  pl-checkbox:
    # (selector-type preferences, see below)
  pl-string-input:
    # (fill-in-type preferences, see below)
  pl-integer-input:
    # (fill-in-type preferences, see below)

additional-elements:
  pl-scinum-input:
    type: fill-in   # which built-in behavior set this extends
    # (fill-in-type preferences, see below)
```
The user is open to a better structure than this sketch if one fits `pl2docx`'s
conventions more naturally — this shape is a starting point, not a hard requirement.

### Selector-type (`pl-multiple-choice`, `pl-checkbox`) preferences

- **`list-style`**: `letter-labels` (capitalized letter in front of each item) |
  `bubble` (round fillable bubble, no letters — **default for `pl-multiple-choice`**) |
  `checkbox` (square checkable box, no letters — **default for `pl-checkbox`**).
- **`bold-correct`**: whether to attempt bolding the correct answer when explicitly
  matched in the answer panel (**default: `true`**).
- **`display`**: instructor override for block vs. inline layout. If not specified, try
  to match PL's own block/inline choice from the source HTML (confirmed real signal:
  `form-check-inline` class on `.form-check`, see `pl-multiple-choice.mustache`/
  `pl-checkbox.mustache`).
- **`dropdown-display`**: block vs. inline default specifically for `pl-multiple-choice`
  questions using `display="dropdown"` in PL (a `<select>`, which has no source
  block/inline signal to match against) — **default: `inline`**.

### Fill-in-type (`pl-string-input`, `pl-integer-input`, …) preferences

- **`format`**:
  - No blank displayed at all — assumes all work/final answer is shown in the answer
    "work space" instead.
  - A blank line, surrounded by the label/suffix text if present in the question panel
    HTML. Optionally boxed (a border) — fine if that has to be a document-style
    concern rather than a `format` option itself, given the `{{p }}`-paragraph-border
    gotcha above (a table-cell-border technique, same as the SOLUTION box, likely
    applies here too if the instructor wants a boxed blank).
  - If no label was found on the page, fall back to today's `"Answer: "` prefix (user
    confirmed this exact fallback when asked during the previous implementation round).

**These element-level choices live in `config.yaml`, handled entirely in Python — NOT
in the docx template.** Explicit user direction: putting per-element formatting logic in
the template would make the templating too complex and too easy for instructors to
break by accident. (This is a deliberate scope boundary distinct from Phase 3A's
document-*layout* being template-driven — element-level formatting stays Python-side.)

### Answer/work space

For now: a fixed default answer space (already implemented in Phase 3A —
`ANSWER_SPACE_BLANK_LINES = 2` in `element_renderer.py`). Eventually the user may want
this "smarter" (e.g. sized based on how long the PL answer-panel solution is) — explicitly
deferred, not in scope now. The `answer_space` field is already exposed in the Jinja
context regardless, so the template can already place/ignore it as desired; only the
*value* pl2docx computes for it needs to get smarter later.

### Answer-key display

- Selector-type: attempt to bold the correct answer inline (as above) when it can be
  matched.
- All types: the processed/formatted contents of the answer panel (`answer_contents`)
  are always accessible in the template — this is already implemented in Phase 3A.
- Any further presentation (a box around the solution, a "SOLUTION:" label, etc.) is a
  **document-template** concern, not Python content-formatting — already how Phase 3A's
  `starter_template.py` demonstrates it (the bordered-table SOLUTION box).

### Rich content / images

Still deferred to Phase 4 as originally planned — **not** in Phase 3B's scope. But
whatever internal representation Phase 3B settles on for question/answer panel content
must be able to accommodate rich content later (i.e. don't design `element_renderer.py`
in a way that makes inserting real HTML→docx rich content awkward to retrofit).

### Compound (multi-widget) questions

Go ahead and attempt to process each input element within a compound question and
insert their renders inline, in source order, within the same `question_contents`
subdoc. If this turns out to be fragile in practice, address whatever specific problems
come up once they're known — don't try to preemptively solve every edge case now. (This
supersedes the earlier "defer to a later phase" framing from
`planning_notes/2026-08-11 image and metadata fetch plus roadmap notes.md` — the user
revised this decision when writing the Phase 3B spec.)

Implementation note carried over from the Phase 3 planning discussion: this likely means
reworking `ParsedQuestion`'s current flat `kind`/`options`/`correct_option_indices`
shape into something like `widgets: list[WidgetResult]` (one entry per distinct named
input-widget group on the page), with `_detect_kind`'s current "raise if more than one
widget group" check replaced by "build one `WidgetResult` per group, of any recognized
kind." `answer_panel_text` likely stays a single combined string for the whole question
(PL's combined answer panel doesn't mark widget boundaries, so splitting it per-widget
isn't reliably possible) — shown once after all widgets' inline renders. This is a
real, blast-radius-having refactor: every current consumer of `.kind`/`.options`/
`.correct_option_indices` (`element_renderer.py`, and most of `test_html_parser.py`'s
fixture-based tests) will need updating to the new shape.

### Configuration scope

For now: a single global setting (one `config.yaml`, applies to the whole tool run).
Explicit user direction: leave room in the design for a future per-question or per-zone
override capability, but don't build that now — just don't paint the implementation
into a corner where it'd be hard to add later (e.g. avoid baking "config is always
exactly one global dict passed in once" so deeply into the architecture that per-question
overrides would require a rewrite rather than an extension).

## Next step

Start Phase 3B by: (1) designing the `element_config.py` module (dataclasses +
`config.yaml` loader) per the structure above, (2) reworking `html_parser.py` for
compound-widget support and the block/inline (`is_inline`/`is_dropdown`) + label/suffix
extraction needed by the new formatting options, (3) extending `element_renderer.py` to
consume the config and produce the configured list-style/display/fill-in-format
rendering. Get the user's sign-off on a concrete plan before implementing, per this
project's working-style convention (CLAUDE.md) — this phase touches a lot of surface
area and the compound-question refactor in particular has real design choices worth
confirming before committing to them.
