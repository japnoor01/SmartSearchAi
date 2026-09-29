#!/usr/bin/env python3
"""
src/models/lstm/inference.py

Production-quality inference and candidate-ranking pipeline for SmartSearch AI:

    User partial query
            ↓
    Real prefix index (SQLite range scan / hot cache)
            ↓
    Candidate suggestions
            ↓
    Deduplication & prefix validity filtering
            ↓
    LSTM ranker (eval mode, batch scoring, no gradients)
            ↓
    Ranked top-K suggestions

The LSTM is strictly a RANKER / RE-RANKER, not a text-generation model.
It evaluates joint (prefix, candidate) representations and scores them.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Sequence

import torch

# Ensure repository root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.models.lstm.model import LSTMRankerConfig, PrefixRankingLSTM  # noqa: E402
from src.models.lstm.tokenizer import Tokenizer  # noqa: E402
from src.retrieval.prefix_index import Candidate, PrefixIndex, normalize_prefix  # noqa: E402

LOG = logging.getLogger("smartsearch_inference")

# Default production artifact locations relative to repository root
DEFAULT_CHECKPOINT_PATH = PROJECT_ROOT / "models" / "lstm" / "best_model.pt"
DEFAULT_TOKENIZER_DIR = PROJECT_ROOT / "models" / "lstm" / "tokenizer"
DEFAULT_INDEX_PATH = PROJECT_ROOT / "data" / "indexes" / "prefix_real" / "prefix_index.sqlite3"


# ---------------------------------------------------------------------------
# Core LSTM Ranker
# ---------------------------------------------------------------------------


class LSTMRanker:
    """Production loadable-from-checkpoint LSTM candidate ranker.

    Loads the trained model weights and tokenizer once into memory, sets the
    model to eval mode, supports both CPU and CUDA inference, and evaluates
    candidate suggestions via batched forward passes with gradient computation
    disabled.
    """

    def __init__(
        self,
        checkpoint_path: str | Path,
        tokenizer_dir: str | Path,
        device: str | torch.device = "auto",
        batch_size: int = 64,
    ) -> None:
        self.checkpoint_path = Path(checkpoint_path)
        if not self.checkpoint_path.exists():
            raise FileNotFoundError(f"Model checkpoint not found: {self.checkpoint_path}")

        self.tokenizer_dir = Path(tokenizer_dir)
        if not self.tokenizer_dir.exists():
            raise FileNotFoundError(f"Tokenizer directory not found: {self.tokenizer_dir}")

        if isinstance(device, str):
            if device == "auto":
                resolved_device = "cuda" if torch.cuda.is_available() else "cpu"
            else:
                resolved_device = device
            self.device = torch.device(resolved_device)
        else:
            self.device = device

        self.batch_size = max(1, batch_size)
        self.tokenizer = Tokenizer.load(self.tokenizer_dir)

        # PyTorch 2.6+ defaults to weights_only=True; checkpoint contains model_config dict
        checkpoint = torch.load(self.checkpoint_path, map_location=self.device, weights_only=False)
        model_config = LSTMRankerConfig.from_dict(checkpoint["model_config"])
        self.model = PrefixRankingLSTM(model_config).to(self.device)
        self.model.load_state_dict(checkpoint["model_state_dict"])
        self.model.eval()
        self.max_seq_length = model_config.max_seq_length

        LOG.info(
            "LSTMRanker initialized on %s (checkpoint: %s, max_seq_length: %d)",
            self.device,
            self.checkpoint_path.name,
            self.max_seq_length,
        )

    def score(
        self,
        prefix: str,
        candidates: Sequence[str],
        batch_size: int | None = None,
    ) -> list[float]:
        """Return raw model logits for each candidate given the prefix.

        Uses torch.inference_mode() to avoid gradient computation and peak RAM.
        Batch size is configurable to prevent out-of-memory on large candidate
        pools.
        """
        if not candidates:
            return []

        bs = batch_size or self.batch_size
        bs = max(1, bs)
        all_logits: list[float] = []

        inf_context = getattr(torch, "inference_mode", torch.no_grad)

        with inf_context():
            for i in range(0, len(candidates), bs):
                chunk = candidates[i : i + bs]
                input_ids: list[list[int]] = []
                lengths: list[int] = []

                for cand in chunk:
                    cand_str = str(cand) if cand is not None else ""
                    ids = self.tokenizer.encode_pair(prefix or "", cand_str, max_length=self.max_seq_length)
                    padded, mask = self.tokenizer.pad(ids, max_length=self.max_seq_length)
                    input_ids.append(padded)
                    lengths.append(max(sum(mask), 1))

                input_ids_t = torch.tensor(input_ids, dtype=torch.long, device=self.device)
                lengths_t = torch.tensor(lengths, dtype=torch.long)
                logits = self.model(input_ids_t, lengths_t)
                all_logits.extend(logits.cpu().tolist())

        return all_logits

    def score_single(self, prefix: str, candidate: str) -> float:
        """Score a single candidate against a prefix."""
        scores = self.score(prefix, [candidate], batch_size=1)
        return scores[0] if scores else 0.0

    def score_pairs(
        self,
        pairs: Sequence[tuple[str, str]],
        batch_size: int | None = None,
    ) -> list[float]:
        """Score arbitrary (prefix, candidate) pairs in batches."""
        if not pairs:
            return []

        bs = batch_size or self.batch_size
        bs = max(1, bs)
        all_logits: list[float] = []

        inf_context = getattr(torch, "inference_mode", torch.no_grad)

        with inf_context():
            for i in range(0, len(pairs), bs):
                chunk = pairs[i : i + bs]
                input_ids: list[list[int]] = []
                lengths: list[int] = []

                for prefix, cand in chunk:
                    cand_str = str(cand) if cand is not None else ""
                    ids = self.tokenizer.encode_pair(prefix or "", cand_str, max_length=self.max_seq_length)
                    padded, mask = self.tokenizer.pad(ids, max_length=self.max_seq_length)
                    input_ids.append(padded)
                    lengths.append(max(sum(mask), 1))

                input_ids_t = torch.tensor(input_ids, dtype=torch.long, device=self.device)
                lengths_t = torch.tensor(lengths, dtype=torch.long)
                logits = self.model(input_ids_t, lengths_t)
                all_logits.extend(logits.cpu().tolist())

        return all_logits

    def rank_candidates(
        self,
        prefix: str,
        candidates: Sequence[str],
        top_k: int = 5,
        batch_size: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return up to top_k candidates ordered by descending model score.

        Ties are broken stably by the candidate's original position in the input
        list, ensuring deterministic output.

        Returns structured dicts:
            [{"suggestion": str, "score": float, "candidate": str}, ...]
        """
        if not candidates:
            return []

        # Convert to list and filter None
        clean_candidates = [str(c) if c is not None else "" for c in candidates]
        scores = self.score(prefix, clean_candidates, batch_size=batch_size)

        # Stable sort: highest score first, tie-break by original index
        ranked = sorted(
            enumerate(zip(clean_candidates, scores)),
            key=lambda x: (-x[1][1], x[0]),
        )

        return [
            {
                "suggestion": cand,
                "score": score,
                "candidate": cand,  # Backwards compatibility alias
            }
            for _, (cand, score) in ranked[: max(0, top_k)]
        ]


# ---------------------------------------------------------------------------
# High-level Autocomplete Ranking Pipeline
# ---------------------------------------------------------------------------


class SmartSearchRanker:
    """End-to-end autocomplete suggestion service class.

    Pipeline:
        1. Normalize user-typed partial query consistently with prefix index
        2. Query prefix index (SQLite range scan / hot cache) for candidate pool
        3. Deduplicate candidate suggestions
        4. Enforce prefix validity (reject candidates not starting with prefix)
        5. Pass candidate pool through trained LSTM ranker
        6. Return top-K suggestions sorted by descending score

    Keeps the prefix index connection and LSTM model loaded in memory for
    sub-millisecond to low-millisecond real-time autocomplete serving.
    """

    def __init__(
        self,
        index: PrefixIndex | str | Path | None = None,
        ranker: LSTMRanker | None = None,
        checkpoint_path: str | Path | None = None,
        tokenizer_dir: str | Path | None = None,
        index_path: str | Path | None = None,
        device: str | torch.device = "auto",
        candidate_pool_size: int = 50,
        default_top_k: int = 5,
        batch_size: int = 64,
    ) -> None:
        self.candidate_pool_size = max(1, candidate_pool_size)
        self.default_top_k = max(1, default_top_k)

        # Initialize or reuse ranker
        if ranker is not None:
            self.ranker = ranker
        else:
            ckpt = checkpoint_path or DEFAULT_CHECKPOINT_PATH
            tok = tokenizer_dir or DEFAULT_TOKENIZER_DIR
            self.ranker = LSTMRanker(ckpt, tok, device=device, batch_size=batch_size)

        # Initialize or reuse prefix index
        self._owns_index = False
        if hasattr(index, "get_candidates"):
            # Any duck-typed or concrete PrefixIndex instance
            self.index = index
        elif isinstance(index, (str, Path)):
            self.index = PrefixIndex(index)
            self._owns_index = True
        elif index is None:
            target_index = index_path or DEFAULT_INDEX_PATH
            self.index = PrefixIndex(target_index)
            self._owns_index = True
        else:
            raise TypeError(f"Invalid index specification of type {type(index).__name__}: {index}")

    def close(self) -> None:
        """Close resources such as the SQLite prefix index if owned."""
        if self._owns_index and hasattr(self.index, "close"):
            self.index.close()

    def __enter__(self) -> "SmartSearchRanker":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def suggest(
        self,
        prefix: str,
        top_k: int | None = None,
        candidate_pool_size: int | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve candidate suggestions from the prefix index and rank them
        using the LSTM model.

        Parameters:
            prefix: User-typed partial query string.
            top_k: Number of suggestions to return (defaults to self.default_top_k).
            candidate_pool_size: Number of raw candidates to fetch from the index
                before re-ranking (defaults to self.candidate_pool_size).

        Returns:
            List of structured suggestion dictionaries:
            [
                {
                    "suggestion": "deep learning",
                    "score": -2.0630,
                    "candidate": "deep learning"
                },
                ...
            ]
        """
        if prefix is None:
            return []

        k = top_k if top_k is not None else self.default_top_k
        if k <= 0:
            return []

        pool_size = candidate_pool_size or self.candidate_pool_size
        pool_size = max(pool_size, k)

        # Step a: Normalize prefix consistently with index build settings
        case_sensitive = getattr(self.index, "case_sensitive", False)
        unicode_form = getattr(self.index, "unicode_form", "NFKC")
        norm_prefix = normalize_prefix(prefix, case_sensitive=case_sensitive, unicode_form=unicode_form)

        # Step b & c: Query prefix index for candidate suggestions
        raw_candidates = self.index.get_candidates(norm_prefix, limit=pool_size)
        if not raw_candidates:
            return []

        # Step d & e: Deduplicate candidates and ensure prefix correspondence
        filtered_candidates: list[str] = []
        seen_normalized: set[str] = set()

        for cand in raw_candidates:
            if isinstance(cand, Candidate):
                text = cand.original_query or cand.normalized_query
            else:
                text = str(cand)

            text = text.strip()
            if not text:
                continue

            # Deduplication check
            cand_norm = normalize_prefix(text, case_sensitive=case_sensitive, unicode_form=unicode_form)
            if cand_norm in seen_normalized:
                continue
            seen_normalized.add(cand_norm)

            # Prefix validity check: ensure candidate corresponds to requested prefix
            if norm_prefix:
                if case_sensitive:
                    if not text.startswith(norm_prefix):
                        continue
                else:
                    if not cand_norm.startswith(norm_prefix):
                        continue

            filtered_candidates.append(text)

        if not filtered_candidates:
            return []

        # Step f, g, h, i: Score and rank candidates via LSTM
        return self.ranker.rank_candidates(prefix, filtered_candidates, top_k=k)

    def predict(
        self,
        prefix: str,
        top_k: int | None = None,
        candidate_pool_size: int | None = None,
    ) -> list[dict[str, Any]]:
        """Alias for suggest() to satisfy predictor interface conventions."""
        return self.suggest(prefix, top_k=top_k, candidate_pool_size=candidate_pool_size)


# Convenience alias for parity with task description naming
LSTMSuggestionRanker = SmartSearchRanker


# ---------------------------------------------------------------------------
# CLI / Demo Entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SmartSearch AI: Real-time LSTM prefix autocomplete and candidate ranker."
    )
    parser.add_argument(
        "--query",
        "--prefix",
        dest="query",
        type=str,
        default="deep learning",
        help="Partial user query prefix to autocomplete.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        help="Number of ranked suggestions to return (default: 5).",
    )
    parser.add_argument(
        "--candidate-pool",
        type=int,
        default=50,
        help="Candidate pool size retrieved from prefix index before ranking (default: 50).",
    )
    parser.add_argument(
        "--candidates",
        nargs="+",
        default=None,
        help="Optional explicit candidate list to rank (bypasses prefix index retrieval).",
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=DEFAULT_CHECKPOINT_PATH,
        help=f"Path to trained LSTM checkpoint (default: {DEFAULT_CHECKPOINT_PATH}).",
    )
    parser.add_argument(
        "--tokenizer-dir",
        type=Path,
        default=DEFAULT_TOKENIZER_DIR,
        help=f"Path to saved tokenizer directory (default: {DEFAULT_TOKENIZER_DIR}).",
    )
    parser.add_argument(
        "--index-path",
        type=Path,
        default=DEFAULT_INDEX_PATH,
        help=f"Path to SQLite prefix index (default: {DEFAULT_INDEX_PATH}).",
    )
    parser.add_argument(
        "--device",
        type=str,
        default="auto",
        help="Inference device: 'auto', 'cpu', or 'cuda' (default: 'auto').",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print structured JSON output instead of plain text.",
    )
    args = parser.parse_args()

    query = args.query

    if args.candidates:
        # Direct candidate re-ranking mode
        ranker = LSTMRanker(
            checkpoint_path=args.checkpoint,
            tokenizer_dir=args.tokenizer_dir,
            device=args.device,
        )
        t0 = time.perf_counter()
        results = ranker.rank_candidates(query, args.candidates, top_k=args.top_k)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
    else:
        # Full end-to-end pipeline: Prefix Index -> Candidates -> LSTM Ranker -> Top-K
        with SmartSearchRanker(
            index_path=args.index_path,
            checkpoint_path=args.checkpoint,
            tokenizer_dir=args.tokenizer_dir,
            device=args.device,
            candidate_pool_size=args.candidate_pool,
            default_top_k=args.top_k,
        ) as pipeline:
            t0 = time.perf_counter()
            results = pipeline.suggest(query, top_k=args.top_k, candidate_pool_size=args.candidate_pool)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0

    if args.json:
        payload = {
            "query": query,
            "latency_ms": round(elapsed_ms, 2),
            "top_k": args.top_k,
            "suggestions": results,
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"Prefix: {query}\n")
        if not results:
            print("  (No suggestions found)")
        else:
            for i, item in enumerate(results, start=1):
                print(f"{i}. {item['suggestion']}")
                print(f"   score: {item['score']:.4f}\n")
        print(f"(Pipeline latency: {elapsed_ms:.2f} ms)")


if __name__ == "__main__":
    main()
