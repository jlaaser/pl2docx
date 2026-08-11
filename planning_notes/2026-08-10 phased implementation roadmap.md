# pl2docx: Phased Implementation Roadmap

## Context

`pl2docx` turns PrairieLearn assessments into paper-based Word documents (N randomized
blank copies + matching answer keys, merged into an instructor template). The approach
was already decided in `planning_notes/2026-08-05 printable assignments planning.md`:
drive a real local PL dev server via the "view as student" mechanism rather than
reimplementing PL's selection/rendering logic. The repo currently contains only
CLAUDE.md, the planning doc, README, and LICENSE — no code yet. CLAUDE.md now also
specifies: Python 3.14 via the `py` launcher, pytest coverage for all critical
functionality, NumPy-style docstrings, a "propose a plan before nontrivial changes"
working style, and a "Repository structure" section that must be kept up to date as
scaffolding lands — each phase below that changes the folder layout should update that
CLAUDE.md section as part of the phase's work.

The user has a working local PL dev server (chem 0110 course loaded, run via
`docker run -it --rm -p 3000:3000 -v ~/pl-pitt-chem0110:/course prairielearn/prairielearn`
in WSL) but no experience breaking a project like this into stages. This plan lays out
an ordered sequence of small, independently-testable phases, so implementation can
proceed one phase at a time in future sessions without having to re-derive the whole
architecture each time.

**Ordering principle:** tackle the riskiest/most PL-internals-dependent piece
(live server interaction) first, since it's most likely to reveal wrong assumptions,
before investing in the docx rendering pipeline. Within docx generation, get the
simplest possible content end-to-end through the real template-merge mechanics before
layering in math, custom static-element rendering, and third-party fallback rendering.
Elements' open design questions (blank scaffolds, `pl-order-blocks` layout) are deferred
to the phase where they're actually implemented, not resolved speculatively now.

## Phase breakdown

### Phase 0 — Project scaffolding
- `uv` project init (`pyproject.toml`), base package layout (e.g. `src/pl2docx/`).
- Config for pointing at a local PL server — base URL (default `http://localhost:3000`),
  course instance ID, assessment ID — via a **config file**, not CLI args.
- Update CLAUDE.md's "Repository structure" section to reflect the new layout once this
  scaffolding is in place.
- No PL-specific logic yet; just a runnable skeleton and dependency management.

### Phase 1 — Live PL server driver (highest risk, done first)
Goal: a Python client that can, against the real local dev server, reliably:
1. Establish an authenticated `requests.Session()`. Confirmed mechanism: PL's dev-mode
   auto-auth (`middlewares/authn.ts:96-125`) authenticates any request automatically
   when `config.devMode` is true (default for non-production Docker runs) — no explicit
   login call needed, but every POST needs a fresh CSRF token scraped from a prior GET's
   `__csrf_token` hidden field (or `x-csrf-token` header), signed per-URL/per-user
   (`middlewares/csrfToken.ts:24-33`).
2. Call `instructorEffectiveUser` to enter "view as student" mode (course-preview
   permission only, no site-admin).
3. Create an assessment instance via `studentAssessment`, and recreate it via
   `__action=regenerate_instance` on `studentAssessmentAccess` for repeatable N-version
   generation.
4. Enumerate `instance_questions` for the instance and GET each `instance_question/:id`
   page while open → this is the **blank** copy HTML.
5. POST `__action=finish` to close the instance, then re-GET the same
   `instance_question/:id` URLs → this is the **answer key** HTML (same variant, so
   blank/key correspond exactly).
Deliverable: a script that, given an assessment ID, produces N pairs of raw HTML files
(blank + key) saved to disk. No parsing/rendering yet — just prove the live-server flow
is correct and repeatable.
Validation: spot-check that instance generation matches real student behavior,
particularly once a `numberChoose` assessment exists (per the planning doc's own
verification checklist item), **and also against an assessment using multiple question
alternatives** (PL's separate "alternatives" mechanism, distinct from `numberChoose` —
see PL docs) once one exists to test against.

### Phase 2 — Minimal HTML → Word pipeline (prove the merge mechanics)
Goal: get `docxtpl` template-merging working end-to-end on real fetched HTML, restricted
to the simplest element types only (e.g. `pl-multiple-choice`, `pl-string-input`,
`pl-integer-input`, `pl-checkbox`, `pl-dropdown` — no math, no images, no custom
elements). Use the Phase 1 output HTML as real input fixtures.
Deliverable: given a blank+key HTML pair and an instructor-supplied template docx,
produce real, openable blank and key `.docx` files with those simple elements rendered
reasonably (even if formatting is not yet configurable).
This validates the template-merge approach before sinking time into per-element
formatting detail.

### Phase 3 — Configurable per-element formatting
Extend Phase 2's renderers with the run-time-configurable formatting the user wants
(e.g. MC as lettered list vs. fillable bubbles; number input as labeled blank vs. open
space). Introduce whatever config surface makes sense (per-assessment or per-element
settings) — deferred design choice, decide when implementing this phase.

### Phase 4 — Math rendering
LaTeX (raw, since MathJax is client-side only) → OMML in the Word output. Spot-check
against source LaTeX per the planning doc's verification checklist.

### Phase 5 — Course-owned static elements (pl-orbitaldiagram, future pl-lewisstructure)
- Define the adapter interface for course-specific static renderers (deferred design
  from CLAUDE.md — do this with real `layout_json`/rendering samples in hand, pulled from
  Phase 1 fetched HTML).
- **Rendering approach is a genuinely open decision, to revisit at this phase rather than
  now**: `pl-lewisstructure` already has a "print" mode that determines what's shown to
  the student and already renders as SVG natively (not a fabric.js canvas), so this
  element may just need its print-mode output consumed directly rather than having its
  geometry reproduced here. `pl-orbitaldiagram` could gain an analogous print mode
  (course-repo change, done by the user in a separate session per CLAUDE.md's rule against
  editing the course repo directly), or this tool could parse `layout_json` and render
  SVG locally as originally sketched (reusing
  `serverFilesCourse/chemutils/utility_scripts/render_orbital_diagram.py`'s approach).
  Since both elements are under active development, reproducing their rendering logic
  here risks drifting out of sync with the source elements — leaning on each element's
  own print-mode output, where available, is likely preferable to reimplementing
  rendering in this tool. Decide per-element when this phase starts, informed by
  whatever each element's print-mode support looks like at that time.
- Whichever approach is chosen, resolve the open item of what "blank scaffold"
  representation to use for the student copy (empty template vs. reusing print-mode
  output) as part of implementing it.
- Embed resulting SVGs into the docx output.

### Phase 6 — Third-party/core fallback (pl-drawing)
Headless-browser (Playwright, per CLAUDE.md) fallback: load the question, wait for
fabric.js init, call `canvas.toSVG()` directly. Generalize enough to be reusable for any
future fabric.js element without native cooperation, without over-engineering ahead of
a second real use case.

### Phase 7 — Remaining element types and long-tail design decisions
- `pl-order-blocks` print representation (design decision deferred from planning doc).
- Any other element types from the verification checklist not yet covered
  (`pl-scinum-input`, `pl-symbolic-input`, `pl-image-capture`, `pl-chemformula-input`,
  etc.) — likely fall under Phase 2/3's generic input-rendering path, confirm case by
  case.
- **Compound questions with multiple named input widgets on one page** (confirmed real
  content, not hypothetical: `physical-or-chemical` renders 3 separate
  `pl-multiple-choice` sub-statements, each its own widget with a distinct `name`, e.g.
  via `display="dropdown"`; `previous-experience` combines a radio group and a text box
  on one page). Phase 2's `ParsedQuestion`/`html_parser.py` deliberately rejects these
  (`UnsupportedElementError`) rather than mis-rendering — real fix needs
  `ParsedQuestion`'s one-widget-per-question shape reworked to hold multiple named
  sub-answers per question.
- Document the Option-1 manual/Cowork fallback path for any question that still doesn't
  fit.

### Phase 8 — End-to-end N-version generation + full verification
Wire Phases 1–7 into a single CLI flow: given an assessment + N, produce N distinct,
correctly-paired blank/key docx files via `regenerate_instance` between each, merged
into the instructor template. Run the full verification checklist from CLAUDE.md:
question-selection fidelity, all element types render without error, math correctness,
no state leakage across the N regenerated instances.

## Verification approach per phase
Each phase should be validated against the real local PL server / real course content
before moving to the next phase, not just unit-tested in isolation — this project's own
ground rule is to avoid confidently-guessed PL behavior. Phase 1 in particular should be
checked against actual student-flow behavior once a `numberChoose` assessment exists,
and separately against an assessment using multiple question alternatives.

## Next step
Once this roadmap is approved, the next session should scope **Phase 0 + Phase 1** into
a concrete implementation plan (dependencies, exact module layout, error handling for
auth/CSRF failures) and begin implementation there.
