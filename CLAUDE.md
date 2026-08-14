# CLAUDE.md

## Project

pl2docx is a standalone, course-agnostic Python tool that generates paper-based (Word)
versions of PrairieLearn assessments: N randomized instances, each as a blank student
copy plus a matching answer key, merged into an instructor-supplied `.docx` template.

It has no hard dependency on any specific course's custom code — course-specific
elements are handled through two general, element-agnostic capabilities (SVG
embedding, canvas capture), not per-element adapters.

## Start here

Three reference documents in `notes/` cover the topics you'll most often need when
helping a user with this project — read the relevant one before digging through
source:

- **[notes/architecture.md](notes/architecture.md)** — how the pipeline (fetch → parse
  → render) fits together, the module map, and the key design decisions and their
  rationale. Read this first for anything touching how the tool works internally, or
  before adding a feature or fixing a bug.
- **[notes/configuration.md](notes/configuration.md)** — full field-by-field reference
  for `config.yaml`. Read this before helping a user set up or troubleshoot their
  config file.
- **[notes/templating.md](notes/templating.md)** — how the instructor-facing docx
  template works (docxtpl/Jinja2 tags), the context variables available to it, and a
  list of confirmed template-authoring gotchas. Read this before touching
  `starter_template.py`, `docx_builder.py`, or helping a user fix a broken template.

## Reference material (read-only, do not modify)

PrairieLearn reference material (use these, don't rely on memory for platform
specifics — this tool depends on exact PL internals, and wrong guesses fail silently):
- Docs: https://docs.prairielearn.com/
- Source repo: https://github.com/PrairieLearn/PrairieLearn

## Ground rules

- **No confidently-guessed PrairieLearn scaffolding.** Check real PL source/docs
  before assuming how a route, middleware, or DB query behaves.
- Prefer driving a real local PL server over reimplementing PL logic (question
  selection, variant generation, correct-answer computation all stay inside PL
  itself) — see [notes/architecture.md](notes/architecture.md) for why.
- This is a standalone tool: don't introduce a dependency from core tool code onto any
  specific course's custom packages. Course-specific rendering goes through the
  generic `config.yaml` mechanisms described in
  [notes/configuration.md](notes/configuration.md), not direct imports.

## Repository structure

```
.
├── src/pl2docx/
│   ├── config.py               # Config dataclass + load_config() — reads config.yaml
│   ├── csrf.py                 # scrape PL's per-request CSRF token
│   ├── pl_client.py            # drives the live PL server: auth, instance
│   │                           #   create/regenerate/close, page fetch, id resolution
│   ├── _browser.py             # shared lazily-launched headless-Chromium singleton
│   ├── canvas_capture.py       # screenshots canvas-based interactive widgets at fetch time
│   ├── fetch.py                # CLI: pl2docx-fetch — generate + fetch instances
│   ├── html_parser.py          # parse_instance_question_html() -> ParsedQuestion
│   ├── element_config.py       # loads/resolves config.yaml's per-element preferences
│   ├── latex_math.py           # compiles LaTeX to print-resolution PNGs
│   ├── svg_render.py           # rasterizes SVG content to PNGs via headless Chromium
│   ├── element_renderer.py     # builds each question's Word content (docxtpl Subdocs)
│   ├── docx_builder.py         # assembles the zones/questions context, renders the template
│   ├── starter_template.py     # CLI: pl2docx-starter-template — generates an example template
│   ├── render.py                # CLI: pl2docx-render — renders one fetched instance
│   └── run.py                  # CLI: pl2docx-run — fetch + render every instance in one command
├── tests/                      # pytest; offline-runnable except one integration test that
│                               #   self-skips without a live PL server, and Chromium/LaTeX-
│                               #   dependent tests that self-skip without those installed
├── notes/                      # public-facing reference docs — see "Start here" above
├── planning_notes/             # gitignored — author's own development history, not public
├── config.example.yaml         # copy to config.yaml (gitignored) and fill in
├── pyproject.toml              # uv-managed; Python 3.14
└── output/                     # gitignored, runtime — fetched HTML + generated docx
```

See [notes/architecture.md](notes/architecture.md) for what each module actually does
and why.

## docxtpl quick reference

The single most important rule, needed constantly when touching templates: any tag
inserting a `docxtpl.Subdoc` must use `{{p ... }}`, not plain `{{ ... }}` — see
[notes/templating.md](notes/templating.md) for this and every other confirmed
templating gotcha before making template-related changes.

## Tooling / environment

- Language: Python 3.14. Package manager: **uv**.
- Docx templating: **docxtpl** + **docxcompose** (docxtpl's subdoc feature requires
  it).
- Headless browser tooling: **Playwright** (Chromium) — used by `svg_render.py` and
  `canvas_capture.py`. Requires a one-time `playwright install chromium` after
  `uv sync`; both modules raise a clear error pointing at this command if Chromium
  isn't installed.
- Math rendering (`latex_math.py`) requires a local **TeX distribution** with `latex`,
  `dvipng`, and `kpsewhich` on `PATH`. Not a pinned Python dependency — degrades
  gracefully (placeholder `$latex$` text) when missing entirely, but fails fast and
  loudly if a *declared* `latex-packages` entry can't be found.
- Requires a local PrairieLearn dev server to fetch against — see
  https://docs.prairielearn.com/installing/.
- For all critical functionality, write pytest tests and place them in `tests/`.
  Verify the full suite passes before committing changes.

## Docstrings

Use **NumPy-style** docstrings. Required elements:

- One-line summary stating *what* the function does (its result/effect), not how it's
  implemented.
- `Parameters` section: every parameter with its type and its physical/domain meaning,
  not just a restatement of the type. State units explicitly for any non-dimensionless
  numeric quantity.
- `Returns` section: same standard as above.
- `Raises` section if the function can raise on invalid input.
- A `Notes` section whenever there's a non-obvious assumption, invariant, or edge case
  — in particular, state explicitly whether a function assumes its input represents a
  valid configuration or operates correctly on arbitrary/invalid states too.
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

## Working style

- For anything nontrivial (schema changes, changes touching multiple submodules),
  propose a short plan before writing code, and wait for confirmation.
- Prefer small, independently verifiable increments over large multi-part changes.
- If existing conventions in this repo conflict with general best practice, follow the
  existing convention and note the discrepancy rather than silently introducing a new
  style.
- **Whenever a change touches rendering** (fetch output, HTML parsing, or docx
  generation), regenerate `output/` with at least 3 example instances — fetched HTML,
  downloaded images, and rendered blank+key docx included — so the user can look them
  over before approving. Old rendered examples don't need to persist between turns
  (fine to overwrite/delete when new ones are generated), but they must exist for the
  user to inspect, not just be used internally for verification and then discarded.
