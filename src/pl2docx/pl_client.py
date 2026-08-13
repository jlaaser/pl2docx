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

Stable-identifier resolution (``resolve_course_id``/``resolve_course_instance_id``/
``resolve_assessment_id`` and their pure ``parse_*``/``extract_*`` counterparts below)
exists because this tool's target PL server runs in an ephemeral Docker container with
no persistent database — every restart re-syncs the course from disk and reassigns new
numeric ids, silently invalidating any numeric id previously written to
``config.yaml``. PL's own human-authored, sync-stable text identifiers are used instead
(confirmed against the PL source and a live server, not guessed):

- A course's ``short_name`` (``database/tables/courses.pg``, distinct from its
  ``title``) is shown, colon-separated from the title, in each course link's text on
  the ``/pl`` homepage's "Courses with instructor access" table
  (``pages/home/home.html.ts``-rendered markup, e.g. ``CHEM 0110: General Chemistry
  I``).
- A course instance's ``short_name`` (``database/tables/course_instances.pg``) is a
  dedicated column, shown as-is, on ``/pl/course/:course_id/course_admin/instances``
  (``pages/instructorCourseAdminInstances/``).
- An assessment's ``tid`` (``database/tables/assessments.pg`` — the assessment's
  directory name under ``assessments/`` in the course repo, i.e. sync-stable
  independent of its editable ``title``) is **not** shown on the assessments list page
  itself (``/pl/course_instance/:id/instructor/instance_admin/assessments`` only
  exposes numeric ids and titles) — resolving it requires an extra request per
  candidate assessment to a page that does embed it, e.g.
  ``/pl/course_instance/:id/instructor/assessment/:aid/settings``, whose React-hydration
  payload includes a raw ``"tid":"..."`` field. A JSON API
  (``/pl/api/v1/course_instances/:id/assessments``, exposing ``assessment_name`` =
  ``tid`` directly) exists and would avoid this per-assessment lookup, but requires a
  separately-issued API token rather than this client's session-cookie auth, so is not
  used here.

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


def parse_courses_with_instructor_access(html: str) -> dict[str, int]:
    """Parse the `/pl` homepage's course list into `{short_name: course_id}`.

    Parameters
    ----------
    html : str
        Raw HTML of the `/pl` homepage, as an authenticated instructor.

    Returns
    -------
    dict[str, int]
        Maps each listed course's `short_name` (e.g. `"CHEM 0110"`) to its
        numeric `course_id`. Only courses shown under "Courses with
        instructor access" have a link matching `/pl/course/:id`, so this
        naturally excludes any (for this tool, unused) "Courses with student
        access" section.

    Notes
    -----
    Pure/offline: does no I/O, so it's testable against a saved HTML
    fixture. See `PLClient.resolve_course_id`, the network-fetching
    counterpart that calls this. Each course link's text is
    `"{short_name}: {title}"`; split on the first colon since `title` may
    itself contain one, but `short_name` is not expected to.
    """
    soup = BeautifulSoup(html, "html.parser")
    courses: dict[str, int] = {}
    for link in soup.find_all("a", href=re.compile(r"^/pl/course/\d+$")):
        course_id = int(link["href"].rsplit("/", 1)[-1])
        text = link.get_text(strip=True)
        short_name = text.split(":", 1)[0].strip()
        if short_name:
            courses[short_name] = course_id
    return courses


def parse_course_instances(html: str) -> dict[str, int]:
    """Parse a course's instances page into `{short_name: course_instance_id}`.

    Parameters
    ----------
    html : str
        Raw HTML of `/pl/course/:course_id/course_admin/instances`.

    Returns
    -------
    dict[str, int]
        Maps each course instance's `short_name` (e.g. `"0110-Test"`) to its
        numeric `course_instance_id`.

    Notes
    -----
    Pure/offline: does no I/O. See `PLClient.resolve_course_instance_id`.
    The table's first column holds a `/pl/course_instance/:id/...` link (the
    instance's long/display name); the second column holds its `short_name`
    as plain text — confirmed against
    `pages/instructorCourseAdminInstances/instructorCourseAdminInstances.html.tsx`
    and a live server's rendered table.
    """
    soup = BeautifulSoup(html, "html.parser")
    id_pattern = re.compile(r"/course_instance/(\d+)/")
    instances: dict[str, int] = {}
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        link = cells[0].find("a", href=True)
        if link is None:
            continue
        match = id_pattern.search(link["href"])
        if match is None:
            continue
        short_name = cells[1].get_text(strip=True)
        if short_name:
            instances[short_name] = int(match.group(1))
    return instances


def parse_assessment_ids(html: str) -> list[int]:
    """Parse a course instance's assessments list page into its assessment ids.

    Parameters
    ----------
    html : str
        Raw HTML of `/pl/course_instance/:id/instructor/instance_admin/assessments`.

    Returns
    -------
    list[int]
        Each listed assessment's numeric id, in on-page order, deduplicated
        (a row's title and any other controls can each link to the same
        assessment). This page does not expose each assessment's `tid`, only
        title and numeric id — see `PLClient.resolve_assessment_id` for how
        `tid` matching is done despite that.

    Notes
    -----
    Pure/offline: does no I/O.
    """
    soup = BeautifulSoup(html, "html.parser")
    pattern = re.compile(r"/instructor/assessment/(\d+)/?")
    ids: list[int] = []
    seen: set[int] = set()
    for link in soup.find_all("a", href=True):
        match = pattern.search(link["href"])
        if match:
            assessment_id = int(match.group(1))
            if assessment_id not in seen:
                seen.add(assessment_id)
                ids.append(assessment_id)
    return ids


def extract_assessment_tid(html: str) -> str | None:
    """Extract an assessment's `tid` from its instructor settings page.

    Parameters
    ----------
    html : str
        Raw HTML of `/pl/course_instance/:id/instructor/assessment/:aid/settings`.

    Returns
    -------
    str or None
        The assessment's `tid` (its directory name under `assessments/` in
        the course repo), or `None` if not found.

    Notes
    -----
    Pure/offline: does no I/O. The settings page's React-hydration payload
    embeds the assessment's data as raw JSON in a `<script
    type="application/json">` tag; rather than locating and parsing that
    whole (large, React-internals-shaped) payload, this regex-matches the
    `"tid":"..."` field directly, which is simpler and just as reliable
    since `tid` is a `text` column with no characters needing escaping
    (`database/tables/assessments.pg`; unlike, say, `title`, which could
    contain a literal `"`).
    """
    match = re.search(r'"tid":"([^"]*)"', html)
    return match.group(1) if match else None


class PLClientError(RuntimeError):
    """Base class for errors raised while driving the PL server."""


class UnexpectedResponseError(PLClientError):
    """Raised when a PL response doesn't have the shape a step expects.

    Covers cases like a redirect not landing on the URL pattern a step
    expects (e.g. no ``assessment_instance_id`` in the post-regenerate
    redirect), which most likely means an assumption about PL's routing
    baked into this client no longer holds.
    """


class StableIdNotFoundError(PLClientError):
    """Raised when a configured stable identifier (`short_name`/`tid`) can't be
    matched to anything on the live server.

    Most likely means the identifier in `config.yaml` is misspelled, or refers
    to a course/course-instance/assessment that doesn't exist on the currently
    running server (e.g. the wrong course repo is mounted). Not raised for
    ordinary numeric-id staleness after a server restart — resolving by stable
    identifier instead of numeric id is exactly what avoids that.
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

    def instance_question_url(self, course_instance_id: int, instance_question_id: int) -> str:
        """Return the live page URL for one `instance_question` (the page this client fetches).

        Public (not `_`-prefixed, unlike this class's other URL builders) so
        callers needing to navigate to the *live* page themselves - not just
        fetch its HTML/binary content via this client's own methods - can
        reuse the exact URL format, e.g. `pl2docx.canvas_capture`
        (Phase 5 subphase 2) driving a headless browser to screenshot a
        canvas-based interactive widget.
        """
        return (
            f"{self.base_url}/pl/course_instance/{course_instance_id}"
            f"/instance_question/{instance_question_id}/"
        )

    def playwright_cookies(self) -> list[dict]:
        """Translate this client's authenticated session cookies into Playwright's cookie format.

        Returns
        -------
        list[dict]
            One dict per cookie in `self.session.cookies` (a `RequestsCookieJar`),
            each shaped for `playwright.sync_api.BrowserContext.add_cookies()`:
            `{"name", "value", "domain", "path", "secure", "expires"}`. `expires`
            is `-1` (Playwright's "session cookie" sentinel) when the source
            cookie has none set. `httpOnly` isn't tracked by `http.cookiejar`
            (what `requests` builds on), so it's always set `True` here - safe
            for this client's purposes, since every cookie it holds is PL's own
            auth-session cookie, never one this code needs to read/write via JS.

        Notes
        -----
        Meant to be called once a request has actually been made (e.g. after
        `fetch_instance_questions`) so the session has real cookies to
        translate - calling this before any request just returns `[]`.
        """
        return [
            {
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain,
                "path": cookie.path,
                "secure": bool(cookie.secure),
                "httpOnly": True,
                "expires": cookie.expires if cookie.expires else -1,
            }
            for cookie in self.session.cookies
        ]

    def _course_admin_instances_url(self, course_id: int) -> str:
        return f"{self.base_url}/pl/course/{course_id}/course_admin/instances"

    def _instructor_assessments_url(self, course_instance_id: int) -> str:
        return f"{self.base_url}/pl/course_instance/{course_instance_id}/instructor/instance_admin/assessments"

    def _instructor_assessment_settings_url(self, course_instance_id: int, assessment_id: int) -> str:
        return (
            f"{self.base_url}/pl/course_instance/{course_instance_id}"
            f"/instructor/assessment/{assessment_id}/settings"
        )

    def resolve_course_id(self, course_short_name: str) -> int:
        """Resolve a course's stable `short_name` to its current numeric `course_id`.

        Parameters
        ----------
        course_short_name : str
            A course's `short_name` (e.g. `"CHEM 0110"`), as configured in
            its `infoCourse.json` — stable across server restarts, unlike
            the numeric id.

        Returns
        -------
        int
            The course's current `course_id`.

        Raises
        ------
        StableIdNotFoundError
            If no course with instructor access has this `short_name`.
        """
        courses = parse_courses_with_instructor_access(self._get(f"{self.base_url}/pl").text)
        if course_short_name not in courses:
            raise StableIdNotFoundError(
                f"No course with instructor access and short_name={course_short_name!r} "
                f"found. Available: {sorted(courses)}"
            )
        return courses[course_short_name]

    def resolve_course_instance_id(self, course_id: int, course_instance_short_name: str) -> int:
        """Resolve a course instance's stable `short_name` to its numeric id.

        Parameters
        ----------
        course_id : int
            The course's current numeric id, e.g. from `resolve_course_id`.
        course_instance_short_name : str
            A course instance's `short_name` (e.g. `"0110-Test"`), as
            configured in its `infoCourseInstance.json` — stable across
            server restarts, unlike the numeric id.

        Returns
        -------
        int
            The course instance's current `course_instance_id`.

        Raises
        ------
        StableIdNotFoundError
            If no course instance with this `short_name` exists under `course_id`.
        """
        instances = parse_course_instances(self._get(self._course_admin_instances_url(course_id)).text)
        if course_instance_short_name not in instances:
            raise StableIdNotFoundError(
                f"No course instance with short_name={course_instance_short_name!r} found "
                f"under course_id={course_id}. Available: {sorted(instances)}"
            )
        return instances[course_instance_short_name]

    def resolve_assessment_id(self, course_instance_id: int, assessment_tid: str) -> int:
        """Resolve an assessment's stable `tid` to its current numeric id.

        Parameters
        ----------
        course_instance_id : int
            The course instance's current numeric id, e.g. from
            `resolve_course_instance_id`.
        assessment_tid : str
            The assessment's `tid` — its directory name under `assessments/`
            in the course repo, stable across server restarts unlike the
            numeric id (and unlike its editable `title`, which this
            deliberately does not match against).

        Returns
        -------
        int
            The assessment's current `assessment_id`.

        Raises
        ------
        StableIdNotFoundError
            If no assessment with this `tid` is found among this course
            instance's assessments.

        Notes
        -----
        The assessments list page doesn't expose `tid`, only numeric ids and
        titles, so this fetches each candidate assessment's settings page in
        turn (stopping at the first `tid` match) — see this module's
        docstring for why a per-assessment request is unavoidable here.
        """
        candidate_ids = parse_assessment_ids(self._get(self._instructor_assessments_url(course_instance_id)).text)
        for assessment_id in candidate_ids:
            settings_html = self._get(
                self._instructor_assessment_settings_url(course_instance_id, assessment_id)
            ).text
            if extract_assessment_tid(settings_html) == assessment_tid:
                return assessment_id
        raise StableIdNotFoundError(
            f"No assessment with tid={assessment_tid!r} found among assessment "
            f"ids {candidate_ids} under course_instance_id={course_instance_id}."
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
            url = self.instance_question_url(course_instance_id, iq_id)
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
