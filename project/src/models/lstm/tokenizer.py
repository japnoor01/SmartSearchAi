"""
src/models/lstm/tokenizer.py

Tokenizer for the LSTM prefix-ranking model.

Requirements from the task (section 3): handle English, multilingual
Unicode text, punctuation, numbers, code-like queries, and unknown tokens,
without being an English-only tokenizer, and be configurable. No PyTorch
dependency here -- this module only needs the standard library plus
`json`, so it can be built, saved, and unit-tested in any environment
(including this sandbox, which lacks PyTorch -- see docs/lstm_model.md).

Design
------
Word-level tokenization is not enough on its own for this vocabulary: code
snippets ("df.groupby('col').mean()"), URLs, and mixed-script queries
produce very high OOV rates if every unseen "word" simply becomes <UNK>.
Pure character-level tokenization handles everything but produces long
sequences and throws away whole-word signal for the common case (plain
natural-language queries), which matters more for an LSTM's limited
context window than for a Transformer's.

This tokenizer therefore does **hybrid word+character fallback**
(configurable -- see `TokenizerConfig.mode`):

  - `"hybrid"` (default): split on a Unicode-aware regex that separates
    runs of "word" characters (`\\w`, which covers Latin, CJK, Cyrillic,
    Greek, etc. under `re.UNICODE`) from runs of punctuation/symbols, so
    "node.js" -> ["node", ".", "js"] and "café" stays one token (NFKC
    normalization keeps combining marks canonical). Any resulting token
    not in the trained vocabulary is NOT immediately mapped to a single
    <UNK> -- it is first decomposed into characters, and each character is
    looked up in a small trained character vocabulary (falling back to a
    single <UNK> only for characters never seen during vocab training
    either). This keeps unseen code identifiers, numbers, and rare
    multilingual tokens partially informative instead of collapsing to a
    single opaque symbol.
  - `"word"`: word-level only, OOV words -> <UNK>. Simpler, smaller
    sequences, higher OOV rate. Useful as an ablation/baseline.
  - `"char"`: pure character-level. Never OOV (beyond the trained
    character set, which is capped only by `max_char_vocab_size`), longest
    sequences.

The trained vocabulary (word tokens meeting `min_frequency`, plus the
character fallback vocabulary) is saved as one JSON file so inference can
load byte-for-byte the same tokenizer used at training time (task
requirement: "Save the trained vocabulary/tokenizer so inference can reuse
exactly the same tokenizer").
"""

from __future__ import annotations

import json
import re
import sqlite3
import tempfile
import time
import unicodedata
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable

PAD_TOKEN = "<PAD>"
UNK_TOKEN = "<UNK>"
CLS_TOKEN = "<CLS>"
SEP_TOKEN = "<SEP>"
SPECIAL_TOKENS = [PAD_TOKEN, UNK_TOKEN, CLS_TOKEN, SEP_TOKEN]

PAD_ID = 0
UNK_ID = 1
CLS_ID = 2
SEP_ID = 3

# Splits into runs of "word" characters (Unicode-aware: covers Latin, CJK,
# Cyrillic, Greek, digits, underscore) vs. individual punctuation/symbol
# characters. `re.UNICODE` is implicit for str patterns in Python 3.
_WORD_OR_SYMBOL_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)


def is_disallowed_control(ch: str) -> bool:
    """Check if character is an ASCII control character (excluding whitespace \t, \n, \r)."""
    code = ord(ch)
    return (code < 32 and ch not in "\t\n\r") or code == 127


def _strip_disallowed_controls(text: str) -> str:
    """Remove ASCII control characters while preserving legitimate Unicode (CJK, accents, etc.)."""
    return "".join(ch for ch in text if not is_disallowed_control(ch))


@dataclass
class TokenizerConfig:
    mode: str = "hybrid"  # "hybrid" | "word" | "char"
    lowercase: bool = True
    unicode_form: str = "NFKC"
    max_vocab_size: int = 20000
    max_char_vocab_size: int = 2000
    min_frequency: int = 1
    max_seq_length: int = 40  # total tokens across [CLS] prefix [SEP] candidate [SEP]


def _pretokenize(text: str, cfg: TokenizerConfig) -> list[str]:
    if not text:
        return []
    cleaned = _strip_disallowed_controls(text)
    if not cleaned:
        return []
    normalized = unicodedata.normalize(cfg.unicode_form, cleaned)
    if cfg.lowercase:
        normalized = normalized.casefold()
    tokens = _WORD_OR_SYMBOL_RE.findall(normalized)
    return [t for t in tokens if not all(is_disallowed_control(c) for c in t)]


class Tokenizer:
    """Trainable, saveable/loadable hybrid word+character tokenizer.

    Usage:
        tok = Tokenizer(TokenizerConfig(mode="hybrid"))
        tok.fit(list_of_query_strings)
        ids = tok.encode_pair("machine lea", "machine learning")
        tok.save("models/lstm/tokenizer")
        tok2 = Tokenizer.load("models/lstm/tokenizer")
    """

    def __init__(self, config: TokenizerConfig | None = None) -> None:
        self.config = config or TokenizerConfig()
        # Single unified id space: special tokens, then whole-word tokens,
        # then character-fallback tokens appended *after* all word ids so
        # the two never collide (a word id and a char id are never the
        # same integer, even though they're built from separate frequency
        # tables) -- word_to_id and char_to_id below are disjoint views
        # into one id space, both usable directly by model.py's single
        # embedding table (vocab_size = len(word_to_id) + len(char_to_id)
        # - len(SPECIAL_TOKENS), since specials are shared).
        self.word_to_id: dict[str, int] = {}
        self.char_to_id: dict[str, int] = {}
        self._fitted = False

    # -- training -----------------------------------------------------

    def fit(self, texts: Iterable[str]) -> "Tokenizer":
        word_freq: dict[str, int] = {}
        char_freq: dict[str, int] = {}
        for text in texts:
            for tok in _pretokenize(text, self.config):
                if any(is_disallowed_control(c) for c in tok):
                    continue
                word_freq[tok] = word_freq.get(tok, 0) + 1
                if self.config.mode in ("hybrid", "char"):
                    for ch in tok:
                        if is_disallowed_control(ch):
                            continue
                        char_freq[ch] = char_freq.get(ch, 0) + 1

        self.word_to_id = {t: i for i, t in enumerate(SPECIAL_TOKENS)}
        if self.config.mode in ("hybrid", "word"):
            ranked = sorted(
                (t for t, f in word_freq.items() if f >= self.config.min_frequency),
                key=lambda t: (-word_freq[t], t),
            )
            budget = max(0, self.config.max_vocab_size - len(SPECIAL_TOKENS))
            for tok in ranked[:budget]:
                self.word_to_id[tok] = len(self.word_to_id)

        # Character ids continue the SAME id space, starting right after
        # the last word id, so a flat list mixing word-hit ids and
        # char-fallback ids (as encode_pair produces) is unambiguous.
        next_id = len(self.word_to_id)
        self.char_to_id = dict(zip(SPECIAL_TOKENS, range(len(SPECIAL_TOKENS))))
        if self.config.mode in ("hybrid", "char"):
            ranked_chars = sorted(char_freq.keys(), key=lambda c: (-char_freq[c], c))
            budget = max(0, self.config.max_char_vocab_size - len(SPECIAL_TOKENS))
            for ch in ranked_chars[:budget]:
                self.char_to_id[ch] = next_id
                next_id += 1

        self._fitted = True
        return self

    def fit_from_pairs_jsonl(
        self,
        pairs_path: str | Path,
        save_dir: str | Path | None = None,
        batch_size: int = 50000,
        progress_callback: callable | None = None,
    ) -> dict:
        """Fit from one training-pairs JSONL file without loading it in RAM.

        Prefixes and candidates are deduplicated in a temporary SQLite store
        before counting, so repeated positives do not multiply vocabulary
        frequency merely because they occur in many training pairs. The
        caller must pass the train split only; dev data is never read here.
        """
        pairs_path = Path(pairs_path)
        started = time.time()
        stats = {
            "pairs_path": str(pairs_path),
            "source_pairs_scanned": 0,
            "positive_pairs_scanned": 0,
            "unique_positive_candidates": 0,
            "unique_texts_fitted": 0,
        }
        with tempfile.TemporaryDirectory(prefix="smartsearch_tokenizer_") as temp_dir:
            connection = sqlite3.connect(Path(temp_dir) / "texts.sqlite3")
            try:
                connection.execute("PRAGMA synchronous = OFF")
                connection.execute("PRAGMA journal_mode = OFF")
                connection.execute("PRAGMA cache_size = -64000")
                connection.execute("PRAGMA temp_store = MEMORY")
                connection.execute("CREATE TABLE texts (text TEXT PRIMARY KEY)")
                connection.execute("CREATE TABLE positive_candidates (text TEXT PRIMARY KEY)")

                text_batch: list[tuple[str]] = []
                pos_batch: list[tuple[str]] = []

                with pairs_path.open("r", encoding="utf-8") as pairs:
                    for line in pairs:
                        line = line.strip()
                        if not line:
                            continue
                        row = json.loads(line)
                        stats["source_pairs_scanned"] += 1
                        prefix = row["prefix"]
                        candidate = row["candidate"]
                        text_batch.append((prefix,))
                        text_batch.append((candidate,))
                        if row.get("label") == 1:
                            stats["positive_pairs_scanned"] += 1
                            pos_batch.append((candidate,))

                        if len(text_batch) >= batch_size:
                            connection.executemany("INSERT OR IGNORE INTO texts(text) VALUES (?)", text_batch)
                            text_batch.clear()

                        if len(pos_batch) >= batch_size:
                            connection.executemany(
                                "INSERT OR IGNORE INTO positive_candidates(text) VALUES (?)", pos_batch
                            )
                            pos_batch.clear()

                        if progress_callback and stats["source_pairs_scanned"] % 500000 == 0:
                            progress_callback(stats["source_pairs_scanned"])

                if text_batch:
                    connection.executemany("INSERT OR IGNORE INTO texts(text) VALUES (?)", text_batch)
                    text_batch.clear()
                if pos_batch:
                    connection.executemany(
                        "INSERT OR IGNORE INTO positive_candidates(text) VALUES (?)", pos_batch
                    )
                    pos_batch.clear()

                connection.commit()
                stats["unique_positive_candidates"] = connection.execute(
                    "SELECT COUNT(*) FROM positive_candidates"
                ).fetchone()[0]
                stats["unique_texts_fitted"] = connection.execute("SELECT COUNT(*) FROM texts").fetchone()[0]
                self.fit((row[0] for row in connection.execute("SELECT text FROM texts ORDER BY text")))
            finally:
                connection.close()

        if save_dir is not None:
            self.save(save_dir)
            stats["save_dir"] = str(save_dir)
        stats["vocab_size"] = self.vocab_size
        stats["word_vocab_size"] = len(self.word_to_id)
        stats["char_vocab_size"] = len(self.char_to_id)
        stats["fit_seconds"] = round(time.time() - started, 3)
        return stats

    # -- encoding -------------------------------------------------------

    def _encode_token(self, tok: str) -> list[int]:
        """Encode a single pre-tokenized unit to one or more ids: a direct
        word-vocab hit is one id; an OOV word in "hybrid" mode falls back
        to per-character ids (each looked up in the char vocab, else
        <UNK>); "word" mode maps any OOV straight to <UNK>."""
        if tok in self.word_to_id:
            return [self.word_to_id[tok]]
        if self.config.mode == "word":
            return [UNK_ID]
        # hybrid (OOV word) or char mode: character fallback.
        return [self.char_to_id.get(ch, UNK_ID) for ch in tok] or [UNK_ID]

    def encode(self, text: str, max_length: int | None = None) -> list[int]:
        if not self._fitted:
            raise RuntimeError("Tokenizer.fit() (or .load()) must be called before encode().")
        ids: list[int] = []
        for tok in _pretokenize(text, self.config):
            ids.extend(self._encode_token(tok))
        if max_length is not None:
            ids = ids[:max_length]
        return ids

    def encode_pair(
        self, prefix: str, candidate: str, max_length: int | None = None
    ) -> list[int]:
        """Build the joint [CLS] prefix [SEP] candidate [SEP] sequence
        (task section 4) that lets the LSTM read both sides in one pass
        and let its recurrent state model interaction between them,
        instead of encoding prefix/candidate independently and comparing
        fixed vectors afterwards. See docs/lstm_model.md, "Sequence
        representation", for the reasoning."""
        max_length = max_length or self.config.max_seq_length
        # Reserve 3 slots for [CLS] and the two [SEP]s up front, then split
        # the remaining budget so a long candidate can't starve the prefix
        # (or vice versa) -- both sides are informative.
        budget = max(0, max_length - 3)
        prefix_budget = budget // 2
        candidate_budget = budget - prefix_budget

        prefix_ids = self.encode(prefix, max_length=prefix_budget)
        candidate_ids = self.encode(candidate, max_length=candidate_budget)

        return [CLS_ID, *prefix_ids, SEP_ID, *candidate_ids, SEP_ID]

    def pad(self, ids: list[int], max_length: int | None = None) -> tuple[list[int], list[int]]:
        """Right-pad `ids` to `max_length`, returning (padded_ids,
        attention_mask). Truncates if already longer."""
        max_length = max_length or self.config.max_seq_length
        ids = ids[:max_length]
        mask = [1] * len(ids)
        pad_n = max_length - len(ids)
        ids = ids + [PAD_ID] * pad_n
        mask = mask + [0] * pad_n
        return ids, mask

    # -- persistence ------------------------------------------------------

    @property
    def vocab_size(self) -> int:
        """Total distinct integer ids this tokenizer can emit -- one
        unified id space (specials, shared, then words, then chars), so
        this is exactly what model.py's `vocab_size` config must equal for
        its single embedding table to cover every id encode_pair() can
        produce."""
        if self.config.mode == "word":
            return len(self.word_to_id)
        if self.config.mode == "char":
            return len(self.char_to_id)
        return len(self.word_to_id) + len(self.char_to_id) - len(SPECIAL_TOKENS)

    def to_dict(self) -> dict:
        return {
            "config": asdict(self.config),
            "word_to_id": self.word_to_id,
            "char_to_id": self.char_to_id,
        }

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        with open(path / "tokenizer.json", "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: str | Path) -> "Tokenizer":
        path = Path(path)
        file_path = path / "tokenizer.json" if path.is_dir() else path
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        tok = cls(TokenizerConfig(**data["config"]))
        tok.word_to_id = data["word_to_id"]
        tok.char_to_id = data["char_to_id"]
        tok._fitted = True
        return tok
