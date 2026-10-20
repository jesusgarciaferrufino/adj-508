# Missed issues: how we could catch them

Source: `data/ground-truth/ground_truth.csv` (#23), the 71 rows with `finding_id = missed`.
Evidence below comes from the source of the four W3C Before pages (`before/{home,news,tickets,survey}.html`, inside `#page`) and their inline `<style>` blocks.

## Summary

| Group | Rows | Criteria |
|---|---|---|
| (a) Code rule on HTML/CSS | 40 | 1.1.1 (F3, F20, F71), 1.3.1 (F46), 1.4.1 (F73 home/news), 2.1.1, 2.4.1, 2.4.6, 2.4.7, 3.2.1, 4.1.2 |
| (b) HTML validator | 4 | 4.1.1 |
| (c) Comparing pages | 4 | 3.2.4 |
| (d) AI on the screenshot / page | 20 | 1.3.1 (F43), 1.3.2, 1.4.1 (F73 tickets/survey, F81), 1.4.3, 1.4.5, 2.4.3, 2.4.4 (F63) |
| (e) Not found in page source | 3 | 1.1.1 (F39 on news, tickets, survey) |
| **Total** | **71** | |

The biggest single win is one rule: **`onfocus="blur()"` on links**. It covers 16 rows (2.1.1, 2.4.7, 3.2.1 F55 and 4.1.2 F79 on all four pages).

## (a) Code rule on HTML/CSS — 40 rows

| Rule | Rows | Criteria (pages) | Exact element / CSS on the page |
|---|---|---|---|
| Link with `onfocus` that calls `blur()` | 16 | 2.1.1 F55, 2.4.7 F55, 3.2.1 F55, 4.1.2 F79 (all 4) | `<a ... onfocus="blur()">` on the 4 nav image links (`nav_home.gif`, `nav_news.gif`, `nav_facts.gif`, `nav_survey.gif`). Home also has it on the 3 teaser headline links, the `morearrow.gif` links, "Click here" and "Read More..." (14 links on home). |
| No visible focus style | 4 | 2.4.7 F78 (all 4) | No `outline` or `:focus` rule in the page CSS. The only focus-ish selector is `#main a:hover, a:active, a:focused, a:visited` (home, news); `:focused` is not a valid pseudo-class, so it matches nothing. Combined with `blur()`, focus is never visible. |
| No skip link before repeated content | 4 | 2.4.1 (all 4) | Zero in-page links (`href="#..."`) inside `#page`; header + nav (QUICKMENU select, 4 nav images) repeat on every page. |
| Visual headings with no heading elements | 4 | 2.4.6 (all 4) | Zero `h1`–`h6` inside `#page`. Headings are `<p class="headline">` (CSS `#main .headline { font: 24px verdana; font-family: impact; color: #41545d }`) or `<font size>`: home 1 `.headline` + 5 `font[size]`, news 8 + 10, tickets 0 + 4, survey 3 + 5. |
| Rollover image swap with no alt | 3 | 1.1.1 F20 (home, news, tickets) | Nav cells: `onmouseover="switchImage('nav_home', './img/nav_home2.gif')"`, `onmouseout` swaps back; the `<img name="nav_home">` has no alt, so nothing is updated. Home also swaps `morearrow.gif` (alt="") on mouseover. |
| Text look-alike characters | 4 | 1.1.1 F71 (all 4) | QUICKMENU `<option>QUICKMENU ----></option>`; survey also has `----->`. A regex for ASCII arrows (`-{2,}>`) in text nodes catches it. |
| Links not distinguished except by color | 2 | 1.4.1 F73 (home, news) | CSS `#main a { color: #226C8E; text-decoration: none }` and the hover/active rule also sets `text-decoration: none`. Inline links in body text (e.g. news "the way that air conditioning works") have no underline in any state. |
| Layout tables using `th` | 2 | 1.3.1 F46 (tickets, survey) | Tickets: an empty `<th>` in the 600px layout table. Survey: question text ("Which is your favorite ci...", "Which city do you find is...", "Do you want to receive a...") in `<th>` inside layout tables, one of them 500px wide. |
| Information image added via CSS | 1 | 1.1.1 F3 (home) | `<div class="image" title="image" style="background: url(./img/panda-sm.jpg) ...">`, same for `oldenburgstudentviolin34.jpg` and `BrainInJar.jpg`. Rule: `div` with `background: url(...)` and a `title` but no text. |

## (b) HTML validator — 4 rows

| Criteria (pages) | What to look for |
|---|---|
| 4.1.1 F70 (all 4) | No duplicate IDs inside `#page`, so the errors are tag/attribute structure. Example from W3C's news annotation: block `<P class=headline>` nested inside inline `<font size="5" color="#FFFFFF">`. Running the Nu HTML Checker on each page is the reliable way to list them; I have not run it. |

## (c) Comparing pages — 4 rows

| Criteria (pages) | Evidence |
|---|---|
| 3.2.4 (all 4) | The same 4 nav images appear on every page (`nav_home`, `nav_news`, `nav_facts`, `nav_survey`) and none has an alt, so the shared nav "is not identified at all" (W3C wording). A cross-page check would flag a repeated component with no accessible name on every page. Note the root cause is already flagged per page as 1.1.1 / 2.4.4 `image-alt` / `link-name`; mapping those to 3.2.4 when the same component repeats across pages may be cheaper than a true page diff. |

## (d) AI on the screenshot / page — 20 rows

| Criteria (pages) | Rows | What the AI has to judge |
|---|---|---|
| 1.3.2 F1, F49, reading order (home F1, F49; news SEQ, F1, F49; tickets F49; survey F49) | 7 | Visual order vs DOM order. Home: three teaser columns read as one run of text (W3C note 06). News: "Your Shout" shows under the violin-case story but comes after "Tough Wahoonie" in the code (note 04). Survey: labels and fields in the wrong order when linearised (note 06). The rest: nested layout tables. Needs screenshot + DOM order compared. |
| 1.4.5 images of text (all 4) | 4 | Header and nav are images of text (`nav_*.gif`, `top_logo.gif`; tickets phone number `telefon_lightgrey_bg.gif`). Recognising text inside an image needs vision. |
| 1.4.3 contrast (news, survey) | 2 | A computed-style check of text colour vs background colour found nothing below 4.5:1 on news, so the failure is likely text on images or in images. Needs the screenshot. |
| 1.4.1 F73 (tickets, survey) | 2 | No inline body-text links on these pages; the only styled links are the 5 footer links (`Copyright`, `W3C`, `MIT`, `ERCIM`, `Keio`, coloured with `<font color>`). Needs a visual check for whether they look like links. |
| 1.4.1 F81 (survey) | 1 | Required fields marked by colour only. All form text computes to `#000`, so the cue is not text colour; probably a coloured background or image. Needs the screenshot. |
| 2.4.4 F63 (tickets, survey) | 2 | Link purpose depends on unrelated context; candidates are the footer links. Needs language judgement, not a scanner rule. |
| 1.3.1 F43 (news) | 1 | Structural markup that doesn't match the content (e.g. styled `<p>` and `<font>` used as headings and list items in the news articles). Needs judgement of what the structure should be. |
| 2.4.3 F85 (survey) | 1 | Focus order of the survey form vs its visual layout (W3C note 06). No `tabindex` on the page, so it's DOM order vs visual order. |

## (e) Not found in page source — 3 rows

| Criteria (pages) | Note |
|---|---|
| 1.1.1 F39 (news, tickets, survey) | W3C's report lists F39 (decorative image with a non-null alt), but no such image is in these pages' `#page`. The only non-empty alts are content images (`top_logo.gif`, `smzappa2.jpg`, `blanca.jpg`, `telefon_lightgrey_bg.gif`). Home's case (`list_bullets.gif` alt="bullet") is real and already matched. Suggest we check with the team whether these three rows should stay in the ground truth. |

## Caveats

- Group choices are judgement calls in a few places: contrast and color-only links can sometimes be computed in code, and fake headings could also be a vision check. The reasons are given per row above.
- Evidence was read from the live W3C pages on Oct 20, 2026.
