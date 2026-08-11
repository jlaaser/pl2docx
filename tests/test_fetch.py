from pl2docx.fetch import _download_images, _is_same_origin


class _StubClient:
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.requested_urls: list[str] = []

    def fetch_binary(self, url: str) -> bytes:
        self.requested_urls.append(url)
        return b"fake-image-bytes"


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
