"""
Sonnet Research API - Client

Wraps the Anthropic Python SDK to provide a simple, retry-aware interface
for querying Claude Sonnet from within Claude Code agent workflows.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import anthropic

from config import (
    ANTHROPIC_API_KEY,
    DEFAULT_MAX_TOKENS,
    MAX_RETRIES,
    MODEL,
    RETRY_BACKOFF_SECONDS,
)


# ---------------------------------------------------------------------------
# Structured result / error types
# ---------------------------------------------------------------------------

@dataclass
class QueryResult:
    """Successful query result."""
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    stop_reason: str | None = None


@dataclass
class QueryError:
    """Structured error returned instead of raising exceptions."""
    error_type: str
    message: str
    retries_attempted: int = 0


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class SonnetResearchClient:
    """Thin wrapper around the Anthropic SDK for research queries."""

    def __init__(self, api_key: str | None = None, model: str = MODEL) -> None:
        key = api_key or ANTHROPIC_API_KEY
        if not key:
            raise EnvironmentError(
                "ANTHROPIC_API_KEY is not set. Export it as an environment variable "
                "or pass it explicitly to SonnetResearchClient()."
            )
        self._client = anthropic.Anthropic(api_key=key)
        self.model = model

        # Session-level token tracking
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.total_queries: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def query(
        self,
        prompt: str,
        system_prompt: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> QueryResult | QueryError:
        """Send a single prompt to Sonnet and return the response text.

        Returns a ``QueryResult`` on success or a ``QueryError`` on failure.
        Retries transient errors with exponential backoff.
        """
        messages = [{"role": "user", "content": prompt}]
        return self._call_with_retry(messages, system_prompt, max_tokens)

    def query_batch(
        self,
        prompts: list[dict[str, Any]],
    ) -> list[QueryResult | QueryError]:
        """Process multiple prompts sequentially.

        Each element of *prompts* should be a dict with at least a ``prompt``
        key and optionally ``system_prompt`` and ``max_tokens``.

        Returns a list of ``QueryResult`` / ``QueryError`` in the same order.
        """
        results: list[QueryResult | QueryError] = []
        for item in prompts:
            result = self.query(
                prompt=item["prompt"],
                system_prompt=item.get("system_prompt"),
                max_tokens=item.get("max_tokens", DEFAULT_MAX_TOKENS),
            )
            results.append(result)
        return results

    def get_usage(self) -> dict[str, int]:
        """Return cumulative token usage for this session."""
        return {
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_queries": self.total_queries,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _call_with_retry(
        self,
        messages: list[dict[str, str]],
        system_prompt: str | None,
        max_tokens: int,
    ) -> QueryResult | QueryError:
        last_error: Exception | None = None

        for attempt in range(MAX_RETRIES + 1):
            try:
                kwargs: dict[str, Any] = {
                    "model": self.model,
                    "max_tokens": max_tokens,
                    "messages": messages,
                }
                if system_prompt:
                    kwargs["system"] = system_prompt

                response = self._client.messages.create(**kwargs)

                # Extract text from content blocks.
                text_parts = [
                    block.text
                    for block in response.content
                    if hasattr(block, "text")
                ]
                text = "\n".join(text_parts)

                # Track tokens.
                input_tok = response.usage.input_tokens
                output_tok = response.usage.output_tokens
                self.total_input_tokens += input_tok
                self.total_output_tokens += output_tok
                self.total_queries += 1

                return QueryResult(
                    text=text,
                    input_tokens=input_tok,
                    output_tokens=output_tok,
                    model=response.model,
                    stop_reason=response.stop_reason,
                )

            except (
                anthropic.RateLimitError,
                anthropic.APIConnectionError,
                anthropic.InternalServerError,
            ) as exc:
                last_error = exc
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_BACKOFF_SECONDS[attempt])
                continue

            except anthropic.APIError as exc:
                # Non-transient API error; don't retry.
                return QueryError(
                    error_type=type(exc).__name__,
                    message=str(exc),
                    retries_attempted=attempt,
                )

            except Exception as exc:  # noqa: BLE001
                return QueryError(
                    error_type=type(exc).__name__,
                    message=str(exc),
                    retries_attempted=attempt,
                )

        # All retries exhausted.
        return QueryError(
            error_type=type(last_error).__name__ if last_error else "UnknownError",
            message=str(last_error) if last_error else "All retries exhausted",
            retries_attempted=MAX_RETRIES,
        )
