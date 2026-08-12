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
│   ├── config.py                  # Config dataclass + load_config() (reads config.yaml,
│   │                               #   incl. template_path)
│   ├── csrf.py                    # extract_csrf_token() - scrape PL's per-request CSRF token
│   ├── pl_client.py               # PLClient - drives the real PL server (auth, effective-user,
│   │                               #   instance create/regenerate, blank/key HTML fetch,
│   │                               #   binary/image fetch); parse_zone_groups() - pure fn parsing
│   │                               #   zone/question structure from the assessment_instance page
│   ├── fetch.py                   # CLI entry point (python -m pl2docx.fetch / `pl2docx-fetch`) -
│   │                               #   also downloads same-origin <img>s into files/ next to each
│   │                               #   instance_question's HTML, rewriting src to the local path,
│   │                               #   and writes structure.json (zone titles + question order/ids)
│   ├── html_parser.py             # parse_instance_question_html() -> ParsedQuestion; supports
│   │                               #   pl-multiple-choice/checkbox/string-input/integer-input only
│   │                               #   (Phase 2/3) - raises UnsupportedElementError for anything
│   │                               #   else or compound multi-widget questions (Phase 3B territory);
│   │                               #   also extracts qid (Staff info panel) and points_numeric
│   ├── element_renderer.py        # build_question_context() - one ParsedQuestion -> the 3 Subdocs
│   │                               #   (question_contents/answer_contents/answer_space) + qid/points
│   │                               #   a question's Jinja context needs. Phase 3A: fixed formatting
│   │                               #   (Phase 2-equivalent); Phase 3B adds config-driven list-style/
│   │                               #   display/fill-in-format choices and compound-widget support.
│   ├── docx_builder.py            # render_document() - assembles the zones/questions context and
│   │                               #   calls docxtpl render/save; does NO per-element formatting
│   │                               #   itself (see element_renderer.py) - document *layout* lives in
│   │                               #   the instructor's template (loop tags + named styles), not here
│   ├── starter_template.py        # build_starter_template() - CLI (`pl2docx-starter-template`)
│   │                               #   generates an editable example instructor template (zones/
│   │                               #   questions loop + named "pl2docx ..." styles), via python-docx
│   │                               #   (not committed as a binary - same reasoning as test fixtures)
│   └── render.py                  # CLI entry point (python -m pl2docx.render / `pl2docx-render`) -
│                                   #   renders one fetch.py output/<instance>/ dir (using its
│                                   #   structure.json for zone/question order) into blank+key docx
├── tests/
│   ├── conftest.py                          # starter_template fixture (generated at test time,
│   │                                         #   via the real build_starter_template())
│   ├── fixtures/instance_question/          # synthetic (not real course content) sample HTML,
│   │                                         #   one blank+key pair per Phase 2 element kind
│   ├── test_csrf.py                         # unit tests, no live server needed
│   ├── test_html_parser.py                  # unit tests, no live server needed
│   ├── test_docx_builder.py                 # unit tests, no live server needed
│   ├── test_starter_template.py             # unit tests, no live server needed
│   ├── test_pl_client.py                    # unit tests for parse_zone_groups(), no live server needed
│   ├── test_fetch.py                        # unit tests for image download/rewrite, no live server needed
│   └── test_pl_client_integration.py        # full flow against the real local server;
│                                             #   self-skips if config.yaml or the server is absent
├── config.example.yaml            # template - copy to config.yaml (gitignored) and fill in
├── pyproject.toml                 # uv-managed; Python 3.14, deps: requests/beautifulsoup4/pyyaml/
│                                   #   docxtpl/docxcompose
└── output/                        # fetched instance HTML + generated docx (gitignored, runtime);
                                    #   per instance: blank/, key/ (each with a files/ subdir of
                                    #   downloaded images), and structure.json (zone/question layout)
```

**docxtpl gotchas** (all confirmed this project, not guessed):
- Inserting a `docxtpl.Subdoc` (from `doc.new_subdoc()`) requires the template tag to be
  `{{p content }}` — the `p` prefix is docxtpl's paragraph-level substitution syntax.
  Plain `{{ content }}` silently produces a broken docx (the subdoc's raw XML ends up as
  literal text inside the placeholder's own run, invisible to `python-docx`'s normal
  readers).
  - **Mechanism** (`docxtpl/template.py`'s `patch_xml`, the `for y in ["tr","tc","p","r"]`
    loop): before Jinja ever sees the document, a regex finds the *entire* `<w:p>...</w:p>`
    containing a `{%p `/`{{p ` (or `tr`/`tc`/`r`) tag and replaces the **whole paragraph
    element** with the bare `{% %}`/`{{ }}` tag text (no `<w:p>` wrapper at all) — that's
    how the substituted subdoc's own `<w:p>` fragments end up as real sibling paragraphs
    instead of nested inside a run's `<w:t>`.
  - **Corollary**: this means `{{p content }}` must be **completely alone** in its
    paragraph — not just other prose, but *any other Jinja tag too* (e.g.
    `{% endif %}{{p content }}` in one paragraph). The regex still matches and still
    replaces the whole paragraph, so the other tag's text is silently deleted as
    collateral damage — not an error at the deletion point, but it breaks that tag's
    open/close pairing, surfacing later as a confusing unrelated error (e.g. Jinja
    reporting an unexpected `endfor` while still expecting `endif`, from an `{% if %}`
    two paragraphs earlier that just lost its matching `{% endif %}`).
- A `{% if %}`/`{% endif %}` (or `{% for %}`/`{% endfor %}`) must each be **on their own
  paragraph** to cleanly omit/repeat a whole paragraph — wrapping just the printed value
  inline (e.g. `{% if x %}{{ x }}{% endif %}` in one paragraph) still leaves an empty,
  styled paragraph behind when the condition is false.
  - **To eliminate that blank paragraph** (distinct from the corollary above — this is
    about *merging* a control tag's paragraph into its neighbor, not combining it with
    another tag): use Jinja's whitespace-trim syntax, `{%- if x -%}`/`{%- endif %}` —
    docxtpl explicitly supports this (dedicated regexes in `patch_xml` merge the
    `<w:t>` before a leading `{%-` and after a trailing `-%}` into the adjacent
    paragraph). Confirmed caveat: the merge keeps only *one* paragraph's style (the
    surviving/absorbing paragraph's), so content that was on its own differently-styled
    paragraph loses that distinct styling once merged in — a real trade-off between
    "no blank line" and "independently stylable", not a free win.
- Never describe `{{ }}`/`{% %}` tag syntax in plain instructional text placed *inside*
  a template docx — docxtpl parses the entire document as Jinja source, so even prose
  like "edit the `{% for %}` tags below" breaks compilation. (Hit this authoring
  `starter_template.py`'s own instructions paragraph.)
- A **paragraph border** applied to a `{{p ... }}` line is silently discarded — same root
  cause as the corollary above (the whole paragraph, `<w:pPr>` and all, gets replaced by
  the substituted subdoc content). Use a **table cell border** instead (1x1 table, border
  on the cell via `<w:tcBorders>`): a cell's border isn't paragraph-level, so it survives
  and correctly wraps the substituted content regardless of how many paragraphs it expands
  into. `starter_template.py`'s `_add_bordered_solution_box()` does this for the answer key
  "SOLUTION:" box.
- If the template docx is open in Word (or a cloud-sync tool like OneDrive hasn't finished
  writing it after a save) when `render_document()` runs, `python-docx` raises a cryptic
  `PackageNotFoundError` deep inside `docxtpl`'s subdoc creation. `docx_builder.py` now
  catches this and re-raises as `TemplateUnreadableError` with an actionable message —
  close the file in Word and let syncing finish, then retry.

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
2. **Blank copy** — GET each `instance_question/:id` while the instance is open. Also
   downloads any same-origin `<img>`s embedded in the page (saved to a `files/` folder
   next to the HTML, `<img src>` rewritten to the local path) — done at fetch time, not
   deferred to rendering, since some image URLs (`generatedFilesQuestion`) are keyed to a
   variant id and aren't guaranteed stable long-term
   (`planning_notes/2026-08-11 image and metadata fetch plus roadmap notes.md`).
3. **Answer key** — POST `__action=finish` to close the instance, then re-GET the same
   `instance_question` URLs (same variant, `showCorrectAnswer` now true).
   `list_instance_questions`/`parse_zone_groups` also capture zone titles and question
   order from the assessment-instance overview page (only place zone titles exist),
   persisted per instance as `structure.json` — not yet consumed by rendering (Phase 3).
4. **Static rendering of interactive elements**:
   - Course-owned fabric.js elements (e.g. `pl-orbitaldiagram`, future `pl-lewisstructure`):
     parse embedded `layout_json` straight out of fetched HTML, render locally as SVG.
   - Core/third-party fabric.js elements (e.g. `pl-drawing`): headless-browser fallback —
     load question, wait for fabric init, call `canvas.toSVG()` directly.
5. **HTML → Word** — purpose-built conversion layer (not generic pandoc). **Phase 3
   architecture**: document *layout* (zone/question loop, headers, page structure,
   named Word styles for appearance) lives in the instructor's own docx template,
   authored/edited in Word; all *content* (per-element formatting, matching/bolding,
   eventually math/rich-HTML) is built in Python and handed to the template as
   `docxtpl.Subdoc`s per question. This split was chosen over generating the whole
   document in Python (Phase 2's approach) specifically so instructors can restyle
   fonts/colors/spacing/borders and rearrange document sections themselves, without a
   pl2docx code change for every cosmetic preference — see
   `planning_notes/2026-08-10 phased implementation roadmap.md`'s Phase 3 note and this
   session's spike (throwaway, confirmed: per-iteration subdocs in a `{% for %}` loop
   don't cross-contaminate; named styles defined in the template are correctly picked
   up by Python-built subdoc content).
   - `element_renderer.py` builds each question's 3 subdocs
     (`question_contents`/`answer_contents`/`answer_space`) plus its `qid`/
     `points_numeric`/`points_text`. **Phase 3A status**: fixed formatting, equivalent
     in substance to Phase 2's old behavior. **Phase 3B** (not yet implemented) adds
     `config.yaml`-driven per-element-type formatting (list style: letter-labels/
     bubble/checkbox; block vs. inline; bold-correct; fill-in blank format) and
     best-effort compound-multi-widget-question support.
   - `docx_builder.py`'s `render_document()` assembles the `zones` context and calls
     `docxtpl`'s render/save — no per-element formatting decisions of its own.
   - `starter_template.py` generates an example instructor template exercising the
     full context shape (zone titles, question number/title/points/qid, the three
     subdoc insertions, an `is_answer_key` branch) with named "pl2docx ..." styles
     ready to restyle in Word.
   - Math/rich-HTML prompt formatting and image embedding remain Phase 4 (raw LaTeX
     and dropped images are still visible in current output — known, not a bug).
   - Compound questions (multiple input widgets on one page, e.g.
     `physical-or-chemical`'s 3 dropdown sub-statements, `previous-experience`'s radio
     group + text box) still raise `UnsupportedElementError` — Phase 3B's scope, not
     yet implemented.

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
  doesn't fit docxtpl's Jinja-style model) + **docxcompose** (docxtpl's subdoc feature
  requires it). See the `{{p content }}` gotcha noted under Repository structure.
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
- **Compound questions with multiple named input widgets on one page** (confirmed real
  content: `physical-or-chemical`'s 3 separate `pl-multiple-choice` dropdown
  sub-statements; `previous-experience`'s radio group + text box). Currently rejected by
  `html_parser.py` (`UnsupportedElementError`) rather than mis-rendered. **Revised
  2026-08-12**: no longer deferred — in scope for Phase 3B, best-effort (render each
  widget inline within the question; address specific fragility if/when it surfaces).
  Full spec: `planning_notes/2026-08-12 phase 3a implementation and phase 3b spec.md`.
- **Document-structure-level formatting configuration**: **done as of Phase 3A**
  (2026-08-12) — question number/title/points/qid and zone titles are all exposed to
  the instructor's template; see Architecture item 5.
- **Element/question-level formatting configuration (Phase 3B)**: not yet implemented.
  Full user-provided specification (config.yaml schema, per-list-style/display/fill-in
  options, defaults) preserved in
  `planning_notes/2026-08-12 phase 3a implementation and phase 3b spec.md` — read that
  before starting Phase 3B rather than re-deriving requirements.
- **Rich HTML → docx conversion and image embedding** (Phase 4, reframed from "Math
  rendering" — same underlying "walk the HTML and convert it properly" work): paragraphs/
  bold/italic/underline, currently flattened to plain text by `html_parser.py`'s
  `get_text()`; inline images, now downloaded and saved locally at fetch time
  (`fetch.py`'s `files/` folders) but not yet embedded in generated docx; math → OMML.

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
- **Whenever a change touches rendering** (fetch output, HTML parsing, or docx
  generation), regenerate `output/` with at least 3 example instances — fetched HTML,
  downloaded images, and rendered blank+key docx included — so the user can look them
  over before approving. Old rendered examples don't need to persist between turns (fine
  to overwrite/delete when new ones are generated), but they must exist for the user to
  inspect, not just be used internally for verification and then discarded.
