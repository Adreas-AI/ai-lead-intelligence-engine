"""LLM providers behind a small JSON-in / JSON-out interface.

The engine never depends on the LLM: every LLM-backed step has a deterministic
fallback. ``MockLLMProvider`` returns deterministic, input-grounded output so the
whole pipeline (including cost accounting) can be exercised without an API key.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.retry import RetryPolicy, call_with_retry, is_retryable

log = get_logger(__name__)


class LLMError(Exception):
    pass


class LLMConfigurationError(LLMError):
    pass


@dataclass
class LLMResult:
    data: dict[str, Any]
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLMProvider(ABC):
    name: str
    model: str

    @abstractmethod
    def complete_json(self, system: str, user: str, *, operation: str) -> LLMResult:
        """Return a parsed JSON object. Raise ``LLMError`` on failure."""

    def cost_usd(self, result: LLMResult) -> float:
        return 0.0


class MockLLMProvider(LLMProvider):
    """Deterministic stand-in. Only ever restates facts it was given."""

    name = "mock"
    model = "mock-llm-1"

    def complete_json(self, system: str, user: str, *, operation: str) -> LLMResult:
        try:
            payload = json.loads(user)
        except json.JSONDecodeError as exc:
            raise LLMError("mock provider expects a JSON user message") from exc
        facts = payload.get("facts", {})
        if operation == "lead_scoring":
            data = self._score(facts, payload.get("rule_result", {}))
        elif operation == "outreach_draft":
            data = self._outreach(facts, payload.get("sender", {}))
        elif operation == "company_summary":
            text = (payload.get("text") or "").strip()
            first = text.split(". ")[0][:280] if text else ""
            data = {"summary": first or None, "insufficient_information": not first}
        else:
            raise LLMError(f"mock provider does not support operation {operation!r}")
        tokens_in = max(1, len(user) // 4)
        tokens_out = max(1, len(json.dumps(data)) // 4)
        return LLMResult(data, self.model, tokens_in, tokens_out)

    @staticmethod
    def _score(facts: dict[str, Any], rule: dict[str, Any]) -> dict[str, Any]:
        categories = facts.get("categories") or []
        signals = facts.get("business_signals") or []
        adjustment = 0
        risks: list[str] = []
        if categories and signals:
            adjustment = 3
        if not facts.get("summary"):
            adjustment -= 3
            risks.append("No verified company summary available.")
        if facts.get("b2c_signals"):
            risks.append("Website shows consumer e-commerce signals.")
        name = facts.get("name", "The company")
        reasoning = (
            f"{name} lists {', '.join(categories) or 'no detected product categories'} "
            f"with business signals: {', '.join(signals) or 'none detected'}."
        )
        return {
            "adjustment": adjustment,
            "reasoning": reasoning,
            "risks": risks,
            "recommended_next_step": (
                "Review and send outreach" if rule.get("rule_score", 0) >= 70 else "Manual review"
            ),
        }

    @staticmethod
    def _outreach(facts: dict[str, Any], sender: dict[str, Any]) -> dict[str, Any]:
        name = facts.get("name", "there")
        cats = facts.get("categories") or []
        focus = " and ".join(c.lower() for c in cats[:2]) if cats else "your products"
        subject = f"{focus.capitalize()} buyers for {name}"
        body = (
            f"Hello {name} team,\n\n"
            f"I saw on your website that you work with {focus}. "
            f"{sender.get('company', 'We')} is "
            f"{sender.get('value_proposition', 'a B2B marketplace')}.\n\n"
            "If it would be useful, I would be glad to share how suppliers in your category use "
            "the platform. Would a short call next week work?\n\n"
            f"Best regards,\n{sender.get('name', '')}\n{sender.get('company', '')}"
        )
        return {"subject": subject, "body": body}


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, settings: Settings, client: Any | None = None):
        if client is None:
            if settings.openai_api_key is None:
                raise LLMConfigurationError("LLM_PROVIDER=openai requires OPENAI_API_KEY")
            from openai import OpenAI

            client = OpenAI(
                api_key=settings.openai_api_key.get_secret_value(),
                timeout=settings.openai_timeout_seconds,
                max_retries=0,  # retries are handled by call_with_retry below
            )
        self.client = client
        self.model = settings.openai_model
        self.input_cost = settings.openai_input_cost_per_1m
        self.output_cost = settings.openai_output_cost_per_1m
        self.retry_policy = RetryPolicy(max_attempts=3, base_delay=1.0, max_delay=20.0)

    @staticmethod
    def _classify(exc: BaseException) -> tuple[bool, float | None]:
        try:
            import openai
        except ImportError:  # pragma: no cover
            return is_retryable(exc)
        retryable = (
            openai.RateLimitError,
            openai.APIConnectionError,
            openai.APITimeoutError,
            openai.InternalServerError,
        )
        if isinstance(exc, retryable):
            return True, None
        return is_retryable(exc)

    def complete_json(self, system: str, user: str, *, operation: str) -> LLMResult:
        def _call():
            return self.client.chat.completions.create(
                model=self.model,
                temperature=0.2,
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            )

        try:
            response = call_with_retry(
                _call, self.retry_policy, operation=f"openai.{operation}", classify=self._classify
            )
        except Exception as exc:
            raise LLMError(f"OpenAI request failed: {type(exc).__name__}: {exc}") from exc

        content = response.choices[0].message.content or ""
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMError("OpenAI returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise LLMError("OpenAI returned a non-object JSON value")
        usage = getattr(response, "usage", None)
        return LLMResult(
            data=data,
            model=getattr(response, "model", self.model),
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )

    def cost_usd(self, result: LLMResult) -> float:
        return round(
            result.prompt_tokens / 1_000_000 * self.input_cost
            + result.completion_tokens / 1_000_000 * self.output_cost,
            6,
        )


def get_llm_provider(settings: Settings) -> LLMProvider | None:
    if settings.llm_provider == "none":
        return None
    if settings.llm_provider == "openai":
        return OpenAIProvider(settings)
    return MockLLMProvider()
