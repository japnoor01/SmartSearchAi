#!/usr/bin/env python3
"""
src/models/lstm/train.py

Train the LSTM ranking model on the (prefix, candidate, label) pairs
produced by create_training_pairs.py.

Architecture:
    prefix -> prefix index -> candidate queries -> LSTM ranking -> top 5

The LSTM reads the joint sequence:
    [CLS] prefix [SEP] candidate [SEP]
and predicts relevance scores. Positive candidates are trained to achieve
higher relevance scores than negatives.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Iterator

import torch
import torch.nn.functional as F
import yaml
from torch import nn
from torch.utils.data import DataLoader, Dataset, IterableDataset

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from src.models.lstm.model import build_model_from_config  # noqa: E402
from src.models.lstm.tokenizer import Tokenizer  # noqa: E402


class PairDataset(Dataset):
    """Reads a .jsonl file into memory for small fixture tests."""

    def __init__(self, pairs_path: Path | str, tokenizer: Tokenizer, max_seq_length: int) -> None:
        self.examples: list[dict] = []
        with open(pairs_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    self.examples.append(json.loads(line))
        self.tokenizer = tokenizer
        self.max_seq_length = max_seq_length

    def __len__(self) -> int:
        return len(self.examples)

    def __getitem__(self, idx: int) -> dict:
        ex = self.examples[idx]
        ids = self.tokenizer.encode_pair(ex["prefix"], ex["candidate"], max_length=self.max_seq_length)
        padded, mask = self.tokenizer.pad(ids, max_length=self.max_seq_length)
        return {
            "input_ids": torch.tensor(padded, dtype=torch.long),
            "length": torch.tensor(sum(mask), dtype=torch.long).clamp(min=1),
            "label": torch.tensor(float(ex["label"]), dtype=torch.float32),
            "prefix": ex["prefix"],
            "candidate": ex["candidate"],
        }


class StreamingPairDataset(IterableDataset):
    """Streams a 3.2M+ pairs JSONL line-by-line without loading into RAM,
    using a bounded shuffle buffer for local randomness."""

    def __init__(
        self,
        pairs_path: Path | str,
        tokenizer: Tokenizer,
        max_seq_length: int,
        buffer_size: int = 10000,
        shuffle: bool = True,
        seed: int = 1337,
        max_examples: int | None = None,
    ) -> None:
        super().__init__()
        self.pairs_path = Path(pairs_path)
        self.tokenizer = tokenizer
        self.max_seq_length = max_seq_length
        self.buffer_size = buffer_size
        self.shuffle = shuffle
        self.seed = seed
        self.max_examples = max_examples

    def _parse_and_encode(self, row: dict) -> dict:
        ids = self.tokenizer.encode_pair(row["prefix"], row["candidate"], max_length=self.max_seq_length)
        padded, mask = self.tokenizer.pad(ids, max_length=self.max_seq_length)
        return {
            "input_ids": torch.tensor(padded, dtype=torch.long),
            "length": torch.tensor(sum(mask), dtype=torch.long).clamp(min=1),
            "label": torch.tensor(float(row["label"]), dtype=torch.float32),
            "prefix": row["prefix"],
            "candidate": row["candidate"],
        }

    def __iter__(self) -> Iterator[dict]:
        worker_info = torch.utils.data.get_worker_info()
        worker_id = worker_info.id if worker_info else 0
        num_workers = worker_info.num_workers if worker_info else 1
        rng = random.Random(self.seed + worker_id)

        def _line_iterator():
            count = 0
            with open(self.pairs_path, "r", encoding="utf-8") as f:
                for line_idx, line in enumerate(f):
                    if line_idx % num_workers != worker_id:
                        continue
                    line = line.strip()
                    if line:
                        yield json.loads(line)
                        count += 1
                        if self.max_examples is not None and count >= self.max_examples:
                            return

        if not self.shuffle:
            for row in _line_iterator():
                yield self._parse_and_encode(row)
            return

        buffer: list[dict] = []
        for row in _line_iterator():
            buffer.append(row)
            if len(buffer) >= self.buffer_size:
                idx = rng.randrange(len(buffer))
                chosen = buffer.pop(idx)
                yield self._parse_and_encode(chosen)

        rng.shuffle(buffer)
        for row in buffer:
            yield self._parse_and_encode(row)


def collate_fn(batch: list[dict]) -> dict:
    return {
        "input_ids": torch.stack([b["input_ids"] for b in batch]),
        "length": torch.stack([b["length"] for b in batch]),
        "label": torch.stack([b["label"] for b in batch]),
        "prefix": [b["prefix"] for b in batch],
        "candidate": [b["candidate"] for b in batch],
    }


def pairwise_hinge_loss(scores: torch.Tensor, labels: torch.Tensor, prefixes: list[str], margin: float) -> torch.Tensor:
    """Group a batch's examples by prefix; for every (positive, negative)
    pair sharing a prefix within the batch, penalize
    max(0, margin - (pos_score - neg_score))."""
    by_prefix: dict[str, list[int]] = defaultdict(list)
    for i, p in enumerate(prefixes):
        by_prefix[p].append(i)

    losses = []
    for idxs in by_prefix.values():
        pos_idxs = [i for i in idxs if labels[i] > 0.5]
        neg_idxs = [i for i in idxs if labels[i] <= 0.5]
        for pi in pos_idxs:
            for ni in neg_idxs:
                losses.append(F.relu(margin - (scores[pi] - scores[ni])))
    if not losses:
        return scores.sum() * 0.0
    return torch.stack(losses).mean()


def compute_ranking_metrics(
    model: nn.Module,
    dev_source: PairDataset | Path | str | list[dict],
    device: torch.device,
    tokenizer: Tokenizer | None = None,
    max_seq_length: int = 40,
    top_k: int = 5,
    max_groups: int | None = None,
) -> dict:
    """Evaluate ranking metrics on validation groups (prefix -> candidates).
    Computes P@1, P@k, Recall@k, MRR@k, Hit Rate@k, and scoring latency."""
    model.eval()

    # Determine examples generator or list
    tok = tokenizer
    if isinstance(dev_source, PairDataset):
        examples_iter = dev_source.examples
        tok = dev_source.tokenizer
        max_seq_length = dev_source.max_seq_length
    elif isinstance(dev_source, list):
        examples_iter = dev_source
    else:
        # Stream lines from Path/str
        def _stream_rows():
            with open(dev_source, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        yield json.loads(line)
        examples_iter = _stream_rows()

    if tok is None:
        raise ValueError("Tokenizer must be provided to compute_ranking_metrics.")

    p_at_1, p_at_k, r_at_k, mrr_at_k, hit_at_k = [], [], [], [], []
    latencies_ms = []

    # Stream groups sequentially by prefix
    current_prefix: str | None = None
    current_group: list[dict] = []
    n_groups = 0

    def _eval_group(group: list[dict]) -> None:
        nonlocal n_groups
        if not any(e["label"] == 1 for e in group):
            return

        input_ids, lengths = [], []
        t0 = time.perf_counter()
        for ex in group:
            ids = tok.encode_pair(ex["prefix"], ex["candidate"], max_length=max_seq_length)
            padded, mask = tok.pad(ids, max_length=max_seq_length)
            input_ids.append(padded)
            lengths.append(sum(mask))

        input_ids_t = torch.tensor(input_ids, dtype=torch.long, device=device)
        lengths_t = torch.tensor(lengths, dtype=torch.long).clamp(min=1)
        with torch.no_grad():
            scores = model(input_ids_t, lengths_t).cpu().tolist()
        latencies_ms.append((time.perf_counter() - t0) * 1000)

        ranked = sorted(zip(group, scores), key=lambda gs: gs[1], reverse=True)
        ranked_labels = [g["label"] for g, _ in ranked]
        n_relevant = sum(1 for l in ranked_labels if l == 1)

        top1 = ranked_labels[:1]
        topk = ranked_labels[:top_k]
        p_at_1.append(1.0 if top1 and top1[0] == 1 else 0.0)
        p_at_k.append(sum(topk) / len(topk) if topk else 0.0)
        r_at_k.append(sum(topk) / n_relevant if n_relevant else 0.0)
        hit_at_k.append(1.0 if any(l == 1 for l in topk) else 0.0)
        rr = 0.0
        for rank, l in enumerate(topk, start=1):
            if l == 1:
                rr = 1.0 / rank
                break
        mrr_at_k.append(rr)
        n_groups += 1

    for row in examples_iter:
        prefix = row["prefix"]
        if current_prefix is None:
            current_prefix = prefix
            current_group = [row]
        elif prefix == current_prefix:
            current_group.append(row)
        else:
            _eval_group(current_group)
            if max_groups is not None and n_groups >= max_groups:
                current_group = []
                break
            current_prefix = prefix
            current_group = [row]

    if current_group and (max_groups is None or n_groups < max_groups):
        _eval_group(current_group)

    def _mean(xs: list[float]) -> float:
        return round(sum(xs) / len(xs), 4) if xs else 0.0

    return {
        "precision@1": _mean(p_at_1),
        f"precision@{top_k}": _mean(p_at_k),
        f"recall@{top_k}": _mean(r_at_k),
        f"mrr@{top_k}": _mean(mrr_at_k),
        f"hit_rate@{top_k}": _mean(hit_at_k),
        "avg_scoring_latency_ms": round(sum(latencies_ms) / len(latencies_ms), 4) if latencies_ms else None,
        "n_groups_evaluated": len(mrr_at_k),
    }


def get_device(requested: str) -> torch.device:
    if requested == "cpu":
        return torch.device("cpu")
    if requested == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("train.device='cuda' requested but torch.cuda.is_available() is False.")
        return torch.device("cuda")
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/lstm.yaml"))
    parser.add_argument("--epochs", type=int, default=None, help="Override epochs from config.")
    parser.add_argument("--batch-size", type=int, default=None, help="Override batch_size from config.")
    parser.add_argument("--max-train-steps", type=int, default=None, help="Cap training steps per epoch.")
    parser.add_argument("--max-dev-groups", type=int, default=1000, help="Cap validation prefix groups per epoch.")
    parser.add_argument("--device", type=str, default=None, help="Override device ('auto', 'cpu', 'cuda').")
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    torch.manual_seed(cfg["seed"])
    random.seed(cfg["seed"])

    tok_cfg = cfg["tokenizer"]
    tokenizer = Tokenizer.load(tok_cfg["save_dir"])

    train_cfg = cfg["train"]
    device_str = args.device or train_cfg.get("device", "auto")
    device = get_device(device_str)
    print(f"Using device: {device} ({'GPU: ' + torch.cuda.get_device_name(0) if device.type == 'cuda' else 'CPU'})")

    batch_size = args.batch_size or train_cfg["batch_size"]
    epochs = args.epochs or train_cfg["epochs"]
    max_seq_length = cfg["model"]["max_seq_length"]
    train_pairs_path = Path(cfg["training_pairs"]["train_pairs_path"])
    dev_pairs_path = Path(cfg["training_pairs"]["dev_pairs_path"])

    print(f"Loading training pairs streaming from: {train_pairs_path}")
    train_ds = StreamingPairDataset(
        train_pairs_path,
        tokenizer,
        max_seq_length,
        buffer_size=10000,
        shuffle=True,
        seed=cfg["seed"],
    )

    train_loader = DataLoader(
        train_ds,
        batch_size=batch_size,
        collate_fn=collate_fn,
        num_workers=train_cfg.get("num_workers", 0),
    )

    model = build_model_from_config(cfg["model"], vocab_size=tokenizer.vocab_size).to(device)
    n_params = model.num_trainable_parameters()
    print(f"Model trainable parameters: {n_params:,}")

    if train_cfg["optimizer"] == "adamw":
        optimizer = torch.optim.AdamW(
            model.parameters(), lr=train_cfg["learning_rate"], weight_decay=train_cfg["weight_decay"]
        )
    else:
        optimizer = torch.optim.Adam(model.parameters(), lr=train_cfg["learning_rate"])

    bce_loss_fn = nn.BCEWithLogitsLoss()

    checkpoint_dir = Path(train_cfg["checkpoint_dir"])
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    best_metric_value = -1.0
    epochs_without_improvement = 0
    metric_key = train_cfg["early_stopping_metric"]
    history = []

    t_start = time.time()
    for epoch in range(1, epochs + 1):
        model.train()
        running_loss = 0.0
        n_batches = 0
        step_times = []

        print(f"\n--- Epoch {epoch}/{epochs} ---")
        t_epoch_start = time.time()

        for step, batch in enumerate(train_loader, start=1):
            t_s = time.perf_counter()
            input_ids = batch["input_ids"].to(device)
            lengths = batch["length"]
            labels = batch["label"].to(device)

            optimizer.zero_grad()
            scores = model(input_ids, lengths)

            if train_cfg["loss"] == "pairwise_hinge":
                loss = pairwise_hinge_loss(scores, labels, batch["prefix"], train_cfg["pairwise_margin"])
            else:
                loss = bce_loss_fn(scores, labels)

            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), train_cfg["grad_clip_norm"])
            optimizer.step()

            step_times.append(time.perf_counter() - t_s)
            running_loss += loss.item()
            n_batches += 1

            if step % train_cfg["log_every_n_steps"] == 0:
                avg_step_ms = (sum(step_times[-train_cfg["log_every_n_steps"]:]) / train_cfg["log_every_n_steps"]) * 1000
                print(f"epoch {epoch} step {step}: loss={running_loss / n_batches:.4f} ({avg_step_ms:.1f} ms/step)")

            if args.max_train_steps is not None and step >= args.max_train_steps:
                print(f"Reached max_train_steps ({args.max_train_steps}) for epoch {epoch}.")
                break

        epoch_train_time = time.time() - t_epoch_start

        # Validation ranking metrics
        print("Running validation ranking evaluation...")
        val_metrics = compute_ranking_metrics(
            model,
            dev_pairs_path,
            device=device,
            tokenizer=tokenizer,
            max_seq_length=max_seq_length,
            top_k=cfg["evaluate"]["top_k"],
            max_groups=args.max_dev_groups,
        )

        epoch_record = {
            "epoch": epoch,
            "train_loss": round(running_loss / max(n_batches, 1), 4),
            "epoch_train_seconds": round(epoch_train_time, 2),
            **val_metrics,
        }
        history.append(epoch_record)
        print(f"Epoch {epoch} finished: {epoch_record}")

        current_metric = val_metrics.get(metric_key, 0.0)
        best_ckpt_path = checkpoint_dir / train_cfg["best_checkpoint_name"]

        if current_metric > best_metric_value:
            best_metric_value = current_metric
            epochs_without_improvement = 0
            torch.save(
                {
                    "model_state_dict": model.state_dict(),
                    "model_config": model.config.to_dict(),
                    "epoch": epoch,
                    "val_metrics": val_metrics,
                },
                best_ckpt_path,
            )
            print(f"  -> New best checkpoint ({metric_key}={current_metric:.4f}) saved to {best_ckpt_path}")
        else:
            epochs_without_improvement += 1
            if epochs_without_improvement >= train_cfg["early_stopping_patience"]:
                print(
                    f"Early stopping at epoch {epoch} (no {metric_key} improvement for "
                    f"{train_cfg['early_stopping_patience']} epochs)."
                )
                break

    total_time = time.time() - t_start
    best_ckpt_path = checkpoint_dir / train_cfg["best_checkpoint_name"]
    checkpoint_size = best_ckpt_path.stat().st_size if best_ckpt_path.exists() else 0

    with open(checkpoint_dir / "model_config.json", "w", encoding="utf-8") as f:
        json.dump(model.config.to_dict(), f, indent=2)

    training_summary = {
        "total_training_seconds": round(total_time, 2),
        "epochs_run": len(history),
        "best_metric": metric_key,
        "best_metric_value": best_metric_value,
        "device": str(device),
        "trainable_parameters": n_params,
        "checkpoint_size_bytes": checkpoint_size,
        "checkpoint_path": str(best_ckpt_path),
        "history": history,
    }
    summary_path = checkpoint_dir / "training_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(training_summary, f, indent=2)

    report_path = Path(cfg["evaluate"].get("report_path", "reports/models/lstm_training_report.json"))
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(training_summary, f, indent=2)

    print("\nTraining summary:")
    print(json.dumps(training_summary, indent=2))


if __name__ == "__main__":
    main()
