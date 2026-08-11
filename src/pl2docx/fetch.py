"""CLI entry point: fetch N distinct blank/answer-key HTML instance sets.

Usage
-----
    python -m pl2docx.fetch [config_path]

`config_path` defaults to "config.yaml" in the current working directory.
"""

from __future__ import annotations

import sys
from pathlib import Path

from pl2docx.config import Config, load_config
from pl2docx.pl_client import PLClient


def _write_html(output_dir: Path, assessment_instance_id: int, subdir: str, pages: dict[int, str]) -> None:
    instance_dir = output_dir / str(assessment_instance_id) / subdir
    instance_dir.mkdir(parents=True, exist_ok=True)
    for instance_question_id, html in pages.items():
        (instance_dir / f"{instance_question_id}.html").write_text(html, encoding="utf-8")


def fetch_n_instances(config: Config) -> list[int]:
    """Generate `config.n_instances` distinct instances and fetch blank + key HTML.

    Parameters
    ----------
    config : Config
        Runtime configuration identifying the target server, course
        instance, and assessment.

    Returns
    -------
    list[int]
        The `assessment_instance_id` of each generated instance, in
        generation order.

    Notes
    -----
    Each instance is fully processed (blank fetch, close, key fetch) before
    the next one is created, so instances never overlap in "open" state.
    Relies on the authenticated user already having course role Previewer or
    above (true for any instructor account) so PL's "Student view without
    access restrictions" bypass applies automatically; see `PLClient`'s
    module docstring for why this tool deliberately does not use the
    "view as student" role-override mechanism.
    """
    client = PLClient(config.base_url)

    instance_ids: list[int] = []
    for i in range(config.n_instances):
        assessment_instance_id = client.create_or_regenerate_instance(
            config.course_instance_id, config.assessment_id
        )
        print(f"[{i + 1}/{config.n_instances}] created assessment_instance {assessment_instance_id}")

        instance_question_ids = client.list_instance_questions(
            config.course_instance_id, assessment_instance_id
        )
        blank_html = client.fetch_instance_questions(config.course_instance_id, instance_question_ids)
        _write_html(config.output_dir, assessment_instance_id, "blank", blank_html)

        client.close_instance(config.course_instance_id, assessment_instance_id)
        key_html = client.fetch_instance_questions(config.course_instance_id, instance_question_ids)
        _write_html(config.output_dir, assessment_instance_id, "key", key_html)

        instance_ids.append(assessment_instance_id)

    return instance_ids


def main() -> None:
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("config.yaml")
    config = load_config(config_path)
    instance_ids = fetch_n_instances(config)
    print(f"Done. Wrote {len(instance_ids)} instance(s) to {config.output_dir}/")


if __name__ == "__main__":
    main()
