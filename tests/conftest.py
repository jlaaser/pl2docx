import pytest

from pl2docx.starter_template import build_starter_template


@pytest.fixture
def starter_template(tmp_path):
    """A real generated pl2docx starter template (see `pl2docx.starter_template`).

    Generated at test time (docx is a binary format, not something to hand-author
    as a checked-in text fixture) rather than committed to the repo. Using the
    actual generator here (rather than a hand-rolled minimal stand-in) keeps tests
    exercising the same template shape real usage does.
    """
    path = tmp_path / "template.docx"
    build_starter_template(path)
    return path
