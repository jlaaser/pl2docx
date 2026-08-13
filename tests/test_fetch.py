import pl2docx.fetch as fetch_module
from pl2docx.element_config import InteractivePreferences
from pl2docx.fetch import _download_images, _is_same_origin, _write_html


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
        client, tmp_path, course_instance_id=1, assessment_instance_id=99, subdir="blank",
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
        client, tmp_path, course_instance_id=1, assessment_instance_id=1, subdir="blank",
        pages=pages, interactive_tags={},
    )

    written = (tmp_path / "1" / "blank" / "1.html").read_text(encoding="utf-8")
    assert "No canvas here." in written
