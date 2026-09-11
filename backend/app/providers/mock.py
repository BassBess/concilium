"""Built-in deterministic MOCK provider.

It performs NO network access and NEVER pretends to be a remote model. It makes
the entire application — councils, debates, workflows, failover, dashboards —
fully exercisable without credentials, and powers automated tests.

Failure injection is configured via instance settings:

    {"rate_limit_every": 3}   -> every 3rd call raises RateLimitError
    {"fail_every": 2}         -> every 2nd call raises a retryable error
    {"force_rate_limit": true}-> ALL calls rate-limit until flipped back
    {"mock_delay_ms": 25}     -> simulated latency / streaming pace
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from .base import (
    ChatMessage,
    GenerateOptions,
    GenerationResult,
    ModelInfo,
    ProviderAdapter,
    ProviderUnavailableError,
    RateLimitError,
    ToolCall,
    Usage,
    now_ms,
)
from .registry import register_provider

ROLE_HINTS = {
    "mock-coder": (
        "Here is a concrete implementation, with the tricky parts handled:\n\n```python\n"
        "# example implementation\n"
        "def solve(data):\n"
        "    # 1. validate  2. transform  3. return\n"
        "    return data\n```\nI also added input validation and a docstring."
    ),
    "mock-reasoner": (
        "Let me work through this step by step.\n"
        "1) Define the precise question.\n2) List constraints.\n"
        "3) Eliminate inconsistent options.\n4) Verify the survivor.\n"
        "Conclusion: the answer follows from premises 1 and 3, and the edge case "
        "to watch is the empty/zero boundary condition."
    ),
    "mock-vision": "Based on the image content described in the prompt, the visible "
                   "elements and their spatial relationships support the following interpretation "
                   "(note: the mock model cannot actually see pixels; use a vision-capable remote/local model).",
    "mock-longcontext": "I considered the full long-context material (the mock model "
                        "accepts up to 1M tokens by metadata) and cross-referenced every section; "
                        "the relevant passages are consistent on the main point.",
    "mock-flaky": "Occasionally I succeed; by design I sometimes fail to let you watch failover work.",
    "mock-general": "Here is my independent take: the goal is clear, so I would (a) state the "
                    "criteria, (b) propose the strongest option with a concrete first step, and "
                    "(c) name the main risk and how to mitigate it.",
}


@register_provider("mock")
class MockAdapter(ProviderAdapter):
    display_name = "Mock models (offline, built-in)"
    kind = "local"
    docs_url = ""
    secret_fields: tuple[str, ...] = ()
    setting_fields: tuple[str, ...] = ("mock_delay_ms", "rate_limit_every", "fail_every")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._call_count: dict[str, int] = {}
        # Runtime test hooks
        self.force_rate_limit: bool = bool(self.settings.get("force_rate_limit"))
        self.force_error: bool = False
        self.rate_limited_models: set[str] = set(self.settings.get("rate_limited_models", []))
        self._fail_once_done: set[str] = set()

    def is_configured(self) -> bool:
        return True

    async def list_models(self) -> list[ModelInfo]:
        return await super().list_models()

    async def health_check(self):
        return {"status": "ok", "models": len(await self.list_models()), "detail": "built-in offline provider"}

    def _last_user_text(self, messages: list[ChatMessage]) -> str:
        for m in reversed(messages):
            if m.role == "user" and m.content:
                return m.content
        return messages[-1].content if messages else ""

    def _system_text(self, messages: list[ChatMessage]) -> str:
        return "\n".join(m.content for m in messages if m.role == "system")

    @staticmethod
    def _extract_question(user_text: str) -> str:
        """Pull the actual question out of stage scaffolding when present."""
        import re

        m = re.search(r"QUESTION[:\s=]*\n?(.+?)(?:={5,}|UPSTREAM|ANSWERS?[:\s=]*-|$)",
                      user_text, re.DOTALL | re.IGNORECASE)
        q = (m.group(1) if m else user_text).strip()
        return q[:400]

    def _stage_body(self, model: str, system: str, user: str, options: GenerateOptions) -> str:
        s = (system + "\n" + user)
        q = self._extract_question(user)
        n_contrib = max(1, user.count("Contributor:"))
        if "SYNTHESIZER" in s.upper() or "final answer" in s.lower():
            return (
                "The contributors converged on three points: (1) clarify the goal and constraints "
                "first, (2) prefer the simplest option that meets them, (3) verify before scaling.\n\n"
                "Points of disagreement were about timing and trade-offs; the weight of evidence favors "
                "a small, reversible first step that can be revisited as data arrives. "
                f"[Contributor: Researcher] supplied the framing; [Contributor: Critic] surfaced the main "
                "risk; [Contributor: Programmer] made it concrete.\n\n"
                f"Applied to: \"{q[:200]}\"\n\n"
                "## Bottom line\nStart with the smallest reversible version, measure, then expand. "
                "The biggest risk is premature complexity; a quick spike reduces it cheaply."
            )
        if "CRITIC" in system.upper() and "review ALL answers" in user:
            return (
                f"- Contributor 1: strong structure, but assumes the problem stays small — flag the "
                "scaling assumption; add a concrete trigger for revisiting the decision.\n"
                "- Contributor 2: the recommendation is practical but its cost claim is unsupported — "
                "needs a number or a citation.\n"
                "- Contributor 3 (YOURS): correct but omits the failure mode where the simple approach "
                "stops working; name the metric that would warn you.\n"
                "Improvement: merge 1's framing with 2's pragmatism and attach an explicit verification step."
            )
        if "Revise your earlier answer" in user:
            return (
                "Revised answer: I keep the core recommendation but now (a) state the scaling assumption "
                "explicitly, (b) add the trigger condition for revisiting the decision, and (c) attach a "
                "verifiable first milestone. Changes came directly from critiques 1 and 3.\n\n"
                f"Recommendation for \"{q[:200]}\": take the smallest reversible step now, with a named "
                "metric and review date."
            )
        if "JUDGE" in system.upper() or "Rank these candidate" in user:
            return (
                "Scoring (correctness/completeness/rigor/practicality/clarity):\n"
                "- Answer 1: 8/7/7/9/8 — most actionable, minor assumption gaps.\n"
                "- Answer 2: 7/8/8/6/7 — most rigorous but heavier.\n"
                "- Answer 3: 7/6/6/8/8 — clear but shallow.\n"
                "Winner: Answer 1, adopting Answer 2's rigor for the risk section."
            )
        body = ROLE_HINTS.get(model, ROLE_HINTS["mock-general"])
        return f"{body}\n\nApplied to your question — \"{q[:300]}\" — the concrete first action is to " \
               "state the decision criteria explicitly, try the cheapest reversible option, and define " \
               "what evidence would change your mind."

    def _maybe_inject_failure(self, model: str) -> None:
        if self.force_rate_limit or bool(self.settings.get("force_rate_limit")) \
                or model in self.rate_limited_models:
            raise RateLimitError("Mock: forced rate limit (cooldown/failover demo)", retry_after=10)
        if self.force_error:
            raise ProviderUnavailableError("Mock: forced provider error")
        if bool(self.settings.get("fail_once")) and model not in self._fail_once_done:
            self._fail_once_done.add(model)
            raise ProviderUnavailableError("Mock: one-shot transient failure")
        n = self._call_count.get(model, 0)
        rl_every = int(self.settings.get("rate_limit_every") or 0)
        fail_every = int(self.settings.get("fail_every") or 0)
        if rl_every and n % rl_every == 0:
            raise RateLimitError(f"Mock: simulated rate limit every {rl_every} calls", retry_after=5)
        if fail_every and n % fail_every == 0:
            raise ProviderUnavailableError(f"Mock: simulated failure every {fail_every} calls")

    def _compose(self, messages: list[ChatMessage], model: str, options: GenerateOptions) -> tuple[str, list[ToolCall]]:
        question = self._last_user_text(messages)
        system = self._system_text(messages)
        # After a tool result comes back, answer using it (terminates the loop).
        prior_tool = next((m for m in messages if m.role == "tool"), None)
        if prior_tool:
            result_line = prior_tool.content.split("\n", 1)[-1]
            return (f"Using the tool result ({result_line.strip()[:120]}), here is the answer. "
                    "The computation is exact as returned by the tool."), []
        if options.tools and any(
            kw in question.lower() for kw in ("calculate", "compute", "what is", "square root", "evaluate")
        ):
            # Demonstrate native tool-calling protocol.
            expr = question.split(":")[-1].strip().split("\n")[0]
            return "", [ToolCall(id="call_mock_1", name="calculator", arguments={"expression": expr})]
        head = f"**{model}** · offline mock model (deterministic)\n\n"
        return head + self._stage_body(model, system, question, options), []

    async def generate(self, messages, model, options=None) -> GenerationResult:
        options = options or GenerateOptions()
        n = self._call_count.get(model, 0)
        self._call_count[model] = n + 1
        delay = float(self.settings.get("mock_delay_ms") or 0) / 1000.0
        if delay:
            await asyncio.sleep(delay)
        started = now_ms()
        self._maybe_inject_failure(model)
        text, calls = self._compose(messages, model, options)
        in_tok = sum(max(1, len(m.content) // 4) for m in messages)
        out_tok = max(1, len(text) // 4)
        return GenerationResult(
            text=text, tool_calls=calls, model=model, provider="mock",
            usage=Usage(in_tok, out_tok, 0.0, {"note": "estimated tokens (mock)"}),
            latency_ms=now_ms() - started,
        )

    async def stream(self, messages, model, options=None) -> AsyncIterator[str]:
        result = await self.generate(messages, model, options)
        words = result.text.split(" ")
        for i, w in enumerate(words):
            yield w + (" " if i < len(words) - 1 else "")
            if self.settings.get("mock_delay_ms"):
                await asyncio.sleep(float(self.settings["mock_delay_ms"]) / 1000.0 / 20)
