#!/usr/bin/env python3
"""Fit and save the LSTM tokenizer from the train-pairs JSONL only."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.models.lstm.tokenizer import Tokenizer, TokenizerConfig  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/lstm.yaml"))
    parser.add_argument("--pairs", type=Path, default=None)
    parser.add_argument("--save-dir", type=Path, default=None)
    args = parser.parse_args()

    with args.config.open("r", encoding="utf-8") as config_file:
        config = yaml.safe_load(config_file)
    tokenizer_options = dict(config["tokenizer"])
    tokenizer_options.pop("save_dir", None)
    tokenizer_config = TokenizerConfig(**tokenizer_options)
    save_dir = args.save_dir or Path(config["tokenizer"]["save_dir"])
    tokenizer = Tokenizer(tokenizer_config)
    pairs_path = args.pairs or Path(config["training_pairs"]["train_pairs_path"])

    def on_progress(count: int) -> None:
        print(f"[fit_tokenizer] Scanned {count:,} training pairs...", file=sys.stderr, flush=True)

    print(f"[fit_tokenizer] Fitting tokenizer from {pairs_path} to {save_dir}...", file=sys.stderr, flush=True)
    stats = tokenizer.fit_from_pairs_jsonl(pairs_path, save_dir=save_dir, progress_callback=on_progress)
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
