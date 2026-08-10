"""Integration test against a real local PrairieLearn dev server.

Requires:
- a local PL server reachable at the configured base_url (default
  http://localhost:3000, matching CLAUDE.md's documented dev setup)
- a config.yaml in the repo root with course_instance_id/assessment_id
  pointing at the Phase 1 test assessment (see the phase 0+1 implementation
  plan / planning_notes for its infoAssessment.json spec)

Both are auto-detected; the whole module is skipped if either is missing,
so this suite is safe to run without a live server (e.g. in CI).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import requests

from pl2docx.config import load_config
from pl2docx.fetch import fetch_n_instances

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config.yaml"

if not CONFIG_PATH.exists():
    pytest.skip(f"No {CONFIG_PATH} found; skipping live-server integration test.", allow_module_level=True)

_config = load_config(CONFIG_PATH)

try:
    requests.get(_config.base_url, timeout=2).raise_for_status()
except requests.RequestException as exc:
    pytest.skip(f"PL server at {_config.base_url} not reachable: {exc}", allow_module_level=True)


def test_fetch_n_instances_produces_distinct_paired_html(tmp_path):
    config = load_config(CONFIG_PATH)
    config = config.__class__(**{**config.__dict__, "n_instances": 2, "output_dir": tmp_path})

    instance_ids = fetch_n_instances(config)

    assert len(instance_ids) == 2
    assert len(set(instance_ids)) == 2, "expected two distinct assessment_instance_ids"

    for instance_id in instance_ids:
        blank_dir = tmp_path / str(instance_id) / "blank"
        key_dir = tmp_path / str(instance_id) / "key"
        assert blank_dir.is_dir()
        assert key_dir.is_dir()

        blank_files = {p.name for p in blank_dir.glob("*.html")}
        key_files = {p.name for p in key_dir.glob("*.html")}
        assert blank_files, "expected at least one instance_question HTML file"
        assert blank_files == key_files, (
            "blank and key should cover the exact same instance_question ids "
            "(same variant, per lib/question-render.ts showCorrectAnswer gating)"
        )

        for name in blank_files:
            assert (blank_dir / name).read_text(encoding="utf-8").strip()
            assert (key_dir / name).read_text(encoding="utf-8").strip()
