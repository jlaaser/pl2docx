"""CLI entry point: fetch N distinct blank/answer-key HTML instance sets.

Usage
-----
    python -m pl2docx.fetch [config_path]

`config_path` defaults to "config.yaml" in the current working directory.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from pl2docx.canvas_capture import capture_interactive_elements, close_browser
from pl2docx.config import Config, load_config
from pl2docx.element_config import InteractivePreferences, additional_interactive_tags, load_element_config
from pl2docx.pl_client import PLClient, ZoneGroup


def _download_images(client: PLClient, files_dir: Path, instance_question_id: int, html: str) -> str:
    """Download same-origin `<img>`s referenced in `html`, rewriting their `src` to local paths.

    Parameters
    ----------
    client : PLClient
        Used to fetch each image with the same authenticated session the
        page itself was fetched with.
    files_dir : pathlib.Path
        Directory (created if needed) to save downloaded images into,
        alongside the HTML that references them.
    instance_question_id : int
        The page's `instance_question_id`, used as a filename prefix so
        images from different questions never collide.
    html : str
        Raw page HTML, as fetched.

    Returns
    -------
    str
        `html` with same-origin `<img src>` values rewritten to
        `files/<local filename>`, relative to the HTML file's own location.

    Notes
    -----
    Only downloads same-origin (root-relative or `client.base_url`-prefixed)
    image URLs — the ones confirmed to require this session's auth and, for
    server-generated images, to not be guaranteed stable at a fixed URL
    long-term (variant-scoped `generatedFilesQuestion` paths). Externally
    hosted images are left as-is.
    """
    soup = BeautifulSoup(html, "html.parser")
    images = [img for img in soup.find_all("img", src=True) if _is_same_origin(client.base_url, img["src"])]
    if not images:
        return html

    files_dir.mkdir(parents=True, exist_ok=True)
    for index, img in enumerate(images):
        src = img["src"]
        absolute_url = urljoin(client.base_url, src)
        original_name = Path(src.split("?", 1)[0]).name or f"image_{index}"
        local_name = f"{instance_question_id}_{index}_{original_name}"
        (files_dir / local_name).write_bytes(client.fetch_binary(absolute_url))
        img["src"] = f"files/{local_name}"
    return str(soup)


def _is_same_origin(base_url: str, src: str) -> bool:
    return src.startswith("/") or src.startswith(base_url)


def _write_html(
    client: PLClient,
    output_dir: Path,
    course_instance_id: int,
    assessment_instance_id: int,
    subdir: str,
    pages: dict[int, str],
    interactive_tags: dict[str, InteractivePreferences],
) -> None:
    instance_dir = output_dir / str(assessment_instance_id) / subdir
    instance_dir.mkdir(parents=True, exist_ok=True)
    files_dir = instance_dir / "files"
    for instance_question_id, html in pages.items():
        if interactive_tags:
            page_url = client.instance_question_url(course_instance_id, instance_question_id)
            html = capture_interactive_elements(
                client, html, page_url, files_dir, instance_question_id, interactive_tags
            )
        html = _download_images(client, files_dir, instance_question_id, html)
        (instance_dir / f"{instance_question_id}.html").write_text(html, encoding="utf-8")


def _write_structure(output_dir: Path, assessment_instance_id: int, zones: list[ZoneGroup]) -> None:
    instance_dir = output_dir / str(assessment_instance_id)
    instance_dir.mkdir(parents=True, exist_ok=True)
    (instance_dir / "structure.json").write_text(
        json.dumps([asdict(zone) for zone in zones], indent=2), encoding="utf-8"
    )


def fetch_n_instances(
    config: Config, interactive_tags: dict[str, InteractivePreferences] | None = None
) -> list[int]:
    """Generate `config.n_instances` distinct instances and fetch blank + key HTML.

    Parameters
    ----------
    config : Config
        Runtime configuration identifying the target server, course
        instance, and assessment.
    interactive_tags : dict[str, InteractivePreferences] or None
        Canvas-based interactive elements to screenshot and flatten to plain
        images at fetch time (Phase 5 subphase 2) - typically
        `pl2docx.element_config.additional_interactive_tags`'s return value.
        `None`/empty means no interactive-element capture is attempted (the
        pre-subphase-2 default).

    Returns
    -------
    list[int]
        The `assessment_instance_id` of each generated instance, in
        generation order.

    Notes
    -----
    Resolves `config`'s stable `course_short_name`/`course_instance_short_name`/
    `assessment_tid` identifiers to the server's *current* numeric ids once,
    up front (`PLClient.resolve_course_id`/`resolve_course_instance_id`/
    `resolve_assessment_id`) — the target PL server runs in an ephemeral
    Docker container with no persistent database, so numeric ids can change
    across restarts; resolving by stable identifier avoids `config.yaml`
    silently going stale (see CLAUDE.md's Docker gotcha note).

    Each instance is fully processed (blank fetch, close, key fetch) before
    the next one is created, so instances never overlap in "open" state.
    Relies on the authenticated user already having course role Previewer or
    above (true for any instructor account) so PL's "Student view without
    access restrictions" bypass applies automatically; see `PLClient`'s
    module docstring for why this tool deliberately does not use the
    "view as student" role-override mechanism.

    Also saves, per instance: any same-origin images referenced in the
    fetched HTML (under `<instance>/{blank,key}/files/`, with the HTML's
    `<img src>` rewritten to match), and the assessment's zone/question
    structure (`<instance>/structure.json`) — both captured now so later
    rendering work doesn't need a live server or a second fetch.
    """
    client = PLClient(config.base_url)
    interactive_tags = interactive_tags or {}

    course_id = client.resolve_course_id(config.course_short_name)
    course_instance_id = client.resolve_course_instance_id(course_id, config.course_instance_short_name)
    assessment_id = client.resolve_assessment_id(course_instance_id, config.assessment_tid)

    instance_ids: list[int] = []
    for i in range(config.n_instances):
        assessment_instance_id = client.create_or_regenerate_instance(course_instance_id, assessment_id)
        print(f"[{i + 1}/{config.n_instances}] created assessment_instance {assessment_instance_id}")

        zones = client.list_instance_questions(course_instance_id, assessment_instance_id)
        _write_structure(config.output_dir, assessment_instance_id, zones)
        instance_question_ids = [iq_id for zone in zones for iq_id in zone.instance_question_ids]

        blank_html = client.fetch_instance_questions(course_instance_id, instance_question_ids)
        _write_html(
            client, config.output_dir, course_instance_id, assessment_instance_id, "blank",
            blank_html, interactive_tags,
        )

        client.close_instance(course_instance_id, assessment_instance_id)
        key_html = client.fetch_instance_questions(course_instance_id, instance_question_ids)
        _write_html(
            client, config.output_dir, course_instance_id, assessment_instance_id, "key",
            key_html, interactive_tags,
        )

        instance_ids.append(assessment_instance_id)

    return instance_ids


def main() -> None:
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("config.yaml")
    config = load_config(config_path)
    element_config = load_element_config(config_path)
    interactive_tags = additional_interactive_tags(element_config)
    try:
        instance_ids = fetch_n_instances(config, interactive_tags)
    finally:
        if interactive_tags:
            close_browser()
    print(f"Done. Wrote {len(instance_ids)} instance(s) to {config.output_dir}/")


if __name__ == "__main__":
    main()
