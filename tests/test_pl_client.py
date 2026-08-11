from pl2docx.pl_client import ZoneGroup, parse_zone_groups

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
