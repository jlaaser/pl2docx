import pytest

from pl2docx.config import load_config

_REQUIRED = """
base_url: "http://localhost:3000"
course_short_name: "CHEM 0110"
course_instance_short_name: "0110-Test"
assessment_tid: "some-assessment"
"""


def _write(tmp_path, contents: str):
    path = tmp_path / "config.yaml"
    path.write_text(contents, encoding="utf-8")
    return path


def test_load_config_missing_required_key_raises(tmp_path):
    path = _write(tmp_path, "base_url: \"http://localhost:3000\"\n")
    with pytest.raises(ValueError):
        load_config(path)


def test_latex_packages_defaults_to_empty_list(tmp_path):
    path = _write(tmp_path, _REQUIRED)
    config = load_config(path)
    assert config.latex_packages == []


def test_latex_packages_parsed_from_config(tmp_path):
    path = _write(tmp_path, _REQUIRED + "\nlatex-packages:\n  - mhchem\n  - siunitx\n")
    config = load_config(path)
    assert config.latex_packages == ["mhchem", "siunitx"]


def test_instance_ids_defaults_to_none(tmp_path):
    path = _write(tmp_path, _REQUIRED)
    config = load_config(path)
    assert config.instance_ids is None


def test_instance_ids_parsed_from_config(tmp_path):
    path = _write(tmp_path, _REQUIRED + '\ninstance_ids:\n  - "Version A"\n  - "Version B"\n')
    config = load_config(path)
    assert config.instance_ids == ["Version A", "Version B"]


def test_instance_ids_empty_list_treated_as_none(tmp_path):
    """An explicit empty list must fall back to n_instances-driven behavior, same
    as omitting the key entirely - not "generate zero instances"."""
    path = _write(tmp_path, _REQUIRED + "\ninstance_ids: []\n")
    config = load_config(path)
    assert config.instance_ids is None


def test_restart_numbering_per_zone_defaults_to_false(tmp_path):
    path = _write(tmp_path, _REQUIRED)
    config = load_config(path)
    assert config.restart_numbering_per_zone is False


def test_restart_numbering_per_zone_parsed_from_config(tmp_path):
    path = _write(tmp_path, _REQUIRED + "\nrestart_numbering_per_zone: true\n")
    config = load_config(path)
    assert config.restart_numbering_per_zone is True
