# pl2docx: Phase 3B Implementation, Stable-ID Resolution, and Fill-In Element Scope

Session handoff note. Covers three pieces of work landed in this session, in
chronological order, each with the *reasoning* behind the decisions made — not just
what shipped. Companion to
`planning_notes/2026-08-12 phase 3a implementation and phase 3b spec.md` (the original
Phase 3B spec this session implemented, with deviations noted below) and superseding it
as the authoritative description of current behavior where they differ.

Commits (in order): `f81b6ec` (Phase 3B core), `d092c6d` (docs for a diagnosed-not-yet-fixed
Docker gotcha), `123539c` (the actual fix — stable-ID resolution), `ccc58de` (fill-in
element scope widening).

## 1. Phase 3B: config-driven element/question-level formatting (`f81b6ec`)

Implemented the spec from the prior session's handoff doc, with three deviations the
user gave mid-implementation (all now the authoritative behavior — see CLAUDE.md's "Open
design decisions" section, not the original spec doc, for current truth):

- **A single `display` vocabulary** (`inline`/`block`/`template`/`none`) shared by
  selector-type (`pl-multiple-choice`/`pl-checkbox`) and fill-in-type widgets, replacing
  the original spec's separate `display`/`dropdown-display` (selector) and `format`
  (fill-in) options. *Why*: the user wanted config-file consistency — one mental model
  for "where does this widget's content go" regardless of element type, rather than two
  parallel but differently-named option sets.
- **Widgets render at their real source position**, interleaved with the surrounding
  prompt text, instead of being appended after the whole prompt (the original spec's
  assumption). *Why*: a question shaped `Text A <widget> Text B` should read that way in
  the output — appending widgets after the prompt was flagged by the user as producing
  incorrect reading order for compound/inline questions. This is the reason
  `ParsedQuestion.prompt_segments` exists (`len(widgets)+1` text segments, one before each
  widget and one trailing) instead of a single flat `prompt_text` string — a bigger
  `html_parser.py` refactor than the original spec anticipated, since flattening the
  prompt to one string (Phase 2/3A's approach) inherently destroys widget position
  information.
- **`draw-border` always uses a tight, run-level character border**
  (`<w:bdr>` inside a run's `<w:rPr>`), regardless of `display` mode. *Why*: the first
  implementation used the table-cell-border technique (already proven for the
  `starter_template.py` SOLUTION box) for `block`/`template`-display content, reasoning
  that a run border can't merge across a paragraph break into one box. The user tried it
  and rejected it — a table cell always spans the full page width, producing a box far
  wider than the boxed text, not what "draw a border around this widget" should look
  like. Switched to always using the run-level technique: one continuous box for
  `inline` content (Word merges adjacent same-bordered runs, including a spacer run
  between options), one tight box *per line* for `block`/`template` content (accepted
  as correct behavior, not a limitation — each option/line getting its own tight box is
  what "text-width, not page-width" actually requires when content spans multiple
  paragraphs).

Also fixed along the way, not originally scoped but discovered during live-content
testing: PL's own auxiliary/non-visible markup was leaking into rendered prompts —
`pl-checkbox`'s Python-generated `<small class="form-text text-muted">Select N
options</small>` help text, and a screen-reader-only `<legend
class="visually-hidden">Checkbox options</legend>`. Neither sits inside any option's own
`.form-check` container, so the existing widget-container stripping never touched them.
`html_parser._strip_help_text()` now removes anything carrying `text-muted` or
`visually-hidden` classes before prompt extraction — matched on those classes
specifically (not the bare `form-text` class alone, which `pl-string-input` uses
legitimately for its `suffix` div, e.g. a unit like "g/mol").

**Compound (multi-widget) question support** shipped as part of this same refactor:
`ParsedQuestion.widgets: list[Widget]`, one per distinct named input group in source
order, replacing the Phase 2/3A flat `kind`/`options`/`correct_option_indices` fields.
This was already anticipated as in-scope by the original spec, but ended up entangled
with the source-position work above (both needed the same underlying DOM-walk rework).

## 2. Local PL server's ephemeral database (`d092c6d` → `123539c`)

`test_pl_client_integration.py` was failing with an HTTP 403 whose page read "This
assessment's configuration does not allow you to access it right now." — this reads like
an access-control/permissions bug (and this project's own architecture notes about the
Previewer-role staff-access bypass made that the natural first suspect). Root cause,
confirmed by inspecting `selectAndAuthzAssessment.sql` and checking the live server as
the authenticated dev user: it was a plain not-found, not an authz denial. The Docker
command in CLAUDE.md's Tooling section runs with `--rm` and no persistent Postgres
volume — **every container restart wipes the database and re-syncs the course from
disk with fresh numeric ids**. `config.yaml`'s hardcoded `assessment_id: 75` had gone
stale after a restart (the real id was now `4`).

First response (`d092c6d`) was just to fix the number and document the gotcha. The user
then asked for something that wouldn't need re-fixing after every future restart —
correctly pointing out that PL's *stable*, human-authored identifiers (a course's
`short_name`, a course instance's `short_name`, an assessment's `tid`) should be
resolvable to whatever the current numeric ids are, at runtime.

**Why not the obvious JSON API?** `/pl/api/v1/course_instances/:id/assessments` exposes
exactly the `tid`→id mapping needed, cleanly. It requires a separately-issued PL API
token, though — a new secret to generate and configure, on top of the session-cookie
auth `pl_client.py` already relies on for everything else. Decided against it purely for
setup-friction reasons; HTML-scraping the equivalent instructor pages needs zero new
credentials.

**Where each identifier is actually shown** (confirmed live, not guessed — see
`pl_client.py`'s module docstring for the full writeup):
- Course `short_name`: `/pl` homepage's "Courses with instructor access" table, e.g.
  `"CHEM 0110: General Chemistry I"` → short_name is `"CHEM 0110"`.
- Course instance `short_name`: its own column on
  `/pl/course/<course_id>/course_admin/instances`.
- Assessment `tid`: **not shown at all** on the assessments list page (only titles and
  numeric ids) — resolving it costs one extra request per candidate assessment, to a
  page whose React-hydration JSON embeds it (`/pl/course_instance/<id>/instructor/
  assessment/<id>/settings`). Accepted as a real cost (a handful of requests, one-time,
  cheap) rather than chasing a cheaper alternative, since correctness (matching by
  `tid`, not the editable `title`) mattered more than shaving a few requests.

`config.yaml` now stores `course_short_name`/`course_instance_short_name`/`assessment_tid`
instead of numeric ids; `PLClient.resolve_course_id`/`resolve_course_instance_id`/
`resolve_assessment_id` (plus pure, offline-testable `parse_*`/`extract_*` counterparts)
translate them to current numeric ids once at the start of every `fetch_n_instances` run.
This class of 403 should not recur.

## 3. Fill-in-type element scope (`ccc58de`)

Two follow-up questions after Phase 3B landed: (1) does the new fill-in formatting
config actually work for PL's other standard fill-in elements, not just
`pl-string-input`/`pl-integer-input`? (2) does the `additional-elements` config
mechanism (for non-built-in elements) actually work end-to-end, e.g. for
`pl-scinum-input`? Answer to both, before this round: no — `element_config.py`'s
preference-resolution logic already handled arbitrary fill-in kinds correctly, but
`html_parser.py`'s widget *detection* was hardcoded to exactly the original 4 element
types, so nothing else ever produced a `Widget` to format in the first place.

**Research finding that shaped the fix**: every PL fill-in-type element checked
(`pl-string-input`, `pl-integer-input`, `pl-number-input`, `pl-symbolic-input`,
`pl-units-input`, and course-specific `pl-scinum-input`) shares one markup convention —
an `.input-group`-classed wrapper around an `<input>`/`<textarea>` whose own class is
exactly `{element-tag-name}-input` (or `-multiline`), with `.input-group-text` siblings
for label/suffix. That convention is what the tag name *is*, not something specific to
each element — so one generic detector (`html_parser._add_fill_in_groups`), parameterized
purely by tag name string, replaced two separate hardcoded per-element loops and works
for both:
- **Built-in kinds** (now 5: string/integer/number/symbolic/units-input) — core PL
  elements, detected with zero instructor config, same as before.
- **`additional-elements`-configured tags** (e.g. `pl-scinum-input`) — course-specific
  elements the instructor opts into via `config.yaml`, using the *identical* mechanism.
  This is the key architectural point: no course-specific string (`"pl-scinum-input"`,
  `"chem 0110"`, etc.) exists anywhere in `pl2docx`'s own code — it only ever comes from
  the instructor's own config file. That's what CLAUDE.md's "no hard dependency on a
  specific course's custom packages" ground rule actually requires in practice, and this
  generic mechanism satisfies it structurally rather than by discipline/vigilance alone.

**Two elements deliberately excluded, both confirmed not guessed**:
- `pl-symbolic-input`'s `formula_editor` rendering mode swaps the visible widget for a
  JS-populated `<math-field>` custom element (a different tag name, though it still
  carries the `pl-symbolic-input-input` class) paired with empty hidden `<input>`s that
  actually carry the submitted value. Restricting `_add_fill_in_groups`'s search to real
  `<input>`/`<textarea>` tag names (not just "any tag with a matching class") is what
  correctly excludes the `<math-field>` — a `formula_editor`-mode `pl-symbolic-input`
  page safely produces `UnsupportedElementError` (no usable widget found) rather than a
  mis-render with empty content. This is a *consequence* of the tag-name restriction,
  not a special case written for symbolic-input specifically.
- `pl-big-o-input` was checked at the user's request (visually similar to the
  fill-in family) and found to genuinely break the shared convention: its `<input>`
  class is `big-o-input-input`, **missing the `pl-` prefix** every other element's class
  follows (confirmed against `pl-big-o-input.mustache`). The generic detector derives
  its match pattern purely from the tag name (`{tag}-input`), so this can't be supported
  without either a hardcoded exception (defeats the point of the generic mechanism) or a
  config-level pattern override (real scope creep) — deferred, not implemented, flagged
  for a later phase alongside other elements needing bespoke handling (e.g.
  `pl-order-blocks`).
- (Noted but out of scope, same reasoning as `pl-big-o-input`): `pl-chemformula-input`'s
  `multiline` mode moves its label/suffix outside the `.input-group` wrapper into
  different tag shapes (`<label class="form-label">`/`<div class="form-text">` instead
  of `.input-group-text`) — its non-multiline mode fits the generic pattern fine, but
  multiline mode would need special-casing too. Not investigated further this session.

**Live verification**: the user added a `TEST/pl-scinum-input` question (already
authored in the course repo, mirroring `TEST/pl-integer-input`, but not wired into any
assessment) to `pl2docx-phase1-test`'s "Text/number-input pool" zone themselves — this
project's course-repo-edit boundary (CLAUDE.md: never edit the course repo directly)
means that step had to be theirs, not mine. That question has 7 separately-named
`pl-scinum-input` widgets on one page, which exercised the generic additional-elements
detection and compound-question grouping together — confirmed all 7 render with correct
labels and tight borders in a real fetched/rendered instance.

## Current status

- All 3 pieces above are implemented, tested (67 tests passing;
  `test_pl_client_integration.py` no longer skipped/failing), and committed.
- `output/` holds 3 freshly-regenerated example instances (14/15/16) with the
  `pl-scinum-input` question rendered via `additional-elements`, for reference.
- Next: **Phase 4** — rich HTML → docx conversion (paragraphs/bold/italic/underline,
  currently flattened to plain text by `html_parser.py`'s `get_text()`), math → OMML,
  and embedding the images `fetch.py` already downloads but doesn't yet place in
  generated docx. Not yet scoped/planned in detail — start there.
