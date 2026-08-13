# pl2docx: Phase 4 Implementation and Design Decisions

Session handoff note. Phase 4 (rich HTML → docx: formatting, images, math) is now
**complete** across all three increments. This doc captures the *reasoning* behind
what shipped — the roadmap doc (`2026-08-10 phased implementation roadmap.md`) sketched
LaTeX→OMML as Phase 4's math-rendering target; what actually shipped is LaTeX→image,
for reasons documented below that weren't knowable until real course content was in
hand. Read this before starting Phase 5.

Commits (in order): `55b0457` (increment 1: rich text), `fc2fd81` (increment 2: image
embedding), `88920b0` (real Word list numbering, a follow-up to increment 1),
`62eec60` (increment 3: LaTeX math rendering), `4cf6501` (config-driven LaTeX
packages, a follow-up to increment 3).

## 1. Increment 1: rich text formatting (`55b0457`, `88920b0`)

`html_parser.py`'s old `get_text()`-based flattening was replaced with a shared,
order-preserving node representation — `TextRun`/`ParagraphBreak`/`ListItemStart`/
`ListItemEnd`/`ImageRef`/`MathRef` — built by a recursive HTML walker
(`_walk_content`/`_walk_into`), instead of collapsing everything to plain strings.
Applies uniformly to prompt text, answer-panel text, and selector-option/fill-in
label/suffix content, since real course content has rich formatting (and, as it
turned out, LaTeX) in all of those places, not just the main prompt.

`element_renderer.py` renders these node sequences into real formatted runs
(bold/italic/underline via `run.bold`/etc.). `ImageRef`/`MathRef` were initially
placeholders (`"[image]"` / literal `"$...$"` text) until increments 2/3 landed.

**Follow-up (`88920b0`)**: replaced flat `"1. "`/`"• "` prepended-text lists with
real Word `<w:numPr>` list paragraphs. Notable subtlety: a list split across multiple
`prompt_segments` by an interleaved widget (real content: `physical-or-chemical`'s
3-item list, each item holding its own dropdown) still needs to count as *one* Word
list — solved via `ListItemStart.list_id` (`id()` of the source `<ol>`/`<ul>` tag,
stable within one parse). Minting a fresh `<w:num>` per distinct `list_id` against one
shared `abstractNum` needed an explicit `<w:lvlOverride><w:startOverride>`, or Word
kept counting from a prior list sharing that `abstractNum` (e.g. a question's own list
at 1/2/3, then its answer-key box picking up at 4/5/6 instead of restarting). A whole
`<li>`'s content renders into one real paragraph via soft line breaks
(`Run.add_break()`), not separate paragraphs — this soft-break-for-internal-structure
pattern became the template increment 3 later reused for display-mode math (see §3.5).

## 2. Increment 2: image embedding (`fc2fd81`)

`ImageRef` gained `width_px`, parsed from the source `<img width>` attribute (PL's own
intended on-page display size, in CSS reference pixels — 96px/inch, not a guess).
`element_renderer.py`'s `_render_image` embeds via `Run.add_picture()`, sized from
that attribute (`Inches(width_px / 96)`) or a fixed default width when absent. Falls
back to alt text — never fails the whole render — when no `image_base_dir` is given or
the file isn't on disk. `image_base_dir` threads through `build_question_context` →
`render_document` → `render.py`'s `render_instance`, resolving each `ImageRef` against
the correct `blank/`/`key/` instance directory `fetch.py` already downloaded images
into (back in Phase 1).

## 3. Increment 3: LaTeX math rendering (`62eec60`) + config-driven packages (`4cf6501`)

The largest and most-revised piece of Phase 4. Summarized here roughly in the order
decisions were made and revised, since several were only discoverable once real
course content or real visual output was in hand.

### 3.1 Why real LaTeX, not a pure-Python LaTeX→OMML converter

Research this session (before any code) established: PrairieLearn renders math
**client-side only**, via MathJax JS running in the browser after page load
(`apps/prairielearn/src/lib/client/mathjax.ts`, no-ops outside a browser). There is no
server-side math rendering pass anywhere in PL. Since `pl2docx` fetches HTML via
`requests`+BeautifulSoup (no headless browser except the separate `pl-drawing`
fallback), the fetched HTML always contains **raw, undelimited LaTeX source text** —
literal `$x^2$`/`\(x^2\)`/`$$...$$`/`\[...\]`. A real interpreter is required; there's
no pre-rendered markup to just extract text from.

Considered three approaches to interpreting it (full comparison in the conversation,
not repeated here):
- **Pure-Python `latex2mathml`+`mathml2omml`**: produces *editable* Word equations,
  but neither library implements `mhchem`'s `\ce{...}` macro (confirmed real usage in
  the course's `chemutils/compounds.py` — chemical formulas, charge/state
  superscripts/subscripts). Hand-rolling just that subset would be an open-ended
  maintenance liability against future course content using more of `mhchem`'s
  grammar (reaction arrows, precipitate/gas markers, etc.).
- **MathJax (Node.js)**: has an `mhchem` extension, but no OMML output of its own —
  still needs a MathML→OMML step (same fragility as above) or SVG→raster (no
  advantage over the option below, plus a Node.js toolchain instead of a TeX one).
- **Chosen: compile real LaTeX to a raster image**, embedded via increment 2's
  already-built, already-tested image pipeline. Any LaTeX construct a course ever
  uses "just works" (it's genuine LaTeX, not a reimplementation) — no subset to
  maintain, ever. Trade-off: equations are non-editable images, not native Word
  equation objects; a TeX distribution must be installed on the machine running
  `pl2docx` (confirmed present here: MiKTeX). Also: `python-docx` cannot embed SVG
  directly (`add_picture` raises `UnrecognizedImageError` — confirmed via
  python-docx's own open issues), so even an SVG-based route would need this same
  rasterize-to-PNG step; going straight to a raster pipeline is simpler than adding an
  SVG intermediate.

### 3.2 Pipeline: `latex`+`dvipng`, not `pdflatex`+`pdftoppm`

First implementation used `pdflatex` → PDF → `pdftoppm` (poppler-utils) → PNG. Revised
after the user's visual review found fraction/subscript baselines floating wrong:
switched to `latex` (DVI, not PDF) → `dvipng`, specifically because `dvipng --depth`
reports exactly how far rendered content extends below the LaTeX baseline (in pixels,
at the render DPI) — `pdftoppm` has no equivalent. `pl2docx.latex_math.RenderedMath`
carries this as `depth_pt`; `element_renderer.py` applies it as a downward OOXML
`<w:position>` run-level shift (negative = lower, ECMA-376 §17.3.2.36), since Word
otherwise always anchors an inline picture's *bottom* edge to the text baseline —
correct only when the image has no descender.

### 3.3 Sizing bug: `dvipng`'s `pHYs` metadata is unreliable

Second visual-review round: math was rendering ~6x too large. Root cause: `dvipng`
writes a **fixed ~96 DPI `pHYs` chunk regardless of the `-D` value actually used to
rasterize** (confirmed: `IHDR` was 591×111px at `-D 600`, but the embedded metadata
claimed 96 DPI). `python-docx`'s `add_picture` auto-sizing trusts that metadata, so it
scaled every equation as if it were a 96 DPI image when it was really 600 DPI. Fixed by
computing `RenderedMath.width_in` directly from the PNG's real pixel width (read from
its `IHDR` chunk) divided by the DPI actually requested, and passing that explicitly as
`add_picture`'s `width=` — never relying on the file's own embedded DPI metadata again.

### 3.4 Clipping, weight, and resolution

Three smaller fixes from the same visual-review round:
- **Glyph-top clipping** (e.g. ascenders on a line with no descender had ~0 margin
  above them): `\setlength\PreviewBorder{1pt}` — a small margin around the `preview`
  package's otherwise pixel-tight bounding box.
- **Math reading visually "light"**: `\boldmath` in the preamble. Confirmed via a
  side-by-side render (bold vs. default weight of the same `\ce{}` formula) that this
  matches PrairieLearn's own MathJax rendering, which the user identified as using a
  visibly heavier weight than plain LaTeX's default math font.
- **DPI bumped 400→600**: free quality improvement given `width_in` is now computed
  from real pixel dimensions (§3.3) — resolution no longer trades off against physical
  size, since size is derived from resolution rather than assumed from possibly-wrong
  metadata.

### 3.5 Display-mode math: soft line breaks, not an isolated centered paragraph

First implementation isolated `$$...$$`/`\[...\]` into its own real Word paragraph,
centered (`w:jc`). Revised after discussion: the user prefers soft line breaks
(`Run.add_break()`, the *same* mechanism increment 1 already uses for line breaks
inside a list item — see §1) around display math instead, sacrificing centering,
specifically because real course content has display math **inside**
`<ol>`/`<ul>` list items, and one soft-break rule that behaves identically whether or
not it's currently inside a list item means that case needs no separate handling when
it's eventually exercised — rather than resurfacing the "how do I isolate a block of
content inline vs. inside a list item" design question a second time.

Centering was dropped as a direct consequence, not a separate compromise: OOXML
alignment (`w:jc`) is a paragraph-level property, so centering *only* the display-math
line while leaving surrounding prose in the same paragraph left-aligned isn't
achievable without tab-stop-based centering tricks, which weren't pursued.
`_render_nodes_into_subdoc`'s `needs_break_before_next` flag (`element_renderer.py`)
implements this: a soft break before display math (unless it's already first in the
paragraph) and one after (unless nothing follows), symmetric with list-item handling.

### 3.6 Double-paragraph-break bug (found via this same display-math work)

While reviewing multi-paragraph answer-key content, the user noticed **two** full
paragraph breaks between adjacent `<p>` tags instead of one — too much vertical space.
Root cause: `_walk_into`'s per-`<p>` handling emits a `ParagraphBreak` at both the end
of one `<p>` and the start of the next; `_normalize_nodes` already collapses *adjacent*
`ParagraphBreak`s, but ordinary source-formatting whitespace between `</p>` and the
next `<p>` (e.g. indentation) survives whitespace-collapse as a single-space `TextRun`
sitting *between* the two breaks — defeating the adjacency check. Fixed by dropping any
whitespace-only `TextRun` immediately touching a `ParagraphBreak` on either side (a
space is never meaningful at a paragraph boundary — nothing renders on the same visual
line across a real paragraph break), then re-collapsing newly-adjacent breaks.

### 3.7 Config-driven LaTeX packages (`4cf6501`)

`mhchem` was initially hardcoded into every equation's preamble alongside
`amsmath`/`amssymb`/`xcolor` — correct for this course, wrong as a `pl2docx` default
(other instructors' TeX installs won't necessarily have it, and CLAUDE.md's own ground
rule prohibits course-specific assumptions in core tool code). Discussed two
alternatives with the user (auto-detecting needed packages from equation content, e.g.
sniffing `\ce{`, vs. explicit config) and chose **config**, specifically because
auto-detection doesn't generalize (would need a growing, hardcoded macro→package table
for every package any course might reach for) and still wouldn't guarantee the package
is actually installed. This mirrors `element_config.py`'s existing `additional-elements`
pattern exactly: one `config.yaml` line, no `pl2docx` code change, ever, for a new
package.

Only `amsmath`/`amssymb`/`xcolor` remain built-in (genuinely universal). Anything else
goes in `config.yaml`'s new `latex-packages` list, registered once per process via
`latex_math.configure_extra_packages()` (called from `render.py`'s `main()`) — module-
level, process-lifetime state, deliberately *not* threaded as a parameter through
`build_question_context`/`_render_nodes_into_subdoc`/etc., since it's a true run-level
constant (same for every equation in one `pl2docx-render` invocation), unlike
`ElementConfig`'s genuinely per-widget preferences.

Per the user's explicit request, declared packages are **validated at startup**
(`kpsewhich <pkg>.sty`) rather than deferred to individual equation failures —
`LatexPackageNotFoundError` is deliberately *not* a subclass of `LatexRenderError`, so
it can never be accidentally caught by `element_renderer.py`'s per-equation
placeholder-text fallback; a config typo should fail the whole run immediately, not
degrade N equations silently. Skipped entirely (no error) when `kpsewhich` itself isn't
on `PATH` — that's the pre-existing "no LaTeX install at all" case, already handled
gracefully by every `render_math_png` call's own fallback.

This user's own (gitignored, local) `config.yaml` now has `latex-packages: [mhchem]`.

## Known limitations / explicitly deferred (not oversights)

- **Math renders as non-editable images**, not native Word equation objects — the
  direct trade-off of choosing real-LaTeX-compilation over a pure-Python OMML
  converter (§3.1). Revisit only if an instructor actually needs to hand-edit a
  rendered equation in Word, which print-only exam generation is unlikely to need.
- **Display-mode math is not centered** — direct consequence of the soft-line-break
  decision (§3.5), an explicit trade-off, not a bug.
- **`mhchem`'s `[version=4]` option was dropped** when packages became configurable
  (no generic per-package-options mechanism was built, since only a flat name list was
  requested) — cosmetic only (silences a "no version specified" warning, doesn't
  affect rendering or fail compilation).
- **`config.yaml`'s `latex-packages` is a flat list**, no per-package options. Natural
  future extension if ever needed, not built now.
- **`pl-image-capture` is confirmed NOT supported** — surfaced this session when the
  user found `reactions/extended/KNO3-synthesis` (an initially-planned stress-test
  question) contains one; they substituted `reactions/practice/limiting-reagents-pct-yield`
  instead (now wired into `pl2docx-phase1-test`'s new "Math conversion pool" zone,
  added by the user directly per CLAUDE.md's course-repo-edit boundary). Flagged here
  as an explicit known gap, not silently discovered later.

## Testing patterns established this phase (useful for Phase 5)

- **Skip-if-tool-missing pattern** for tests needing a real external tool: both
  `test_latex_math.py` (needs `latex`+`dvipng`) and `test_pl_client_integration.py`
  (needs a live PL server) use `pytest.mark.skipif` keyed on `shutil.which`/similar,
  rather than failing on a machine without the dependency. Phase 5's headless-browser
  work (`pl-drawing`, Phase 6) will likely want the same pattern for Playwright.
- **Mocked-dependency pattern** for testing render-tree logic without the real
  external tool: `test_element_renderer.py`'s `_mock_render_math_png` monkeypatches
  `render_math_png` to return a fixed `RenderedMath` (backed by the existing
  `tiny_dot.png` test fixture), so paragraph/soft-break structural tests
  (`test_display_math_separated_by_soft_breaks` etc.) run unconditionally, independent
  of whether a LaTeX install is present.
- **Autouse fixture for shared module state**: `test_latex_math.py`'s
  `_reset_extra_packages` autouse fixture resets `configure_extra_packages([])`
  before/after every test, since that state is a process-lifetime module global (by
  design — see §3.7) and would otherwise leak between tests.

## Suggested prep for Phase 5

Per the roadmap doc's Phase 5 description (course-owned static elements —
`pl-orbitaldiagram`, future `pl-lewisstructure`): the adapter interface for
course-specific static renderers is explicitly **not yet designed** (deferred in
CLAUDE.md's "Extensibility" section, and again in the roadmap doc, pending real
`layout_json`/rendering samples). Two concrete things worth doing at the *start* of a
Phase 5 session, before any design:
1. Fetch a real instance of a question using `pl-orbitaldiagram` (or
   `pl-lewisstructure`, if the course has one wired into a test assessment) and look at
   its actual embedded `layout_json`/rendered markup — the roadmap doc flags this
   as a prerequisite, not optional research.
2. Check whether `pl-lewisstructure` and/or `pl-orbitaldiagram` have gained (or could
   gain) a "print mode" server-rendered SVG output since `2026-08-10`'s roadmap note —
   the roadmap doc's own reasoning was that leaning on each element's own print-mode
   output (where available) is likely preferable to reimplementing rendering geometry
   in `pl2docx`, since both elements are under active development in the course repo
   and reimplementing risks drifting out of sync. This is a real open question to
   resolve with the user, not an implementation detail to guess at.
