"""Load and validate pl2docx's runtime configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Config:
    """Runtime configuration for driving a local PrairieLearn server.

    Parameters
    ----------
    base_url : str
        Root URL of the PrairieLearn server, with no trailing slash, e.g.
        "http://localhost:3000".
    course_short_name : str
        The target course's stable `short_name` (e.g. `"CHEM 0110"`), as
        configured in its `infoCourse.json`. Resolved to a numeric
        `course_id` at runtime (`pl2docx.pl_client.PLClient.resolve_course_id`)
        rather than stored as a numeric id directly, since a course's
        numeric id can change across restarts of an ephemeral-database PL
        server (see CLAUDE.md's Docker gotcha note).
    course_instance_short_name : str
        The target course instance's stable `short_name` (e.g.
        `"0110-Test"`), as configured in its `infoCourseInstance.json`.
        Resolved to a numeric `course_instance_id` at runtime for the same
        reason as `course_short_name`.
    assessment_tid : str
        The target assessment's stable `tid` — its directory name under
        `assessments/` in the course repo (e.g. `"pl2docx-phase1-test"`).
        Resolved to a numeric `assessment_id` at runtime for the same
        reason as `course_short_name`. Deliberately not matched by title,
        which is editable independent of `tid`.
    n_instances : int
        Number of instances to generate when running the fetch script. Ignored when
        `instance_ids` is set (non-empty) — see that field.
    instance_ids : list[str] or None
        Explicit instance identifiers, one instance generated per string, in order.
        When set (non-empty), this replaces `n_instances` entirely as the source of
        how many instances to generate. Each string becomes the output folder name
        (`output_dir/<instance_id>/...`, instead of the PL-assigned numeric
        `assessment_instance_id`) and the value of the `instance_ID` template
        variable exposed to the docx template (see `pl2docx.fetch`/
        `pl2docx.docx_builder.render_document`) — PL itself still mints its own
        numeric instance id under the hood regardless; this is a purely
        pl2docx-side label with no PL-side meaning. `None`/empty (the default)
        falls back to `n_instances` numbered instances, with output folders named
        by PL's numeric id (unchanged from before this field existed) and each
        instance's `instance_ID` template variable defaulting to `"Instance
        {n}"` (1-based).
    output_dir : pathlib.Path
        Directory under which fetched HTML for each instance is saved.
    template_path : pathlib.Path
        Path to the instructor-supplied docx template used when rendering.
        Existence isn't checked here — only when actually opened for
        rendering — matching how other paths in this config aren't
        pre-validated at load time.
    latex_packages : list[str]
        Extra LaTeX packages (names only, no `.sty` extension, e.g.
        `["mhchem"]`) to load in every rendered math snippet's preamble, on
        top of `pl2docx.latex_math`'s small built-in default set
        (`amsmath`/`amssymb`/`xcolor` — packages virtually every TeX install
        has). Deliberately *not* auto-detected from equation content (e.g.
        sniffing for a package-specific macro) — that doesn't generalize to
        arbitrary packages and still wouldn't guarantee the package is
        installed; see
        `pl2docx.latex_math.configure_extra_packages`, which validates these
        resolve via `kpsewhich` at startup. `[]` (the default) if absent —
        every math snippet still renders, just without whatever notation the
        missing package would have provided (e.g. mhchem's `\\ce{}` chemistry
        formulas fall back to placeholder text without `mhchem` declared here).
    restart_numbering_per_zone : bool
        Whether question numbering (`question.number` in the template context)
        restarts at 1 at the beginning of every zone, instead of running
        continuously across the whole document. `False` (the default) matches
        PL's own numbering convention and this project's original behavior.
    block_display_indent_inches : float
        Left indent applied to every widget whose content renders as its own
        block (a fresh paragraph or table, not sharing a line with prompt
        text) - `pl-multiple-choice`/`pl-checkbox`/fill-in-type widgets with
        `display: block` (whether that's an explicit config override or the
        auto-detected default for a non-inline, non-dropdown selector), plus
        `pl-matching` and `pl-order-blocks` (always block-shaped - a table by
        default, or two paragraphs for `pl-order-blocks`' `horizontal`
        layout), regardless of `display` since neither kind exposes that
        setting. A single global value, not configurable per element kind -
        see `pl2docx.element_renderer`'s `DEFAULT_BLOCK_DISPLAY_INDENT_INCHES`
        for exactly which widgets/paragraphs this applies to (this field's own
        default, `0.125`, is intentionally kept in sync with that constant by
        hand rather than imported from it - `pl2docx.element_renderer` pulls
        in `python-docx`/`docxtpl`/`playwright`, deliberately heavier
        dependencies this module stays free of). `0` disables indentation
        entirely.

    Notes
    -----
    Does not validate that the referenced course/course-instance/assessment
    actually exist on the target server — that's `PLClient.resolve_*`'s job,
    called once per `fetch_n_instances` run against the live server.
    """

    base_url: str
    course_short_name: str
    course_instance_short_name: str
    assessment_tid: str
    n_instances: int
    output_dir: Path
    template_path: Path
    latex_packages: list[str] = field(default_factory=list)
    instance_ids: list[str] | None = None
    restart_numbering_per_zone: bool = False
    block_display_indent_inches: float = 0.125


def load_config(path: str | Path) -> Config:
    """Load a pl2docx configuration from a YAML file.

    Parameters
    ----------
    path : str or pathlib.Path
        Path to a YAML config file (see `config.example.yaml` for the
        expected keys).

    Returns
    -------
    Config
        The parsed, validated configuration.

    Raises
    ------
    FileNotFoundError
        If `path` does not exist.
    ValueError
        If a required key is missing or still set to its `null` placeholder
        value (i.e. `config.example.yaml` was copied but not filled in).
    """
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    required = ("base_url", "course_short_name", "course_instance_short_name", "assessment_tid")
    missing = [key for key in required if raw.get(key) is None]
    if missing:
        raise ValueError(
            f"Config at {path} is missing required value(s): {', '.join(missing)}. "
            "Copy config.example.yaml to config.yaml and fill these in."
        )

    return Config(
        base_url=str(raw["base_url"]).rstrip("/"),
        course_short_name=str(raw["course_short_name"]),
        course_instance_short_name=str(raw["course_instance_short_name"]),
        assessment_tid=str(raw["assessment_tid"]),
        n_instances=int(raw.get("n_instances", 1)),
        output_dir=Path(raw.get("output_dir", "output")),
        template_path=Path(raw.get("template_path", "template.docx")),
        latex_packages=[str(pkg) for pkg in (raw.get("latex-packages") or [])],
        instance_ids=[str(i) for i in raw["instance_ids"]] if raw.get("instance_ids") else None,
        restart_numbering_per_zone=bool(raw.get("restart_numbering_per_zone", False)),
        block_display_indent_inches=float(raw.get("block_display_indent_inches", 0.125)),
    )
