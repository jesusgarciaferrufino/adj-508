# AGAINST prompt comparison: `evidence_against_v1` vs `evidence_against_v2`, 2026-10-23

Model `us.anthropic.claude-sonnet-4-6`, temperature 0. FOR (`evidence_for_v1`) ran once per finding and both AGAINST prompts saw the same evidence. Made by `python -m scripts.compare_against --write`.

| Finding | Known answer | Recommendation old -> new | Citations verified old -> new | No worse |
| --- | --- | --- | --- | --- |
| `F-before-home-el0210-2.4.4` | fail | likely_fail -> likely_fail | True -> True | yes |
| `F-before-home-el0194-2.1.1` | none (not in ground truth) | unsure -> unsure | False -> False | yes |
| `F-before-home-el0231-1.3.1` | none (not in ground truth) | unsure -> unsure | True -> False | NO |
| `F-after-home-el0062-4.1.2` | pass | unsure -> unsure | True -> True | yes |

**Automatic checks: worse on at least one finding. Keep `evidence_against_v1`.**

## `F-before-home-el0210-2.4.4` (criterion 2.4.4)

- No problems found by the checks.

**FOR**

> The link text is 'Click here', which conveys no destination or purpose on its own. The surrounding context lists multiple topics ('Killer bees', 'Onions') each followed by a separate 'Click here' link, making the programmatically determined context ambiguous — a user cannot determine which 'Click here' leads where. The scanner flagged this link specifically for failing to identify its purpose through text or context.
> Quoted: "Click here"; "Killer bees. Click here. Onions. Click here."; "Check that the link text combined with programmatically determined link context identifies the purpose of the link"
> Strength: 4/5

**AGAINST, `evidence_against_v1`**

> The link text 'Click here' is generic and provides no indication of its destination or purpose. The surrounding text lists 'Killer bees. Click here. Onions. Click here.' — two consecutive 'Click here' links with no programmatically associated context distinguishing them. Neither the link text alone nor its programmatic context identifies the purpose of this specific link. No honest case for a pass exists.
> Quoted: "Click here"; "Killer bees. Click here. Onions. Click here."
> Strength: 1/5

**AGAINST, `evidence_against_v2`**

> The surrounding visible text lists 'Killer bees. Click here. Onions. Click here.' The link in question is the second 'Click here,' which follows 'Onions.' A reader scanning the paragraph encounters 'Onions' immediately before this link, providing programmatically determined link context. However, the list structure uses plain text separators, not semantic markup, so 'Onions' is not a programmatically associated context—it is merely adjacent visible text with no structural relationship to the link.
> Quoted: "Onions. Click here."; "Click here"
> Strength: 2/5

## `F-before-home-el0194-2.1.1` (criterion 2.1.1)

- No problems found by the checks.

**FOR**

> The img element uses 'onmouseover' and 'onmouseout' event handlers to swap image sources, providing visual feedback exclusively through mouse interaction. No equivalent 'onfocus' or 'onblur' keyboard events are present. The scanner confirms this functionality is unavailable via keyboard. This means the interactive state change triggered by mousing over this element cannot be replicated through keyboard operation, violating 2.1.1.
> Quoted: "onmouseover="this.src='./img/morearrow_a.gif'""; "onmouseout="this.src='./img/morearrow.gif'""; "Ensure the functionality provided by mousing over this element is available through the keyboard; for instance, using the focus event."; "Ensure the functionality provided by mousing out of this element is available through the keyboard; for instance, using the blur event."
> Strength: 2/5

**AGAINST, `evidence_against_v1`**

> The onmouseover/onmouseout handlers only swap the image source between two arrow GIF files, producing a purely visual hover effect. This is a decorative image inside an anchor element. The underlying functionality — activating the link — is fully operable via keyboard through the parent anchor. The image swap is a visual embellishment with no functional consequence; losing it on keyboard focus causes no loss of functionality as defined by 2.1.1.
> Quoted: "onmouseover="this.src='./img/morearrow_a.gif'""; "onmouseout="this.src='./img/morearrow.gif'""; "alt="""; "span > a > img"
> Strength: 3/5

**AGAINST, `evidence_against_v2`**

> The img element is nested inside an anchor element ('span > a > img'). The anchor is the interactive control, and it is keyboard operable by default. The onmouseover/onmouseout handlers change only the image source as a visual hover effect; the underlying function—activating the link—does not depend on path of movement. The image swap is purely decorative feedback, not a distinct functionality that must be separately keyboard accessible under 2.1.1.
> Quoted: "span > a > img"; "onmouseover="this.src='./img/morearrow_a.gif'""; "onmouseout="this.src='./img/morearrow.gif'""
> Strength: 3/5

## `F-before-home-el0231-1.3.1` (criterion 1.3.1)

- citations verified with the old prompt but not with the new one

**FOR**

> The text 'Free Penguins' is wrapped in a <b> tag used purely for visual bold styling, not a semantic element like <strong> or a heading tag. The scanner confirms 'Semantic markup should be used to mark emphasised or special text so that it can be programmatically determined.' The structural role or emphasis of this text cannot be programmatically determined from a presentational <b> element alone, violating 1.3.1.
> Quoted: "Semantic markup should be used to mark emphasised or special text so that it can be programmatically determined"; "<b>Free Penguins</b>"
> Strength: 2/5

**AGAINST, `evidence_against_v1`**

> The scanner flags <b> as lacking semantic emphasis, but 1.3.1 requires that information and relationships conveyed through presentation be programmatically determinable. The text 'Free Penguins' appears to function as a label or heading, not as stressed emphasis. <b> does convey bold presentation programmatically; the question is whether the structural role is lost. However, no honest case exists that a bare <b> inside nested layout tables adequately exposes structure under 1.3.1.
> Quoted: "<b>Free Penguins</b>"; "Semantic markup should be used to mark emphasised or special text so that it can be programmatically determined"
> Strength: 1/5

**AGAINST, `evidence_against_v2`**

> The element <b>Free Penguins</b> is used for visual bold styling, not to convey semantic emphasis or a structural relationship. Under 1.3.1, the requirement is that information, structure, and relationships conveyed through presentation be programmatically determinable. Bold styling here presents no structural role—no heading, list, table relationship, or emphasis meaning—so there is no information or relationship that needs to be programmatically determined beyond what the visible text itself already provides.
> Quoted: "<b>Free Penguins</b>"; "Free Penguins"; "Information, structure, and relationships conveyed through presentation can be programmatically determined or are available in text"
> Strength: 3/5

## `F-after-home-el0062-4.1.2` (criterion 4.1.2)

- No problems found by the checks.

**FOR**

> The anchor element lacks an href, id, and name attribute. Without href, the element has no defined role as an interactive link—it functions as a placeholder anchor with no programmatically determinable role. WCAG 4.1.2 requires that role be programmatically determined for user interface components. The scanner confirms this structural deficiency. Assistive technologies cannot expose this as an actionable link because the element lacks the attributes that establish its role and purpose.
> Quoted: "<a><img src="./img/toplogo.png" alt="Citylights: your access to the city."></a>"; "Anchor element found with link content, but no href, ID or name attribute has been supplied."
> Strength: 3/5

**AGAINST, `evidence_against_v1`**

> The anchor element contains an image with a descriptive alt attribute, providing an accessible name: 'Citylights: your access to the city.' However, the element lacks an href, id, or name attribute, meaning it has no defined role as a link or anchor. Without href, it is not an interactive control, so 4.1.2 does not apply to it as a user interface component. A non-interactive anchor is not a UI component requiring name/role exposure under 4.1.2.
> Quoted: "no href, ID or name attribute has been supplied"; "alt="Citylights: your access to the city.""
> Strength: 2/5

**AGAINST, `evidence_against_v2`**

> The anchor element contains an img with a descriptive alt attribute: "Citylights: your access to the city." This provides a programmatically determinable name for the element. However, the element lacks an href, id, or name attribute, meaning it is not a focusable or interactive user interface component. An anchor without href is not a link in the interactive sense and therefore 4.1.2 does not apply to it as a UI component.
> Quoted: "<a><img src="./img/toplogo.png" alt="Citylights: your access to the city.">"; "Anchor element found with link content, but no href, ID or name attribute has been supplied"
> Strength: 3/5
