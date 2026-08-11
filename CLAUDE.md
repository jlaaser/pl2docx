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
  Treat this as documentation only — never edit it. This clone can go stale relative to
  upstream (e.g. it missed the newer `accessControl` system for a while) — if research
  against it turns up something that seems to contradict current PL docs or behavior,
  ask the user to `git pull` it before concluding a feature doesn't exist.
  
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
  
## Repository structure

This repository should be structured such that it can be distributed as a python package that other users can download and install.

Current structure:
```
.
├── .git/                          # Git repository for pl2docx project
├── planning_notes/                # notes about architecture decisions, etc
├── src/pl2docx/                   # package source (uv src layout)
│   ├── config.py                  # Config dataclass + load_config() (reads config.yaml)
│   ├── csrf.py                    # extract_csrf_token() - scrape PL's per-request CSRF token
│   ├── pl_client.py               # PLClient - drives the real PL server (auth, effective-user,
│   │                               #   instance create/regenerate, blank/key HTML fetch)
│   └── fetch.py                   # CLI entry point (python -m pl2docx.fetch / `pl2docx-fetch`)
├── tests/
│   ├── test_csrf.py                        # unit tests, no live server needed
│   └── test_pl_client_integration.py       # full flow against the real local server;
│                                             #   self-skips if config.yaml or the server is absent
├── config.example.yaml            # template - copy to config.yaml (gitignored) and fill in
├── pyproject.toml                 # uv-managed; Python 3.14, deps: requests/beautifulsoup4/pyyaml
└── output/                        # fetched instance HTML (gitignored, created at runtime)
```

Update this repository structure description as needed when significant changes are made to the folder structure or organization of files.

## Architecture (see planning doc for full rationale)

1. **Instance generation** — drive a real local PL server as an authenticated course
   staff member (Previewer role or above), hitting the real student-facing routes
   directly (GET the assessment page, POST `__action=regenerate_instance` to the
   resulting `assessment_instance` page) to create/recreate real `assessment_instances`
   rows. **Correction (2026-08-10):** earlier versions of this doc recommended the
   "view as student" role-override mechanism (`instructorEffectiveUser`,
   `pl_requested_course_role=None`). That was wrong for this tool's actual goal:
   overriding down to plain "Student" makes PL enforce the assessment's real
   `accessControl` rules, which 403s when none are configured — exactly what this tool
   wants (to avoid ever exposing a real exam/quiz to real students via access windows).
   Instead, staying at the authenticated user's real staff role and skipping any role
   override triggers PL's "Student view without access restrictions" bypass
   (`lib/assessment-access-control/resolver.ts`'s `isStaff()` check short-circuits past
   `accessControl` entirely for Previewer+ roles) — same routes, same downstream
   instance-creation code, no access rules needed. Also note: the `regenerate_instance`
   POST goes to the *`assessment_instance`* page (that's where PL's own regenerate form
   submits, since it has no `action` attribute), not the `assessment` page.
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
	- **Python interpreter**: use Python 3.14 (`py -3.14` via the Windows `py` launcher) for all
  local Python work in this repo — running scripts, running pytest, installing packages for
  local verification. The Anaconda 3.9 install on this machine is legacy and may have a broken
  environment (numpy/ssl DLL issues have been observed there); don't fall back to it.
  - For all critical functionality, write appropriate pytest tests and place them in the test directory. Verify that all tests pass before committing changes.
- Package manager: **uv**.
- Docx templating: **docxtpl** (fall back to raw `python-docx` only if a merge pattern
  doesn't fit docxtpl's Jinja-style model).
- Headless browser tooling for the `pl-drawing` fallback: **Playwright**.
- **Local PrairieLearn Server**: a local PrairieLearn dev instance
  is running via wsl and Docker at http://localhost:3000/.  If this does not load, it can be restarted by running `docker run -it --rm -p 3000:3000 -v ~/pl-pitt-chem0110:/course prairielearn/prairielearn` in wsl.  If this fails, ask for help - it may require the user to perform a manual restart.
  
## Docstrings

Use **NumPy-style** docstrings. Required elements:

- One-line summary stating *what* the function does (its result/effect), not how it's
  implemented.
- `Parameters` section: every parameter with its type and its physical/domain meaning,
  not just a restatement of the type. State units explicitly for any non-dimensionless
  numeric quantity.
- `Returns` section: same standard as above.
- `Raises` section if the function can raise on invalid input.
- A `Notes` section whenever there's a non-obvious assumption, invariant, or edge case —
  in particular, state explicitly whether a function assumes its input represents a
  physically *valid* configuration or operates correctly on arbitrary/invalid states too
  (this distinction matters throughout this codebase; see the chemutils section above).
- A short `Examples` block is encouraged but not required, especially for anything
  non-obvious from the signature alone.

Example:

```python
def hund_violations(system: OrbitalSystem) -> list[EnergyLevel]:
    """Identify degenerate energy levels that violate Hund's rule of maximum multiplicity.

    Parameters
    ----------
    system : OrbitalSystem
        The orbital system to check. May represent a physically invalid
        configuration; this function does not assume `system` is otherwise
        valid.

    Returns
    -------
    list[EnergyLevel]
        Energy levels whose degenerate slot group violates Hund's rule. Empty
        if none. Non-degenerate levels are never included.

    Notes
    -----
    Checks Hund's rule in isolation from Aufbau and Pauli exclusion — a level
    can appear here even if it also has other violations.
    """
```

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

## Working style

- For anything nontrivial (schema changes, changes touching multiple
  submodules), propose a short plan before writing code, and wait for confirmation.
- Prefer small, independently verifiable increments over large multi-part changes.
- If existing conventions in this repo conflict with general best practice, follow the
  existing convention and note the discrepancy rather than silently introducing a new style.
