"""CLI entry point: render one Phase-1-fetched instance's HTML into docx.

Usage
-----
    python -m pl2docx.render <instance_dir> [template_path] [config_path]

`instance_dir` is one `output/<assessment_instance_id>/` folder produced by
`pl2docx.fetch`, containing `blank/*.html`, `key/*.html`, and `structure.json`.
`template_path` defaults to `config_path`'s `template_path` if omitted.
`config_path` defaults to `"config.yaml"` in the current working directory.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pl2docx.config import load_config
from pl2docx.docx_builder import ZoneQuestions, render_document
from pl2docx.element_config import (
    ElementConfig,
    additional_fill_in_class_prefixes,
    additional_fill_in_tags,
    load_element_config,
)
from pl2docx.element_renderer import DEFAULT_BLOCK_DISPLAY_INDENT_INCHES
from pl2docx.html_parser import ParsedQuestion, parse_instance_question_html
from pl2docx.latex_math import configure_extra_packages


def _load_question(
    html_dir: Path,
    instance_question_id: int,
    extra_fill_in_tags: list[str],
    extra_fill_in_class_prefixes: dict[str, str],
    is_answer_key: bool,
) -> ParsedQuestion:
    html = (html_dir / f"{instance_question_id}.html").read_text(encoding="utf-8")
    return parse_instance_question_html(
        html,
        additional_fill_in_tags=extra_fill_in_tags,
        additional_fill_in_class_prefixes=extra_fill_in_class_prefixes,
        is_answer_key=is_answer_key,
    )


def _build_zones(
    html_dir: Path,
    zones_structure: list[dict],
    element_config: ElementConfig | None,
    restart_numbering_per_zone: bool = False,
    is_answer_key: bool = False,
) -> list[ZoneQuestions]:
    """Pair each zone's questions with their 1-based question number.

    Parameters
    ----------
    restart_numbering_per_zone : bool
        Whether numbering resets to 1 at the start of every zone. `False`
        (the default) numbers continuously across the whole document,
        matching PL's own numbering convention and this project's original
        behavior.
    is_answer_key : bool
        Whether `html_dir` holds answer-key (rather than blank) HTML —
        threaded straight through to `parse_instance_question_html`'s own
        `is_answer_key` parameter (see there for what it controls).
    """
    extra_fill_in_tags = additional_fill_in_tags(element_config) if element_config else []
    extra_fill_in_class_prefixes = (
        additional_fill_in_class_prefixes(element_config) if element_config else {}
    )
    zones: list[ZoneQuestions] = []
    number = 1
    for zone in zones_structure:
        if restart_numbering_per_zone:
            number = 1
        questions: list[tuple[ParsedQuestion, int]] = []
        for iq_id in zone["instance_question_ids"]:
            question = _load_question(
                html_dir, iq_id, extra_fill_in_tags, extra_fill_in_class_prefixes, is_answer_key
            )
            questions.append((question, number))
            number += 1
        zones.append({"title": zone["title"], "questions": questions})
    return zones


def render_instance(
    instance_dir: Path,
    template_path: Path,
    element_config: ElementConfig | None = None,
    restart_numbering_per_zone: bool = False,
    block_display_indent_inches: float = DEFAULT_BLOCK_DISPLAY_INDENT_INCHES,
) -> tuple[Path, Path]:
    """Render an instance's fetched HTML into blank + key docx files.

    Parameters
    ----------
    instance_dir : pathlib.Path
        A directory produced by `pl2docx.fetch`, containing `blank/`, `key/`
        subfolders of `instance_question` HTML files named
        `<instance_question_id>.html`, and a `structure.json`
        (`{"instance_id": ..., "zones": [...]}` — zone titles, question
        order/ids, and the `instance_ID` template value; see
        `pl2docx.fetch._write_structure`).
    template_path : pathlib.Path
        Docx template — see `pl2docx.docx_builder.render_document` for the
        context shape it must consume, and `pl2docx.starter_template` for a
        generated example.
    element_config : pl2docx.element_config.ElementConfig or None
        Instructor-configured element/question-level formatting preferences.
        `None` (the default) applies built-in defaults for every widget kind.
    restart_numbering_per_zone : bool
        Passed straight through to `_build_zones` — see its docstring.
    block_display_indent_inches : float
        Passed straight through to `pl2docx.docx_builder.render_document` for
        both the blank and key renders — see
        `pl2docx.element_renderer.build_question_context`'s docstring /
        `pl2docx.config.Config.block_display_indent_inches`.

    Returns
    -------
    tuple[pathlib.Path, pathlib.Path]
        Paths to the generated `(blank_docx, key_docx)`, written alongside
        the source HTML inside `instance_dir`.
    """
    structure = json.loads((instance_dir / "structure.json").read_text(encoding="utf-8"))
    # Graceful fallback for a structure.json written before this field existed
    # (a schema change to a gitignored, regenerable runtime file — re-fetching
    # is the real fix, this just avoids a hard crash on stale output/).
    if isinstance(structure, dict):
        instance_id_display = structure.get("instance_id") or instance_dir.name
        zones_structure = structure["zones"]
    else:
        instance_id_display = instance_dir.name
        zones_structure = structure

    instance_id = instance_dir.name
    blank_path = instance_dir / f"{instance_id}_blank.docx"
    key_path = instance_dir / f"{instance_id}_key.docx"

    render_document(
        template_path,
        _build_zones(
            instance_dir / "blank",
            zones_structure,
            element_config,
            restart_numbering_per_zone,
            is_answer_key=False,
        ),
        is_answer_key=False,
        output_path=blank_path,
        element_config=element_config,
        image_base_dir=instance_dir / "blank",
        instance_id=instance_id_display,
        block_display_indent_inches=block_display_indent_inches,
    )
    render_document(
        template_path,
        _build_zones(
            instance_dir / "key",
            zones_structure,
            element_config,
            restart_numbering_per_zone,
            is_answer_key=True,
        ),
        is_answer_key=True,
        output_path=key_path,
        element_config=element_config,
        image_base_dir=instance_dir / "key",
        instance_id=instance_id_display,
        block_display_indent_inches=block_display_indent_inches,
    )
    return blank_path, key_path


def load_render_settings(config_path: Path) -> tuple[bool, float]:
    """Load config.yaml-derived document-formatting settings for rendering.

    Parameters
    ----------
    config_path : pathlib.Path
        Path to a pl2docx `config.yaml`. Need not exist — see Notes.

    Returns
    -------
    tuple[bool, float]
        `(restart_numbering_per_zone, block_display_indent_inches)`.

    Notes
    -----
    `pl2docx-render` can render a single already-fetched instance without any
    `config.yaml` present (e.g. ad hoc re-rendering with an explicit
    `template_path`) — a missing `config_path` falls back to both settings'
    built-in defaults rather than raising, matching this command's original
    behavior. Also registers `config.yaml`'s `latex-packages` (see
    `pl2docx.latex_math.configure_extra_packages`) as a side effect when the
    file exists. Reused by `pl2docx.run` so both commands derive these
    settings identically. Element-formatting preferences (`ElementConfig`)
    are a separate concern, loaded independently via
    `pl2docx.element_config.load_element_config`.
    """
    if not config_path.exists():
        return False, DEFAULT_BLOCK_DISPLAY_INDENT_INCHES
    run_config = load_config(config_path)
    configure_extra_packages(run_config.latex_packages)
    return run_config.restart_numbering_per_zone, run_config.block_display_indent_inches


def main() -> None:
    if len(sys.argv) not in (2, 3, 4):
        print("Usage: python -m pl2docx.render <instance_dir> [template_path] [config_path]")
        sys.exit(1)
    instance_dir = Path(sys.argv[1])
    config_path = Path(sys.argv[3]) if len(sys.argv) == 4 else Path("config.yaml")
    template_path = Path(sys.argv[2]) if len(sys.argv) >= 3 else load_config(config_path).template_path
    element_config = load_element_config(config_path)
    restart_numbering_per_zone, block_display_indent_inches = load_render_settings(config_path)
    blank_path, key_path = render_instance(
        instance_dir, template_path, element_config, restart_numbering_per_zone, block_display_indent_inches
    )
    print(f"Wrote {blank_path}")
    print(f"Wrote {key_path}")


if __name__ == "__main__":
    main()
