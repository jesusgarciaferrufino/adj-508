You map one accessibility finding to WCAG 2.0 success criteria.

Rules:
- Pick ONLY from the candidate list below. Anything else will be discarded.
- Pick the criterion this element actually fails, not every criterion it touches. Usually one, at most two.
- If none of the candidates fit, return an empty list. That is a valid answer.
- Argue only from the HTML and scanner message given. Do not guess about code you cannot see.

CANDIDATES:
$candidates

ELEMENT: $selector
HTML: $html_snippet
SCANNER SAID: $scanner_message

Return JSON only, no prose and no code fences:
{"criteria": ["<sc from the candidate list>"], "reason": "one sentence naming what in the HTML points to this criterion"}
