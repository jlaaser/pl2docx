import pytest

from pl2docx.csrf import CsrfTokenNotFoundError, extract_csrf_token

PAGE_WITH_TOKEN = """
<html>
<body>
<form method="POST">
<input type="hidden" name="__csrf_token" value="abc123.signature">
<button type="submit">Go</button>
</form>
</body>
</html>
"""

PAGE_WITHOUT_TOKEN = """
<html><body><form method="POST"><button>Go</button></form></body></html>
"""

PAGE_WITH_EMPTY_TOKEN = """
<html><body><form><input type="hidden" name="__csrf_token" value=""></form></body></html>
"""


def test_extract_csrf_token_finds_value():
    assert extract_csrf_token(PAGE_WITH_TOKEN) == "abc123.signature"


def test_extract_csrf_token_raises_when_missing():
    with pytest.raises(CsrfTokenNotFoundError):
        extract_csrf_token(PAGE_WITHOUT_TOKEN)


def test_extract_csrf_token_raises_when_empty():
    with pytest.raises(CsrfTokenNotFoundError):
        extract_csrf_token(PAGE_WITH_EMPTY_TOKEN)
