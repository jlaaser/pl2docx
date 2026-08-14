pl2docx is a utility for creating printable assignments from [PrairieLearn](https://www.prairielearn.com/) question banks.
Assessments are fetched from a live PrairieLearn server (typically running locally via Docker - see below) and rendered onto an instructor-provided .docx template, providing assessments that can either be printed as-is or fine-tuned by the instructor before printing.

# Supported elements

pl2docx currently supports the following core PrairieLearn input and display elements:
- {{ LIST SUPPORTED ELEMENTS }}

pl2docx currently explicitly *does not* support the following core PrairieLearn input and display elements:
- {{ LIST UNSUPPORTED CORE ELEMENTS }}

For course-specific elements (e.g. those defined in your course's `elements` folder), pl2docx offers a config file-based approach for adding support for elements that fit into any of the following three categories:
- "selector"-type elements analogous to pl-multiple-choice and pl-checkboxes
- "fill-in"-type elements analogous to pl-string-input, pl-integer-input, etc.
- "interactive" elements that render to a <canvas> object on the page

For information about how to add custom elements of each type, see the "Configuration Options" section, below.

Unsupported elements (either those in the PrairieLearn core or custom course-specific elements) will {{ BRIEFLY DESCRIBE WHAT HAPPENS IF AN ELEMENT IS UNSUPPORTED }}.


# Installation

Before using pl2docx, you will need to:

1. Set up a local instance of PrairieLearn running under Docker.
	1. See [PrairieLearn's doc page on installing and running locally](https://docs.prairielearn.com/installing/) for instructions
2. Make sure your machine has a working LaTeX install and is up-to-date with any specialty packages (such as `mhchem` or `siunitx`) used in your course's LaTeX markup
3. 


# Running pl2docx 

To generate a printable assignment using pl2docx, do the following:
1. Set up a PrairieLearn assessment defining the assignment for which you wish to generate printable copies.  Set this assessment up exactly as you would if you were going to administer it via PrairieLearn - zones, questions, question alternatives, point values, the works.
2. If the assessment was set up on the remote PrairieLearn servers, make sure to pull the up-to-date copy of your course repo to your local machine before proceeding.
3. Start a local instance of the PrairieLearn server, and add its address to your config.yaml file
4. Update your config.yaml file with appropriate IDs for your course, course instance, assessment, and any other desired configuratoin parameters
{{ FILL IN THE REST OF THE ABOVE LIST }}


# Configuration options 

As noted above, pl2docx offers a number of configuration options for controlling the formatting of the .docx output and rendering of specific elements.

Details of these options are provided in {{ WHERE ARE THESE FOUND? }}, but broadly, the config file offers the ability for you to configure all of the following:
1. How/where to access the assessment on PrairieLearn 
2. How many print-ready variants of the assessment should be generated
3. Global preferences for how different classes of PrairieLearn elements should be rendered into the .docx output 
4. How course-specific or non-core input elements should be processed
5. Which nonstandard LaTeX packages are required for rendering the math markup your questions

# Assessment templating

Assessments are templated using [docxtpl](https://docxtpl.readthedocs.io/en/latest/), which uses `jinja2`-style templating.

To render a starter template, which is designed to mimic the look of LaTeX's `exam` class, run 

{{ COMMAND }}

The template can then be opened in Word and edited to (1) include instructor- provided prefaces (for example, exam instructions) and appendices (for example, equation sheets or other reference material), and (2) tweak the formatting used to render the assessment content.

The following parameters are exposed in the context passed to docxtpl and can be used in the instructor's template:

{{ LIST OF PARAMETERS }}

We note that, in testing, we have found these templates to be somewhat "fragile", e.g. it is possible to accidentally make the template fail even just when removing linebreaks between control tags and their surroundings.  Common failure points are documented in the default CLAUDE.md, so if you run into templating problems, we recommend pointing Claude Code to this project and asking it for help fixing your template.

# Run into a problem or have a feature request?

Short answer: you'll probably need to fix it yourself - but Claude can help!

Longer answer: this project was designed specifically to address assignment printing needs for one of the author's courses at the University of Pittsburgh.  Given the significant other demands on her time, this project will be maintained only to the extent necessary to (1) address bugs that she encounters in use or that arise from updates to the PrairieLearn platform, and (2) add support for currently un-supported elements if and when she adds questions containing those elements to her course's question bank.

If you want this utility to be able to do something beyond its current capabilities, feel free to submit an Issue on github if you think it is something that will be generally useful, but you'll probably need to implement the change yourself.  HOWEVER!  This entire project was put together (ok, vibe-coded) using Claude Code, and the overall design and implementation architecture are pretty thoroughly documented in the planning_notes folder.  If you point Claude Code to your local copy of this repo and clearly specify what you want to be able to do, Claude will probably be able to figure it out.

