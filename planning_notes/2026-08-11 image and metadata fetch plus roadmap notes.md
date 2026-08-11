# pl2docx: Image Fetching + Zone/Title Metadata Capture, and Roadmap Placement

This note supplements (does not replace)
`planning_notes/2026-08-10 phased implementation roadmap.md`. It records what was
implemented in this session (fetch-stage work, landed as part of Phase 1/2's ongoing
scope) and where the still-deferred formatting/rendering pieces should land in the
existing phased roadmap.

## What prompted this

Inspecting real `output/` content (from the already-implemented Phase 1 fetch +
Phase 2 render) surfaced two gaps:

1. **Images were silently dropped.** Real fetched HTML embeds `<img>` tags (e.g. a
   `matter-classification` question's cartoon images) that `html_parser.py`'s
   `get_text()`-based prompt extraction just discarded — no trace, not even alt text.
2. **No zone-title or question-numbering/reference metadata was captured.** Zone titles
   only exist on the assessment-instance overview page, not on individual question pages,
   so they weren't available at all once we moved on to per-question fetching. Question
   titles are hidden from students on Exam-type assessments (shown for Homework, which is
   why this hadn't surfaced yet — the test assessment is Homework-type), which would make
   an Exam-type answer key hard for the instructor to cross-reference.

## Why the fetch-stage fix couldn't wait

pl2docx's design already splits "fetch now" from "render later" — HTML is persisted to
disk between phases specifically so later work doesn't need a live server. Verified from
PL source this session: `lib/question-render.ts:183-235`'s `generatedFilesQuestion` image
URLs are keyed to a `variant.id`, not the question — dynamically-generated images are not
guaranteed to stay available at a fixed URL long-term. If image-downloading were deferred
to whichever later phase does image *embedding*, previously-fetched HTML's image
references could already be dead by then. So the fetch/save half needed to happen now,
independent of when the embed-into-docx half gets built.

## What was implemented (this session)

- `PLClient.fetch_binary(url) -> bytes` — authenticated binary fetch, reusing the same
  session as every other request.
- `pl_client.parse_zone_groups(html) -> list[ZoneGroup]` (pure function, network-fetching
  wrapped by `PLClient.list_instance_questions`) — walks the assessment-instance overview
  page's per-zone `<tbody>` groups (confirmed structure:
  `studentAssessmentInstance/components/QuestionTableBody.tsx:49-119`) instead of
  discarding zone grouping like the old flat `list[int]` return did.
- `fetch.py` now downloads same-origin `<img>`s referenced in fetched HTML (both blank and
  key), saves them under `<instance>/{blank,key}/files/`, and rewrites the saved HTML's
  `<img src>` to the local relative path. Externally-hosted images are left untouched.
- `fetch.py` now writes `<instance>/structure.json`: the zone titles + question order/ids,
  captured once per instance alongside the HTML.
- Confirmed via PL source this session (for when Phase 3 gets to question-title/numbering
  configuration): every `instance_question` page includes a "Staff information" panel with
  a `QID:` row (`InstructorInfoPanel.tsx:19-100,183-195`), gated only on course-staff role
  (which pl2docx always has) — **not** on assessment type. So the real qid is already
  present in every fetched page today, Homework or Exam; nothing further needs fetching
  for that — it's a parsing task for whichever phase adds it.

None of this changes `html_parser.py`/`docx_builder.py` rendering behavior — images are
still invisible in the generated docx today, and zone titles/question numbering aren't
used yet. This was deliberately fetch-only: capture the raw material completely and
durably now, render it properly later.

## Roadmap placement for the deferred formatting/rendering work

Recorded here rather than edited into the original roadmap doc, per the user's
preference to keep dated planning notes as an append-only history rather than rewriting
past ones.

- **Phase 3** ("configurable per-element formatting") should also cover
  *document-structure*-level configuration, not just per-element-type formatting — at
  least: whether question titles are shown vs. numbered (and, when numbered/hidden, using
  the QID as the instructor-facing reference in the key), and whether/how zone titles are
  displayed (now available via `structure.json`). **The user may want additional
  document-level formatting options beyond these — when Phase 3 actually starts, ask for
  their full list of desired configurable behaviors before writing that phase's plan,
  rather than assuming this note's list is complete.**
- **Phase 4** ("Math rendering") should be reframed as general faithful HTML→docx
  conversion — rich text formatting (paragraphs, bold/italic/underline, currently
  flattened to plain text by `html_parser.py`'s `get_text()`), inline image embedding
  (using the files now saved by this session's work), and math→OMML — since these are the
  same underlying "walk the HTML and convert it properly" work, not independent add-ons.
