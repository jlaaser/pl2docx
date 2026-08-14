import json

from docx import Document

from pl2docx.render import _build_zones, render_instance


def _question_html(title: str) -> str:
    return f"""
    <div class="question-block">
      <div class="card-header"><h1>{title}</h1></div>
      <div class="card-body question-body">
        <p>Prompt text for {title}.</p>
      </div>
    </div>
    """


def _write_instance_question(html_dir, iq_id: int, title: str) -> None:
    html_dir.mkdir(parents=True, exist_ok=True)
    (html_dir / f"{iq_id}.html").write_text(_question_html(title), encoding="utf-8")


_ZONES_STRUCTURE = [
    {"title": "Zone One", "instance_question_ids": [1, 2]},
    {"title": "Zone Two", "instance_question_ids": [3]},
]


def test_build_zones_numbers_continuously_by_default(tmp_path):
    html_dir = tmp_path / "blank"
    for iq_id, title in ((1, "Q1"), (2, "Q2"), (3, "Q3")):
        _write_instance_question(html_dir, iq_id, title)

    zones = _build_zones(html_dir, _ZONES_STRUCTURE, element_config=None)

    numbers = [number for zone in zones for _question, number in zone["questions"]]
    assert numbers == [1, 2, 3]


def test_build_zones_restarts_numbering_per_zone(tmp_path):
    html_dir = tmp_path / "blank"
    for iq_id, title in ((1, "Q1"), (2, "Q2"), (3, "Q3")):
        _write_instance_question(html_dir, iq_id, title)

    zones = _build_zones(html_dir, _ZONES_STRUCTURE, element_config=None, restart_numbering_per_zone=True)

    zone_one_numbers = [number for _question, number in zones[0]["questions"]]
    zone_two_numbers = [number for _question, number in zones[1]["questions"]]
    assert zone_one_numbers == [1, 2]
    assert zone_two_numbers == [1]


def _write_fetched_instance(instance_dir, instance_id: str) -> None:
    for iq_id, title in ((1, "Q1"),):
        _write_instance_question(instance_dir / "blank", iq_id, title)
        _write_instance_question(instance_dir / "key", iq_id, title)
    (instance_dir / "structure.json").write_text(
        json.dumps(
            {
                "instance_id": instance_id,
                "zones": [{"title": "Zone One", "instance_question_ids": [1]}],
            }
        ),
        encoding="utf-8",
    )


def _rendered_text(path) -> str:
    doc = Document(str(path))
    return "\n".join(p.text for p in doc.paragraphs)


def test_render_instance_threads_instance_id_into_template(tmp_path, starter_template):
    instance_dir = tmp_path / "output" / "Version A"
    _write_fetched_instance(instance_dir, instance_id="Version A")

    blank_path, key_path = render_instance(instance_dir, starter_template)

    assert "Version A" in _rendered_text(blank_path)
    assert "Version A" in _rendered_text(key_path)


def test_render_instance_falls_back_to_folder_name_when_instance_id_missing(tmp_path, starter_template):
    """A structure.json written before the instance_id field existed (a schema
    change to a gitignored, regenerable runtime file) must not crash - falls
    back to the folder name rather than requiring a re-fetch."""
    instance_dir = tmp_path / "output" / "42"
    _write_instance_question(instance_dir / "blank", 1, "Q1")
    _write_instance_question(instance_dir / "key", 1, "Q1")
    (instance_dir / "structure.json").write_text(
        json.dumps([{"title": "Zone One", "instance_question_ids": [1]}]),
        encoding="utf-8",
    )

    blank_path, _key_path = render_instance(instance_dir, starter_template)

    assert "42" in _rendered_text(blank_path)
