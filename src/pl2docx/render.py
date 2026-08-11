"""CLI entry point: render one Phase-1-fetched instance's HTML into docx.

Usage
-----
    python -m pl2docx.render <instance_dir> <template_path>

`instance_dir` is one `output/<assessment_instance_id>/` folder produced by
`pl2docx.fetch`, containing `blank/*.html` and `key/*.html`.
"""

from __future__ import annotations

import sys
from pathlib import Path

from pl2docx.docx_builder import build_document
from pl2docx.html_parser import ParsedQuestion, parse_instance_question_html


def _load_questions(html_dir: Path) -> list[ParsedQuestion]:
    files = sorted(html_dir.glob("*.html"), key=lambda p: int(p.stem))
    return [parse_instance_question_html(f.read_text(encoding="utf-8")) for f in files]


def render_instance(instance_dir: Path, template_path: Path) -> tuple[Path, Path]:
    """Render an instance's fetched HTML into blank + key docx files.

    Parameters
    ----------
    instance_dir : pathlib.Path
        A directory produced by `pl2docx.fetch`, containing `blank/` and
        `key/` subfolders of `instance_question` HTML files named
        `<instance_question_id>.html`.
    template_path : pathlib.Path
        Docx template with a `{{p content }}` placeholder.

    Returns
    -------
    tuple[pathlib.Path, pathlib.Path]
        Paths to the generated `(blank_docx, key_docx)`, written alongside
        the source HTML inside `instance_dir`.
    """
    blank_questions = _load_questions(instance_dir / "blank")
    key_questions = _load_questions(instance_dir / "key")

    instance_id = instance_dir.name
    blank_path = instance_dir / f"{instance_id}_blank.docx"
    key_path = instance_dir / f"{instance_id}_key.docx"

    build_document(template_path, blank_questions, blank_path)
    build_document(template_path, key_questions, key_path)
    return blank_path, key_path


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: python -m pl2docx.render <instance_dir> <template_path>")
        sys.exit(1)
    instance_dir = Path(sys.argv[1])
    template_path = Path(sys.argv[2])
    blank_path, key_path = render_instance(instance_dir, template_path)
    print(f"Wrote {blank_path}")
    print(f"Wrote {key_path}")


if __name__ == "__main__":
    main()
