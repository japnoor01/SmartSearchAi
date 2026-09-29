"""Tests for streaming train-pair tokenizer fitting, Unicode handling, and control character rejection."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.models.lstm.tokenizer import (  # noqa: E402
    CLS_ID,
    PAD_ID,
    SEP_ID,
    UNK_ID,
    Tokenizer,
    TokenizerConfig,
    is_disallowed_control,
)


def _write_pairs(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _rows() -> list[dict]:
    return [
        {"prefix": "ma", "candidate": "machine learning", "label": 1, "source": "positive"},
        {"prefix": "mach", "candidate": "machine learning", "label": 1, "source": "positive"},
        {"prefix": "中", "candidate": "中文查询", "label": 1, "source": "positive"},
        {"prefix": "zz", "candidate": "dev only token", "label": 0, "source": "index_retrieved"},
    ]


def test_streaming_fit_is_deterministic_and_deduplicates_positive_queries(tmp_path: Path) -> None:
    first_path = tmp_path / "first.jsonl"
    second_path = tmp_path / "second.jsonl"
    duplicated_path = tmp_path / "duplicated.jsonl"
    rows = _rows()
    _write_pairs(first_path, rows)
    _write_pairs(second_path, list(reversed(rows)))
    _write_pairs(duplicated_path, rows + [rows[0]])

    config = TokenizerConfig(max_vocab_size=100, max_char_vocab_size=100, max_seq_length=10)
    first = Tokenizer(config)
    second = Tokenizer(config)
    duplicated = Tokenizer(config)
    first_stats = first.fit_from_pairs_jsonl(first_path)
    second.fit_from_pairs_jsonl(second_path)
    duplicated.fit_from_pairs_jsonl(duplicated_path)

    assert first.to_dict() == second.to_dict()
    assert first.to_dict() == duplicated.to_dict()
    assert first_stats["source_pairs_scanned"] == 4
    assert first_stats["positive_pairs_scanned"] == 3
    assert first_stats["unique_positive_candidates"] == 2


def test_train_only_vocabulary_unknown_handling_and_unicode(tmp_path: Path) -> None:
    pairs_path = tmp_path / "train.jsonl"
    _write_pairs(pairs_path, _rows()[:3])
    dev_path = tmp_path / "dev.jsonl"
    _write_pairs(dev_path, [{"prefix": "", "candidate": "devonlytoken", "label": 1}])
    tokenizer = Tokenizer(TokenizerConfig(mode="word", max_vocab_size=100, max_char_vocab_size=4))
    tokenizer.fit_from_pairs_jsonl(pairs_path)

    assert "devonlytoken" not in tokenizer.word_to_id
    assert tokenizer.encode("devonlytoken") == [UNK_ID]
    assert "中文查询" in tokenizer.word_to_id
    assert tokenizer.encode("中文查询") != [UNK_ID]
    assert dev_path.exists()

    hybrid = Tokenizer(TokenizerConfig(max_vocab_size=100, max_char_vocab_size=4))
    hybrid.fit(["known"])
    assert hybrid.encode("🤖") == [UNK_ID]


def test_control_character_rejection(tmp_path: Path) -> None:
    dirty_path = tmp_path / "dirty.jsonl"
    dirty_rows = [
        {"prefix": "\x08", "candidate": "\x0812路进4路出混音器", "label": 1},
        {"prefix": "test\x00null", "candidate": "test\x1bescape query\x7f", "label": 1},
        {"prefix": "clean", "candidate": "clean candidate query", "label": 1},
    ]
    _write_pairs(dirty_path, dirty_rows)

    tokenizer = Tokenizer(TokenizerConfig(mode="hybrid", max_vocab_size=100, max_char_vocab_size=100))
    tokenizer.fit_from_pairs_jsonl(dirty_path)

    # 1. No control characters exist anywhere in word_to_id
    for word in tokenizer.word_to_id:
        for ch in word:
            assert not is_disallowed_control(ch), f"Control character {ord(ch)} found in word {word!r}"

    # 2. No control characters exist anywhere in char_to_id
    for ch in tokenizer.char_to_id:
        if ch in ("<PAD>", "<UNK>", "<CLS>", "<SEP>"):
            continue
        assert not is_disallowed_control(ch), f"Control character {ord(ch)} found in char_to_id"

    # 3. Legitimate Unicode text is preserved despite the control character in the raw string
    assert "12路进4路出混音器" in tokenizer.word_to_id or any("12" in w for w in tokenizer.word_to_id)
    assert any("混音器" in w or "路" in w for w in tokenizer.word_to_id)

    # 4. Pure control characters encode to empty list
    assert tokenizer.encode("\x08") == []
    assert tokenizer.encode("\x00\x1b\x7f") == []

    # 5. Mixed strings have control characters stripped during encode
    assert tokenizer.encode("\x08clean") == tokenizer.encode("clean")


def test_unicode_preservation_multilingual() -> None:
    multilingual_texts = [
        "中文查询 搜索",
        "日本語の検索クエリ",
        "café au lait naïve façade",
        "поиск информации в яндексе",
        "مرحبا بالعالم",
        "2026 年新手机",
    ]
    tok = Tokenizer(TokenizerConfig(mode="hybrid", max_vocab_size=200, max_char_vocab_size=200))
    tok.fit(multilingual_texts)

    for text in multilingual_texts:
        ids = tok.encode(text)
        assert len(ids) > 0
        # Should not collapse to all UNK
        assert any(i != UNK_ID for i in ids)

    # Individual Unicode characters in char_to_id
    for ch in "中文検索café":
        assert ch in tok.char_to_id or ch in tok.word_to_id


def test_unknown_token_handling_hybrid_and_word_modes() -> None:
    # Train only on 'apple' and 'banana'
    hybrid_tok = Tokenizer(TokenizerConfig(mode="hybrid", max_vocab_size=20, max_char_vocab_size=20))
    hybrid_tok.fit(["apple", "banana"])

    # Unseen word made of known characters 'a', 'p', 'l', 'e' -> decomposed into char ids
    encoded_plea = hybrid_tok.encode("plea")
    assert all(i != UNK_ID for i in encoded_plea)
    assert len(encoded_plea) == 4

    # Unseen word with completely unseen character 'z' (not in apple, banana)
    encoded_zebra = hybrid_tok.encode("zebra")
    # 'z' -> UNK_ID, 'e' -> char id, 'b' -> char id, 'r' -> UNK_ID, 'a' -> char id
    assert UNK_ID in encoded_zebra

    # Word-mode tokenizer: any unseen word becomes single UNK_ID
    word_tok = Tokenizer(TokenizerConfig(mode="word", max_vocab_size=20))
    word_tok.fit(["apple", "banana"])
    assert word_tok.encode("plea") == [UNK_ID]
    assert word_tok.encode("apple") == [word_tok.word_to_id["apple"]]


def test_configured_limits_respected() -> None:
    # Generate many words and characters
    words = [f"word{i:04d}" for i in range(500)]
    tok = Tokenizer(
        TokenizerConfig(
            mode="hybrid",
            max_vocab_size=50,
            max_char_vocab_size=30,
            min_frequency=1,
            max_seq_length=15,
        )
    )
    tok.fit(words)
    # len(word_to_id) must not exceed max_vocab_size (50)
    assert len(tok.word_to_id) <= 50
    # len(char_to_id) must not exceed max_char_vocab_size (30)
    assert len(tok.char_to_id) <= 30
    assert tok.vocab_size == len(tok.word_to_id) + len(tok.char_to_id) - 4


def test_encode_truncates_sequences_before_padding() -> None:
    tokenizer = Tokenizer(TokenizerConfig(max_vocab_size=100, max_seq_length=8))
    tokenizer.fit(["machine learning"])
    full = tokenizer.encode("machine learning")
    assert tokenizer.encode("machine learning", max_length=1) == full[:1]

    padded, mask = tokenizer.pad(full, max_length=4)
    assert padded[:len(full)] == full
    assert padded[len(full):] == [PAD_ID] * (4 - len(full))
    assert mask == [1] * len(full) + [0] * (4 - len(full))


def test_pair_truncation_padding_and_save_load_consistency(tmp_path: Path) -> None:
    tokenizer = Tokenizer(TokenizerConfig(max_vocab_size=100, max_char_vocab_size=100, max_seq_length=8))
    tokenizer.fit(["machine learning", "中文查询"])
    pair = tokenizer.encode_pair("machine learning", "machine learning course", max_length=8)
    padded, mask = tokenizer.pad(pair, max_length=12)

    assert len(pair) <= 8
    assert len(padded) == 12
    assert len(mask) == 12
    assert padded[0] == CLS_ID
    assert SEP_ID in pair
    assert all(padded[index] == PAD_ID for index in range(len(pair), 12))

    tokenizer.save(tmp_path / "tokenizer")
    loaded = Tokenizer.load(tmp_path / "tokenizer")
    assert loaded.to_dict() == tokenizer.to_dict()
    assert loaded.encode_pair("machine", "中文查询") == tokenizer.encode_pair("machine", "中文查询")
