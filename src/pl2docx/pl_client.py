"""HTTP client that drives a real local PrairieLearn (PL) server.

Route paths and `__action` literals used here were verified against the PL
source (reference clone) rather than guessed, per this project's ground rule
against confidently-guessed PrairieLearn scaffolding:

- ``regenerate_instance``: ``apps/prairielearn/src/middlewares/studentAssessmentAccess.ts:67``
- ``finish``: ``apps/prairielearn/src/pages/studentAssessmentInstance/studentAssessmentInstance.ts:162``
- instance_question links and zone grouping: rendered by
  ``apps/prairielearn/src/pages/studentAssessmentInstance/components/QuestionTableBody.tsx:49-119``
  (one ``<tbody>`` per zone; a ``<tr><th scope="rowgroup">`` row holding the zone's
  ``<span>`` title, when it has one, precedes that zone's ``instance_question`` rows).
- image URLs embedded in question HTML (e.g. ``clientFilesCourse``) are mounted inside
  the same authenticated ``instance_question`` route tree
  (``apps/prairielearn/src/server.ts:1522``) — no separate public path — and some
  (``generatedFilesQuestion``) are keyed to a variant id rather than the question, so are
  not guaranteed stable at a fixed URL long-term; download promptly rather than persisting
  just the URL.

Deliberately does *not* use the "view as student" role-override mechanism
(``instructorEffectiveUser``) that an earlier version of this project's
planning docs recommended. That mechanism overrides the caller's course role
down to plain "Student", which makes PL evaluate the assessment's real
``accessControl``/``allowAccess`` rules — an assessment with none configured
(as this tool wants, to avoid ever exposing a real exam/quiz to students)
then 403s. Instead, this client hits the student-facing routes directly
*without* any role-override cookies. As long as the authenticated user's
real course role is Previewer or above (true for any instructor account),
PL's access resolver short-circuits past the rule check entirely and grants
access — this is exactly the "Student view without access restrictions"
navbar option, which is not a separate action but simply the absence of a
role override (``lib/assessment-access-control/resolver.ts:219-225,444`` —
``isStaff()`` returns ``STAFF_OVERRIDE_RESULT`` before ``pickEffectiveRule``
is ever called). The routes and downstream instance-creation code hit are
otherwise identical to genuine student access.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

from pl2docx.csrf import extract_csrf_token


@dataclass(frozen=True)
class ZoneGroup:
    """One zone's worth of `instance_question`s, in on-page order.

    Parameters
    ----------
    title : str or None
        The zone's title, as shown on the assessment-instance overview page.
        `None` if the zone has no title (PL doesn't require one).
    instance_question_ids : list[int]
        `instance_question_id` values belonging to this zone, in the order
        they appear on the page.
    """

    title: str | None
    instance_question_ids: list[int]


def parse_zone_groups(html: str) -> list[ZoneGroup]:
    """Parse an assessment-instance overview page's zone/question structure.

    Parameters
    ----------
    html : str
        Raw HTML of an `assessment_instance/:id` overview page.

    Returns
    -------
    list[ZoneGroup]
        One entry per zone with at least one `instance_question` link,
        in page order. Zones with no questions (shouldn't normally occur)
        are omitted; empty list if the page has no recognizable zone/
        question table at all.

    Notes
    -----
    Pure/offline: does no I/O, so it's testable against a saved HTML
    fixture without a live server. See `PLClient.list_instance_questions`,
    which is the network-fetching counterpart that calls this.
    """
    soup = BeautifulSoup(html, "html.parser")
    iq_pattern = re.compile(r"/instance_question/(\d+)/?")

    zones: list[ZoneGroup] = []
    seen: set[int] = set()
    for tbody in soup.find_all("tbody"):
        title_tag = tbody.select_one('th[scope="rowgroup"] span')
        title = title_tag.get_text(strip=True) if title_tag else None
        ids: list[int] = []
        for link in tbody.find_all("a", href=True):
            match = iq_pattern.search(link["href"])
            if match:
                iq_id = int(match.group(1))
                if iq_id not in seen:
                    seen.add(iq_id)
                    ids.append(iq_id)
        if ids:
            zones.append(ZoneGroup(title=title, instance_question_ids=ids))
    return zones


class PLClientError(RuntimeError):
    """Base class for errors raised while driving the PL server."""


class UnexpectedResponseError(PLClientError):
    """Raised when a PL response doesn't have the shape a step expects.

    Covers cases like a redirect not landing on the URL pattern a step
    expects (e.g. no ``assessment_instance_id`` in the post-regenerate
    redirect), which most likely means an assumption about PL's routing
    baked into this client no longer holds.
    """


class PLClient:
    """Drives a local PrairieLearn dev server via its real instructor/student routes.

    Parameters
    ----------
    base_url : str
        Root URL of the PL server, no trailing slash, e.g. "http://localhost:3000".

    Notes
    -----
    Relies on PL's dev-mode auto-authentication
    (``middlewares/authn.ts``): any request from a fresh `requests.Session`
    is automatically authenticated as the configured dev user when the
    server is running with `config.devMode` true (the default for a
    non-production Docker run). No explicit login call is made or needed.

    Every POST requires a CSRF token that is signed per-URL and
    per-authenticated-user (``middlewares/csrfToken.ts``); this client always
    scrapes a fresh token from a GET of the exact URL it's about to POST to,
    immediately before posting, rather than caching/reusing tokens across
    URLs.
    """

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()

    def _get(self, url: str) -> requests.Response:
        response = self.session.get(url)
        response.raise_for_status()
        return response

    def fetch_binary(self, url: str) -> bytes:
        """Fetch a binary resource (e.g. an embedded image) using the same session.

        Parameters
        ----------
        url : str
            Absolute URL to fetch, e.g. an `<img src>` value resolved against
            `base_url`. Same-origin PL URLs (question images included)
            require this client's authenticated session, same as every
            other request this class makes.

        Returns
        -------
        bytes
            The raw response body.
        """
        return self._get(url).content

    def _post_with_fresh_csrf(self, url: str, data: dict[str, str]) -> requests.Response:
        """POST to `url`, scraping a fresh CSRF token from a GET of `url` first."""
        token_page = self._get(url)
        token = extract_csrf_token(token_page.text)
        response = self.session.post(url, data={**data, "__csrf_token": token})
        response.raise_for_status()
        return response

    def _assessment_url(self, course_instance_id: int, assessment_id: int) -> str:
        return f"{self.base_url}/pl/course_instance/{course_instance_id}/assessment/{assessment_id}"

    def _assessment_instance_url(self, course_instance_id: int, assessment_instance_id: int) -> str:
        return (
            f"{self.base_url}/pl/course_instance/{course_instance_id}"
            f"/assessment_instance/{assessment_instance_id}"
        )

    def _instance_question_url(self, course_instance_id: int, instance_question_id: int) -> str:
        return (
            f"{self.base_url}/pl/course_instance/{course_instance_id}"
            f"/instance_question/{instance_question_id}/"
        )

    def create_or_regenerate_instance(self, course_instance_id: int, assessment_id: int) -> int:
        """Create a fresh assessment instance, deleting any existing one first.

        Parameters
        ----------
        course_instance_id : int
            Numeric PL course_instance id.
        assessment_id : int
            Numeric PL assessment id to generate an instance of.

        Returns
        -------
        int
            The new `assessment_instance_id`.

        Notes
        -----
        Always produces a genuinely fresh instance: a first GET of the
        assessment page creates an instance if none exists yet (real
        `makeAssessmentInstance`, per the project's planning doc) and
        redirects to that instance's `assessment_instance` page, then this
        method always additionally POSTs `__action=regenerate_instance` to
        *that* `assessment_instance` URL to delete and recreate it, so
        repeated calls reliably yield distinct instances rather than reusing
        whatever existed before this call. The regenerate form has no
        `action` attribute in PL's own markup, so it (and this client) posts
        to the current `assessment_instance` page, not the `assessment`
        page — confirmed by inspecting the live rendered form, since this
        differs from where the `studentAssessmentAccess` middleware was
        found to be *registered* versus where the actual UI submits to.
        """
        assessment_url = self._assessment_url(course_instance_id, assessment_id)
        # Ensures an instance exists (creates one if needed) and gives us its
        # assessment_instance URL via the redirect.
        landing_page = self._get(assessment_url)
        instance_url = landing_page.url
        response = self._post_with_fresh_csrf(instance_url, {"__action": "regenerate_instance"})
        return self._extract_assessment_instance_id(response.url)

    @staticmethod
    def _extract_assessment_instance_id(url: str) -> int:
        match = re.search(r"/assessment_instance/(\d+)", url)
        if match is None:
            raise UnexpectedResponseError(
                f"Expected a redirect to an assessment_instance URL, got: {url}"
            )
        return int(match.group(1))

    def list_instance_questions(
        self, course_instance_id: int, assessment_instance_id: int
    ) -> list[ZoneGroup]:
        """List the `instance_question`s belonging to an assessment instance, by zone.

        Parameters
        ----------
        course_instance_id : int
            Numeric PL course_instance id.
        assessment_instance_id : int
            Numeric PL assessment_instance id.

        Returns
        -------
        list[ZoneGroup]
            One entry per zone, in the order zones appear on the
            assessment-instance overview page, each holding that zone's
            `instance_question_id`s in on-page order.

        Raises
        ------
        UnexpectedResponseError
            If no `instance_question` links are found on the page at all.

        Notes
        -----
        The overview page renders one `<tbody>` per zone
        (`QuestionTableBody.tsx:49-119`), with an optional
        `<tr><th scope="rowgroup">` zone-title row preceding that zone's
        question rows. A zone lacking a title still gets a `ZoneGroup`
        entry (with `title=None`) so instance_question order/grouping is
        preserved even for untitled zones.
        """
        url = self._assessment_instance_url(course_instance_id, assessment_instance_id)
        response = self._get(url)
        zones = parse_zone_groups(response.text)
        if not zones:
            raise UnexpectedResponseError(
                f"No instance_question links found on assessment instance page: {url}"
            )
        return zones

    def fetch_instance_questions(
        self, course_instance_id: int, instance_question_ids: list[int]
    ) -> dict[int, str]:
        """Fetch raw HTML for each given `instance_question`.

        Parameters
        ----------
        course_instance_id : int
            Numeric PL course_instance id.
        instance_question_ids : list[int]
            `instance_question_id` values to fetch, e.g. from
            `list_instance_questions`.

        Returns
        -------
        dict[int, str]
            Maps each `instance_question_id` to its raw page HTML. Called
            while the instance is open, this is the blank student copy; called
            after `close_instance`, the same URLs render the answer key
            (`showCorrectAnswer` flips automatically once
            `assessment_instance.open` is false, per
            `lib/question-render.ts:335-344` — no re-submission needed).
        """
        result: dict[int, str] = {}
        for iq_id in instance_question_ids:
            url = self._instance_question_url(course_instance_id, iq_id)
            result[iq_id] = self._get(url).text
        return result

    def close_instance(self, course_instance_id: int, assessment_instance_id: int) -> None:
        """Close an assessment instance so its answer key becomes visible.

        Parameters
        ----------
        course_instance_id : int
            Numeric PL course_instance id.
        assessment_instance_id : int
            Numeric PL assessment_instance id to close.

        Notes
        -----
        POSTs `__action=finish`
        (`pages/studentAssessmentInstance/studentAssessmentInstance.ts:162`).
        Usable with instructor edit permission; no special privilege needed.
        """
        url = self._assessment_instance_url(course_instance_id, assessment_instance_id)
        self._post_with_fresh_csrf(url, {"__action": "finish"})
