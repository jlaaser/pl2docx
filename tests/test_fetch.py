import json

import pl2docx.fetch as fetch_module
from pl2docx.config import Config
from pl2docx.element_config import InteractivePreferences
from pl2docx.fetch import _download_images, _is_same_origin, _write_html, fetch_n_instances
from pl2docx.pl_client import ZoneGroup


class _StubClient:
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.requested_urls: list[str] = []

    def fetch_binary(self, url: str) -> bytes:
        self.requested_urls.append(url)
        return b"fake-image-bytes"

    def instance_question_url(self, course_instance_id: int, instance_question_id: int) -> str:
        return f"{self.base_url}/pl/course_instance/{course_instance_id}/instance_question/{instance_question_id}/"


def test_is_same_origin():
    base = "http://localhost:3000"
    assert _is_same_origin(base, "/pl/course_instance/1/instance_question/1/foo.png")
    assert _is_same_origin(base, "http://localhost:3000/pl/foo.png")
    assert not _is_same_origin(base, "https://example.com/foo.png")


def test_download_images_rewrites_same_origin_src_and_saves_file(tmp_path):
    client = _StubClient("http://localhost:3000")
    html = (
        '<div class="question-body">'
        '<img src="/pl/course_instance/1/instance_question/111/clientFilesCourse/foo/bar.png" width="150">'
        "</div>"
    )
    files_dir = tmp_path / "files"

    result_html = _download_images(client, files_dir, instance_question_id=111, html=html)

    assert client.requested_urls == [
        "http://localhost:3000/pl/course_instance/1/instance_question/111/clientFilesCourse/foo/bar.png"
    ]
    saved_files = list(files_dir.glob("*"))
    assert len(saved_files) == 1
    assert saved_files[0].name == "111_0_bar.png"
    assert saved_files[0].read_bytes() == b"fake-image-bytes"
    assert "files/111_0_bar.png" in result_html
    assert "/pl/course_instance/1/instance_question/111/clientFilesCourse" not in result_html


def test_download_images_leaves_external_images_untouched(tmp_path):
    client = _StubClient("http://localhost:3000")
    html = '<img src="https://example.com/external.png">'
    files_dir = tmp_path / "files"

    result_html = _download_images(client, files_dir, instance_question_id=1, html=html)

    assert client.requested_urls == []
    assert not files_dir.exists()
    assert "https://example.com/external.png" in result_html


def test_download_images_no_images_returns_html_unchanged(tmp_path):
    client = _StubClient("http://localhost:3000")
    html = "<p>No images here.</p>"

    result_html = _download_images(client, tmp_path / "files", instance_question_id=1, html=html)

    assert result_html == html


def test_write_html_calls_capture_interactive_elements_when_configured(tmp_path, monkeypatch):
    """Phase 5 subphase 2: `_write_html` must call `capture_interactive_elements`
    (before `_download_images`) with the live page URL, when `interactive_tags` is
    non-empty - verified via monkeypatching, no Chromium/live server needed."""
    calls = []

    def fake_capture(client, html, page_url, files_dir, instance_question_id, interactive_tags):
        calls.append((page_url, instance_question_id, list(interactive_tags)))
        return html.replace("<canvas></canvas>", '<img src="files/captured.png" alt="diagram">')

    monkeypatch.setattr(fetch_module, "capture_interactive_elements", fake_capture)

    client = _StubClient("http://localhost:3000")
    interactive_tags = {"pl-orbitaldiagram": InteractivePreferences(container_selector=".pl-orbitaldiagram")}
    pages = {42: '<div class="pl-orbitaldiagram"><canvas></canvas></div>'}

    _write_html(
        client, tmp_path, course_instance_id=1, folder_label="99", subdir="blank",
        pages=pages, interactive_tags=interactive_tags,
    )

    assert len(calls) == 1
    page_url, instance_question_id, tags = calls[0]
    assert page_url == "http://localhost:3000/pl/course_instance/1/instance_question/42/"
    assert instance_question_id == 42
    assert tags == ["pl-orbitaldiagram"]

    written = (tmp_path / "99" / "blank" / "42.html").read_text(encoding="utf-8")
    assert "captured.png" in written
    assert "<canvas>" not in written


def test_write_html_skips_capture_when_no_interactive_tags_configured(tmp_path, monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("capture_interactive_elements should not be called")

    monkeypatch.setattr(fetch_module, "capture_interactive_elements", fail_if_called)

    client = _StubClient("http://localhost:3000")
    pages = {1: "<p>No canvas here.</p>"}

    _write_html(
        client, tmp_path, course_instance_id=1, folder_label="1", subdir="blank",
        pages=pages, interactive_tags={},
    )

    written = (tmp_path / "1" / "blank" / "1.html").read_text(encoding="utf-8")
    assert "No canvas here." in written


class _StubPLClient:
    """Stub covering every `PLClient` method `fetch_n_instances` calls, so its
    whole loop (previously entirely untested) can run offline against no real
    server. `create_or_regenerate_instance` mints a fresh, ever-increasing
    numeric id each call, mirroring PL always assigning its own id regardless
    of any local instance_ids labeling (see `Config.instance_ids`'s docstring)."""

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
        return [ZoneGroup(title="Zone", instance_question_ids=[assessment_instance_id])]

    def fetch_instance_questions(self, course_instance_id, instance_question_ids):
        return {iq: f"<p>content {iq}</p>" for iq in instance_question_ids}

    def close_instance(self, course_instance_id, assessment_instance_id):
        pass

    def instance_question_url(self, course_instance_id, instance_question_id):
        return f"{self.base_url}/pl/course_instance/{course_instance_id}/instance_question/{instance_question_id}/"


def _base_config(tmp_path, **overrides) -> Config:
    defaults = dict(
        base_url="http://localhost:3000",
        course_short_name="TEST",
        course_instance_short_name="TEST",
        assessment_tid="test",
        n_instances=2,
        output_dir=tmp_path / "output",
        template_path=tmp_path / "template.docx",
    )
    defaults.update(overrides)
    return Config(**defaults)


def test_fetch_n_instances_default_numbering(tmp_path, monkeypatch):
    """With no instance_ids configured: folders are named by PL's numeric id
    (unchanged from before instance_ids existed), and each structure.json's
    instance_id defaults to "Instance {n}" (1-based)."""
    monkeypatch.setattr(fetch_module, "PLClient", _StubPLClient)
    config = _base_config(tmp_path, n_instances=2)

    result = fetch_n_instances(config)

    assert result == [101, 102]
    for i, assessment_instance_id in enumerate(result, start=1):
        instance_dir = config.output_dir / str(assessment_instance_id)
        assert instance_dir.is_dir()
        structure = json.loads((instance_dir / "structure.json").read_text(encoding="utf-8"))
        assert structure["instance_id"] == f"Instance {i}"
        assert structure["zones"][0]["title"] == "Zone"
        assert (instance_dir / "blank" / f"{assessment_instance_id}.html").is_file()
        assert (instance_dir / "key" / f"{assessment_instance_id}.html").is_file()


def test_fetch_n_instances_with_explicit_instance_ids(tmp_path, monkeypatch):
    """instance_ids configured: folders are named by the configured labels
    (not PL's numeric id), each structure.json's instance_id is that same
    label, n_instances is ignored, and the returned list is still the real
    PL-assigned numeric ids (PL always mints its own regardless of labeling)."""
    monkeypatch.setattr(fetch_module, "PLClient", _StubPLClient)
    config = _base_config(tmp_path, n_instances=99, instance_ids=["Version A", "Version B"])

    result = fetch_n_instances(config)

    assert result == [101, 102]
    for label in ("Version A", "Version B"):
        instance_dir = config.output_dir / label
        assert instance_dir.is_dir()
        structure = json.loads((instance_dir / "structure.json").read_text(encoding="utf-8"))
        assert structure["instance_id"] == label
