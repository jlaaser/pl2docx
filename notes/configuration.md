# Configuration reference (`config.yaml`)

This is the detailed field-by-field reference for `config.yaml`. For a quick-start
version with less explanation, see the commented `config.example.yaml` at the repo
root — copy it to `config.yaml` and fill in the values. This document exists so
someone helping a user set up their config file doesn't need to read the
implementation (`config.py`/`element_config.py`) to understand what a field does or
what values it accepts.

## Connecting to PrairieLearn

| Field | Type | Required | Description |
|---|---|---|---|
| `base_url` | string | yes | Root URL of the PL server, no trailing slash, e.g. `"http://localhost:3000"`. |
| `course_short_name` | string | yes | The target course's stable `short_name`, from its `infoCourse.json`. Shown on the PL homepage's course list, colon-separated from the title (e.g. `"CHEM 0110: General Chemistry I"` → short name is `"CHEM 0110"`). |
| `course_instance_short_name` | string | yes | The target course instance's stable `short_name`, from its `infoCourseInstance.json`. Shown as its own "Short name" column on `/pl/course/<course_id>/course_admin/instances`. |
| `assessment_tid` | string | yes | The target assessment's `tid` — its directory name under `assessments/` in the course repo. Deliberately not the assessment's (editable) title. |

These are matched by PL's own stable, human-authored identifiers rather than numeric
database ids, and resolved to the server's current numeric ids at the start of every
run. If your PL server's database isn't persistent across restarts, this means
`config.yaml` never needs updating after a restart even though the underlying numeric
ids change.

## Generating instances

| Field | Type | Default | Description |
|---|---|---|---|
| `n_instances` | int | `1` | Number of distinct instances to generate. Ignored when `instance_ids` is set. |
| `instance_ids` | list of strings | unset | Explicit instance labels, one instance generated per string, in order. Replaces `n_instances` entirely when set (non-empty). Each label becomes both the output folder name (instead of PL's numeric instance id) and the value of the `instance_ID` variable available in the docx template (see [templating.md](templating.md)). When unset, output folders are named by PL's numeric id and `instance_ID` defaults to `"Instance 1"`, `"Instance 2"`, etc. |

## Output

| Field | Type | Default | Description |
|---|---|---|---|
| `output_dir` | path | `"output"` | Directory where fetched HTML and generated docx files are saved, one subfolder per instance. Resolved relative to the directory you run the tool from. |
| `template_path` | path | `"template.docx"` | Path to the instructor-supplied docx template used when rendering. Generate a starting point with `pl2docx-starter-template`. Resolved relative to the directory you run the tool from. |

## Document-wide formatting

| Field | Type | Default | Description |
|---|---|---|---|
| `restart_numbering_per_zone` | bool | `false` | Whether question numbering (`1.`, `2.`, `3.`, ...) restarts at 1 at the start of every zone, instead of running continuously across the whole document. |
| `block_display_indent_inches` | float | `0.125` | Left indent (inches) applied to every widget whose content renders as its own block (a fresh paragraph or table) rather than sharing a line with the prompt text. Set to `0` to disable. |

## Per-element formatting: `global-element-preferences`

An optional section keyed by PL element tag name (e.g. `pl-multiple-choice`), letting
you override how that element kind renders. All keys within an entry are optional;
anything you don't set uses a built-in default. This section covers PL's built-in
element types; for elements not natively supported, see `additional-elements` below.

Fields available for **selector-type** elements (`pl-multiple-choice`, `pl-checkbox`):

| Key | Values | Default | Description |
|---|---|---|---|
| `list-style` | `letter-labels` \| `bubble` \| `checkbox` | element-specific (`bubble` for multiple-choice, `checkbox` for checkbox) | Marker style shown before each option. |
| `bold-correct` | bool | `true` | Whether to bold the correct option in the answer key. |
| `display` | `inline` \| `block` \| `template` \| `none` \| unset | auto-detected | Where/how this widget's content is placed — see "The `display` field" below. |
| `draw-border` | bool | `false` | Draw a box around the widget's entire rendered content (all options together). |
| `blank-answer-lines` | int \| unset | `2` | Blank lines given in the blank copy's answer space for a question using this widget kind. A question with multiple widget kinds uses the largest configured value across them. |

Fields available for **fill-in-type** elements (`pl-string-input`, `pl-integer-input`,
`pl-number-input`, `pl-symbolic-input`, `pl-units-input`, `pl-rich-text-editor`, plus
any `additional-elements` entry declaring `type: fill-in`):

| Key | Values | Default | Description |
|---|---|---|---|
| `display` | `inline` \| `block` \| `template` \| `none` \| unset | `block` | Where/how this widget's fill-in-the-blank content is placed (no auto-detection signal exists for this element class). |
| `draw-border` | bool | `false` | Draw a box around the widget's label/blank/suffix content. |
| `default-label` | string \| unset | none | Fallback label text (e.g. `"Answer:"`) shown before the blank, used only when the element's own source HTML supplies no label of its own. Never applied when the widget already has a label. |
| `blank-answer-lines` | int \| unset | `2` (`8` for `pl-rich-text-editor`) | Same meaning as above. |

`pl-order-blocks` has its own single setting:

| Key | Values | Default | Description |
|---|---|---|---|
| `layout` | `vertical` \| `horizontal` | `vertical` | `vertical` renders a 2-column table (lettered block pool \| order blank). `horizontal` renders the same content inline instead. |

### The `display` field

- `inline` — content shares a line with the surrounding prompt text.
- `block` — content starts on its own paragraph/table, indented by
  `block_display_indent_inches`.
- `template` — content is routed to a separate `answer_element` template slot instead
  of the main question body (see [templating.md](templating.md)) — useful for widgets
  you want to place somewhere other than inline with the prompt.
- `none` — content isn't rendered at all.
- unset — for selector-type elements, auto-detected from the source HTML when
  possible, else falls back to `block`. Fill-in-type elements have no detection signal
  and always fall back to `block`.

## Elements not built into pl2docx: `additional-elements`

An optional section for element kinds pl2docx doesn't natively recognize — typically
course-specific elements. Each entry declares which behavior class it should be
treated as:

```yaml
additional-elements:
  my-custom-input:
    type: fill-in       # selector | fill-in | interactive
    # ...any of the fill-in fields from the table above...
```

- `type: selector` / `type: fill-in` — the element is treated exactly like the
  matching built-in class above, and accepts the same formatting keys. **Detection is
  purely tag-name-derived**: a fill-in-type element is detected by its `<input>` /
  `<textarea>` carrying a `"{tag}-input"` (or `"{tag}-multiline"`) CSS class — no
  element-specific code is needed in pl2docx. If your element's real class doesn't
  follow that convention (e.g. it's missing the usual `pl-` prefix), set `class-prefix`
  to override the base string used to build that pattern:

  ```yaml
  additional-elements:
    my-custom-input:
      type: fill-in
      class-prefix: my-custom  # if the real class is "my-custom-input", not "my-custom-input-input"
  ```

- `type: interactive` — for canvas-based widgets a student draws on directly. Unlike
  the other two types, this doesn't configure how a widget renders in the docx
  template — the widget is screenshotted (via a headless browser) and flattened to a
  plain image entirely at fetch time, so by the time rendering happens it's already an
  ordinary embedded picture.

  ```yaml
  additional-elements:
    my-canvas-widget:
      type: interactive
      # container-selector omitted -> pl2docx guesses based on the tag name (tries a
      # narrower ".{tag}-canvas-wrap" selector first, falling back to the whole
      # ".{tag}" element). Not every element follows this convention — set explicitly
      # if the guess doesn't match your element's real DOM structure:
      # container-selector: ".my-canvas-widget-container"
      #
      # hide-selectors omitted -> pl2docx tries a handful of guessed toolbar/controls
      # selectors before falling back to capturing whatever toolbar is visible. Set
      # explicitly for anything else:
      # hide-selectors: [".my-canvas-widget-toolbar"]
  ```

### Worked examples: two core PL elements that need explicit configuration

Two of PL's own core elements don't follow pl2docx's default conventions closely
enough to be auto-detected, and need to be added via `additional-elements` if you use
them:

- **`pl-big-o-input`** (a fill-in-type element) — its `<input>` element's CSS class is
  `big-o-input-input`, missing the usual `pl-` prefix every other fill-in element's
  class carries, so it needs an explicit `class-prefix` override:

  ```yaml
  additional-elements:
    pl-big-o-input:
      type: fill-in
      class-prefix: big-o-input
  ```

- **`pl-drawing`** (a canvas-based interactive element) — unlike the elements
  pl2docx's default container-selector guess is based on, `pl-drawing` wraps its
  canvas in a container whose class is `pl-drawing-container` (not just `pl-drawing`),
  and its toolbar/sidebar has its own distinct class, so both need to be set
  explicitly:

  ```yaml
  additional-elements:
    pl-drawing:
      type: interactive
      container-selector: ".pl-drawing-container"
      hide-selectors: [".pl-drawing-sidebar"]
  ```

## LaTeX packages

| Field | Type | Default | Description |
|---|---|---|---|
| `latex-packages` | list of strings | `[]` | Extra LaTeX packages (names only, no `.sty` extension) to load in every rendered math snippet's preamble, beyond the small built-in default set (`amsmath`, `amssymb`, `xcolor`). Declare anything your course's questions need here — this isn't auto-detected from equation content. Validated at the start of every render; a missing package fails fast with a clear error rather than each affected equation silently falling back to placeholder text. |

## Notes on path resolution

`output_dir` and `template_path` (and `config.yaml` itself, if you pass a path to
`pl2docx-run`/`pl2docx-fetch`/`pl2docx-render` explicitly) are resolved relative to
whatever directory you run the command from — not relative to where pl2docx is
installed. This means you can keep a given assessment's `config.yaml` + `template.docx`
in its own folder anywhere on disk and just `cd` there before running the command.
