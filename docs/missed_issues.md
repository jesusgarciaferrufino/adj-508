# Missed issues: how we could catch them

Covers the 71 rows in `data/ground-truth/ground_truth.csv` (#23) with `finding_id = missed`, out of 130 rows (59 matched today).

Evidence was checked on Oct 20, 2026 against the four W3C Before pages (`before/{home,news,tickets,survey}.html`, inside `#page`), their inline `<style>` blocks and `meta.css`, W3C's annotated versions of the pages, the rendered pages in Chrome (computed colours and link styles), the Nu HTML Checker, and `data/findings/before_*.json` on main.

Confidence: **H** = the element is named in W3C's annotations or is unambiguous in the source; **M** = clear in the source but W3C doesn't point at it; **L** = best guess.

## Summary

| Group | Rows |
|---|---|
| Already caught: GT correction | 2 |
| (a) Code rule on HTML/CSS | 36 |
| (b) HTML validator | 4 |
| (c) Comparing pages | 4 |
| (d) AI on the screenshot | 13 |
| (e) No instance found on the served page | 12 |
| **Total** | **71** |

Two things change the ground truth itself, so they should go into #23 alongside Daniel's spot-check:

1. **Two rows are already caught.** home 1.1.1 F20 is `F-before-home-el0217-1.1.1` and tickets 1.1.1 F71 is `F-before-tickets-el0165-1.1.1` (details in the table). That takes matched rows from 59 to 61.
2. **12 rows have no instance on the served page** (group e). W3C's reports repeat some failures across pages; for these rows nothing in the page source or the rendered page matches. The team should decide whether to keep them, drop them, or keep them with a note. Dropping them shrinks the denominator.

## (a) Code rules, ranked by rows covered

| Rule | Rows | Criteria (pages) |
|---|---|---|
| Link with `onfocus` calling `blur()` | 20 | 2.1.1 F55 (all 4); 2.4.7 F55 (all 4); 2.4.7 F78 (all 4); 3.2.1 F55 (all 4); 4.1.2 F79 (all 4) |
| No in-page link before repeated blocks | 4 | 2.4.1 (all 4) |
| Page has heading-like text but no `h1`-`h6` | 4 | 2.4.6 (all 4) |
| ASCII look-alikes in text nodes (`-{2,}>`) | 3 | 1.1.1 F71 (home, news, survey) |
| Inline link with no underline in any state | 2 | 1.4.1 F73 (home, news) |
| Layout table containing `th` | 2 | 1.3.1 F46 (tickets, survey) |
| Image conveyed via CSS background with no text | 1 | 1.1.1 F3 (home) |

## (b) Validator note

On every page `<NOSCRIPT><B><FONT>` sits in `<head>`, which ends the head early (stray `</noscript>`, `</head>`, second `<body>`). The validator stops at that point, so the per-page errors below come from re-running it with that `<NOSCRIPT>` removed. Only parse errors (tags, nesting, attributes) are listed; the many obsolete-element warnings are HTML5 conformance, not WCAG 2.0 4.1.1.

## Every missed row

| # | Page | WCAG | Cause | Group | How to catch | Exact element / CSS | Conf. |
|---|---|---|---|---|---|---|---|
| 1 | home | 1.1.1 | F20 | fix | Already found: pa11y G94 notice | `<img src="telefon_white_bg.gif" alt="1234 56789">`, outdated/placeholder alt (W3C note 10). Matches `F-before-home-el0217-1.1.1` | H |
| 2 | home | 1.1.1 | F3 | a | Image conveyed via CSS background with no text | `<div class="image" title="image" style="background: url(./img/BrainInJar.jpg) ...">`, also `panda-sm.jpg`, `oldenburgstudentviolin34.jpg` (W3C note 04) | H |
| 3 | home | 1.1.1 | F71 | a | ASCII look-alikes in text nodes (`-{2,}>`) | `<option>QUICKMENU ----></option>` in the header select. Not annotated by W3C; best candidate | M |
| 4 | home | 1.3.2 | F1 | d | Compare visual order with DOM order | Three teaser columns built with CSS `float: left` on `.newsheadline`, `.image`, `.story`; read as one run of text (W3C note 06) | H |
| 5 | home | 1.3.2 | F49 | d | Compare visual order with DOM order | Nested layout tables for the whole page (header, nav column, content); not annotated per element | M |
| 6 | home | 1.4.1 | F73 | a | Inline link with no underline in any state | CSS `#main * { text-decoration: none }` + `#main a { color: #226C8E }`; inline links "Click here" (x2), "Read More..." (x2) differ from text only by colour (3.6:1), no hover/focus cue | H |
| 7 | home | 1.4.5 | IMGTXT | d | Detect text inside images | `nav_*.gif` (4), `top_logo.gif`, `telefon_white_bg.gif` | H |
| 8 | home | 2.1.1 | F55 | a | Link with `onfocus` calling `blur()` | `<a ... onfocus="blur()">` on 14 links: the 4 nav image links, 3 teaser headlines, 3 `morearrow.gif` links, 2 "Click here", 2 "Read More..." | H |
| 9 | home | 2.4.1 | BYPASS | a | No in-page link before repeated blocks | Zero `href="#..."` links and zero `a[name]` targets inside `#page`; header, QUICKMENU and nav repeat on every page | H |
| 10 | home | 2.4.6 | HEAD | a | Page has heading-like text but no `h1`-`h6` | Zero `h1`-`h6` in `#page`; `.headline` "Welcome to CityLights", `.subheadline` "Elsewhere on the Web" | H |
| 11 | home | 2.4.7 | F55 | a | Link with `onfocus` calling `blur()` | `<a ... onfocus="blur()">` on 14 links: the 4 nav image links, 3 teaser headlines, 3 `morearrow.gif` links, 2 "Click here", 2 "Read More..." | H |
| 12 | home | 2.4.7 | F78 | a | Link with `onfocus` calling `blur()` (same detector) | No `outline` or `:focus` rule in the page CSS or `meta.css`; the indicator is removed only by `blur()` | M |
| 13 | home | 3.2.1 | F55 | a | Link with `onfocus` calling `blur()` | `<a ... onfocus="blur()">` on 14 links: the 4 nav image links, 3 teaser headlines, 3 `morearrow.gif` links, 2 "Click here", 2 "Read More..." | H |
| 14 | home | 3.2.4 | CONSIST | c | Compare repeated components across pages | Nav markup is identical on all 4 pages (`td#home/news/tickets/survey` > `img name=nav_*`), and none of the images has an alt, so the shared nav "is not identified at all" (W3C wording) | H |
| 15 | home | 4.1.1 | F70 | b | Nu HTML Checker, parse errors only | unclosed `<FONT>` in `td#WEATHER` (L215), `</TD>` with open elements (L227), stray `</TR>` (L362), cell spanning past its row group (L305) | H |
| 16 | home | 4.1.2 | F79 | a | Link with `onfocus` calling `blur()` | `<a ... onfocus="blur()">` on 14 links: the 4 nav image links, 3 teaser headlines, 3 `morearrow.gif` links, 2 "Click here", 2 "Read More..." | H |
| 17 | news | 1.1.1 | F20 | e | - | Not annotated by W3C. Not the nav rollovers: survey has the same `switchImage` rollovers and no F20 row. Only candidates: `blanca.jpg`, `smzappa2.jpg` alts | L |
| 18 | news | 1.1.1 | F39 | e | - | No decorative image with a non-empty alt in `#page`; the only non-empty alts are content images (`top_logo.gif`, `smzappa2.jpg`, `blanca.jpg`). Home's real case (`list_bullets.gif` alt="bullet") is already matched | M |
| 19 | news | 1.1.1 | F71 | a | ASCII look-alikes in text nodes (`-{2,}>`) | `<option>QUICKMENU ----></option>` in the header select. Not annotated by W3C; best candidate | M |
| 20 | news | 1.3.1 | F43 | d | Judge whether markup matches the content structure | Not annotated as F43. Candidates: italic `<span style="font-style: italic">` used as emphasis (note 07), `<p align=center><font size=3>Return To Sender</font>` (note 08) | L |
| 21 | news | 1.3.2 | F1 | d | Compare visual order with DOM order | 11 inline-styled `<div style="float:left/right; width:48%">` columns | M |
| 22 | news | 1.3.2 | F49 | d | Compare visual order with DOM order | Nested layout tables for the whole page (header, nav column, content); not annotated per element | M |
| 23 | news | 1.3.2 | SEQ | d | Compare visual order with DOM order | "Your Shout" shows under the violin-case story but comes after "Tough Wahoonie" in the code (W3C note 04) | H |
| 24 | news | 1.4.1 | F73 | a | Inline link with no underline in any state | CSS `#main a { color: #226C8E; text-decoration: none }` and the hover/active rule also `text-decoration: none`; inline links "the way that air conditioning works" (W3C note 06), "trombone forgery debacle" | H |
| 25 | news | 1.4.3 | CR | e | - | A computed check of every text node (colour vs resolved background, 4.5:1 or 3:1 for large text) found nothing failing, and no text sits on a background image. Possibly text inside images | L |
| 26 | news | 1.4.5 | IMGTXT | d | Detect text inside images | `nav_*.gif` (4), `top_logo.gif` | H |
| 27 | news | 2.1.1 | F55 | a | Link with `onfocus` calling `blur()` | `<a ... onfocus="blur()">` on the 4 nav image links (`nav_home/news/facts/survey.gif`) | H |
| 28 | news | 2.4.1 | BYPASS | a | No in-page link before repeated blocks | Zero `href="#..."` links and zero `a[name]` targets inside `#page`; header, QUICKMENU and nav repeat on every page | H |
| 29 | news | 2.4.6 | HEAD | a | Page has heading-like text but no `h1`-`h6` | Zero `h1`-`h6` in `#page`; 8 `p.headline` (e.g. "Heat wave linked to temperatures", "Your Shout") plus `<font size=5/7>` | H |
| 30 | news | 2.4.7 | F55 | a | Link with `onfocus` calling `blur()` | `<a ... onfocus="blur()">` on the 4 nav image links (`nav_home/news/facts/survey.gif`) | H |
| 31 | news | 2.4.7 | F78 | a | Link with `onfocus` calling `blur()` (same detector) | No `outline` or `:focus` rule in the page CSS or `meta.css`; the indicator is removed only by `blur()`. The CSS's only focus-like selector is `a:focused`, which is invalid and matches nothing | M |
| 32 | news | 3.2.1 | F55 | a | Link with `onfocus` calling `blur()` | `<a ... onfocus="blur()">` on the 4 nav image links (`nav_home/news/facts/survey.gif`) | H |
| 33 | news | 3.2.4 | CONSIST | c | Compare repeated components across pages | Nav markup is identical on all 4 pages (`td#home/news/tickets/survey` > `img name=nav_*`), and none of the images has an alt, so the shared nav "is not identified at all" (W3C wording) | H |
| 34 | news | 4.1.1 | F70 | b | Nu HTML Checker, parse errors only | unclosed `<FONT>` in `td#WEATHER` (L117), `</TD>` with open elements (L129), `<TD>` start tag in table body (L202), `<p class=headline>` directly in `<table>` (L210; validator stops here, so the list is partial) | H |
| 35 | news | 4.1.2 | F79 | a | Link with `onfocus` calling `blur()` | `<a ... onfocus="blur()">` on the 4 nav image links (`nav_home/news/facts/survey.gif`) | H |
| 36 | tickets | 1.1.1 | F20 | e | - | Not annotated by W3C. Not the nav rollovers (same reason as news). Only candidate: `telefon_lightgrey_bg.gif` alt="music line phone number" (x2) | L |
| 37 | tickets | 1.1.1 | F39 | e | - | No decorative image with a non-empty alt in `#page`; the only non-empty alts are content images (`top_logo.gif`, `telefon_lightgrey_bg.gif`) | M |
| 38 | tickets | 1.1.1 | F71 | fix | Already found: axe image-alt violation | `<img src="headline_ticket_offers.gif">` with no alt, image of text used as a heading; W3C note 01 cites F71 for it. Matches `F-before-tickets-el0165-1.1.1` | M |
| 39 | tickets | 1.3.1 | F46 | a | Layout table containing `th` | One-row 600px table whose only cell is `<th style="padding-bottom:10px"><img src="headline_ticket_prices.gif"></th>` | H |
| 40 | tickets | 1.3.2 | F49 | d | Compare visual order with DOM order | Nested layout tables for the whole page (header, nav column, content); not annotated per element | M |
| 41 | tickets | 1.4.1 | F73 | e | - | No links in body text. The only links are the nav images and the footer links, which render underlined | M |
| 42 | tickets | 1.4.5 | IMGTXT | d | Detect text inside images | `headline_ticket_offers.gif`, `headline_ticket_prices.gif`, `telefon_lightgrey_bg.gif` (W3C notes 01, 04), `nav_*.gif` | H |
| 43 | tickets | 2.1.1 | F55 | a | Link with `onfocus` calling `blur()` | `<a ... onfocus="blur()">` on the 4 nav image links (`nav_home/news/facts/survey.gif`) | H |
| 44 | tickets | 2.4.1 | BYPASS | a | No in-page link before repeated blocks | Zero `href="#..."` links and zero `a[name]` targets inside `#page`; header, QUICKMENU and nav repeat on every page | H |
| 45 | tickets | 2.4.4 | F63 | e | - | No links in body text; only nav image links and the footer (Copyright, W3C, MIT, ERCIM, Keio) | M |
| 46 | tickets | 2.4.6 | HEAD | a | Page has heading-like text but no `h1`-`h6` | Zero `h1`-`h6` in `#page`; headings are images (`headline_ticket_offers.gif`, `headline_ticket_prices.gif`) plus `<font size=4>` "Citylights Terms and Conditions" | H |
| 47 | tickets | 2.4.7 | F55 | a | Link with `onfocus` calling `blur()` | `<a ... onfocus="blur()">` on the 4 nav image links (`nav_home/news/facts/survey.gif`) | H |
| 48 | tickets | 2.4.7 | F78 | a | Link with `onfocus` calling `blur()` (same detector) | No `outline` or `:focus` rule in the page CSS or `meta.css`; the indicator is removed only by `blur()` | M |
| 49 | tickets | 3.2.1 | F55 | a | Link with `onfocus` calling `blur()` | `<a ... onfocus="blur()">` on the 4 nav image links (`nav_home/news/facts/survey.gif`) | H |
| 50 | tickets | 3.2.4 | CONSIST | c | Compare repeated components across pages | Nav markup is identical on all 4 pages (`td#home/news/tickets/survey` > `img name=nav_*`), and none of the images has an alt, so the shared nav "is not identified at all" (W3C wording) | H |
| 51 | tickets | 4.1.1 | F70 | b | Nu HTML Checker, parse errors only | unclosed `<FONT>` in `td#WEATHER` (L112), `</TD>` with open elements (L124), `<td>` start tag in table body (L192), stray `</TR>` (L290) | H |
| 52 | tickets | 4.1.2 | F79 | a | Link with `onfocus` calling `blur()` | `<a ... onfocus="blur()">` on the 4 nav image links (`nav_home/news/facts/survey.gif`) | H |
| 53 | survey | 1.1.1 | F39 | e | - | No decorative image with a non-empty alt in `#page`; the only non-empty alt is `top_logo.gif`, and the 25 `gif.gif` spacers correctly use alt="" | M |
| 54 | survey | 1.1.1 | F71 | a | ASCII look-alikes in text nodes (`-{2,}>`) | `<option>QUICKMENU ----></option>` and `<option>select a city -----></option>` | M |
| 55 | survey | 1.3.1 | F46 | a | Layout table containing `th` | Question text in `<th>` inside the form's layout tables: "Which is your favorite city park" (500px table with 4 nested tables), "Which city do you find is the greenest", "Do you want to receive a free newsletter" | H |
| 56 | survey | 1.3.2 | F49 | d | Compare visual order with DOM order | Form labels and fields linearise in the wrong order: Name: [radio] Mr. [radio] Mrs. [text] [text] [text] eMail Address ... (W3C note 06) | H |
| 57 | survey | 1.4.1 | F73 | e | - | No links in body text. The only links are the nav images and the footer links, which render underlined | M |
| 58 | survey | 1.4.1 | F81 | e | - | No colour cue on the served form: all text computes `#000`, no asterisks, inputs styled alike. The form posts to `survey.php`; the cue may only appear on the error page after submitting, which we never scan | L |
| 59 | survey | 1.4.3 | CR | e | - | A computed check of every text node (colour vs resolved background, 4.5:1 or 3:1 for large text) found nothing failing, and no text sits on a background image. Possibly text inside images | L |
| 60 | survey | 1.4.5 | IMGTXT | d | Detect text inside images | `nav_*.gif` (4), `top_logo.gif` | H |
| 61 | survey | 2.1.1 | F55 | a | Link with `onfocus` calling `blur()` | `<a ... onfocus="blur()">` on the 4 nav image links (`nav_home/news/facts/survey.gif`) | H |
| 62 | survey | 2.4.1 | BYPASS | a | No in-page link before repeated blocks | Zero `href="#..."` links and zero `a[name]` targets inside `#page`; header, QUICKMENU and nav repeat on every page | H |
| 63 | survey | 2.4.3 | F85 | d | Compare focus order with visual layout | No `tabindex` on the page; DOM order of the newsletter form differs from its visual layout (W3C note 06, cites 2.4.3) | M |
| 64 | survey | 2.4.4 | F63 | e | - | No links in body text; only nav image links and the footer (Copyright, W3C, MIT, ERCIM, Keio) | M |
| 65 | survey | 2.4.6 | HEAD | a | Page has heading-like text but no `h1`-`h6` | Zero `h1`-`h6` in `#page`; 3 `.headline` ("Citylights Survey", "This Week's Survey...", "Last Weeks's Survey Results") | H |
| 66 | survey | 2.4.7 | F55 | a | Link with `onfocus` calling `blur()` | `<a ... onfocus="blur()">` on the 4 nav image links (`nav_home/news/facts/survey.gif`) | H |
| 67 | survey | 2.4.7 | F78 | a | Link with `onfocus` calling `blur()` (same detector) | No `outline` or `:focus` rule in the page CSS or `meta.css`; the indicator is removed only by `blur()` | M |
| 68 | survey | 3.2.1 | F55 | a | Link with `onfocus` calling `blur()` | `<a ... onfocus="blur()">` on the 4 nav image links (`nav_home/news/facts/survey.gif`) | H |
| 69 | survey | 3.2.4 | CONSIST | c | Compare repeated components across pages | Nav markup is identical on all 4 pages (`td#home/news/tickets/survey` > `img name=nav_*`), and none of the images has an alt, so the shared nav "is not identified at all" (W3C wording) | H |
| 70 | survey | 4.1.1 | F70 | b | Nu HTML Checker, parse errors only | unclosed `<FONT>` in `td#WEATHER` (L118), `</TD>` with open elements (L130), no space between attributes `<div id="data"style=...>` (L541), stray `</TR>` (L591) | H |
| 71 | survey | 4.1.2 | F79 | a | Link with `onfocus` calling `blur()` | `<a ... onfocus="blur()">` on the 4 nav image links (`nav_home/news/facts/survey.gif`) | H |

## What this means for 90%

90% of 130 rows is 117. Cumulative best case, if every new finding is counted as an issue:

| Step | Caught | Recall |
|---|---|---|
| Today | 59 | 45% |
| + GT corrections | 61 | 47% |
| + (a) code rules | 97 | 75% |
| + (b) validator | 101 | 78% |
| + (c) page comparison | 105 | 81% |
| + (d) AI on screenshot | 118 | 91% |

Group (e) (12 rows) can't be caught by any detector until we know what W3C meant. If the team drops them, the denominator becomes 118 and the same steps reach 100% (every remaining row); without group (d) it would be 89%.

These are upper bounds: a finding only counts toward issue-level recall if the router reports it as an issue (machine, or grey with `likely_fail`).

