# Architecture

This document is for anyone (human or AI assistant) picking up this codebase cold and
needing to add a feature or fix a bug. It explains how the pieces fit together and why
a few non-obvious design choices were made. For configuration file reference, see
[configuration.md](configuration.md); for docx template mechanics and gotchas, see
[templating.md](templating.md).

## What this tool does

pl2docx generates paper (Word) versions of PrairieLearn assessments: N randomized
instances, each rendered as a blank student copy plus a matching answer key, merged
into an instructor-supplied `.docx` template. It drives a real local PrairieLearn
server rather than reimplementing PL's question-selection/rendering logic.

## Pipeline

1. **Instance generation** (`pl_client.py`) — authenticate against the local PL server
   and create real `assessment_instance` rows by hitting the same routes a
   student-facing browser would (GET the assessment page, POST
   `__action=regenerate_instance`). The authenticated user stays at their real staff
   role (Previewer or above) rather than switching to a "view as student" role
   override — PL's own access-control resolver already bypasses `accessControl` rules
   entirely for staff roles, which is exactly the behavior this tool wants (never
   depend on an assessment having open access windows configured).

   Course/course-instance/assessment are identified in `config.yaml` by PL's stable,
   human-authored identifiers (`short_name`/`short_name`/`tid`), not numeric database
   ids — resolved to current numeric ids at the start of every run
   (`PLClient.resolve_*`). This matters if your PL server's database isn't persistent
   across restarts (e.g. an ephemeral Docker container): numeric ids can silently
   change between runs, but the stable identifiers don't.

2. **Fetch** (`fetch.py`) — for each instance: GET every `instance_question` page while
   the instance is open (the blank copy), close the instance, then re-GET the same
   pages with `showCorrectAnswer` now true (the answer key). Same-origin `<img>`s are
   downloaded alongside the HTML. Zone titles and question order/ids are captured from
   the assessment-instance overview page and saved as `structure.json`, so rendering
   never needs a live server or a second fetch.

3. **Render** (`render.py`, `html_parser.py`, `element_renderer.py`, `docx_builder.py`)
   — parse each fetched page's widgets, build per-question Word content, and merge it
   into the instructor's template. See "Document layout vs. content" below for why this
   is split the way it is. As part of this step, any LaTeX math markup on the page is
   compiled to an image and embedded inline — see "Key design decisions" below for how
   and why.

`run.py` chains steps 1–3 into a single command (`pl2docx-run`) for the common case of
"fetch and render everything."

## Module map

- `config.py` — `Config` dataclass + `load_config()`; reads `config.yaml`.
- `csrf.py` — scrapes PL's per-request CSRF token.
- `pl_client.py` — drives the live PL server: auth, instance create/regenerate/close,
  page fetch, id resolution. Pure parsing helpers (zone/question structure, id
  resolution) are separated out so they're testable offline against saved HTML.
- `_browser.py` — a shared, lazily-launched headless-Chromium singleton used by both
  `svg_render.py` and `canvas_capture.py`.
- `canvas_capture.py` — screenshots canvas-based interactive widgets (things a student
  draws on directly) at fetch time, since their content only exists after real
  JavaScript execution against the live page.
- `fetch.py` — CLI entry point; orchestrates instance generation + the two-pass
  blank/key fetch; downloads images; writes `structure.json`.
- `html_parser.py` — `parse_instance_question_html()` extracts a `ParsedQuestion`
  (prompt text/HTML interleaved with one `Widget` per named input group, in source
  order) from a fetched page. Compound (multi-widget) questions are supported.
- `element_config.py` — loads and resolves `config.yaml`'s per-element formatting
  preferences (see [configuration.md](configuration.md)).
- `latex_math.py` — compiles raw LaTeX to print-resolution PNGs via a real `latex` +
  `dvipng` subprocess pipeline (not a pure-Python LaTeX→OMML converter), so any LaTeX
  package your course's questions use "just works" rather than needing a matching
  hand-maintained subset.
- `svg_render.py` — rasterizes any SVG reaching a page (a raw embedded `<svg>`, an
  element's own inline-SVG output, or a downloaded `.svg` file) to a PNG via headless
  Chromium, then embeds it like any other image.
- `element_renderer.py` — builds each question's Word content (as `docxtpl` `Subdoc`s)
  from its `ParsedQuestion` + resolved `ElementConfig` preferences.
- `docx_builder.py` — assembles the zones/questions Jinja context and renders it
  against the instructor's template. Does no per-element formatting itself.
- `starter_template.py` — generates an editable example instructor template.
- `render.py` — CLI entry point; renders one fetched instance folder into blank+key
  docx.
- `run.py` — CLI entry point; fetches and renders every configured instance in one
  command.

## Key design decisions

- **Drive a real PL server, don't reimplement its logic.** Question/alternative
  selection (e.g. `numberChoose`), variant generation, and correct-answer computation
  all happen inside PL itself. Reimplementing any of this locally would mean staying in
  sync with PL's own logic indefinitely — instead, this tool always talks to a real
  server and only parses its output.
- **Document layout vs. content.** Document *layout* (headers, page structure, named
  Word styles) lives in the instructor's own template, editable in Word. All *content*
  (per-widget formatting, image/math embedding) is built in Python and handed to the
  template as `docxtpl` `Subdoc`s. This split exists so instructors can restyle fonts,
  colors, spacing, and rearrange sections themselves without a code change for every
  cosmetic preference. See [templating.md](templating.md) for the template-side
  mechanics and gotchas this split creates.
- **Two general capabilities instead of per-element adapters.** Course-specific
  interactive/visual elements are handled by two element-agnostic mechanisms rather
  than one adapter per element type:
  - **SVG embedding** — any SVG reaching a page (however it got there) is rasterized
    and embedded as an image, detected purely by `<svg>` tag presence / `.svg` file
    extension.
  - **Canvas capture** — a canvas-based interactive widget's container is
    screenshotted (after hiding toolbar/controls elements) and replaced with a plain
    `<img>`, at fetch time.

  This was chosen over a per-element adapter interface because element internals
  (e.g. a JS library's internal layout format) change as elements are actively
  developed elsewhere, and a generic image-based approach needs no per-element code at
  all — including for elements this tool has never seen. See
  [configuration.md](configuration.md) for how to declare a non-built-in element as
  one of `selector`/`fill-in`/`interactive`.
- **Generic tag-name-derived detection, not hardcoded element names.** Built-in and
  configured fill-in-type elements are detected purely from their tag name and a
  conventional `{tag}-input` CSS class pattern — no element-specific code. An element
  whose class doesn't follow that convention can still be detected via an explicit
  `class-prefix` override (see [configuration.md](configuration.md)).
- **Math renders via a real local LaTeX install, not a Word-native equation format.**
  `latex_math.py` compiles each equation through a genuine `latex` + `dvipng`
  subprocess pipeline and embeds the result as an image, rather than converting to
  OMML (Word's native math format) or hand-rolling a LaTeX subset. This means any
  LaTeX construct — including specialty packages like `mhchem` or `siunitx` — "just
  works" without pl2docx needing to understand it, at the cost of non-editable
  (image, not native equation) math output. A local TeX distribution
  (`latex`/`dvipng`/`kpsewhich` on `PATH`) is required to get real typeset math; when
  it's missing entirely, math falls back to placeholder `$latex$` text rather than
  failing the render. Packages beyond the small built-in default set
  (`amsmath`/`amssymb`/`xcolor`) must be declared in `config.yaml`'s `latex-packages`
  (see [configuration.md](configuration.md)) and are validated up front — a
  *declared-but-missing* package fails the whole run immediately with a clear error,
  rather than degrading silently equation-by-equation.

## Testing conventions

- Tests that don't need a live server or a real browser/LaTeX install run fully
  offline: `pl_client.py`'s live-server-driving methods are stubbed (see
  `tests/test_fetch.py`'s `_StubPLClient` pattern) so `fetch_n_instances`'s whole loop
  can be exercised without a server.
- Chromium- and LaTeX-dependent tests are skip-gated (`tests/conftest.py`'s
  `chromium_available()`, and an equivalent `shutil.which`-based check for the LaTeX
  toolchain) rather than always required, so the bulk of the suite runs anywhere.
- `tests/conftest.py`'s `starter_template` fixture generates a real starter template
  at test time via `build_starter_template()` — docx is a binary format, not something
  to hand-author as a checked-in text fixture.
- One integration test (`test_pl_client_integration.py`) exercises the real fetch flow
  end-to-end against a live local PL server; it self-skips if `config.yaml` or the
  server isn't available, so it never blocks running the rest of the suite offline.

## Known limitations

- Elements whose only interaction is capturing an uploaded/drawn image via a
  file-upload-style widget (rather than a canvas the student draws on directly) are
  not supported.
- A fill-in-type element with a rich formula-editor input mode (rather than a plain
  text field) is not supported.
- Canvas-capture's default container/toolbar-selector guesses assume conventions
  confirmed against a couple of real course elements; an element that doesn't follow
  them needs explicit `container-selector`/`hide-selectors` overrides (see
  [configuration.md](configuration.md)).

## Where to look for common tasks

- **Add support for a new built-in element type** — start in `html_parser.py` (widget
  detection) and `element_renderer.py` (how it renders to Word).
- **Support a non-built-in (e.g. course-specific) element without writing code** — see
  `config.yaml`'s `additional-elements` section in [configuration.md](configuration.md).
- **Fix or extend the instructor-facing template** — see
  [templating.md](templating.md), especially its gotchas section before making any
  change involving `docxtpl` tags.
- **Diagnose PL-server-driving behavior** (auth, routes, id resolution) — check
  `pl_client.py`'s module docstring and, if needed, the real PrairieLearn source
  (https://github.com/PrairieLearn/PrairieLearn) or docs
  (https://docs.prairielearn.com/) rather than guessing — this tool depends on exact
  PL internals (cookies, routes, SQL selection logic), and wrong guesses fail silently.
