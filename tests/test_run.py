import pl2docx.fetch as fetch_module
from pl2docx.pl_client import ZoneGroup
from pl2docx.run import run_all


class _StubPLClient:
    """Minimal stand-in covering every `PLClient` method `fetch_n_instances`
    calls — see `tests/test_fetch.py`'s `_StubPLClient` for the pattern this
    mirrors."""

    def __init__(self, base_url: str):
        self.base_url = base_url
        self._next_id = 100

    def resolve_course_id(self, short_name):
        return 1

    def resolve_course_instance_id(self, course_id, short_name):
        return 2

    def resolve_assessment_id(self, course_instance_id, tid):
        return 3

    def create_or_regenerate_instance(self, course_instance_id, assessment_id):
        self._next_id += 1
        return self._next_id

    def list_instance_questions(self, course_instance_id, assessment_instance_id):
        return [ZoneGroup(title="Zone One", instance_question_ids=[assessment_instance_id])]

    def fetch_instance_questions(self, course_instance_id, instance_question_ids):
        return {
            iq: f'<div class="question-block"><div class="card-header"><h1>Q{iq}</h1></div>'
            f'<div class="card-body question-body"><p>Prompt {iq}.</p></div></div>'
            for iq in instance_question_ids
        }

    def close_instance(self, course_instance_id, assessment_instance_id):
        pass

    def instance_question_url(self, course_instance_id, instance_question_id):
        return f"{self.base_url}/pl/course_instance/{course_instance_id}/instance_question/{instance_question_id}/"


def _write_config(tmp_path, starter_template, **overrides) -> None:
    lines = [
        'base_url: "http://localhost:3000"',
        'course_short_name: "TEST"',
        'course_instance_short_name: "TEST"',
        'assessment_tid: "test"',
        f"n_instances: {overrides.get('n_instances', 2)}",
        f'output_dir: "{(tmp_path / "output").as_posix()}"',
        f'template_path: "{starter_template.as_posix()}"',
    ]
    if overrides.get("instance_ids"):
        ids = ", ".join(f'"{i}"' for i in overrides["instance_ids"])
        lines.append(f"instance_ids: [{ids}]")
    (tmp_path / "config.yaml").write_text("\n".join(lines), encoding="utf-8")


def test_run_all_fetches_and_renders_every_instance(tmp_path, starter_template, monkeypatch):
    monkeypatch.setattr(fetch_module, "PLClient", _StubPLClient)
    _write_config(tmp_path, starter_template, n_instances=2)

    instance_dirs = run_all(tmp_path / "config.yaml")

    assert len(instance_dirs) == 2
    for instance_dir, assessment_instance_id in zip(instance_dirs, [101, 102]):
        assert instance_dir == tmp_path / "output" / str(assessment_instance_id)
        assert (instance_dir / f"{assessment_instance_id}_blank.docx").is_file()
        assert (instance_dir / f"{assessment_instance_id}_key.docx").is_file()


def test_run_all_uses_configured_instance_ids_as_folder_names(tmp_path, starter_template, monkeypatch):
    monkeypatch.setattr(fetch_module, "PLClient", _StubPLClient)
    _write_config(tmp_path, starter_template, instance_ids=["Version A", "Version B"])

    instance_dirs = run_all(tmp_path / "config.yaml")

    assert [d.name for d in instance_dirs] == ["Version A", "Version B"]
    for instance_dir in instance_dirs:
        assert list(instance_dir.glob("*_blank.docx"))
        assert list(instance_dir.glob("*_key.docx"))
