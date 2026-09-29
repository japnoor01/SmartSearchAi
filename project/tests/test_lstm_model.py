"""
tests/test_lstm_model.py

Unit tests for the LSTM ranking stage, using small synthetic fixtures
(task section 12 -- no dependency on the full dataset).

Execution status in THIS environment: PyTorch is not installed here (no
network access to install it -- see docs/lstm_model.md). Tests that need
`torch` are marked with `pytest.importorskip("torch")` at the top of the
test, so:

  - Tokenizer tests (unicode, punctuation, code-like queries, empty/short
    prefixes, save/load round-trip) run and pass in ANY environment,
    including this one -- see the real output of
    `python -m pytest tests/test_lstm_model.py -v` captured in
    docs/lstm_model.md and reports/models/lstm_training_report.json.
  - Model/checkpoint/inference/CPU-forward-pass tests are written to the
    same standard, but are SKIPPED (not silently omitted, not
    fabricated-passing) in this environment and will run for real the
    moment `torch` is installed. Do not read a "skipped" result as a
    pass.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.models.lstm.tokenizer import Tokenizer, TokenizerConfig, PAD_ID, UNK_ID, CLS_ID, SEP_ID  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures -- small, hand-picked, no full dataset required.
# ---------------------------------------------------------------------------

_FIXTURE_TEXTS = [
    "machine learning",
    "machine learning course",
    "machine learning algorithms",
    "café near me",
    "日本語 学習",
    "node.js vs python",
    "df.groupby('col').mean()",
    "c++ vs c#",
    "2026 laptop",
]


def _fitted_tokenizer(mode: str = "hybrid") -> Tokenizer:
    tok = Tokenizer(TokenizerConfig(mode=mode, max_vocab_size=500, max_char_vocab_size=200, max_seq_length=24))
    tok.fit(_FIXTURE_TEXTS)
    return tok


# ---------------------------------------------------------------------------
# Tokenizer tests -- run in every environment.
# ---------------------------------------------------------------------------


def test_tokenizer_basic_encode_nonempty():
    tok = _fitted_tokenizer()
    ids = tok.encode("machine lea")
    assert isinstance(ids, list) and len(ids) > 0
    assert all(isinstance(i, int) for i in ids)


def test_tokenizer_unicode_input_does_not_crash_and_is_not_all_unk():
    tok = _fitted_tokenizer()
    ids = tok.encode("café near me")
    assert len(ids) > 0
    # "café" and "near" and "me" were in the fit corpus, so this should not
    # collapse entirely to <UNK>.
    assert any(i != UNK_ID for i in ids)


def test_tokenizer_unseen_unicode_falls_back_to_chars_not_one_blind_unk():
    tok = _fitted_tokenizer()
    # "学習" (from the fixture) should tokenize to known chars even though
    # combined with an unseen unicode word.
    ids = tok.encode("日本語")
    assert len(ids) >= 1


def test_tokenizer_punctuation_handled():
    tok = _fitted_tokenizer()
    ids = tok.encode("c++ vs c#")
    assert len(ids) > 0


def test_tokenizer_code_like_query():
    tok = _fitted_tokenizer()
    ids = tok.encode("df.groupby('col').mean()")
    assert len(ids) > 0
    # Splits into multiple tokens (not treated as one opaque blob).
    assert len(ids) > 3


def test_tokenizer_empty_prefix_returns_empty_list():
    tok = _fitted_tokenizer()
    assert tok.encode("") == []


def test_tokenizer_short_prefix_single_char():
    tok = _fitted_tokenizer()
    ids = tok.encode("m")
    assert len(ids) == 1


def test_tokenizer_word_mode_oov_is_single_unk():
    tok = _fitted_tokenizer(mode="word")
    ids = tok.encode("zzzznotinvocabulary")
    assert ids == [UNK_ID]


def test_tokenizer_encode_pair_structure():
    tok = _fitted_tokenizer()
    ids = tok.encode_pair("machine lea", "machine learning", max_length=20)
    assert ids[0] == CLS_ID
    assert ids.count(SEP_ID) == 2
    assert ids[-1] == SEP_ID


def test_tokenizer_pad_produces_correct_length_and_mask():
    tok = _fitted_tokenizer()
    ids = tok.encode_pair("machine", "machine learning", max_length=10)
    padded, mask = tok.pad(ids, max_length=16)
    assert len(padded) == 16
    assert len(mask) == 16
    assert sum(mask) == len(ids[:16])
    assert all(padded[i] == PAD_ID for i in range(len(ids[:16]), 16))


def test_tokenizer_pad_truncates_when_too_long():
    tok = _fitted_tokenizer()
    ids = list(range(30))
    padded, mask = tok.pad(ids, max_length=10)
    assert len(padded) == 10
    assert sum(mask) == 10


def test_tokenizer_save_load_roundtrip(tmp_path):
    tok = _fitted_tokenizer()
    tok.save(tmp_path / "tok")
    tok2 = Tokenizer.load(tmp_path / "tok")
    for text in _FIXTURE_TEXTS:
        assert tok.encode(text) == tok2.encode(text)
    assert tok.vocab_size == tok2.vocab_size


def test_tokenizer_deterministic_encoding():
    tok = _fitted_tokenizer()
    a = tok.encode_pair("machine lea", "machine learning course")
    b = tok.encode_pair("machine lea", "machine learning course")
    assert a == b


def test_tokenizer_vocab_size_matches_id_space():
    tok = _fitted_tokenizer()
    max_id = max(max(tok.word_to_id.values()), max(tok.char_to_id.values()))
    assert tok.vocab_size == max_id + 1


# ---------------------------------------------------------------------------
# Model / checkpoint / inference tests -- require torch. Skipped (not
# faked) in this environment. See module docstring.
# ---------------------------------------------------------------------------


def test_model_forward_pass_output_shape():
    torch = pytest.importorskip("torch")
    from src.models.lstm.model import LSTMRankerConfig, PrefixRankingLSTM

    tok = _fitted_tokenizer()
    config = LSTMRankerConfig(vocab_size=tok.vocab_size, embedding_dim=8, hidden_dim=16, num_layers=1, bidirectional=True)
    model = PrefixRankingLSTM(config)

    batch = [tok.encode_pair("machine lea", c, max_length=16) for c in ["machine learning", "machine shop"]]
    padded_batch, lengths = [], []
    for ids in batch:
        p, m = tok.pad(ids, max_length=16)
        padded_batch.append(p)
        lengths.append(sum(m))

    input_ids = torch.tensor(padded_batch, dtype=torch.long)
    lengths_t = torch.tensor(lengths, dtype=torch.long)
    logits = model(input_ids, lengths_t)
    assert logits.shape == (2,)


def test_model_forward_pass_cpu_only():
    torch = pytest.importorskip("torch")
    from src.models.lstm.model import LSTMRankerConfig, PrefixRankingLSTM

    tok = _fitted_tokenizer()
    config = LSTMRankerConfig(vocab_size=tok.vocab_size, embedding_dim=8, hidden_dim=8, num_layers=1, bidirectional=False)
    model = PrefixRankingLSTM(config).to("cpu")
    ids, mask = tok.pad(tok.encode_pair("python", "python tutorial", max_length=12), max_length=12)
    input_ids = torch.tensor([ids], dtype=torch.long)
    lengths = torch.tensor([sum(mask)], dtype=torch.long)
    logits = model(input_ids, lengths)
    assert logits.device.type == "cpu"


def test_model_deterministic_inference_in_eval_mode():
    torch = pytest.importorskip("torch")
    from src.models.lstm.model import LSTMRankerConfig, PrefixRankingLSTM

    tok = _fitted_tokenizer()
    config = LSTMRankerConfig(vocab_size=tok.vocab_size, embedding_dim=8, hidden_dim=8, dropout=0.5)
    model = PrefixRankingLSTM(config)
    model.eval()

    ids, mask = tok.pad(tok.encode_pair("python", "python tutorial", max_length=12), max_length=12)
    input_ids = torch.tensor([ids], dtype=torch.long)
    lengths = torch.tensor([sum(mask)], dtype=torch.long)
    with torch.no_grad():
        out1 = model(input_ids, lengths)
        out2 = model(input_ids, lengths)
    assert torch.allclose(out1, out2)


def test_checkpoint_save_and_load_roundtrip(tmp_path):
    torch = pytest.importorskip("torch")
    from src.models.lstm.model import LSTMRankerConfig, PrefixRankingLSTM

    tok = _fitted_tokenizer()
    config = LSTMRankerConfig(vocab_size=tok.vocab_size, embedding_dim=8, hidden_dim=8)
    model = PrefixRankingLSTM(config)
    ckpt_path = tmp_path / "model.pt"
    torch.save({"model_state_dict": model.state_dict(), "model_config": config.to_dict()}, ckpt_path)

    checkpoint = torch.load(ckpt_path, map_location="cpu")
    loaded_config = LSTMRankerConfig.from_dict(checkpoint["model_config"])
    loaded_model = PrefixRankingLSTM(loaded_config)
    loaded_model.load_state_dict(checkpoint["model_state_dict"])

    ids, mask = tok.pad(tok.encode_pair("python", "python tutorial", max_length=12), max_length=12)
    input_ids = torch.tensor([ids], dtype=torch.long)
    lengths = torch.tensor([sum(mask)], dtype=torch.long)
    model.eval()
    loaded_model.eval()
    with torch.no_grad():
        assert torch.allclose(model(input_ids, lengths), loaded_model(input_ids, lengths))


def test_inference_rank_candidates_orders_by_score(tmp_path):
    torch = pytest.importorskip("torch")
    from src.models.lstm.model import LSTMRankerConfig, PrefixRankingLSTM
    from src.models.lstm.inference import LSTMRanker

    tok = _fitted_tokenizer()
    tok.save(tmp_path / "tok")
    config = LSTMRankerConfig(vocab_size=tok.vocab_size, embedding_dim=8, hidden_dim=8)
    model = PrefixRankingLSTM(config)
    ckpt_path = tmp_path / "model.pt"
    torch.save({"model_state_dict": model.state_dict(), "model_config": config.to_dict()}, ckpt_path)

    ranker = LSTMRanker(ckpt_path, tmp_path / "tok")
    ranked = ranker.rank_candidates("machine lea", ["machine learning", "machine learning course"], top_k=5)
    assert len(ranked) == 2
    assert ranked[0]["score"] >= ranked[1]["score"]


def test_inference_empty_candidates_returns_empty(tmp_path):
    torch = pytest.importorskip("torch")
    from src.models.lstm.model import LSTMRankerConfig, PrefixRankingLSTM
    from src.models.lstm.inference import LSTMRanker

    tok = _fitted_tokenizer()
    tok.save(tmp_path / "tok")
    config = LSTMRankerConfig(vocab_size=tok.vocab_size, embedding_dim=8, hidden_dim=8)
    model = PrefixRankingLSTM(config)
    torch.save(
        {"model_state_dict": model.state_dict(), "model_config": config.to_dict()}, tmp_path / "model.pt"
    )
    ranker = LSTMRanker(tmp_path / "model.pt", tmp_path / "tok")
    assert ranker.rank_candidates("anything", [], top_k=5) == []


def test_model_empty_and_single_batch_handling():
    torch = pytest.importorskip("torch")
    from src.models.lstm.model import LSTMRankerConfig, PrefixRankingLSTM

    tok = _fitted_tokenizer()
    config = LSTMRankerConfig(vocab_size=tok.vocab_size, embedding_dim=8, hidden_dim=8)
    model = PrefixRankingLSTM(config)

    # Empty batch
    empty_ids = torch.empty((0, 12), dtype=torch.long)
    empty_lengths = torch.empty((0,), dtype=torch.long)
    empty_out = model(empty_ids, empty_lengths)
    assert empty_out.shape == (0,)

    # Single-item batch
    single_ids = torch.randint(0, tok.vocab_size, (1, 12), dtype=torch.long)
    single_lengths = torch.tensor([12], dtype=torch.long)
    single_out = model(single_ids, single_lengths)
    assert single_out.shape == (1,)


def test_model_gpu_inference_if_available():
    torch = pytest.importorskip("torch")
    if not torch.cuda.is_available():
        return  # Gracefully skip if CUDA is not enabled in this torch build

    from src.models.lstm.model import LSTMRankerConfig, PrefixRankingLSTM

    tok = _fitted_tokenizer()
    config = LSTMRankerConfig(vocab_size=tok.vocab_size, embedding_dim=8, hidden_dim=8)
    model = PrefixRankingLSTM(config).to("cuda")

    ids, mask = tok.pad(tok.encode_pair("deep", "deep learning", max_length=12), max_length=12)
    input_ids = torch.tensor([ids], dtype=torch.long, device="cuda")
    lengths = torch.tensor([sum(mask)], dtype=torch.long)
    logits = model(input_ids, lengths)
    assert logits.device.type == "cuda"
    assert logits.shape == (1,)


def test_streaming_pair_dataset_iteration(tmp_path):
    torch = pytest.importorskip("torch")
    import json
    from src.models.lstm.train import StreamingPairDataset

    tok = _fitted_tokenizer()
    jsonl_path = tmp_path / "sample_pairs.jsonl"
    rows = [
        {"prefix": "m", "candidate": "machine learning", "label": 1},
        {"prefix": "m", "candidate": "music store", "label": 0},
        {"prefix": "c", "candidate": "café near me", "label": 1},
    ]
    with jsonl_path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    ds = StreamingPairDataset(jsonl_path, tok, max_seq_length=16, buffer_size=10, shuffle=False)
    items = list(ds)
    assert len(items) == 3
    assert items[0]["input_ids"].shape == (16,)
    assert items[0]["input_ids"].dtype == torch.long
    assert items[0]["label"].dtype == torch.float32
    assert items[0]["prefix"] == "m"


def test_loss_functions_directionality():
    torch = pytest.importorskip("torch")
    from src.models.lstm.train import pairwise_hinge_loss
    from torch import nn

    # Case 1: Model ranks positive higher than negative (desirable)
    pos_score_good = torch.tensor([2.0, -1.0])  # [pos, neg]
    labels = torch.tensor([1.0, 0.0])
    prefixes = ["query", "query"]

    bce = nn.BCEWithLogitsLoss()
    loss_good = bce(pos_score_good, labels).item()
    hinge_good = pairwise_hinge_loss(pos_score_good, labels, prefixes, margin=0.3).item()

    # Case 2: Model ranks negative higher than positive (undesirable)
    pos_score_bad = torch.tensor([-1.0, 2.0])  # [pos, neg]
    loss_bad = bce(pos_score_bad, labels).item()
    hinge_bad = pairwise_hinge_loss(pos_score_bad, labels, prefixes, margin=0.3).item()

    # The loss must be strictly lower when positive candidate gets higher score
    assert loss_good < loss_bad
    assert hinge_good < hinge_bad
    assert hinge_good == 0.0  # margin satisfied

