pl2docx is a utility for creating printable assignments from [PrairieLearn](https://www.prairielearn.com/) question banks.
Assessments are fetched from a live PrairieLearn server (typically running locally via Docker - see below) and rendered onto an instructor-provided .docx template, providing assessments that can either be printed as-is or fine-tuned by the instructor before printing.

# Supported elements

pl2docx currently supports the following core PrairieLearn elements out of the box, with no configuration needed:
- `pl-multiple-choice` (including its dropdown display mode)
- `pl-checkbox`
- `pl-string-input`
- `pl-integer-input`
- `pl-number-input`
- `pl-symbolic-input` (plain text-entry mode only - see below)
- `pl-units-input`
- `pl-rich-text-editor`
- `pl-matching`
- `pl-order-blocks`
- `pl-figure`

Additional notes about supported content and elements:
- Static content - images, embedded SVG diagrams, and LaTeX math markup - renders automatically wherever it appears, independent of which input elements are on the page.
- `pl-big-o-input` and `pl-drawing` are supported if added via the "additional-elements" section of the config file - see [notes/configuration.md](notes/configuration.md) for more details.

pl2docx currently *does not* support:
- `pl-matrix-component-input`
- `pl-matrix-input`
- `pl-image-capture` (file-upload-style image capture)
- `pl-sketch` (note: *may* work if added as an additional interactive element, as described below, but this has not been tested)
- `pl-excalidraw`
- `pl-file-upload`
- `pl-file-editor`
- `pl-symbolic-input`'s `formula_editor` rendering mode (a rich equation editor, rather than a plain text field)
- `pl-graph`
- `pl-code`
- `pl-python-variable`
- `pl-dataframe`
- `pl-matrix-latex`
- `pl-variable-output`
- `pl-external-grader-variables`
- `pl-overlay`

Most of these elements won't cause an error, but may render unpredictably in the .docx output, so use at your own risk.

For course-specific elements (e.g. those defined in your course's `elements` folder), or other core PrairieLearn elements not listed above, pl2docx offers a config file-based approach for adding support for elements that fit into any of the following three categories:
- "selector"-type elements analogous to `pl-multiple-choice` and `pl-checkbox`
- "fill-in"-type elements analogous to `pl-string-input`, `pl-integer-input`, etc.
- "interactive" elements that render to a `<canvas>` object a student draws on directly (e.g. a diagram-drawing widget)

For information about how to add custom elements of each type, see the "Configuration options" section, below.

An element pl2docx doesn't recognize (and hasn't been configured to treat as one of the three categories above) won't cause an error - the surrounding prompt text and any static content still renders - but that specific input control won't get its own fill-in blank or answer space. If you need one, either configure it (if it follows a supported markup convention) or extend pl2docx to support it (see "Run into a problem or have a feature request?", below).

# Installation

Before using pl2docx, you will need to:

1. Set up a local instance of PrairieLearn running under Docker. See [PrairieLearn's doc page on installing and running locally](https://docs.prairielearn.com/installing/) for instructions. pl2docx needs this running and reachable whenever you fetch an assessment.
2. Install [uv](https://docs.astral.sh/uv/), if you don't already have it.
3. Install Playwright's headless Chromium browser (used to rasterize SVG diagrams and screenshot canvas-based interactive elements):
   ```bash
   uv run playwright install chromium
   ```
4. If any of your course's questions use LaTeX math markup, make sure your machine has a working LaTeX install (with `latex`, `dvipng`, and `kpsewhich` on `PATH`) and is up to date with any specialty packages (such as `mhchem` or `siunitx`) your course's markup uses. This is optional - without it, math renders as placeholder text instead of a typeset image - but recommended for real use.
5. From this repo's directory, install pl2docx as a command-line tool available from anywhere on your machine:
   ```bash
   uv tool install --editable .
   ```
   (Use `--editable` if you plan to keep modifying pl2docx's own code; otherwise a plain `uv tool install .` works too.) This puts `pl2docx-fetch`, `pl2docx-render`, `pl2docx-run`, and `pl2docx-starter-template` on your `PATH`.

# Running pl2docx

To generate a printable assignment using pl2docx, do the following:

1. Set up a PrairieLearn assessment defining the assignment for which you wish to generate printable copies. Set this assessment up exactly as you would if you were going to administer it via PrairieLearn - zones, questions, question alternatives, point values, the works.
2. If the assessment was set up on the remote PrairieLearn servers, make sure to pull the up-to-date copy of your course repo to your local machine before proceeding.
3. Start your local PrairieLearn server, pointed to the local copy of your course repo.
4. Create a folder for this assessment's pl2docx files (anywhere on your machine - it doesn't need to be inside this repo). In that folder:
   - Copy `config.example.yaml` from this repo to `config.yaml` and fill in your server address, course/course-instance/assessment identifiers, and any other desired settings - see [notes/configuration.md](notes/configuration.md) for the full field reference.
   - Generate a starting template with `pl2docx-starter-template`, then open `template.docx` in Word and customize it (see "Assessment templating" below).
5. From that folder, run:
   ```bash
   pl2docx-run
   ```
   This fetches the configured number of instances from your PrairieLearn server and renders each one to a blank copy + answer key `.docx`, written under `output/`.

   (`pl2docx-fetch` and `pl2docx-render` are also available separately, if you want to re-render already-fetched instances against an updated template without re-fetching - `pl2docx-run` just chains the two together for the common case.)

All paths in `config.yaml` (`output_dir`, `template_path`) are resolved relative to whatever directory you run the command from, so a given assessment's config/template/output can all live together in their own folder.

# Configuration options

pl2docx offers a number of configuration options for controlling the formatting of the .docx output and rendering of specific elements.

Broadly, the config file lets you configure:
1. How/where to access the assessment on PrairieLearn
2. How many print-ready variants of the assessment should be generated
3. Global preferences for how different classes of PrairieLearn elements should be rendered into the .docx output
4. How course-specific or non-core input elements should be processed
5. Which nonstandard LaTeX packages are required for rendering the math markup in your questions

A full field-by-field reference is in [notes/configuration.md](notes/configuration.md); `config.example.yaml` is a commented, ready-to-copy starting point.

# Assessment templating

Assessments are templated using [docxtpl](https://docxtpl.readthedocs.io/en/latest/), which uses `jinja2`-style templating.

To generate a starter template, which is designed to mimic the look of LaTeX's `exam` class, run:

```bash
pl2docx-starter-template
```

The template can then be opened in Word and edited to (1) include instructor-provided prefaces (for example, exam instructions) and appendices (for example, equation sheets or other reference material), and (2) tweak the formatting used to render the assessment content.

See [notes/templating.md](notes/templating.md) for the full list of variables exposed to the template and, importantly, a list of confirmed "gotchas" that can silently break a template if you're not aware of them (docxtpl templates are somewhat fragile - for example, it's possible to accidentally break a template just by removing a linebreak between a control tag and its surroundings). If you run into templating problems, point Claude Code at this repo and ask for help - `CLAUDE.md` and `notes/templating.md` together give it what it needs to diagnose most template issues without additional context from you.

# Run into a problem or have a feature request?

Short answer: you'll probably need to fix it yourself - but Claude can help!

Longer answer: this project was designed specifically to address assignment printing needs for one of the author's courses at the University of Pittsburgh. Given the significant other demands on her time, this project will be maintained only to the extent necessary to (1) address bugs that she encounters in use or that arise from updates to the PrairieLearn platform, and (2) add support for currently unsupported elements if and when she adds questions containing those elements to her course's question bank.

If you want this utility to do something beyond its current capabilities, feel free to submit an Issue on GitHub if you think it's something that will be generally useful, but you'll probably need to implement the change yourself. If it helps, though, this entire project was put together (read: vibe-coded) using Claude Code, and the overall design and implementation architecture are documented in [`CLAUDE.md`](CLAUDE.md) and the [`notes/`](notes/) folder specifically so that a fresh Claude Code session, with no memory of building this project, can still pick it up effectively. If you point Claude Code at your local copy of this repo and clearly describe what you want to be able to do, it will probably be able to figure it out.
