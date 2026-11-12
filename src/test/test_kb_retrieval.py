from __future__ import annotations

import uuid
from collections import Counter

import pytest
from reyvo.services.knowledge_service import (
    RetrievedChunk,
    bm25_scores,
    build_context,
    cited_indexes,
    cosine_similarity,
    is_context_follow_up,
    reciprocal_rank_fusion,
    term_frequency,
    tokenize,
)


def _chunk(content: str, *, heading: str = "5. Multi-Tenancy") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        document_slug="reyvo-design-document",
        document_title="Reyvo Detailed Design Document",
        heading_path=heading,
        anchor="5-multi-tenancy",
        content=content,
        score=0.5,
    )


def test_tokenize_drops_stopwords_and_punctuation():
    assert tokenize("How is the tenant isolation enforced?") == [
        "tenant",
        "isolation",
        "enforced",
    ]


def test_bm25_ranks_the_matching_document_first():
    documents = [
        tokenize("Tenant isolation uses PostgreSQL row level security."),
        tokenize("Campaign builder steps and sending schedules."),
        tokenize("Stripe billing invoices and subscriptions."),
    ]

    scores = bm25_scores(tokenize("row level security"), documents)

    assert scores[0] > scores[1]
    assert scores[0] > scores[2]


def test_bm25_returns_zero_without_matches():
    documents = [tokenize("campaign builder"), tokenize("billing invoices")]

    assert bm25_scores(tokenize("quantum capacitor"), documents) == [0.0, 0.0]


def test_bm25_matches_morphological_variants():
    documents = [
        tokenize("A recipient can unsubscribe from the footer link."),
        tokenize("Campaigns support up to eight steps."),
    ]

    scores = bm25_scores(tokenize("What happens when a recipient unsubscribes?"), documents)

    assert scores[0] > scores[1]


def test_term_frequency_prefers_exact_matches_and_ignores_short_fragments():
    counts = Counter(tokenize("unsubscribe unsubscribe link"))

    assert term_frequency("unsubscribe", counts) == 2
    assert term_frequency("unsubscribes", counts) == 2
    assert term_frequency("un", counts) == 0


def test_cosine_similarity_handles_identical_and_orthogonal_vectors():
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert cosine_similarity([], [1.0]) == 0.0
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_reciprocal_rank_fusion_rewards_documents_in_both_lists():
    fused = reciprocal_rank_fusion([[0, 1, 2], [1, 2, 0]])

    assert fused[1] > fused[0]
    assert fused[1] > fused[2]


def test_build_context_numbers_sources_and_builds_citations():
    results = [
        _chunk("Tenant isolation uses PostgreSQL row level security."),
        _chunk("Campaigns support up to eight steps.", heading="7.4 Campaign builder"),
    ]

    context, citations = build_context(results)

    assert context.startswith("[1] Reyvo Detailed Design Document > 5. Multi-Tenancy")
    assert "[2] Reyvo Detailed Design Document > 7.4 Campaign builder" in context
    assert [citation["index"] for citation in citations] == [1, 2]
    assert citations[0]["document_slug"] == "reyvo-design-document"
    assert citations[0]["anchor"] == "5-multi-tenancy"
    assert citations[0]["score"] == 0.5


def test_build_context_stops_at_the_character_budget():
    first = _chunk("short source")
    huge = _chunk("x" * 500)

    context, citations = build_context([first, huge], max_chars=100)

    assert context == "[1] Reyvo Detailed Design Document > 5. Multi-Tenancy\nshort source"
    assert len(citations) == 1


def test_citation_snippet_is_collapsed_and_truncated():
    _, citations = build_context([_chunk("line one\n\nline two " + "y" * 400)])

    snippet = citations[0]["snippet"]
    assert "\n" not in snippet
    assert snippet.endswith("…")


def test_build_context_handles_no_results():
    assert build_context([]) == ("", [])


def test_cited_indexes_ignores_out_of_range_markers():
    assert cited_indexes("Answer [1] and [9].", 2) == {1}
    assert cited_indexes("No markers here.", 2) == set()


@pytest.mark.parametrize(
    "question",
    [
        "tell me more",
        "Tell me more, please.",
        "why?",
        "How does that work?",
        "what about it",
        "can you explain that?",
        "and the second one",
        "give me an example",
    ],
)
def test_context_only_follow_ups_are_detected(question):
    assert is_context_follow_up(question) is True


@pytest.mark.parametrize(
    "question",
    [
        "What about campaigns?",
        "How is tenant isolation enforced?",
        "Does it support SSO?",
        "What is the capital of France?",
        "Which section covers pricing?",
    ],
)
def test_standalone_questions_are_not_treated_as_follow_ups(question):
    assert is_context_follow_up(question) is False
