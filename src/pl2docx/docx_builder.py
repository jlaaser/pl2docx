"""Assemble a zones/questions Jinja context and render it via an instructor template.

Phase 3 architecture: document layout (the zone/question loop, headers, named
styles) lives in the instructor's docx template — see `pl2docx.starter_template`
for a generated starting point. This module's job is just to open that template,
build the `zones` context it expects (delegating each question's actual content —
the three `Subdoc`s — to `pl2docx.element_renderer`), render, and save. It does no
per-element formatting itself; that split between "layout" and "content" is
deliberate (see CLAUDE.md's Architecture section).
"""

from __future__ import annotations

from pathlib import Path
from typing import TypedDict

from docx.opc.exceptions import PackageNotFoundError
from docxtpl import DocxTemplate

from pl2docx.element_config import ElementConfig
from pl2docx.element_renderer import build_question_context
from pl2docx.html_parser import ParsedQuestion


class ZoneQuestions(TypedDict):
    """One zone's title and its `(ParsedQuestion, document-wide number)` pairs."""

    title: str | None
    questions: list[tuple[ParsedQuestion, int]]


class TemplateUnreadableError(RuntimeError):
    """Raised when the template docx can't be opened as a valid docx package.

    Usually means the file is currently open in Word (which can make it
    unreadable as a valid zip to another process at that instant) or is
    still mid-sync with a cloud-storage provider (e.g. OneDrive) right
    after being saved — not a corrupted template. Close the file in Word
    and let any sync finish, then retry.
    """


def render_document(
    template_path: Path,
    zones: list[ZoneQuestions],
    is_answer_key: bool,
    output_path: Path,
    element_config: ElementConfig | None = None,
) -> None:
    """Render `zones` into a docx built from `template_path`.

    Parameters
    ----------
    template_path : pathlib.Path
        Instructor-supplied docx template. Must define, at minimum, a
        `{% for zone in zones %}` / `{% for question in zone.questions %}`
        loop consuming the context shape this function builds (see
        `pl2docx.element_renderer.build_question_context` for the
        per-question fields), plus a reference to the top-level
        `is_answer_key` variable if the template wants to branch on it
        (e.g. to choose between a question's `answer_contents` and
        `answer_space`). See `pl2docx.starter_template` for a generated
        example that does exactly this.
    zones : list[ZoneQuestions]
        Zones in document order, each holding its questions paired with
        their already-assigned document-wide `number`.
    is_answer_key : bool
        Whether this render pass is the answer key or the blank copy.
        Exposed to the template as a top-level `is_answer_key` variable;
        this function doesn't otherwise interpret the flag — whether/how a
        question's answer data actually differs between passes is
        entirely a function of which `ParsedQuestion`s were passed in
        (blank-parse vs. key-parse), same as Phase 2.
    output_path : pathlib.Path
        Where to save the resulting docx. Parent directories are created if
        needed.
    element_config : pl2docx.element_config.ElementConfig or None
        Instructor-configured element/question-level formatting preferences
        (see `pl2docx.element_config`). `None` (the default) applies
        built-in defaults for every widget kind, same as an empty
        `ElementConfig`.

    Raises
    ------
    TemplateUnreadableError
        If `template_path` can't be opened as a valid docx package — most
        often because it's currently open in Word, or a cloud-storage sync
        (e.g. OneDrive) hasn't finished writing it yet after a save.
    """
    if element_config is None:
        element_config = ElementConfig(preferences={}, behavior_class={})

    tpl = DocxTemplate(str(template_path))
    try:
        tpl.init_docx()
    except PackageNotFoundError as exc:
        raise TemplateUnreadableError(
            f"Could not open template at {template_path} as a docx file. This "
            "usually means it's currently open in Word, or a cloud-sync tool "
            "(e.g. OneDrive) hasn't finished saving it yet — close it in Word "
            "and wait a moment for syncing to finish, then try again."
        ) from exc

    zones_context = [
        {
            "title": zone["title"],
            "questions": [
                build_question_context(tpl, question, number, element_config)
                for question, number in zone["questions"]
            ],
        }
        for zone in zones
    ]

    tpl.render({"zones": zones_context, "is_answer_key": is_answer_key})
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tpl.save(str(output_path))
