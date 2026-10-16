# FOR/AGAINST evidence samples (Week 3, Track C)

Real Bedrock output for the four findings named in the Week 3 handoff. Run folders in
`data/runs/` are gitignored, so the case pairs are kept here.

- Model: `us.anthropic.claude-sonnet-4-6` (from `settings.py`), temperature 0
- Prompts: `prompts/evidence_for_v1.md` + `prompts/evidence_against_v1.md`
- Selection: `ADJ_EVIDENCE_ONLY` set to these four IDs (Week 3 cut list item 1)
- Date: 2026-10-15

## Router bucket counts (all 8 pages)

| Page | Total | machine | grey | human-only |
| --- | --- | --- | --- | --- |
| before home | 155 | 51 | 90 | 14 |
| before news | 141 | 49 | 78 | 14 |
| before tickets | 228 | 44 | 170 | 14 |
| before survey | 200 | 46 | 140 | 14 |
| after home | 78 | 0 | 64 | 14 |
| after news | 80 | 0 | 66 | 14 |
| after tickets | 124 | 0 | 110 | 14 |
| after survey | 74 | 0 | 60 | 14 |

Rule 3 (criterion marked `human_only` in the corpus) currently moves no findings: the only
such criteria, 2.4.3 and 3.2.4, appear only as page-level notices, which rule 2 already routes.

## Summary

| Finding | Recommendation | Citations verified | Tokens in / out |
| --- | --- | --- | --- |
| `F-before-home-el0210-2.4.4` | likely_fail | True | 1129 / 309 |
| `F-before-home-el0194-2.1.1` | unsure | True | 1373 / 425 |
| `F-before-home-el0231-1.3.1` | unsure | True | 1065 / 377 |
| `F-after-home-el0062-4.1.2` | unsure | True | 1195 / 361 |

## `F-before-home-el0210-2.4.4`

- Criterion: 2.4.4, scanner status `notice`, bucket `grey` (rule 4: scanner can flag but not judge (scanner_status=notice, criterion 2.4.4))
- HTML: `<a onfocus="blur();" href="../offsite.html" target="_blank">Click here</a>`
- Surrounding text: "Please see the following websites for important information. Citylights take no responsibility for their content. For artichoke advice, call the number below. Killer bees. Click here. Onions. Click he"
- Run: `data/runs/20261015-181754-1d68`

**FOR**

> The link text is 'Click here', which conveys no destination or purpose on its own. The surrounding context lists multiple topics ('Killer bees', 'Onions') each followed by a separate 'Click here' link, making the programmatically determined context ambiguous — a user cannot determine which 'Click here' leads where. The scanner flagged this link specifically for failing to identify its purpose through text or context.
> Quoted: "Click here"; "Killer bees. Click here. Onions. Click here."; "Check that the link text combined with programmatically determined link context identifies the purpose of the link"
> Strength: 4/5

**AGAINST**

> The link text 'Click here' is generic and provides no indication of its destination or purpose. The surrounding text lists multiple topics ('Killer bees', 'Onions') each followed by their own 'Click here' link, making the programmatic context insufficient to distinguish between them. No honest case exists that this link passes 2.4.4.
> Quoted: "Click here"; "Killer bees. Click here. Onions. Click here."
> Strength: 1/5

## `F-before-home-el0194-2.1.1`

- Criterion: 2.1.1, scanner status `needs_review`, bucket `grey` (rule 4: scanner can flag but not judge (scanner_status=needs_review, criterion 2.1.1))
- HTML: `<img src="./img/morearrow.gif" width="48" height="10" alt="" border="0" onmouseover="this.src='./img/morearrow_a.gif'" onmouseout="this.src='./img/morearrow.gif'" style="vertical-align: bottom">`
- Surrounding text: "After three years of effort city scientists now agree that the primary cause of the 2003 heatwave was hot air from our"
- Run: `data/runs/20261015-182118-4012`

**FOR**

> The img element uses 'onmouseover' and 'onmouseout' event handlers to swap image sources, providing visual feedback exclusively through mouse interaction. No equivalent 'onfocus' or 'onblur' keyboard events are present. The scanner confirms this functionality is unavailable via keyboard. This means the interactive state change triggered by mousing over this element cannot be replicated through keyboard operation, violating 2.1.1.
> Quoted: "onmouseover="this.src='./img/morearrow_a.gif'""; "onmouseout="this.src='./img/morearrow.gif'""; "Ensure the functionality provided by mousing over this element is available through the keyboard; for instance, using the focus event."; "Ensure the functionality provided by mousing out of this element is available through the keyboard; for instance, using the blur event."
> Strength: 2/5

**AGAINST**

> The onmouseover/onmouseout handlers only swap the image source between two arrow GIF files, producing a purely visual hover effect. This is a decorative image inside an anchor element. The underlying functionality is navigation via the anchor link, not the image swap. The image swap itself conveys no information or functionality beyond decoration. However, the scanner flags a real concern and the anchor's keyboard operability cannot be confirmed from this evidence alone.
> Quoted: "onmouseover="this.src='./img/morearrow_a.gif'""; "onmouseout="this.src='./img/morearrow.gif'""; "alt="""
> Strength: 2/5

## `F-before-home-el0231-1.3.1`

- Criterion: 1.3.1, scanner status `needs_review`, bucket `grey` (rule 4: scanner can flag but not judge (scanner_status=needs_review, criterion 1.3.1))
- HTML: `<b>Free Penguins</b>`
- Surrounding text: "Free Penguins"
- Run: `data/runs/20261015-181754-1d68`

**FOR**

> The text 'Free Penguins' is wrapped in a <b> tag used purely for visual bold styling, not a semantic element like <strong> or a heading tag. The scanner confirms 'Semantic markup should be used to mark emphasised or special text so that it can be programmatically determined.' The structural role or emphasis of this text cannot be programmatically determined from a presentational <b> element alone, violating 1.3.1.
> Quoted: "Semantic markup should be used to mark emphasised or special text so that it can be programmatically determined"; "<b>Free Penguins</b>"
> Strength: 2/5

**AGAINST**

> The scanner flags <b> as lacking semantic meaning, but 1.3.1 asks whether information and relationships conveyed through presentation can be programmatically determined. The surrounding visible text is identical — 'Free Penguins' — so no additional information or relationship is conveyed by the bold styling beyond the text itself. If the bold conveys no structural role (heading, label, emphasis of meaning), there is no information or relationship to programmatically expose, and 1.3.1 does not apply.
> Quoted: "<b>Free Penguins</b>"; "Free Penguins"; "Semantic markup should be used to mark emphasised or special text so that it can be programmatically determined."
> Strength: 2/5

## `F-after-home-el0062-4.1.2`

- Criterion: 4.1.2, scanner status `needs_review`, bucket `grey` (rule 4: scanner can flag but not judge (scanner_status=needs_review, criterion 4.1.2))
- HTML: `<a><img src="./img/toplogo.png" alt="Citylights: your access to the city."></a>`
- Surrounding text: "Explore Site by Topic: Quick Menu Broadcasting Education Electricity Fire service Gas service Health care Police service Public Libraries Social services Social housing Telecommunications Town plannin"
- Run: `data/runs/20261015-181824-5bf9`

**FOR**

> The anchor element lacks an href, id, or name attribute. Without href, the element has no defined role as an interactive link—it functions as an inert placeholder. WCAG 4.1.2 requires that the role of user interface components be programmatically determinable. An anchor without href is not a link per HTML specification, meaning assistive technologies cannot determine its role or expose it as an interactive control, failing the role requirement of 4.1.2.
> Quoted: "<a><img src="./img/toplogo.png" alt="Citylights: your access to the city."></a>"; "Anchor element found with link content, but no href, ID or name attribute has been supplied."
> Strength: 3/5

**AGAINST**

> The anchor element contains an image with a descriptive alt attribute, providing an accessible name: 'Citylights: your access to the city.' However, the element lacks an href, id, or name attribute, meaning it has no defined role as a link or anchor. Without href, it is not an interactive control, so 4.1.2 does not apply to it as a user interface component. A non-interactive anchor is not a UI component requiring name/role exposure under 4.1.2.
> Quoted: "no href, ID or name attribute has been supplied"; "alt="Citylights: your access to the city.""
> Strength: 2/5

## Notes

- "Click here": AGAINST honestly concedes (strength 1). The prompts allow that instead of forcing a
  devil's advocate (Week 2 plan, C4). W3C also fails this link.
- After logo: lands `unsure` (FOR 3, AGAINST 2). Under router policy v1 only `likely_fail` counts as an
  issue, so this is not a false alarm, but AGAINST did not win as the handoff expected. Candidate fix for
  Week 4: a `_v2` prompt pair that also sends the corpus `intent` text, checked on more than one finding.
- The first run of the arrow finding failed verification because AGAINST quoted part of the selector.
  After the Oct 15 audit the selector does NOT count as a quote source (fragments of it, like "a" or
  "href", match almost anything). Quotes must be at least 6 characters, and each side needs at least
  one quote from the HTML or surrounding text. All four cases above still verify under the stricter
  check (`tests/test_evidence.py::test_recorded_week3_cases_still_verify`).
