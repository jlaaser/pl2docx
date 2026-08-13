# CLAUDE.md

## Project

Standalone, course-agnostic Python tool that generates paper-based (Word) versions of
PrairieLearn assessments: N randomized instances each as a blank student copy + matching
answer key, merged into an instructor-supplied template.

This tool is deliberately **not** part of any specific course repo. It should have no
hard dependency on any course's custom packages (e.g. the `chemutils` in the `../../chem 0110/pl-pitt-chem0110` course); course-specific
static/interactive elements are handled by two general, element-agnostic capabilities
(SVG embedding, canvas capture — see "Extensibility" below), not a per-element adapter.

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
- Per-phase session handoff notes (read the most recent one first when resuming work):
  `planning_notes/2026-08-13 phase 5 implementation and design decisions.md` (SVG
  embedding, canvas capture, zero-widget questions — replaces the original Phase 5/6
  adapter-interface plan), `planning_notes/2026-08-12 phase 4 implementation and
  design decisions.md` (rich HTML/image/math rendering), and earlier notes in the same
  folder for Phases 1-3.

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
│   │                               #   incl. template_path). course/course_instance/assessment are
│   │                               #   identified by PL's stable short_name/tid strings, not numeric
│   │                               #   ids - see pl_client.py's resolve_* methods
│   ├── csrf.py                    # extract_csrf_token() - scrape PL's per-request CSRF token
│   ├── pl_client.py               # PLClient - drives the real PL server (auth, effective-user,
│   │                               #   instance create/regenerate, blank/key HTML fetch,
│   │                               #   binary/image fetch, short_name/tid -> numeric id resolution
│   │                               #   via resolve_course_id/resolve_course_instance_id/
│   │                               #   resolve_assessment_id); parse_zone_groups() and friends -
│   │                               #   pure fns parsing zone/question structure and id-resolution
│   │                               #   source pages, each testable offline against saved HTML.
│   │                               #   Phase 5 subphase 2: instance_question_url() (public - not
│   │                               #   `_`-prefixed like this class's other URL builders, since
│   │                               #   canvas_capture.py needs to navigate a headless browser to
│   │                               #   the live page itself) and playwright_cookies() (translates
│   │                               #   this client's authenticated session.cookies into
│   │                               #   Playwright's BrowserContext.add_cookies() format).
│   ├── _browser.py                # Phase 5 subphase 2: get_browser()/close_browser() - a shared,
│   │                               #   lazily-launched headless-Chromium singleton used by BOTH
│   │                               #   svg_render.py and canvas_capture.py, so the two features
│   │                               #   share one browser process instead of each launching its
│   │                               #   own. Leading underscore: internal shared utility, not a
│   │                               #   public CLI/API surface. BrowserUnavailableError raised when
│   │                               #   Chromium isn't installed; each caller translates that into
│   │                               #   its own module's error type/fallback (e.g.
│   │                               #   svg_render.SvgRenderError).
│   ├── canvas_capture.py          # Phase 5 subphase 2: capture_interactive_elements() -
│   │                               #   screenshots canvas-based interactive PL elements (fabric.js
│   │                               #   widgets a student draws on directly, e.g.
│   │                               #   pl-orbitaldiagram/pl-lewisstructure's non-print mode) at
│   │                               #   FETCH time (not render time, unlike svg_render.py) - a
│   │                               #   canvas's content only exists after real JS execution
│   │                               #   against the live variant, so this drives a headless browser
│   │                               #   (via _browser.py) to the real instance_question URL with
│   │                               #   PLClient's session cookies attached
│   │                               #   (PLClient.playwright_cookies()), not something replayable
│   │                               #   from already-saved static HTML. Confirmed real DOM
│   │                               #   convention (both pl-orbitaldiagram and pl-lewisstructure,
│   │                               #   NOT core PL's pl-drawing): root container class equals the
│   │                               #   element's own tag name verbatim - default
│   │                               #   container_selector guess when not configured. No single
│   │                               #   toolbar-class-suffix convention holds across all three
│   │                               #   confirmed elements, so the default hide_selectors guess
│   │                               #   tries several candidate suffixes
│   │                               #   (_DEFAULT_HIDE_SUFFIXES) rather than one hardcoded string;
│   │                               #   a non-matching candidate is a harmless no-op. Every matched
│   │                               #   container is ALWAYS replaced with an <img> tag before
│   │                               #   returning - a real one on success, or a placeholder
│   │                               #   (empty src, alt="[interactive content unavailable]") on
│   │                               #   any capture failure - deliberately never leaves raw
│   │                               #   canvas/toolbar markup in the HTML, since html_parser.py's
│   │                               #   generic-tag walk would otherwise recurse into it and leak
│   │                               #   stray toolbar-button text into the rendered prompt. The
│   │                               #   empty-src placeholder needs zero html_parser.py/
│   │                               #   element_renderer.py changes - _render_image()'s existing
│   │                               #   fallback already treats a falsy local_path as "use alt
│   │                               #   text". Same reason no new Widget kind/ContentNode type was
│   │                               #   needed for this feature at all: by the time
│   │                               #   html_parser.py ever runs, a captured (or placeholder)
│   │                               #   canvas is already an ordinary <img>, indistinguishable
│   │                               #   from any other downloaded image.
│   ├── fetch.py                   # CLI entry point (python -m pl2docx.fetch / `pl2docx-fetch`) -
│   │                               #   also downloads same-origin <img>s into files/ next to each
│   │                               #   instance_question's HTML, rewriting src to the local path,
│   │                               #   and writes structure.json (zone titles + question order/ids).
│   │                               #   Phase 5 subphase 2: when config.yaml declares any
│   │                               #   additional-elements type: interactive entries, also calls
│   │                               #   canvas_capture.capture_interactive_elements() per
│   │                               #   instance_question (before _download_images - order doesn't
│   │                               #   functionally matter, since captured <img src="files/...">
│   │                               #   paths are already-local and untouched by
│   │                               #   _download_images' same-origin filter) for both the blank fetch (interactive
│   │                               #   question panel, toolbar visible until hidden) and key
│   │                               #   fetch (answer panel, no toolbar) - reuses the existing
│   │                               #   two-pass blank/key architecture unchanged.
│   ├── html_parser.py             # parse_instance_question_html() -> ParsedQuestion; built-in
│   │                               #   support: pl-multiple-choice, pl-checkbox, and 5 fill-in-type
│   │                               #   elements sharing one markup pattern (pl-string-input,
│   │                               #   pl-integer-input, pl-number-input, pl-symbolic-input,
│   │                               #   pl-units-input - _add_fill_in_groups() detects any of these
│   │                               #   generically from its tag name alone). An
│   │                               #   additional_fill_in_tags param extends detection to
│   │                               #   instructor-configured, non-built-in fill-in elements (e.g.
│   │                               #   course-specific pl-scinum-input) via the identical
│   │                               #   tag-name-derived mechanism - see element_config.py's
│   │                               #   additional_fill_in_tags() for how config.yaml's
│   │                               #   additional-elements feeds this. pl-big-o-input was checked
│   │                               #   and deliberately NOT supported: its <input> class omits the
│   │                               #   "pl-" prefix every other fill-in element uses, breaking the
│   │                               #   tag-name-derived pattern this mechanism depends on.
│   │                               #   ParsedQuestion.widgets: list[Widget], one per distinct named
│   │                               #   input group in source (DOM) order - compound (multi-widget)
│   │                               #   questions are supported (Phase 3B), not rejected.
│   │                               #   ParsedQuestion.prompt_segments is aligned 1:1 around widgets
│   │                               #   (len(widgets)+1) so callers can interleave rendered widget
│   │                               #   content at its real source position. Phase 5 subphase 3
│   │                               #   (2026-08-13): a page with ZERO recognized widgets is no
│   │                               #   longer an UnsupportedElementError - a diagram-only question
│   │                               #   (SVG-only, or a captured-canvas-only interactive element -
│   │                               #   see svg_render.py/canvas_capture.py) is legitimate;
│   │                               #   ParsedQuestion.widgets is simply []. Needed to render real
│   │                               #   assessments in full - element_renderer.py's
│   │                               #   _build_question_contents already had a `if not
│   │                               #   question.widgets:` fast path from earlier phases, so this was
│   │                               #   purely relaxing html_parser.py's own guard, no renderer
│   │                               #   changes needed. UnsupportedElementError now only covers the
│   │                               #   page's generic containers (.question-block/.question-body)
│   │                               #   not being found at all - i.e. not looking like a real PL page.
│   │                               #   Also extracts qid (Staff info panel) and points_numeric.
│   ├── element_config.py          # Phase 3B: SelectorPreferences/FillInPreferences/ElementConfig
│   │                               #   dataclasses + load_element_config()/resolve_preferences() -
│   │                               #   loads config.yaml's global-element-preferences/
│   │                               #   additional-elements sections; all keys optional, built-in
│   │                               #   defaults apply when absent. Phase 5 subphase 2: a third
│   │                               #   BehaviorClass, "interactive", with its own
│   │                               #   InteractivePreferences dataclass (container_selector/
│   │                               #   hide_selectors) and additional_interactive_tags()
│   │                               #   accessor - kept in a SEPARATE ElementConfig.
│   │                               #   interactive_preferences dict, deliberately NOT merged into
│   │                               #   ElementPreferences/resolve_preferences(), since an
│   │                               #   interactive-typed tag is consumed by fetch.py/
│   │                               #   canvas_capture.py at fetch time and never becomes a Widget
│   │                               #   needing a render-time preference at all.
│   ├── latex_math.py              # Phase 4 increment 3: render_math_png() - compiles raw LaTeX
│   │                               #   (MathRef.latex) to a depth-annotated, print-resolution PNG
│   │                               #   via a real latex + dvipng subprocess pipeline (not pdflatex +
│   │                               #   pdftoppm - dvipng --depth reports how far content descends
│   │                               #   below the LaTeX baseline, needed to correct inline picture
│   │                               #   vertical alignment; pdftoppm has no equivalent), and not a
│   │                               #   pure-Python LaTeX->OMML converter - chosen so real courses'
│   │                               #   specialty packages (e.g. mhchem's \ce{...} chemistry
│   │                               #   notation, confirmed real usage in one course's
│   │                               #   chemutils/compounds.py et al.) "just work" with zero
│   │                               #   special-casing, since it's genuine LaTeX. Only amsmath/
│   │                               #   amssymb/xcolor are built-in defaults (genuinely universal);
│   │                               #   anything else (mhchem, siunitx, ...) must be declared via
│   │                               #   config.yaml's latex-packages and registered once per process
│   │                               #   via configure_extra_packages() - deliberately config-driven,
│   │                               #   not auto-detected from equation content (macro-sniffing
│   │                               #   doesn't generalize to arbitrary packages and still wouldn't
│   │                               #   guarantee the package is installed), mirroring
│   │                               #   element_config.py's additional-elements pattern.
│   │                               #   configure_extra_packages() validates declared packages
│   │                               #   resolve via kpsewhich at startup, raising
│   │                               #   LatexPackageNotFoundError (NOT caught by
│   │                               #   element_renderer.py's per-equation fallback - a config
│   │                               #   mistake should fail the whole run, not degrade silently)
│   │                               #   rather than deferring to N separate per-equation failures.
│   │                               #   render_math_png() itself raises LatexRenderError (missing
│   │                               #   latex/dvipng, compile failure, timeout) so
│   │                               #   element_renderer.py can fall back to placeholder "$latex$"
│   │                               #   text rather than fail the whole render. Requires a local TeX
│   │                               #   distribution (latex + dvipng on PATH) - not a pinned Python
│   │                               #   dependency like the rest of this project's deps. Results are
│   │                               #   cached per-process (module-level dict + temp dir), keyed by
│   │                               #   (latex, display_mode, font_size_pt, dpi); the cache is
│   │                               #   cleared whenever configure_extra_packages() is called, since
│   │                               #   a cached PNG may have been rendered under a different package
│   │                               #   configuration.
│   ├── svg_render.py              # Phase 5 subphase 1: render_svg_png() - rasterizes any SVG
│   │                               #   (raw inline <svg>, or a downloaded .svg file e.g. from
│   │                               #   <pl-figure>) to a print-resolution PNG via a real headless
│   │                               #   Chromium browser (Playwright), not a pure-Python SVG library -
│   │                               #   chosen for full CSS/SVG fidelity and because a later,
│   │                               #   deferred feature (canvas-based interactive element capture)
│   │                               #   will need the same headless-browser technology. Sizing is
│   │                               #   measured from the browser's own rendered bounding box
│   │                               #   (Playwright locator.bounding_box()), not hand-parsed from the
│   │                               #   SVG's width/height/viewBox attributes - not every SVG an
│   │                               #   instructor might embed reliably sets those, so this defers to
│   │                               #   the browser's own complete SVG-sizing algorithm (including its
│   │                               #   300x150 spec default) instead of re-implementing it. Requires
│   │                               #   Chromium installed via `playwright install chromium` (a
│   │                               #   one-time step separate from the `playwright` pip package
│   │                               #   itself); raises SvgRenderError (caught by
│   │                               #   element_renderer.py's per-item fallback to alt text) when
│   │                               #   unavailable, mirroring latex_math.py's LatexRenderError.
│   │                               #   Results cached per-process (module-level dict + temp dir),
│   │                               #   keyed by (sha256(svg_markup), dpi); browser/context lifecycle
│   │                               #   is a lazy module-level singleton, reused across every SVG
│   │                               #   render in the process (close_browser() for explicit teardown).
│   ├── element_renderer.py        # build_question_context() - one ParsedQuestion + ElementConfig ->
│   │                               #   the 4 Subdocs (question_contents/answer_contents/answer_space/
│   │                               #   answer_element) + qid/points a question's Jinja context needs.
│   │                               #   Phase 3B: widgets render at their real source position
│   │                               #   (interleaved with prompt_segments, not appended after);
│   │                               #   config-driven list-style/bold-correct/display/draw-border per
│   │                               #   widget. display="template" routes a widget's block-rendered
│   │                               #   content into answer_element instead of question_contents.
│   │                               #   Phase 4 increment 3: MathRef nodes render via
│   │                               #   latex_math.render_math_png(), embedded as a real inline
│   │                               #   picture (reusing the same _render_image() path ImageRef uses,
│   │                               #   with default_width=None so python-docx auto-sizes from the
│   │                               #   PNG's own DPI metadata instead of a fixed fallback width).
│   │                               #   Phase 5 subphase 1: SvgRef nodes (raw inline <svg>) and
│   │                               #   .svg-suffixed ImageRef nodes (a downloaded .svg file) both
│   │                               #   render via svg_render.render_svg_png(), same _render_image()
│   │                               #   reuse pattern as MathRef.
│   ├── docx_builder.py            # render_document() - assembles the zones/questions context and
│   │                               #   calls docxtpl render/save; does NO per-element formatting
│   │                               #   itself (see element_renderer.py) - document *layout* lives in
│   │                               #   the instructor's template (loop tags + named styles), not here.
│   │                               #   Takes an optional ElementConfig, threaded to
│   │                               #   build_question_context per question.
│   ├── starter_template.py        # build_starter_template() - CLI (`pl2docx-starter-template`)
│   │                               #   generates an editable example instructor template (zones/
│   │                               #   questions loop + named "pl2docx ..." styles + an
│   │                               #   {{p question.answer_element }} tag), via python-docx (not
│   │                               #   committed as a binary - same reasoning as test fixtures).
│   │                               #   Default formatting (confirmed with the user, targeting
│   │                               #   planning_notes/reference_examples/MT1_A_KEY.pdf's look, not
│   │                               #   an exact match): "Normal" itself is overridden to black
│   │                               #   Times New Roman 12pt, single line spacing, no bold/italic/
│   │                               #   underline - the only exceptions are the assessment title/
│   │                               #   zone heading (16pt) and the "SOLUTION:" label (bold, a
│   │                               #   run-level override). "pl2docx Question Title"/"pl2docx QID
│   │                               #   Reference" get no overrides at all now, existing purely as
│   │                               #   named customization hooks. All 4 custom styles set
│   │                               #   base_style = Normal explicitly - python-docx custom styles
│   │                               #   don't inherit Normal by default (confirmed: base_style is
│   │                               #   None unless set), so skipping this silently falls back to
│   │                               #   Word's own Calibri-11 theme default instead of the
│   │                               #   overridden Normal. Every for/if/else/endfor control-flow
│   │                               #   tag uses docxtpl's `{%p ... %}` paragraph-consuming prefix
│   │                               #   (not plain `{% %}`, not Jinja `{%- -%}` whitespace-trim) -
│   │                               #   confirmed the hard way this session: a plain control tag's
│   │                               #   own paragraph survives rendering as a real empty `<w:p>`
│   │                               #   (2-5 stray blanks per zone/question boundary in a real
│   │                               #   render), and naively trimming one adjacent to a `{{p ...}}`
│   │                               #   subdoc tag corrupts that tag's own paragraph-stripping
│   │                               #   regex (docxtpl's `patch_xml` runs trim-merging *before*
│   │                               #   `{{p }}`-paragraph-stripping), producing a
│   │                               #   `TemplateSyntaxError` with no indication of the real cause.
│   │                               #   `{%p if %}`/`{%p endif %}`/`{%p for %}`/`{%p endfor %}` sidestep
│   │                               #   this entirely (verified safe directly adjacent to `{{p }}`
│   │                               #   tags on both sides, and against a table in one `{%p if %}`
│   │                               #   branch) - see the module's own docstring for the full
│   │                               #   mechanism. The `is_answer_key`/QID pair is the one exception
│   │                               #   left on `{%- -%}` trim (predates this session, real content
│   │                               #   on both sides, not `{{p }}` tags, so it's safe and already
│   │                               #   proven working). A related-but-distinct bug found writing
│   │                               #   this session's regression test: `element_renderer.py`'s
│   │                               #   `_build_question_contents` always started a *fresh* paragraph
│   │                               #   after a block-display widget - harmless when more content
│   │                               #   follows, but a stray trailing empty paragraph when that
│   │                               #   widget was the question's last content. Fixed via
│   │                               #   `_trim_trailing_empty_paragraph`, applied in both
│   │                               #   `_build_question_contents` return paths (the normal one and
│   │                               #   the zero-widget fast path) - not a template/Jinja issue, so
│   │                               #   it lives in element_renderer.py, not here.
│   └── render.py                  # CLI entry point (python -m pl2docx.render / `pl2docx-render`) -
│                                   #   renders one fetch.py output/<instance>/ dir (using its
│                                   #   structure.json for zone/question order) into blank+key docx;
│                                   #   loads element_config.yaml's preferences via
│                                   #   element_config.load_element_config() alongside config.py
├── tests/
│   ├── conftest.py                          # starter_template fixture (generated at test time,
│   │                                         #   via the real build_starter_template())
│   ├── fixtures/instance_question/          # synthetic (not real course content) sample HTML,
│   │                                         #   one blank+key pair per Phase 2 element kind
│   ├── test_csrf.py                         # unit tests, no live server needed
│   ├── test_html_parser.py                  # unit tests, no live server needed
│   ├── test_element_config.py               # unit tests, no live server needed
│   ├── test_element_renderer.py             # unit tests, no live server needed; SVG-embedding cases
│   │                                         #   split Chromium-independent (fallback-on-error paths,
│   │                                         #   monkeypatched) from Chromium-dependent (real
│   │                                         #   end-to-end picture embedding), same split as the
│   │                                         #   existing LaTeX-math tests
│   ├── test_svg_render.py                   # unit tests for svg_render.py; skip-gated on Chromium
│   │                                         #   availability (mirrors test_latex_math.py's
│   │                                         #   shutil.which-based skip, adapted to a Playwright
│   │                                         #   launch try/except)
│   ├── test_docx_builder.py                 # unit tests, no live server needed
│   ├── test_starter_template.py             # unit tests, no live server needed
│   ├── test_pl_client.py                    # unit tests for parse_zone_groups()/instance_question_url()/
│   │                                         #   playwright_cookies(), no live server needed
│   ├── test_fetch.py                        # unit tests for image download/rewrite + interactive-capture
│   │                                         #   wiring (monkeypatched capture_interactive_elements, no
│   │                                         #   Chromium/live server needed), no live server needed
│   ├── test_canvas_capture.py                # unit tests for canvas_capture.py; skip-gated on Chromium
│   │                                         #   availability, exercised against a small local static
│   │                                         #   HTML fixture served over a local HTTP server (not the
│   │                                         #   real PL dev server) so this stays offline/CI-safe
│   └── test_pl_client_integration.py        # full flow against the real local server;
│                                             #   self-skips if config.yaml or the server is absent
├── config.example.yaml            # template - copy to config.yaml (gitignored) and fill in
├── pyproject.toml                 # uv-managed; Python 3.14, deps: requests/beautifulsoup4/pyyaml/
│                                   #   docxtpl/docxcompose/playwright
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
- **A run-level character border (`<w:bdr>` inside a run's `<w:rPr>`) is a separate,
  simpler technique from the table-cell-border technique above** — confirmed working
  this session (Phase 3B, `element_renderer.py`'s `_add_run_border()`), for a different
  problem: boxing one *widget's* rendered content (built directly in Python, not a
  `{{p ... }}` template tag) with a box tight to the text, sitting inline with
  surrounding content, matching a PL-style "boxed multiple-choice option" look. Since it
  lives inside the run itself rather than the run's containing paragraph, it isn't
  affected by the paragraph-replacement mechanism at all (that only discards the
  *paragraph* container a `{{p ... }}` tag sits in) — apply it directly to
  `docx.text.run.Run` objects built by `element_renderer.py` immediately after adding
  their text. Word visually merges adjacent runs sharing identical border formatting into
  one continuous box, so applying it to every run of a widget's inline content (including
  inter-option spacer runs) yields a single box around the whole widget rather than one
  box per run.
  - **Do not fall back to the table-cell-border technique for multi-paragraph widget
    content** (e.g. `display: block`, one selector option per line) — tried and rejected
    by the user: a table cell always spans the full page width, producing a box far
    wider than the boxed text, unlike the table-cell technique's *intended* use above
    (wrapping a whole `{{p ... }}` subdoc's content, where full-width is fine/expected).
    `draw-border` always uses the run-level border, regardless of `display` — for
    `block`/`template` content this means one tight box per line (one per paragraph,
    since a run border can't merge across a paragraph break) rather than one box around
    the whole multi-line block; that's the accepted, correct behavior here, not a
    limitation to work around.

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
4. **Static rendering of interactive elements** — **Correction (2026-08-13):** this item
   originally called for per-element static renderers (parsing each course element's own
   `layout_json`, or a `canvas.toSVG()` fallback for core/third-party fabric.js elements).
   Replaced with two general, element-agnostic capabilities — see "Extensibility" below:
   - **SVG embedding** (`svg_render.py`, done): any SVG reaching a page — whether an
     instructor's own raw `<svg>`, a course element's print mode emitting inline SVG
     (confirmed real case: `pl-lewisstructure`'s `print="true"` mode), or a downloaded
     `.svg` file referenced via `<img src>` (e.g. through `<pl-figure>`) — rasterizes to a
     PNG via a headless browser and embeds like any other image.
   - **Canvas capture** (`canvas_capture.py`, done as of 2026-08-13): screenshots a
     canvas-based widget's container `<div>` directly at fetch time (e.g.
     `pl-lewisstructure`'s non-print fabric.js mode, `pl-orbitaldiagram`), rather than
     reaching into each element's own JS API — see `canvas_capture.py`'s repo-structure
     entry above.
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
   - `element_renderer.py` builds each question's 4 subdocs
     (`question_contents`/`answer_contents`/`answer_space`/`answer_element`) plus its
     `qid`/`points_numeric`/`points_text`. **Phase 3B status (done)**:
     `config.yaml`-driven per-widget formatting via `element_config.py` — list style
     (letter-labels/bubble/checkbox), bold-correct, `display`
     (`inline`/`block`/`template`/`none`, shared vocabulary for selector- and
     fill-in-type widgets), and `draw-border` (a tight, text-width box around a widget's
     rendered content, via a run-level character border, applied the same way regardless
     of `display` — one continuous box for `inline` content, one box per line for
     `block`/`template` content; see the docxtpl gotchas list above for why a table-cell
     border was tried and rejected here). Widgets render at their real source position,
     interleaved with `ParsedQuestion.prompt_segments`, not appended after the prompt.
     Compound (multi-widget) questions are supported: each widget renders independently,
     in source order. `html_parser.py` also strips PL's own non-visible/auxiliary markup
     (`text-muted` help text, `visually-hidden` accessibility legends) before extracting
     prompt text, so it doesn't leak into the rendered question (see `_strip_help_text`).
   - `docx_builder.py`'s `render_document()` assembles the `zones` context and calls
     `docxtpl`'s render/save — no per-element formatting decisions of its own. Takes an
     optional `ElementConfig`, threaded to `build_question_context` per question.
   - `starter_template.py` generates an example instructor template exercising the
     full context shape (zone titles, question number/title/points/qid, the four
     subdoc insertions including `answer_element`, an `is_answer_key` branch) with
     named "pl2docx ..." styles ready to restyle in Word.
   - Rich-HTML prompt formatting (bold/italic/underline/lists, Phase 4 increment 1),
     image embedding (increment 2), and math rendering (increment 3, **done**) are all
     implemented. Math renders via `latex_math.py`'s real `latex`+`dvipng` pipeline
     rather than a pure-Python LaTeX→OMML converter — chosen specifically because real
     course content can need specialty LaTeX packages (e.g. `mhchem`'s `\ce{...}`
     chemistry-formula macro, confirmed in one course's `chemutils/compounds.py` et
     al.) that general-purpose LaTeX→MathML libraries don't implement; compiling
     through real LaTeX means it "just works" with no subset to maintain, at the cost
     of non-editable (image, not native Word-equation) math output and requiring a
     local TeX distribution. Only `amsmath`/`amssymb`/`xcolor` are built-in defaults;
     anything else (`mhchem`, `siunitx`, ...) is declared per-course via
     `config.yaml`'s `latex-packages`, validated (`kpsewhich`) at startup rather than
     auto-detected from equation content — see `latex_math.py`'s repo-structure entry
     above for the full reasoning. Falls back to placeholder `$latex$` text (unchanged
     from pre-increment-3 behavior) when no LaTeX install is available or a given
     snippet fails to compile (e.g. it needs a package not declared in
     `latex-packages`).

## Extensibility

**Revised (2026-08-13), replacing the original per-element adapter-interface plan**:
an adapter interface for course-specific static renderers (e.g. pl-orbitaldiagram,
pl-lewisstructure) was judged too fragile — element internals (fabric.js layout JSON,
canvas structure) change as elements are actively developed, so an adapter interface
would need constant upkeep, and it wouldn't help with SVG/canvas content an
instructor embeds directly with no PL element involved at all. Replaced with two
general capabilities that need no per-element code:
- **SVG embedding** (`svg_render.py`, done) — rasterizes any SVG reaching a page
  (raw embedded `<svg>`, a course element's print mode injecting inline SVG, or an
  `.svg` file referenced via `<img src>`, e.g. through `<pl-figure>`) to a PNG via a
  headless browser, then embeds it like any other image. Detection is based purely on
  `<svg>` tag presence / `.svg` file extension, never on which PL element produced it.
- **Canvas capture** (`canvas_capture.py`, done as of 2026-08-13) — screenshots the
  container `<div>` of a canvas-based interactive widget at fetch time, after hiding
  toolbar/controls elements (configured or guessed — see `canvas_capture.py`'s
  repo-structure entry), replacing it with a plain `<img>` the same way SVG embedding
  does, again with no per-element code. Configured via `additional-elements`' `type:
  interactive` (a third `BehaviorClass` alongside `selector`/`fill-in`) — see
  `config.example.yaml`. Confirmed real detection convention (container class equals
  the element's own tag name) checked against two course elements
  (`pl-lewisstructure`, `pl-orbitaldiagram`) but explicitly **not** universal — core
  PL's `pl-drawing` doesn't follow it, hence the explicit `container-selector`/
  `hide-selectors` override support.

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
- Headless browser tooling: **Playwright**. Used by both `svg_render.py` (static SVG
  rasterization, subphase 1) and `canvas_capture.py` (live-page canvas screenshotting,
  subphase 2), which share one Chromium process via `_browser.py`'s
  `get_browser()`/`close_browser()` singleton rather than each launching its own.
  Requires a one-time `playwright install chromium` after `uv sync` (the `playwright`
  pip package itself is a pinned dependency, but the Chromium browser binary is a
  separate download — `svg_render.py`/`canvas_capture.py` raise a clear error pointing
  at this command if Chromium isn't installed, rather than failing with an opaque
  launch error).
- Math rendering (`latex_math.py`): requires a local **TeX distribution** with
  `latex`, `dvipng`, and `kpsewhich` on `PATH` (MiKTeX confirmed installed and
  working on this machine). Built-in packages (`amsmath`/`amssymb`/`xcolor`) need no
  further setup; anything else a course's content needs (e.g. `mhchem` for this
  course) must be declared in `config.yaml`'s `latex-packages` — see
  `config.example.yaml`. Not a pinned Python dependency — `pl2docx` degrades
  gracefully (placeholder `$latex$` text) when the TeX install itself is missing, so
  this is a "nice to have for real math output" prerequisite, not a hard install
  requirement. A *declared-but-missing* package, by contrast, fails fast and loudly
  at `pl2docx-render` startup (`LatexPackageNotFoundError`) rather than degrading
  silently — see `latex_math.py`'s repo-structure entry above.
- **Local PrairieLearn Server**: a local PrairieLearn dev instance
  is running via wsl and Docker at http://localhost:3000/.  If this does not load, it can be restarted by running `docker run -it --rm -p 3000:3000 -v ~/pl-pitt-chem0110:/course prairielearn/prairielearn` in wsl.  If this fails, ask for help - it may require the user to perform a manual restart.
  - **Confirmed gotcha (2026-08-12), fixed structurally the same day: every restart
    reassigns fresh numeric IDs.** The `--rm` flag with no persistent volume for
    Postgres means each container start is a genuinely fresh database — the course
    gets re-synced from disk from scratch, and `assessment`/`course_instance`/`course`
    rows get new auto-increment ids, not necessarily the same ones as before.
    Originally hit as: `config.yaml`'s numeric `assessment_id` silently went stale
    after a restart, and `pl2docx-fetch`/`test_pl_client_integration.py` failed with a
    403 whose page body says "This assessment's configuration does not allow you to
    access it right now." — reads like an access-control/permissions problem, but is
    actually `selectAndAuthzAssessment.sql`'s `WHERE a.id = $assessment_id AND
    a.course_instance_id = $course_instance_id` matching zero rows (a not-found, not a
    real authz denial). If you ever see that exact message, check for a numeric-id
    mismatch first, not a real permissions/accessControl issue.
    **Fix (implemented in `pl_client.py`/`config.py`/`fetch.py`)**: `config.yaml` no
    longer stores numeric ids at all — it stores PL's stable, human-authored text
    identifiers (`course_short_name`, `course_instance_short_name`, `assessment_tid`),
    resolved to the server's *current* numeric ids at the start of every
    `fetch_n_instances` run via `PLClient.resolve_course_id`/
    `resolve_course_instance_id`/`resolve_assessment_id`. See `pl_client.py`'s module
    docstring for exactly which pages each stable identifier is scraped from
    (`tid` in particular needs an extra per-assessment request, since PL's assessments
    list page doesn't expose it — see that docstring before assuming a shortcut
    exists). A `/pl/api/v1/` JSON API exists for the assessment lookup specifically but
    requires a separately-issued API token, so isn't used, to keep this working purely
    off the same session-cookie auth the rest of `pl_client.py` already relies on.
  
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
  sub-statements; `previous-experience`'s radio group + text box). **Done as of Phase 3B**
  (2026-08-12) — `html_parser.py`'s `ParsedQuestion.widgets` holds one `Widget` per
  named group, in source order; each is rendered independently at its own source
  position (see Architecture item 5 / `element_renderer.py`), best-effort per the
  original spec (address specific fragility if/when it surfaces, not preemptively).
- **Document-structure-level formatting configuration**: **done as of Phase 3A**
  (2026-08-12) — question number/title/points/qid and zone titles are all exposed to
  the instructor's template; see Architecture item 5.
- **Element/question-level formatting configuration (Phase 3B)**: **done as of Phase 3B**
  (2026-08-12) — `config.yaml`'s `global-element-preferences`/`additional-elements`
  sections, resolved via `pl2docx.element_config`, drive per-widget list-style/
  bold-correct/display/draw-border formatting in `element_renderer.py`. Diverged from
  the original spec in `planning_notes/2026-08-12 phase 3a implementation and phase 3b
  spec.md` in three ways (captured there in the Phase 3B section, and in the
  implementation itself): a single `display` vocabulary
  (`inline`/`block`/`template`/`none`) shared by selector- and fill-in-type widgets
  instead of separate `display`/`format` options; widgets render inline at their real
  source position rather than appended after the whole prompt; and `draw-border` draws
  one box around a widget's *entire* rendered content, not one box per option.
- **Fill-in-type element scope (2026-08-12)**: originally just `pl-string-input`/
  `pl-integer-input`; extended the same day to `pl-number-input`/`pl-symbolic-input`/
  `pl-units-input` (built-in — confirmed core PL elements sharing the exact same markup
  pattern) plus a generic `additional-elements` detection pathway for non-built-in
  fill-in elements following that same convention (e.g. `pl-scinum-input`, course-
  specific). `pl-big-o-input` was checked and explicitly deferred (irregular class
  name breaks the pattern) — see `html_parser.py`'s repo-structure entry above.
- **Rich HTML → docx conversion, image embedding, and math rendering (Phase 4)**:
  **done** as of 2026-08-12 — paragraphs/bold/italic/underline/lists (increment 1,
  with real Word list numbering as a same-day follow-up), inline images (increment 2),
  and math (increment 3, rendered as real-`latex`-compiled images rather than OMML —
  see `latex_math.py`'s repo-structure entry above and
  `planning_notes/2026-08-12 phase 4 implementation and design decisions.md` for why
  the OMML target sketched in the original roadmap doc was revised, plus every other
  Phase 4 design decision and known limitation).
- **SVG embedding (Phase 5 subphase 1, replaces the original course-adapter Phase
  5/6 plan)**: **done** as of 2026-08-13 — see "Extensibility" above for why the
  adapter-interface approach was replaced, `svg_render.py`'s repo-structure entry for
  the rendering pipeline itself, and
  `planning_notes/2026-08-13 phase 5 implementation and design decisions.md` for the
  full session writeup (all three subphases) including a real sizing bug found and
  fixed mid-session.
- **Canvas-based interactive element capture (Phase 5 subphase 2)**: **done** as of
  2026-08-13 — see `canvas_capture.py`'s repo-structure entry and the same planning
  note above. Verified against a real `pl-orbitaldiagram` question added to the test
  assessment specifically for this (note: `lewis-structures-extended`, used to verify
  subphase 1, has `print="true"` set and so only ever emits SVG — it was never a
  live-canvas test case, a correction from this session's initial planning). Default
  toolbar-hide and container-selector guesses were revised mid-session after visual
  review found toolbar-reserved whitespace and low resolution in real captures — see
  the planning note's §2 for the full before/after.
- **Zero-widget questions supported (Phase 5 subphase 3)**: **done** as of
  2026-08-13 — subphases 1/2 flatten SVG/canvas content to plain images, but a
  question whose *only* content is one of those diagrams (no other input widget on
  the page at all, e.g. `lewis-structures-extended`/the new `pl-orbitaldiagram`
  question) still hit `html_parser.py`'s "zero recognized widgets" guard, blocking
  `pl2docx-render` for the whole assessment. `element_renderer.py` already handled
  `widgets == []` from an earlier phase, so this was purely relaxing that one guard
  — see `html_parser.py`'s repo-structure entry. Verified: the full
  `pl2docx-phase1-test` assessment (all 4 zones, including both diagram-only
  questions) now renders end-to-end with no regressions to previously-working zones.

## Verification checklist (once implemented)

- Generated instance question selection matches a real student flow (incl. `numberChoose`).
- All element types in current use render without errors in blank + key:
  `pl-multiple-choice`, `pl-number-input`, `pl-string-input`, `pl-checkbox`,
  `pl-scinum-input`, `pl-order-blocks`, `pl-dropdown`, `pl-symbolic-input`,
  `pl-integer-input`, `pl-image-capture`, `pl-orbitaldiagram`, `pl-chemformula-input`,
  `pl-drawing`. **Confirmed 2026-08-12: `pl-image-capture` is not yet supported**
  (surfaced when `reactions/extended/KNO3-synthesis` was ruled out as a Phase 4 math
  stress-test question for exactly this reason) — real work still needed here, not
  just an untested item. **Confirmed 2026-08-13: `pl-orbitaldiagram` now supported**
  via canvas capture (Phase 5 subphase 2, `canvas_capture.py`) — its fabric.js canvas
  is screenshotted and embedded as a plain image, not rendered as an editable widget.
  `pl-drawing` is not yet configured/tested with canvas capture (no test question in
  the current test assessment) but is expected to work with an explicit
  `container-selector`/`hide-selectors` override, since it doesn't follow the default
  tag-name-derived guesses — see `canvas_capture.py`'s module docstring.
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
