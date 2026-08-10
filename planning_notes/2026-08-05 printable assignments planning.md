Discussed possible ways of generating printable assignments from PrairieLearn assignments with Claude.  Summary of discussion to feed in when I am ready to implement this:

# Recommendation: Generating paper-based assessments from a PrairieLearn question bank

**This is an informational/planning document only — no code changes are proposed or made in this session.**

## Context

The user defines assessments in PrairieLearn but also wants to generate paper-based versions of
those same assessments: N randomized versions each, as a blank student copy and a matching
answer key, in Word (preferred) or LaTeX, merged into an instructor-supplied template
(instructions, reference data), with interactive elements (pl-orbitaldiagram, future
pl-lewisstructure, core pl-drawing) rendered statically and other element types (multiple
choice, fill-in, pl-order-blocks, etc.) formatted sensibly for print. This capability is also
wanted by other instructors and is on PrairieLearn's own roadmap, but not shipping soon enough —
so the plan targets a **standalone, course-agnostic tool** (own repo), not something wired into
this course repo specifically.

Three options were brainstormed: (1) manual Cowork-driven browser workflow, (2) a script that
drives the real PrairieLearn server programmatically, (3) a script that bypasses the PL server
and reimplements question/assessment generation directly from infoAssessment.json + question
files. This doc recommends between them based on research into both this course repo and the
actual PrairieLearn source (reference clone at `teaching-materials/PrairieLearn`), refined
through follow-up questions from the user.

## Recommendation: Option 2 (script drives a real local PL server), refined

**Reject Option 3.** Some future assessments will use `numberChoose`/question alternatives (user
confirmed none currently do, but will soon). That selection logic
(`apps/prairielearn/src/lib/assessment.sql`, ~lines 137–260) is a raw Postgres CTE chain using
`random()` + `row_number() OVER (PARTITION BY ...)`, tightly coupled to the DB. Reimplementing it
risks silent drift from real PL behavior, conflicting with this repo's own ground rule against
confidently-guessed PrairieLearn scaffolding. Per-element `render()`/`parse()` logic would also
need duplicating for every element type in the bank.

**Reject Option 1 as the primary mechanism** (manual, not repeatable at scale), though it remains
a reasonable fallback for the rare question that doesn't fit the automated formatter.

### How to generate real, randomized assessment instances (revised)

Not the site-admin `generate_assessment_instances` admin-query route originally proposed —
that's unnecessarily high-privilege. Instead, drive the same **"view as student"** mechanism
already exposed in the instructor UI, which the user pointed out lets an instructor regenerate
fresh instances at will:

- `instructorEffectiveUser` (`apps/prairielearn/src/pages/instructorEffectiveUser/instructorEffectiveUser.ts:41-89`)
  just sets role-override cookies (`pl2_requested_course_role`/`pl2_requested_course_instance_role`).
  POSTing `changeCourseRole=None`/`changeCourseInstanceRole=None` (no fake `changeUid` needed —
  same account works) is enough; only "course preview" permission is required, not site-admin
  (`middlewares/authzCourseOrInstance.ts:503-506`).
- With that cookie set, GET/POST against `studentAssessment` creates a **real** row in
  `assessment_instances` via the exact same `makeAssessmentInstance()`
  (`lib/assessment.ts:122`) → `insert_instance_questions` (real zone/`numberChoose` selection)
  that a genuine student hits (`pages/studentAssessment/studentAssessment.ts:73,166`).
- For N versions: `studentAssessmentAccess.ts:67-83`'s `__action=regenerate_instance` deletes and
  recreates the instance cleanly, gated only by `canDeleteAssessmentInstance()` — no fake users,
  no admin privileges, fully repeatable.

### Getting blank HTML and the answer key from the same instance

- **Blank copy**: right after creating/regenerating the instance (while still open), GET each
  `instance_question/:id` page (enumerable from the instance's `instance_questions` rows). This
  lazily creates each question's variant and returns server-rendered HTML with raw LaTeX source
  (MathJax is client-side only — confirmed in `lib/client/mathjax.ts` — so no headless browser
  is needed for math).
- **Answer key, same instance**: POST `__action=finish` to the assessment-instance route
  (`studentAssessmentInstance.ts:135-186`) to close it — instructor edit permission suffices, no
  special privilege needed. `question-render.ts:335-344` shows that once
  `assessment_instance.open` is false, `showCorrectAnswer` flips true automatically for that
  question's render, and **no submission is required**. Re-GET the *same* `instance_question`
  URLs used for the blank copy — same variant is reused, so blank and key correspond exactly —
  and the answer panel now renders.
- This resolves what was previously an inconsistency in this plan (mixing an admin-query
  generation path with an instructor-preview render path); it's now a single coherent flow:
  view-as-student → generate/regenerate instance → GET each question (blank) → close → GET same
  questions again (key).

### Rendering interactive elements statically for print

Research (this session) found that **both `pl-drawing` (PrairieLearn core) and this course's
`pl-orbitaldiagram` already render genuinely non-interactive `fabric.StaticCanvas` instances for
submission/answer panels** — no drag/select capability, not just a hidden toolbar
(`pl-drawing.js:211-217`; `pl-orbitaldiagram.js` `setupStatic()`/`setupInteractive()` split, with
`selectable:false`/`evented:false` set on the JSON payload itself). So there's no grading-UI bug
to fix here — the print-mode work below is purely additive, for print output specifically.
*(Correction to an earlier version of this plan, which incorrectly implied a grading-view gap.)*

Two different mechanisms, by ownership:

1. **This course's own elements (pl-orbitaldiagram, future pl-lewisstructure) — no PrairieLearn
   changes needed at all.** Per CLAUDE.md's own fabric.js convention, these elements already
   compute static geometry server-side and embed it as JSON in a `<script>` tag on the page
   (`layout_json`, etc.) — this JSON is present in the plain-HTTP-fetched question-panel HTML
   regardless of interactivity. The print tool can simply **parse that embedded JSON straight out
   of the fetched HTML** and feed it to a small local static-SVG renderer, reusing the pattern
   already established by the standalone scripts
   `serverFilesCourse/chemutils/utility_scripts/render_orbital_diagram.py` (and the planned
   `render_lewis_structure.py`, per `LEWIS_STRUCTURE_NOTES.md:104-132`). No new "print mode"
   signal needs to be threaded into `render()`, no live-server cooperation required beyond the
   normal question-panel fetch — this runs entirely in the print tool's own process. The answer
   key's correct-diagram geometry comes from parsing the equivalent JSON in the answer panel's
   HTML (needs a quick implementation-time check that pl-orbitaldiagram's answer panel actually
   embeds full correct-state geometry, not just a diff/summary).
   - Open design nuance: for these draw-it-yourself element types, the *blank* print version
     needs an empty scaffold (axes/template) for the student to fill in by hand, not the
     interactive-question-panel markup — worth a small implementation-time check on exactly what
     the question-panel's embedded JSON contains when unanswered, versus needing a
     dedicated "blank" render.

2. **Third-party/core elements not under our control (pl-drawing, currently used in 1-2
   questions, likely more later) — need a generic browser-automation fallback**, since research
   confirmed PrairieLearn core never calls fabric's `canvas.toSVG()` anywhere and there is no
   server-side static-render or export path to reuse. The fallback: load the question in a real
   (headless) browser, wait for the fabric canvas to finish client-side init, then inject JS to
   call `canvas.toSVG()` directly against the live fabric instance — fabric.js supports this
   method even though PrairieLearn's own code never invokes it, so this yields real vector output
   rather than a raster screenshot. This path is naturally reusable for *any* future
   fabric.js-based element the print tool doesn't have native cooperation with, so it doesn't need
   to be special-cased per element beyond knowing which canvas/DOM node to target.

### HTML → Word conversion, with user-configurable formatting

The user wants tight layout control, so this should be a purpose-built conversion layer (not
generic pandoc) that special-cases PL's element output patterns — **and should expose
run-time-configurable formatting choices** rather than hardcoding one style per element type,
since the same element type prints differently depending on the assessment's answer-sheet setup.
Examples the user raised:
- `pl-multiple-choice`/`pl-checkbox`: lettered list (separate answer sheet) vs. fillable bubbles
  (answer given directly on the page) — a formatting option, not a fixed choice.
- `pl-number-input`/`pl-scinum-input`/etc.: a labeled blank-with-units line vs. open free-response
  space — also a formatting option.
- Math spans → Word equations (OMML) from the raw LaTeX source.
- `pl-orbitaldiagram`/`pl-lewisstructure`/`pl-drawing` → embedded SVG.
- `pl-order-blocks` print representation still needs a specific design decision (not resolved
  yet — likely boxed source list + blank ordering area, but worth deciding deliberately).

The instructor-supplied template (instructions, reference data) merges with generated content via
a docx templating approach (e.g. `docxtpl` or `python-docx` with placeholder bookmarks/sections).

### Fallback for the long tail

If some question/element type doesn't fit the automated formatter well, Option 1 (manual,
Cowork-assisted handling) remains a reasonable stopgap for just that question, rather than
blocking the whole pipeline.

## Key files (from research this session)

- `apps/prairielearn/src/pages/instructorEffectiveUser/instructorEffectiveUser.ts:41-89` — role
  override cookies ("view as student")
- `apps/prairielearn/src/middlewares/authzCourseOrInstance.ts:503-603` — permission checks for
  effective-user downgrade (only course-preview needed, not site-admin)
- `apps/prairielearn/src/pages/studentAssessment/studentAssessment.ts:73,166` — real instance
  creation via `makeAssessmentInstance`
- `apps/prairielearn/src/pages/studentAssessmentAccess/studentAssessmentAccess.ts:67-83` —
  `__action=regenerate_instance` (delete+recreate) for repeatable N-version generation
- `apps/prairielearn/src/lib/assessment.ts:122,178,255,292-301` — `makeAssessmentInstance`,
  `updateAssessmentInstance` (real zone/`numberChoose` selection), `gradeAssessmentInstance`
  (close)
- `apps/prairielearn/src/pages/studentAssessmentInstance/studentAssessmentInstance.ts:135-186` —
  `__action=finish` (close), usable by instructor edit permission
- `apps/prairielearn/src/lib/question-render.ts:335-344,474-501` — `showCorrectAnswer` gating on
  `assessment_instance.open`; variant creation doesn't require an open instance for Exam-type
- `apps/prairielearn/src/lib/client/mathjax.ts` — confirms MathJax is client-side only (raw LaTeX
  in server HTML)
- `elements/pl-drawing/pl-drawing.py:315-317`, `pl-drawing.js:198-217` — real
  `fabric.StaticCanvas` non-interactive mode already in core; no `toSVG()` usage anywhere in PL
- `elements/pl-orbitaldiagram/pl-orbitaldiagram.py:207-296`, `pl-orbitaldiagram.js`
  `setupStatic()`/`setupInteractive()` — same real non-interactive pattern in this course's
  element; embedded `layout_json` is the reusable print-rendering input
- `serverFilesCourse/chemutils/utility_scripts/render_orbital_diagram.py` — existing standalone
  static-SVG renderer to build the print path from
- `serverFilesCourse/chemutils/LEWIS_STRUCTURE_NOTES.md:104-132` — planned analogous approach for
  `pl-lewisstructure`

## Open items to resolve before implementation (not decided in this session)

- Whether pl-orbitaldiagram's answer-panel JSON payload contains full correct-state geometry
  (needed for the answer key) vs. only a diff/score summary — quick live check.
- Exact "blank scaffold" representation for pl-orbitaldiagram/pl-lewisstructure's print blank
  copy (empty axes/template vs. reusing question-panel JSON as-is).
- `pl-order-blocks` print representation design.
- Choice of headless browser tooling for the pl-drawing (and future similar) fallback path.
- Docx templating mechanics for merging instructor template with generated content.
- Repo/packaging setup for the new standalone tool (name, license if shared publicly, how a
  per-course "adapter" for custom elements like pl-orbitaldiagram would plug in without the core
  tool depending on any specific course's `chemutils` package).
- Implementation language: **Python**, confirmed by the user (readability/comfort over any other
  language consideration).

## Verification (once implemented)

- Confirm generated instances' question selection matches what a real student would see for an
  assessment using `numberChoose` (spot-check against a real student-flow instance).
- Confirm blank and answer-key documents render every element type currently in use in the bank
  (sampled: `pl-multiple-choice`, `pl-number-input`, `pl-string-input`, `pl-checkbox`,
  `pl-scinum-input`, `pl-order-blocks`, `pl-dropdown`, `pl-symbolic-input`, `pl-integer-input`,
  `pl-image-capture`, `pl-orbitaldiagram`, `pl-chemformula-input`, `pl-drawing`) without errors or
  missing content.
- Confirm math renders correctly in the resulting Word document (spot-check equations against
  source LaTeX).
- Confirm regenerating an instance N times and closing each produces N distinct, correctly-paired
  blank/key documents with no state leakage between versions (e.g. via `regenerate_instance`
  between each).