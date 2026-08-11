import pytest
from docx import Document


@pytest.fixture
def minimal_template(tmp_path):
    """A minimal docx template with a single `{{p content }}` placeholder.

    The `p` prefix is docxtpl's required syntax for paragraph-level
    subdocument insertion (plain `{{ content }}` embeds the subdocument's
    raw XML as literal text inside the placeholder's own run instead of
    splicing in real paragraphs — confirmed by testing both forms).

    Generated at test time (docx is a binary format, not something to hand-author
    as a checked-in text fixture) rather than committed to the repo.
    """
    doc = Document()
    doc.add_paragraph("{{p content }}")
    path = tmp_path / "template_minimal.docx"
    doc.save(str(path))
    return path
