from pl2docx.pl_client import (
    PLClient,
    ZoneGroup,
    extract_assessment_tid,
    parse_assessment_ids,
    parse_course_instances,
    parse_courses_with_instructor_access,
    parse_zone_groups,
)

OVERVIEW_PAGE = """
<html><body>
<table>
  <tbody>
    <tr><th scope="rowgroup"><span>Choice-type pool</span></th></tr>
    <tr><td><a href="/pl/course_instance/1/instance_question/111/">U1.1</a></td></tr>
  </tbody>
  <tbody>
    <tr><th scope="rowgroup"><span>Text/number-input pool</span></th></tr>
    <tr><td><a href="/pl/course_instance/1/instance_question/112/">U1.2</a></td></tr>
  </tbody>
</table>
</body></html>
"""

UNTITLED_ZONE_PAGE = """
<html><body>
<table>
  <tbody>
    <tr><td><a href="/pl/course_instance/1/instance_question/5/">U1.1</a></td></tr>
    <tr><td><a href="/pl/course_instance/1/instance_question/6/">U1.2</a></td></tr>
  </tbody>
</table>
</body></html>
"""

NO_QUESTIONS_PAGE = "<html><body><p>Nothing here.</p></body></html>"


def test_parse_zone_groups_titled_zones():
    zones = parse_zone_groups(OVERVIEW_PAGE)
    assert zones == [
        ZoneGroup(title="Choice-type pool", instance_question_ids=[111]),
        ZoneGroup(title="Text/number-input pool", instance_question_ids=[112]),
    ]


def test_parse_zone_groups_untitled_zone():
    zones = parse_zone_groups(UNTITLED_ZONE_PAGE)
    assert zones == [ZoneGroup(title=None, instance_question_ids=[5, 6])]


def test_parse_zone_groups_no_questions():
    assert parse_zone_groups(NO_QUESTIONS_PAGE) == []


def test_parse_zone_groups_dedupes_repeated_links():
    html = """
    <tbody>
      <tr><th scope="rowgroup"><span>Zone</span></th></tr>
      <tr><td>
        <a href="/pl/course_instance/1/instance_question/9/">Link A</a>
        <a href="/pl/course_instance/1/instance_question/9/?variant_id=1">Link A again</a>
      </td></tr>
    </tbody>
    """
    zones = parse_zone_groups(html)
    assert zones == [ZoneGroup(title="Zone", instance_question_ids=[9])]


HOMEPAGE = """
<html><body>
<table><tbody>
<tr><td><a href="/pl/course/1">CHEM 0110<!-- -->: <!-- -->General Chemistry I</a></td></tr>
<tr><td><a href="/pl/course/3">QA 101<!-- -->: <!-- -->Test Course</a></td></tr>
<tr><td><a href="/pl/course/2">XC 101<!-- -->: <!-- -->Example Course</a></td></tr>
</tbody></table>
</body></html>
"""


def test_parse_courses_with_instructor_access():
    courses = parse_courses_with_instructor_access(HOMEPAGE)
    assert courses == {"CHEM 0110": 1, "QA 101": 3, "XC 101": 2}


COURSE_INSTANCES_PAGE = """
<html><body>
<table>
<thead><tr><th>Long Name</th><th>Short name</th></tr></thead>
<tbody>
<tr><td class="align-left"><a href="/pl/course_instance/3/instructor/instance_admin">CHEM 0110 - Fall 2026 - Laaser</a></td><td class="align-left">laaser-f26</td></tr>
<tr><td class="align-left"><a href="/pl/course_instance/1/instructor/instance_admin">CHEM 0110 Test Course</a></td><td class="align-left">0110-Test</td></tr>
</tbody>
</table>
</body></html>
"""


def test_parse_course_instances():
    instances = parse_course_instances(COURSE_INSTANCES_PAGE)
    assert instances == {"laaser-f26": 3, "0110-Test": 1}


def test_parse_course_instances_ignores_header_row():
    instances = parse_course_instances(COURSE_INSTANCES_PAGE)
    assert "Short name" not in instances
    assert "Long Name" not in instances


ASSESSMENTS_LIST_PAGE = """
<html><body>
<table><tbody>
<tr><td><a href="/pl/course_instance/1/instructor/assessment/2/">PrairieLearn Intro/Tutorial</a></td></tr>
<tr><td><a href="/pl/course_instance/1/instructor/assessment/4/">pl2docx Phase 1 test</a></td></tr>
</tbody></table>
</body></html>
"""


def test_parse_assessment_ids():
    assert parse_assessment_ids(ASSESSMENTS_LIST_PAGE) == [2, 4]


def test_parse_assessment_ids_dedupes_repeated_links():
    html = """
    <a href="/pl/course_instance/1/instructor/assessment/4/">pl2docx Phase 1 test</a>
    <a href="/pl/course_instance/1/instructor/assessment/4/questions">Questions</a>
    """
    assert parse_assessment_ids(html) == [4]


def test_extract_assessment_tid_found():
    html = '<script type="application/json">{"assessment":{"id":"4","tid":"pl2docx-phase1-test"}}</script>'
    assert extract_assessment_tid(html) == "pl2docx-phase1-test"


def test_extract_assessment_tid_not_found():
    assert extract_assessment_tid("<html><body>no json here</body></html>") is None


def test_instance_question_url_format():
    client = PLClient("http://localhost:3000")
    url = client.instance_question_url(course_instance_id=1, instance_question_id=1367)
    assert url == "http://localhost:3000/pl/course_instance/1/instance_question/1367/"


def test_playwright_cookies_empty_session():
    client = PLClient("http://localhost:3000")
    assert client.playwright_cookies() == []


def test_playwright_cookies_translates_session_cookie_jar():
    client = PLClient("http://localhost:3000")
    client.session.cookies.set("pl_authn", "abc123", domain="localhost", path="/")
    cookies = client.playwright_cookies()
    assert len(cookies) == 1
    cookie = cookies[0]
    assert cookie["name"] == "pl_authn"
    assert cookie["value"] == "abc123"
    assert cookie["domain"] == "localhost"
    assert cookie["path"] == "/"
    assert cookie["secure"] is False
    assert cookie["httpOnly"] is True
    assert cookie["expires"] == -1  # no expiry set -> session-cookie sentinel
