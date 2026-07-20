"""Anthropic client configuration for the AI service module.

Lazy-init pattern: the AsyncAnthropic client is only instantiated on first call
so import-time doesn't fail when ANTHROPIC_API_KEY is absent (e.g., test environments,
dev server without key). The plan's test suite monkeypatches run_onboarding_parse
directly, so the client is never instantiated during mocked tests.
"""
import os
from typing import Optional

from anthropic import AsyncAnthropic

# Sonnet 4.6 model string — verified against anthropic SDK 0.117.0 ModelParam type:
# ModelParam includes 'claude-sonnet-4-6' as a valid literal.
SONNET_MODEL = "claude-sonnet-4-6"


def _get_api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. "
            "Set it before starting the server (Railway variable or local .env)."
        )
    return key


# Module-level singleton — only allocated on first get_client() call.
_client: Optional[AsyncAnthropic] = None


def get_client() -> AsyncAnthropic:
    """Return the shared AsyncAnthropic client, creating it on first call.

    Raises RuntimeError if ANTHROPIC_API_KEY is not set.
    Phase 4 cost governor will wrap this client's usage at the module boundary.
    """
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=_get_api_key())
    return _client
