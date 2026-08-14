"""CLI entry point: fetch and render every instance in one command.

Usage
-----
    python -m pl2docx.run [config_path]

`config_path` defaults to "config.yaml" in the current working directory.
Combines `pl2docx.fetch.fetch_n_instances` and `pl2docx.render.render_instance`
so a full assessment (N instances, each as a blank + key docx) is produced
from one invocation instead of running `pl2docx-fetch` followed by one
`pl2docx-render` per instance folder.

All paths (`config_path` itself, plus `config.yaml`'s `output_dir` and
`template_path`) are resolved relative to the process's current working
directory — there is nothing package- or install-location-relative about
path handling anywhere in this project (see `pl2docx.config.load_config`).
This means the intended workflow is: install this package once (e.g.
`uv tool install .` from the pl2docx repo), then `cd` into whatever folder
holds a given assessment's own `config.yaml` + `template.docx` and run
`pl2docx-run` there.
"""

from __future__ import annotations

import sys
from pathlib import Path

from pl2docx.canvas_capture import close_browser
from pl2docx.config import load_config
from pl2docx.element_config import additional_interactive_tags, load_element_config
from pl2docx.fetch import fetch_n_instances, instance_folder_label
from pl2docx.render import load_render_settings, render_instance


def run_all(config_path: Path) -> list[Path]:
    """Fetch every configured instance, then render each to blank + key docx.

    Parameters
    ----------
    config_path : pathlib.Path
        Path to a pl2docx `config.yaml` — see `pl2docx.config.load_config`.

    Returns
    -------
    list[pathlib.Path]
        Each generated instance's output folder
        (`config.output_dir/<folder_label>/`), in generation order.

    Notes
    -----
    Fetching and rendering both run against the same loaded `config`/
    `element_config`, so instance folders are located via
    `pl2docx.fetch.instance_folder_label` applied to `fetch_n_instances`'s
    returned numeric ids, rather than re-derived independently — this keeps
    the folder-naming logic (configured `instance_ids` vs. PL's own numeric
    id) in one place.
    """
    config = load_config(config_path)
    element_config = load_element_config(config_path)
    interactive_tags = additional_interactive_tags(element_config)

    try:
        assessment_instance_ids = fetch_n_instances(config, interactive_tags)
    finally:
        if interactive_tags:
            close_browser()

    restart_numbering_per_zone, block_display_indent_inches = load_render_settings(config_path)

    instance_dirs: list[Path] = []
    for i, assessment_instance_id in enumerate(assessment_instance_ids):
        folder_label = instance_folder_label(config, i, assessment_instance_id)
        instance_dir = config.output_dir / folder_label
        render_instance(
            instance_dir,
            config.template_path,
            element_config,
            restart_numbering_per_zone,
            block_display_indent_inches,
        )
        instance_dirs.append(instance_dir)

    return instance_dirs


def main() -> None:
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("config.yaml")
    instance_dirs = run_all(config_path)
    for instance_dir in instance_dirs:
        print(f"Wrote {instance_dir}/")
    print(f"Done. Fetched and rendered {len(instance_dirs)} instance(s).")


if __name__ == "__main__":
    main()
