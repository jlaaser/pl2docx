# CLAUDE.md

## Project

Standalone, course-agnostic Python tool that generates paper-based (Word) versions of
PrairieLearn assessments: N randomized instances each as a blank student copy + matching
answer key, merged into an instructor-supplied template.

This tool is deliberately **not** part of any specific course repo. It should have no
hard dependency on any course's custom packages (e.g. the `chemutils` in the `../../chem 0110/pl-pitt-chem0110` course); course-specific
element rendering plugs in via an adapter (see "Extensibility" below).

## Reference material (read-only, do not modify)

PrairieLearn reference material (use these, don't rely on memory for platform specifics):
- Docs: https://docs.prairielearn.com/
- Source repo: https://github.com/PrairieLearn/PrairieLearn
- A read-only reference clone of the PrairieLearn repo lives at: ../../PrairieLearn
  Treat this as documentation only — never edit it. 
  
Course reference:
- Original course repo this was designed against: `../../chem 0110/pl-pitt-chem0110`
  DO NOT edit this course repo.  If making one of the course-specific input elements printable requires edits to the element definitions, provide the user with explicit instructions about what you need and wait for them to do it in a separate, dedicated session.

Design/planning notes:
- Full design/planning doc for this project: `planning_notes/2026-08-05_printable_assignments_planning.md`) — read this before making architectural
  changes; it captures the reasoning behind the approach below, not just the conclusions.

## Ground rules

- **No confidently-guessed PrairieLearn scaffolding.** Do not assume how a PL route,
  middleware, or DB query behaves — check it against the real PL source at
  `../../PrairieLearn` first. This tool depends on exact PL internals (cookies, routes,
  SQL selection logic); wrong guesses here fail silently and produce incorrect assessments.
- Prefer driving a real local PL server over reimplementing PL logic. Do not reimplement
  question/assessment selection (e.g. `numberChoose`) locally — see planning doc for why
  this was explicitly rejected.
- This is a standalone tool. Don't introduce a dependency from core tool code onto any
  specific course's custom packages — course-specific rendering goes through the adapter
  interface, not direct imports.

## Architecture (see planning doc for full rationale)

1. **Instance generation** — drive a real local PL server via the "view as student"
   mechanism (`instructorEffectiveUser`), then `studentAssessment` / `regenerate_instance`
   to create/recreate real `assessment_instances` rows.
2. **Blank copy** — GET each `instance_question/:id` while the instance is open.
3. **Answer key** — POST `__action=finish` to close the instance, then re-GET the same
   `instance_question` URLs (same variant, `showCorrectAnswer` now true).
4. **Static rendering of interactive elements**:
   - Course-owned fabric.js elements (e.g. `pl-orbitaldiagram`, future `pl-lewisstructure`):
     parse embedded `layout_json` straight out of fetched HTML, render locally as SVG.
   - Core/third-party fabric.js elements (e.g. `pl-drawing`): headless-browser fallback —
     load question, wait for fabric init, call `canvas.toSVG()` directly.
5. **HTML → Word** — purpose-built conversion layer (not generic pandoc), with
   run-time-configurable formatting per element type (e.g. MC as lettered list vs.
   fillable bubbles). Math via LaTeX → OMML. Template merge via `<TEMPLATING_LIBRARY>`
   (e.g. docxtpl / python-docx).

## Extensibility

Course-specific static renderers (like pl-orbitaldiagram and pl-lewisstructure in the example course) should be pluggable as
adapters, not hardcoded into the core tool. **Interface design deferred** — needs to be
worked out with real `layout_json` examples from the PL source/course repo in hand,
not guessed at up front.

## Tooling / environment

- Language: Python (confirmed — readability/comfort priority over alternatives).
- Package manager: **uv**.
- Docx templating: **docxtpl** (fall back to raw `python-docx` only if a merge pattern
  doesn't fit docxtpl's Jinja-style model).
- Headless browser tooling for the `pl-drawing` fallback: **Playwright**.
- Local PL server: `<HOW_TO_START_LOCAL_PL_SERVER>` — TBD with Claude Code once working
  against the real PL clone (setup, seed data, auth for the "view as student" flow).

## Open design decisions (not yet resolved — see planning doc "Open items")

- Blank-scaffold representation for draw-it-yourself elements (empty axes/template vs.
  reusing question-panel JSON).
- `pl-order-blocks` print representation.
- Docx templating mechanics for merging instructor template with generated content.
- Repo name / license / packaging.

## Verification checklist (once implemented)

- Generated instance question selection matches a real student flow (incl. `numberChoose`).
- All element types in current use render without errors in blank + key:
  `pl-multiple-choice`, `pl-number-input`, `pl-string-input`, `pl-checkbox`,
  `pl-scinum-input`, `pl-order-blocks`, `pl-dropdown`, `pl-symbolic-input`,
  `pl-integer-input`, `pl-image-capture`, `pl-orbitaldiagram`, `pl-chemformula-input`,
  `pl-drawing`.
- Math renders correctly in the resulting Word doc (spot-check against source LaTeX).
- N regenerations produce N distinct, correctly-paired blank/key docs with no state
  leakage between versions.
