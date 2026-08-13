"""CLI entry point: render one Phase-1-fetched instance's HTML into docx.

Usage
-----
    python -m pl2docx.render <instance_dir> [template_path]

`instance_dir` is one `output/<assessment_instance_id>/` folder produced by
`pl2docx.fetch`, containing `blank/*.html`, `key/*.html`, and `structure.json`.
`template_path` defaults to `config.yaml`'s `template_path` if omitted (a
`config.yaml` must exist in the current working directory in that case).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pl2docx.config import load_config
from pl2docx.docx_builder import ZoneQuestions, render_document
from pl2docx.element_config import ElementConfig, additional_fill_in_tags, load_element_config
from pl2docx.html_parser import ParsedQuestion, parse_instance_question_html
from pl2docx.latex_math import configure_extra_packages


def _load_question(
    html_dir: Path, instance_question_id: int, extra_fill_in_tags: list[str]
) -> ParsedQuestion:
    html = (html_dir / f"{instance_question_id}.html").read_text(encoding="utf-8")
    return parse_instance_question_html(html, additional_fill_in_tags=extra_fill_in_tags)


def _build_zones(
    html_dir: Path, structure: list[dict], element_config: ElementConfig | None
) -> list[ZoneQuestions]:
    """Pair each zone's questions with their document-wide 1-based number.

    Numbering is continuous across the whole document (not restarted per
    zone) — matching PL's own numbering convention seen in fetched content,
    and the simplest behavior consistent with this phase's scope. Per-zone
    restart is a plausible future config option, not implemented here.
    """
    extra_fill_in_tags = additional_fill_in_tags(element_config) if element_config else []
    zones: list[ZoneQuestions] = []
    number = 1
    for zone in structure:
        questions: list[tuple[ParsedQuestion, int]] = []
        for iq_id in zone["instance_question_ids"]:
            questions.append((_load_question(html_dir, iq_id, extra_fill_in_tags), number))
            number += 1
        zones.append({"title": zone["title"], "questions": questions})
    return zones


def render_instance(
    instance_dir: Path, template_path: Path, element_config: ElementConfig | None = None
) -> tuple[Path, Path]:
    """Render an instance's fetched HTML into blank + key docx files.

    Parameters
    ----------
    instance_dir : pathlib.Path
        A directory produced by `pl2docx.fetch`, containing `blank/`, `key/`
        subfolders of `instance_question` HTML files named
        `<instance_question_id>.html`, and a `structure.json` (zone titles +
        question order/ids).
    template_path : pathlib.Path
        Docx template — see `pl2docx.docx_builder.render_document` for the
        context shape it must consume, and `pl2docx.starter_template` for a
        generated example.
    element_config : pl2docx.element_config.ElementConfig or None
        Instructor-configured element/question-level formatting preferences.
        `None` (the default) applies built-in defaults for every widget kind.

    Returns
    -------
    tuple[pathlib.Path, pathlib.Path]
        Paths to the generated `(blank_docx, key_docx)`, written alongside
        the source HTML inside `instance_dir`.
    """
    structure = json.loads((instance_dir / "structure.json").read_text(encoding="utf-8"))

    instance_id = instance_dir.name
    blank_path = instance_dir / f"{instance_id}_blank.docx"
    key_path = instance_dir / f"{instance_id}_key.docx"

    render_document(
        template_path,
        _build_zones(instance_dir / "blank", structure, element_config),
        is_answer_key=False,
        output_path=blank_path,
        element_config=element_config,
        image_base_dir=instance_dir / "blank",
    )
    render_document(
        template_path,
        _build_zones(instance_dir / "key", structure, element_config),
        is_answer_key=True,
        output_path=key_path,
        element_config=element_config,
        image_base_dir=instance_dir / "key",
    )
    return blank_path, key_path


def main() -> None:
    if len(sys.argv) not in (2, 3):
        print("Usage: python -m pl2docx.render <instance_dir> [template_path]")
        sys.exit(1)
    instance_dir = Path(sys.argv[1])
    if len(sys.argv) == 3:
        template_path = Path(sys.argv[2])
    else:
        template_path = load_config("config.yaml").template_path
    element_config = load_element_config("config.yaml")
    config_path = Path("config.yaml")
    if config_path.exists():
        configure_extra_packages(load_config(config_path).latex_packages)
    blank_path, key_path = render_instance(instance_dir, template_path, element_config)
    print(f"Wrote {blank_path}")
    print(f"Wrote {key_path}")


if __name__ == "__main__":
    main()
