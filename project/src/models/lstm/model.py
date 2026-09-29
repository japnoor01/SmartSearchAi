"""
src/models/lstm/model.py

PyTorch LSTM ranking model for the SmartSearchAI autocomplete pipeline.

*** Execution status: this file has NOT been run in this session. ***
This sandbox has no network access and PyTorch is not installed, so this
module could not be imported or unit-tested here (see docs/lstm_model.md,
"Data & execution status", for the full explanation and what to run once
PyTorch is available). The code below is written to the same standard as
the rest of this codebase and follows the task's architecture spec exactly
-- it is the deliverable, just not one this environment could execute.

Architecture (task section 5)
------------------------------
    token ids  ([CLS] prefix [SEP] candidate [SEP], from tokenizer.py)
        v
    Embedding(vocab_size, embedding_dim, padding_idx=PAD_ID)
        v
    LSTM(embedding_dim, hidden_dim, num_layers, bidirectional?, batch_first=True)
      -- packed with pack_padded_sequence so padding never contributes to
         the recurrent state (see PrefixRankingLSTM.forward)
        v
    take the final layer's last real (non-pad) hidden state
      (both directions, concatenated, if bidirectional)
        v
    Dropout(dropout)
        v
    Linear(lstm_output_dim, 1)
        v
    a single relevance logit (sigmoid gives a 0..1 score; see
    docs/lstm_model.md, "Loss function", for why the model outputs a raw
    logit rather than an already-sigmoided score)

Joint-sequence design (task section 4)
----------------------------------------
Rather than encoding the prefix and candidate independently with two
separate LSTM towers and comparing two fixed vectors afterwards (a
"siamese" design), this model reads ONE sequence -- [CLS] prefix [SEP]
candidate [SEP] -- through a single LSTM. This lets the recurrent state
that has just finished reading the prefix directly condition how the
candidate's tokens are read (e.g. it can notice "candidate does not
actually continue the prefix's last partial word" while still inside the
recurrence, rather than only after the fact via a similarity function on
two separately-pooled vectors). The cost is that the prefix must be
re-encoded for every candidate compared against it, rather than once --
acceptable here because a production serving path would rank a bounded
candidate_pool_size (default 50, see configs/lstm.yaml) per keystroke, not
the full corpus.
"""

from __future__ import annotations

from dataclasses import dataclass

import torch
from torch import nn


@dataclass
class LSTMRankerConfig:
    vocab_size: int
    embedding_dim: int = 128
    hidden_dim: int = 256
    num_layers: int = 1
    dropout: float = 0.2
    bidirectional: bool = True
    max_seq_length: int = 40
    pad_id: int = 0

    def to_dict(self) -> dict:
        return {
            "vocab_size": self.vocab_size,
            "embedding_dim": self.embedding_dim,
            "hidden_dim": self.hidden_dim,
            "num_layers": self.num_layers,
            "dropout": self.dropout,
            "bidirectional": self.bidirectional,
            "max_seq_length": self.max_seq_length,
            "pad_id": self.pad_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LSTMRankerConfig":
        return cls(**d)


class PrefixRankingLSTM(nn.Module):
    """LSTM ranking model: token ids -> Embedding -> LSTM -> Dropout ->
    Linear -> one relevance logit per (prefix, candidate) sequence."""

    def __init__(self, config: LSTMRankerConfig) -> None:
        super().__init__()
        self.config = config

        self.embedding = nn.Embedding(
            num_embeddings=config.vocab_size,
            embedding_dim=config.embedding_dim,
            padding_idx=config.pad_id,
        )

        # dropout inside nn.LSTM only applies between stacked layers, so it
        # is a no-op (and PyTorch warns) when num_layers == 1. That is
        # expected -- the explicit self.dropout below (task's "Dropout"
        # box, applied to the pooled representation) is what actually
        # regularizes a single-layer model.
        self.lstm = nn.LSTM(
            input_size=config.embedding_dim,
            hidden_size=config.hidden_dim,
            num_layers=config.num_layers,
            batch_first=True,
            bidirectional=config.bidirectional,
            dropout=config.dropout if config.num_layers > 1 else 0.0,
        )

        lstm_output_dim = config.hidden_dim * (2 if config.bidirectional else 1)
        self.dropout = nn.Dropout(config.dropout)
        self.fc = nn.Linear(lstm_output_dim, 1)

    def forward(self, input_ids: torch.Tensor, lengths: torch.Tensor) -> torch.Tensor:
        """
        input_ids: (batch, seq_len) int64, right-padded with config.pad_id.
        lengths:   (batch,) int64, the true (unpadded) length of each
                   sequence -- required so padding never influences the
                   LSTM's hidden state (via pack_padded_sequence) and so we
                   read the *actual* last timestep, not a padded one.

        Returns: (batch,) float32 raw logits (apply torch.sigmoid for a
        0..1 score; see docs/lstm_model.md for why BCEWithLogitsLoss is
        used directly on these logits during training instead).
        """
        if input_ids.shape[0] == 0:
            return torch.empty(0, device=input_ids.device)

        embedded = self.embedding(input_ids)  # (batch, seq_len, embedding_dim)

        # pack_padded_sequence requires lengths on CPU and sorting is
        # handled internally via enforce_sorted=False, so callers don't
        # need to pre-sort the batch by length.
        packed = nn.utils.rnn.pack_padded_sequence(
            embedded, lengths.cpu(), batch_first=True, enforce_sorted=False
        )
        packed_output, (h_n, _c_n) = self.lstm(packed)
        # h_n: (num_layers * num_directions, batch, hidden_dim) -- the last
        # *real* hidden state per sequence, already excluding padding.
        if self.config.bidirectional:
            # Final layer's forward + backward hidden states.
            forward_h = h_n[-2, :, :]
            backward_h = h_n[-1, :, :]
            pooled = torch.cat([forward_h, backward_h], dim=-1)
        else:
            pooled = h_n[-1, :, :]

        pooled = self.dropout(pooled)
        logits = self.fc(pooled).squeeze(-1)  # (batch,)
        if logits.dim() == 0:
            logits = logits.unsqueeze(0)
        return logits

    def num_trainable_parameters(self) -> int:
        return sum(p.numel() for p in self.parameters() if p.requires_grad)


def build_model_from_config(model_cfg: dict, vocab_size: int) -> PrefixRankingLSTM:
    """Convenience constructor used by train.py/evaluate.py/inference.py so
    all three build the model identically from configs/lstm.yaml's
    `model` section plus the tokenizer's actual vocab_size."""
    config = LSTMRankerConfig(
        vocab_size=vocab_size,
        embedding_dim=model_cfg["embedding_dim"],
        hidden_dim=model_cfg["hidden_dim"],
        num_layers=model_cfg["num_layers"],
        dropout=model_cfg["dropout"],
        bidirectional=model_cfg["bidirectional"],
        max_seq_length=model_cfg["max_seq_length"],
    )
    return PrefixRankingLSTM(config)
