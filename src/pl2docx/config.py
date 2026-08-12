"""Load and validate pl2docx's runtime configuration."""

from __future__ import annotations

from dataclasses import dataclass
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
        Number of instances to generate when running the fetch script.
    output_dir : pathlib.Path
        Directory under which fetched HTML for each instance is saved.
    template_path : pathlib.Path
        Path to the instructor-supplied docx template used when rendering.
        Existence isn't checked here — only when actually opened for
        rendering — matching how other paths in this config aren't
        pre-validated at load time.

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
    )
