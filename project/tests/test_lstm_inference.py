"""
tests/test_lstm_inference.py

Unit and integration tests for the SmartSearch AI LSTM inference and candidate
ranking pipeline (src/models/lstm/inference.py).

Features tested:
  - Model checkpoint and tokenizer loading
  - Missing/invalid checkpoint and tokenizer path handling (FileNotFoundError)
  - CPU and CUDA inference auto-selection and execution
  - Empty, whitespace, Unicode, and unknown-token handling
  - Candidate scoring and deterministic rank_candidates ordering
  - High-level SmartSearchRanker / LSTMSuggestionRanker pipeline
  - Duplicate candidate removal
  - Prefix validity enforcement (rejects mismatched candidates)
  - Top-K truncation and empty-candidate behavior
  - Singleton model loading (checkpoint loaded once, not per request)
  - Batching candidate scoring consistency
  - Real prefix index integration test (guarded by RUN_REAL_INDEX_TESTS=1)

Tests run in unit-test mode without requiring the 3.48GB real index.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.models.lstm.inference import (
    DEFAULT_CHECKPOINT_PATH,
    DEFAULT_INDEX_PATH,
    DEFAULT_TOKENIZER_DIR,
    LSTMRanker,
    LSTMSuggestionRanker,
    SmartSearchRanker,
)
from src.models.lstm.model import LSTMRankerConfig, PrefixRankingLSTM
from src.models.lstm.tokenizer import Tokenizer, TokenizerConfig
from src.retrieval.prefix_index import Candidate


# ---------------------------------------------------------------------------
# Test Fixtures & Helpers
# ---------------------------------------------------------------------------

_FIXTURE_TEXTS = [
    "deep learning",
    "deep learning tutorial",
    "deep learning projects",
    "machine learning",
    "machine learning course",
    "café near me",
    "自然语言处理",
    "münchen wetter",
]


def _create_test_artifacts(tmp_path: Path):
    """Create a tiny, fast tokenizer and model checkpoint in tmp_path."""
    torch = pytest.importorskip("torch")

    # Fit small tokenizer
    tok_dir = tmp_path / "tokenizer"
    tok = Tokenizer(TokenizerConfig(mode="hybrid", max_vocab_size=100, max_char_vocab_size=50, max_seq_length=20))
    tok.fit(_FIXTURE_TEXTS)
    tok.save(tok_dir)

    # Initialize small model
    config = LSTMRankerConfig(
        vocab_size=tok.vocab_size,
        embedding_dim=16,
        hidden_dim=32,
        num_layers=1,
        bidirectional=True,
        max_seq_length=20,
    )
    model = PrefixRankingLSTM(config)
    ckpt_path = tmp_path / "model.pt"
    torch.save(
        {
            "model_state_dict": model.state_dict(),
            "model_config": config.to_dict(),
        },
        ckpt_path,
    )
    return ckpt_path, tok_dir


class MockPrefixIndex:
    """In-memory mock of PrefixIndex for isolated unit tests."""

    def __init__(self, candidates_map: dict[str, list[Any]] | None = None) -> None:
        self.candidates_map = candidates_map or {}
        self.case_sensitive = False
        self.unicode_form = "NFKC"
        self.call_count = 0

    def get_candidates(self, prefix: str, limit: int = 50) -> list[Any]:
        self.call_count += 1
        results = self.candidates_map.get(prefix, [])
        return results[:limit]

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Unit Tests: LSTMRanker
# ---------------------------------------------------------------------------


def test_checkpoint_and_tokenizer_loading(tmp_path: Path) -> None:
    """Verify clean loading of model checkpoint and tokenizer."""
    ckpt_path, tok_dir = _create_test_artifacts(tmp_path)
    ranker = LSTMRanker(ckpt_path, tok_dir, device="cpu")

    assert ranker.model is not None
    assert not ranker.model.training  # Model must be in eval mode
    assert ranker.max_seq_length == 20
    assert ranker.tokenizer.vocab_size > 0


def test_missing_checkpoint_raises_filenotfound(tmp_path: Path) -> None:
    """Verify FileNotFoundError on nonexistent checkpoint path."""
    _, tok_dir = _create_test_artifacts(tmp_path)
    with pytest.raises(FileNotFoundError, match="checkpoint not found"):
        LSTMRanker(tmp_path / "nonexistent.pt", tok_dir)


def test_missing_tokenizer_raises_filenotfound(tmp_path: Path) -> None:
    """Verify FileNotFoundError on nonexistent tokenizer directory."""
    ckpt_path, _ = _create_test_artifacts(tmp_path)
    with pytest.raises(FileNotFoundError, match="Tokenizer directory not found"):
        LSTMRanker(ckpt_path, tmp_path / "nonexistent_tok_dir")


def test_cpu_and_cuda_inference(tmp_path: Path) -> None:
    """Verify CPU inference and safe CUDA auto-detection."""
    torch = pytest.importorskip("torch")
    ckpt_path, tok_dir = _create_test_artifacts(tmp_path)

    # Explicit CPU
    ranker_cpu = LSTMRanker(ckpt_path, tok_dir, device="cpu")
    assert ranker_cpu.device.type == "cpu"
    scores_cpu = ranker_cpu.score("deep", ["deep learning", "deep learning tutorial"])
    assert len(scores_cpu) == 2
    assert all(isinstance(s, float) for s in scores_cpu)

    # Auto device
    ranker_auto = LSTMRanker(ckpt_path, tok_dir, device="auto")
    expected_type = "cuda" if torch.cuda.is_available() else "cpu"
    assert ranker_auto.device.type == expected_type
    scores_auto = ranker_auto.score("deep", ["deep learning"])
    assert len(scores_auto) == 1


def test_empty_and_whitespace_prefix(tmp_path: Path) -> None:
    """Verify empty or whitespace-only prefix produces valid scores without crash."""
    ckpt_path, tok_dir = _create_test_artifacts(tmp_path)
    ranker = LSTMRanker(ckpt_path, tok_dir, device="cpu")

    scores_empty = ranker.score("", ["deep learning", "machine learning"])
    assert len(scores_empty) == 2
    assert all(isinstance(s, float) for s in scores_empty)

    scores_spaces = ranker.score("    ", ["deep learning", "machine learning"])
    assert len(scores_spaces) == 2
    assert all(isinstance(s, float) for s in scores_spaces)


def test_unicode_prefix_preservation(tmp_path: Path) -> None:
    """Verify Unicode prefixes (accents, CJK, symbols) run safely without crash."""
    ckpt_path, tok_dir = _create_test_artifacts(tmp_path)
    ranker = LSTMRanker(ckpt_path, tok_dir, device="cpu")

    # Accented text
    scores_accents = ranker.score("café", ["café near me", "café paris"])
    assert len(scores_accents) == 2
    assert all(isinstance(s, float) for s in scores_accents)

    # CJK text
    scores_cjk = ranker.score("自然", ["自然语言处理", "自然科学"])
    assert len(scores_cjk) == 2
    assert all(isinstance(s, float) for s in scores_cjk)


def test_unknown_token_input(tmp_path: Path) -> None:
    """Verify completely unseen out-of-vocabulary words are safely handled."""
    ckpt_path, tok_dir = _create_test_artifacts(tmp_path)
    ranker = LSTMRanker(ckpt_path, tok_dir, device="cpu")

    scores = ranker.score("xyzqwk998811", ["xyzqwk998811 foobar", "unknown query completely"])
    assert len(scores) == 2
    assert all(isinstance(s, float) for s in scores)


def test_candidate_ranking_and_determinism(tmp_path: Path) -> None:
    """Verify rank_candidates sorts descending by score and is deterministic."""
    ckpt_path, tok_dir = _create_test_artifacts(tmp_path)
    ranker = LSTMRanker(ckpt_path, tok_dir, device="cpu")

    candidates = ["deep learning", "deep learning tutorial", "deep learning projects"]
    ranked_1 = ranker.rank_candidates("deep", candidates, top_k=3)
    ranked_2 = ranker.rank_candidates("deep", candidates, top_k=3)

    assert len(ranked_1) == 3
    # Check descending score order
    assert ranked_1[0]["score"] >= ranked_1[1]["score"] >= ranked_1[2]["score"]

    # Check structure
    for item in ranked_1:
        assert "suggestion" in item
        assert "score" in item
        assert "candidate" in item

    # Check determinism: multiple calls must yield identical outputs
    assert ranked_1 == ranked_2


def test_batch_candidate_scoring_consistency(tmp_path: Path) -> None:
    """Verify chunked batch scoring yields identical results to unchunked scoring."""
    ckpt_path, tok_dir = _create_test_artifacts(tmp_path)
    ranker = LSTMRanker(ckpt_path, tok_dir, device="cpu")

    candidates = [f"deep learning variant {i}" for i in range(15)]
    scores_b2 = ranker.score("deep", candidates, batch_size=2)
    scores_b15 = ranker.score("deep", candidates, batch_size=15)

    assert len(scores_b2) == 15
    for s1, s2 in zip(scores_b2, scores_b15):
        assert abs(s1 - s2) < 1e-5


def test_score_single_and_pairs(tmp_path: Path) -> None:
    """Verify score_single and score_pairs helpers."""
    ckpt_path, tok_dir = _create_test_artifacts(tmp_path)
    ranker = LSTMRanker(ckpt_path, tok_dir, device="cpu")

    single_score = ranker.score_single("deep", "deep learning")
    assert isinstance(single_score, float)

    pairs = [("deep", "deep learning"), ("machine", "machine learning")]
    pair_scores = ranker.score_pairs(pairs, batch_size=1)
    assert len(pair_scores) == 2
    assert abs(pair_scores[0] - single_score) < 1e-5


# ---------------------------------------------------------------------------
# Unit Tests: SmartSearchRanker / LSTMSuggestionRanker Pipeline
# ---------------------------------------------------------------------------


def test_pipeline_suggest_end_to_end(tmp_path: Path) -> None:
    """Verify end-to-end flow: index -> filter -> LSTM score -> top-K."""
    ckpt_path, tok_dir = _create_test_artifacts(tmp_path)
    ranker = LSTMRanker(ckpt_path, tok_dir, device="cpu")

    mock_candidates = [
        Candidate(
            qid="1",
            original_query="deep learning",
            normalized_query="deep learning",
            popularity=10,
            duplicate_count=0,
            matched_prefix="deep",
            prefix_match_length=4,
            source="range_scan",
        ),
        Candidate(
            qid="2",
            original_query="deep learning tutorial",
            normalized_query="deep learning tutorial",
            popularity=5,
            duplicate_count=0,
            matched_prefix="deep",
            prefix_match_length=4,
            source="range_scan",
        ),
    ]
    mock_index = MockPrefixIndex({"deep": mock_candidates})

    pipeline = SmartSearchRanker(index=mock_index, ranker=ranker)
    suggestions = pipeline.suggest("deep", top_k=2)

    assert len(suggestions) == 2
    assert suggestions[0]["score"] >= suggestions[1]["score"]
    assert all("suggestion" in s and "score" in s for s in suggestions)
    # Check predict alias
    assert pipeline.predict("deep", top_k=2) == suggestions


def test_duplicate_candidate_removal(tmp_path: Path) -> None:
    """Verify pipeline removes exact duplicates returned from candidate retrieval."""
    ckpt_path, tok_dir = _create_test_artifacts(tmp_path)
    ranker = LSTMRanker(ckpt_path, tok_dir, device="cpu")

    duplicates = [
        "deep learning",
        "Deep Learning",  # Normalized duplicate
        "deep learning",  # Exact duplicate
        "deep learning tutorial",
    ]
    mock_index = MockPrefixIndex({"deep": duplicates})

    pipeline = SmartSearchRanker(index=mock_index, ranker=ranker)
    suggestions = pipeline.suggest("deep", top_k=10)

    # Should only have 2 unique suggestions: "deep learning" and "deep learning tutorial"
    assert len(suggestions) == 2
    unique_suggestions = {s["suggestion"].lower() for s in suggestions}
    assert unique_suggestions == {"deep learning", "deep learning tutorial"}


def test_prefix_validity_filtering(tmp_path: Path) -> None:
    """Verify pipeline drops candidates that do not correspond to the requested prefix."""
    ckpt_path, tok_dir = _create_test_artifacts(tmp_path)
    ranker = LSTMRanker(ckpt_path, tok_dir, device="cpu")

    # Mock index returns a stray candidate that does not start with "deep"
    candidates = [
        "deep learning",
        "machine learning",  # Invalid for prefix "deep"
        "deep neural networks",
    ]
    mock_index = MockPrefixIndex({"deep": candidates})

    pipeline = SmartSearchRanker(index=mock_index, ranker=ranker)
    suggestions = pipeline.suggest("deep", top_k=5)

    assert len(suggestions) == 2
    for s in suggestions:
        assert s["suggestion"].startswith("deep")


def test_top_k_behavior(tmp_path: Path) -> None:
    """Verify top-K correctly limits number of returned suggestions."""
    ckpt_path, tok_dir = _create_test_artifacts(tmp_path)
    ranker = LSTMRanker(ckpt_path, tok_dir, device="cpu")

    candidates = [f"deep learning project {i}" for i in range(10)]
    mock_index = MockPrefixIndex({"deep": candidates})
    pipeline = SmartSearchRanker(index=mock_index, ranker=ranker)

    # top_k = 3
    s3 = pipeline.suggest("deep", top_k=3)
    assert len(s3) == 3

    # top_k = 1
    s1 = pipeline.suggest("deep", top_k=1)
    assert len(s1) == 1
    assert s1[0] == s3[0]

    # top_k <= 0
    assert pipeline.suggest("deep", top_k=0) == []
    assert pipeline.suggest("deep", top_k=-1) == []


def test_no_candidates_returned_from_index(tmp_path: Path) -> None:
    """Verify pipeline returns empty list when index has no matching candidates."""
    ckpt_path, tok_dir = _create_test_artifacts(tmp_path)
    ranker = LSTMRanker(ckpt_path, tok_dir, device="cpu")

    mock_index = MockPrefixIndex({})  # No entries
    pipeline = SmartSearchRanker(index=mock_index, ranker=ranker)

    suggestions = pipeline.suggest("nonexistentprefix", top_k=5)
    assert suggestions == []


def test_invalid_and_empty_candidate_inputs(tmp_path: Path) -> None:
    """Verify graceful handling of empty or None inputs."""
    ckpt_path, tok_dir = _create_test_artifacts(tmp_path)
    ranker = LSTMRanker(ckpt_path, tok_dir, device="cpu")

    # LSTMRanker with empty candidate list
    assert ranker.rank_candidates("prefix", []) == []

    # SmartSearchRanker with None prefix
    mock_index = MockPrefixIndex({})
    pipeline = SmartSearchRanker(index=mock_index, ranker=ranker)
    assert pipeline.suggest(None) == []


def test_model_loaded_only_once(tmp_path: Path, monkeypatch) -> None:
    """Verify that multiple suggest() queries do NOT reload model from disk."""
    torch = pytest.importorskip("torch")
    ckpt_path, tok_dir = _create_test_artifacts(tmp_path)

    load_call_count = 0
    real_torch_load = torch.load

    def tracked_torch_load(*args, **kwargs):
        nonlocal load_call_count
        load_call_count += 1
        return real_torch_load(*args, **kwargs)

    monkeypatch.setattr(torch, "load", tracked_torch_load)

    mock_index = MockPrefixIndex({"deep": ["deep learning", "deep learning tutorial"]})
    pipeline = SmartSearchRanker(
        index=mock_index,
        checkpoint_path=ckpt_path,
        tokenizer_dir=tok_dir,
        device="cpu",
    )

    # Initial loading should have called torch.load once
    assert load_call_count == 1

    # Multiple subsequent suggest() calls must NOT call torch.load again
    for _ in range(5):
        pipeline.suggest("deep", top_k=2)

    assert load_call_count == 1, "Model was reloaded during suggest() calls!"


def test_lstmsuggestionranker_alias() -> None:
    """Verify LSTMSuggestionRanker is an exact alias for SmartSearchRanker."""
    assert LSTMSuggestionRanker is SmartSearchRanker


# ---------------------------------------------------------------------------
# Integration Test: Real Prefix Index (guarded by RUN_REAL_INDEX_TESTS=1)
# ---------------------------------------------------------------------------


def test_real_prefix_index_integration() -> None:
    """Integration test using the real 3.48GB SQLite index and trained model.

    Skipped by default in unit-test runs. Enable with:
        RUN_REAL_INDEX_TESTS=1 python tests/_minimal_pytest_runner.py
    """
    if os.environ.get("RUN_REAL_INDEX_TESTS") != "1":
        pytest.importorskip("pytest").skip("Real index test skipped (set RUN_REAL_INDEX_TESTS=1 to run)")

    if not DEFAULT_CHECKPOINT_PATH.exists():
        pytest.importorskip("pytest").skip(f"Real checkpoint missing: {DEFAULT_CHECKPOINT_PATH}")

    if not DEFAULT_INDEX_PATH.exists():
        pytest.importorskip("pytest").skip(f"Real prefix index missing: {DEFAULT_INDEX_PATH}")

    with SmartSearchRanker(
        index_path=DEFAULT_INDEX_PATH,
        checkpoint_path=DEFAULT_CHECKPOINT_PATH,
        tokenizer_dir=DEFAULT_TOKENIZER_DIR,
        device="auto",
    ) as pipeline:
        results = pipeline.suggest("deep learning", top_k=5)
        assert len(results) > 0
        assert all(isinstance(r["score"], float) for r in results)
        assert all("deep learning" in r["suggestion"].lower() for r in results)
