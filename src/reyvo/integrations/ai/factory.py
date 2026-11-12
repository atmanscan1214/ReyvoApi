from __future__ import annotations

from reyvo.integrations.ai.base import ChatProvider, DraftProvider
from reyvo.integrations.ai.embeddings import EmbeddingProvider, SemanticKernelEmbeddingProvider
from reyvo.integrations.ai.semantic_kernel_chat import SemanticKernelChatProvider
from reyvo.integrations.ai.semantic_kernel_provider import SemanticKernelDraftProvider

_draft_override: DraftProvider | None = None
_chat_override: ChatProvider | None = None
_embedding_override: EmbeddingProvider | None = None

# Default providers are cached so a process reuses one HTTP client and one
# Semantic Kernel instance instead of creating (and leaking) a client per call.
_draft_default: DraftProvider | None = None
_chat_default: ChatProvider | None = None
_embedding_default: EmbeddingProvider | None = None


def get_draft_provider() -> DraftProvider:
    """Return the active draft provider.

    Tests (and future vendor swaps) can replace the default Semantic Kernel
    implementation through :func:`set_draft_provider`.
    """

    global _draft_default
    if _draft_override is not None:
        return _draft_override
    if _draft_default is None:
        _draft_default = SemanticKernelDraftProvider()
    return _draft_default


def set_draft_provider(provider: DraftProvider | None) -> None:
    global _draft_override
    _draft_override = provider


def reset_draft_provider() -> None:
    global _draft_override, _draft_default
    _draft_override = None
    _draft_default = None


def get_chat_provider() -> ChatProvider:
    """Return the active knowledge-base chat provider."""

    global _chat_default
    if _chat_override is not None:
        return _chat_override
    if _chat_default is None:
        _chat_default = SemanticKernelChatProvider()
    return _chat_default


def set_chat_provider(provider: ChatProvider | None) -> None:
    global _chat_override
    _chat_override = provider


def reset_chat_provider() -> None:
    global _chat_override, _chat_default
    _chat_override = None
    _chat_default = None


def get_embedding_provider() -> EmbeddingProvider:
    """Return the active embedding provider.

    The provider reports ``available=False`` when embeddings are not
    configured, which lets retrieval fall back to lexical search.
    """

    global _embedding_default
    if _embedding_override is not None:
        return _embedding_override
    if _embedding_default is None:
        _embedding_default = SemanticKernelEmbeddingProvider()
    return _embedding_default


def set_embedding_provider(provider: EmbeddingProvider | None) -> None:
    global _embedding_override
    _embedding_override = provider


def reset_embedding_provider() -> None:
    global _embedding_override, _embedding_default
    _embedding_override = None
    _embedding_default = None


def reset_ai_providers() -> None:
    reset_draft_provider()
    reset_chat_provider()
    reset_embedding_provider()
