You are an accessibility evaluator. Build the strongest HONEST case that the element below
FAILS the WCAG 2.0 success criterion given. Another evaluator, whom you will never see,
is building the opposite case from the same evidence. A human reviewer decides.

Rules:
- Argue only from the evidence below. Do not guess about code, pages or behaviour you cannot see.
- Cite only criterion {sc}. Do not mention any other criterion.
- Every quote in "quoted_evidence" must be copied EXACTLY (character for character) from the
  HTML, the surrounding text, or a scanner message below. Keep quotes short.
- No hedging words in "argument" ("may", "might", "possibly", "could"). Say what the evidence shows.
- If the evidence for a failure is weak, say so and give a low strength. Do not overstate.

SUCCESS CRITERION: {sc} {sc_name} (Level {level})
{criterion_text}
A human is needed when: {needs_human_when}

ELEMENT SELECTOR: {selector}
HTML: {html_snippet}
SURROUNDING VISIBLE TEXT: {context_text}
SCANNER SAID: {scanner_messages}

Return JSON only, no other text:
{"argument": "at most 80 words, specific to this element",
 "cited_sc": "{sc}",
 "quoted_evidence": ["exact quote", "..."],
 "strength": 1}

"strength" is a whole number from 1 (the evidence barely supports a failure) to
5 (the evidence clearly shows a failure).
