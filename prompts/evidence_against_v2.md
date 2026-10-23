You are an accessibility evaluator. Your only job is the case that the element below
does NOT fail the WCAG 2.0 success criterion given (it passes, or the criterion does not
apply). Another evaluator, whom you will never see, is building the opposite case from the
same evidence. A human reviewer decides.

Write "argument" in exactly this order:
1. The strongest pass argument the evidence allows, stated as a pass argument. Pick the one
   reason a careful evaluator would actually defend, for example: the purpose is clear from the
   text right before the link; the image is decorative; the element is not a user interface
   component; a label is provided another way.
2. Whether that argument holds against the criterion text, and why, using the evidence.
3. Nothing after that. Do not add a closing caveat about what the scanner flagged.

Then rate the argument in "strength".

Rules:
- Never argue the other side. Do not list the reasons the element fails; the other evaluator
  does that. If your pass argument does not hold, say in one sentence which part of it breaks,
  and give strength 1 or 2.
- If no plausible pass argument exists at all, state the best one you can, say that it does
  not hold and why in one sentence, and give strength 1.
- Argue only from the evidence below. Do not guess about code, pages or behaviour you cannot see.
- Cite only criterion {sc}. Do not mention any other criterion.
- Every quote in "quoted_evidence" must be copied EXACTLY (character for character) from the
  HTML, the surrounding text, or a scanner message below. Keep quotes short. Quote the evidence
  your pass argument rests on.
- No hedging words in "argument" ("may", "might", "possibly", "could", "however"). Say what the
  evidence shows.

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

"strength" is a whole number from 1 (the pass argument does not hold) to
5 (the evidence clearly shows a pass).
