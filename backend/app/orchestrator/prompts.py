"""Prompt template rendering with {{variable}} substitution."""
from __future__ import annotations

import re

_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def render(template: str, context: dict) -> str:
    """Replace ``{{var}}`` tokens; unknown vars become empty strings."""
    def repl(m: re.Match) -> str:
        return str(context.get(m.group(1), ""))

    return _VAR_RE.sub(repl, template)


def extract_variables(template: str) -> list[str]:
    return sorted(set(_VAR_RE.findall(template)))


# ---------------------------------------------------------------------------
# Council stage prompts
# ---------------------------------------------------------------------------

CRITIQUE_INSTRUCTION = """\
You are participating in a multi-model review of the question below.

================ QUESTION ================
{{question}}
{{context_block}}
================ INDEPENDENT ANSWERS ================
{{solutions}}

Your task: review ALL answers (including your own earlier one, labeled "YOURS").
Identify specifically:
1. Strengths worth preserving
2. Weaknesses, errors and omissions
3. Contradictions between answers (who is right and why)
4. Unsupported or questionable assumptions
5. Concrete improvements

Write your critique as tight bullet points keyed to each contributor. \
Do not rewrite a full solution yet."""

REVISION_INSTRUCTION = """\
Revise your earlier answer using the critiques below.

================ QUESTION ================
{{question}}
{{context_block}}
================ YOUR ORIGINAL ANSWER ================
{{own_solution}}

================ CRITIQUES ================
{{critiques}}

Produce your improved final answer. Keep what was correct, fix what was flagged, \
and briefly note what you changed and why."""

SYNTHESIS_INSTRUCTION = """\
You are the final SYNTHESIZER for a multi-model council.

================ QUESTION ================
{{question}}
{{context_block}}
================ CONTRIBUTOR ANSWERS ================
{{solutions}}
{{critiques_block}}
================ TASK ================
Combine the strongest ideas into ONE coherent, decisive final answer.
- Resolve contradictions explicitly with reasoning.
- Preserve important caveats and uncertainty rather than flattening them.
- Attribute key ideas inline as [Contributor: <label>].
- Structure for clarity; end with a "Bottom line" section.
Do not merely concatenate — synthesize."""

JUDGE_INSTRUCTION = """\
Rank these candidate answers for the question below.

QUESTION:
{{question}}

ANSWERS:
{{solutions}}

Score each 0-10 on correctness, completeness, rigor, practicality and clarity; \
give a one-paragraph verdict; name the winner."""
