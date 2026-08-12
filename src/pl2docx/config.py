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
    course_instance_id : int
        Numeric PrairieLearn course_instance ID to operate within.
    assessment_id : int
        Numeric PrairieLearn assessment ID to generate instances for.
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
    Assumes all fields are already resolved to concrete values; does not
    validate that the referenced course instance or assessment actually
    exist on the target server (that only becomes apparent when the first
    request fails).
    """

    base_url: str
    course_instance_id: int
    assessment_id: int
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

    required = ("base_url", "course_instance_id", "assessment_id")
    missing = [key for key in required if raw.get(key) is None]
    if missing:
        raise ValueError(
            f"Config at {path} is missing required value(s): {', '.join(missing)}. "
            "Copy config.example.yaml to config.yaml and fill these in."
        )

    return Config(
        base_url=str(raw["base_url"]).rstrip("/"),
        course_instance_id=int(raw["course_instance_id"]),
        assessment_id=int(raw["assessment_id"]),
        n_instances=int(raw.get("n_instances", 1)),
        output_dir=Path(raw.get("output_dir", "output")),
        template_path=Path(raw.get("template_path", "template.docx")),
    )
