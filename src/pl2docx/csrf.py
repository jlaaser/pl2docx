"""Extract PrairieLearn's per-request CSRF token from fetched page HTML."""

from __future__ import annotations

from bs4 import BeautifulSoup


class CsrfTokenNotFoundError(RuntimeError):
    """Raised when a page's `__csrf_token` hidden field cannot be found."""


def extract_csrf_token(html: str) -> str:
    """Extract the `__csrf_token` value embedded in a PrairieLearn page.

    PrairieLearn signs CSRF tokens per-URL and per-authenticated-user
    (`middlewares/csrfToken.ts`), so a token scraped from one page cannot be
    reused for a POST to a different URL. Callers must GET the exact URL
    they intend to POST to and extract a fresh token immediately before
    each POST.

    Parameters
    ----------
    html : str
        Raw HTML of a PrairieLearn page that contains a form with a hidden
        `__csrf_token` input (true of essentially all instructor/student
        pages that support a POST action).

    Returns
    -------
    str
        The token value.

    Raises
    ------
    CsrfTokenNotFoundError
        If no `__csrf_token` hidden input is present in `html`.

    Notes
    -----
    Assumes `html` is a real PrairieLearn page render; does not attempt to
    validate the token itself (that's the server's job).
    """
    soup = BeautifulSoup(html, "html.parser")
    field = soup.find("input", attrs={"name": "__csrf_token"})
    value = field.get("value") if field is not None else None
    if not value:
        raise CsrfTokenNotFoundError("No __csrf_token hidden input found in page HTML.")
    return str(value)
