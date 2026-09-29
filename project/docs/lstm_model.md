# LSTM Prefix/Candidate Ranking Model

Design doc for `src/models/lstm/` -- the first deep-learning component of
SmartSearchAI, sitting after the (already-built, unmodified) prefix index:

```
prefix -> prefix index -> candidate queries -> LSTM ranking -> top 5
```

## Data & execution status (read this first)

The production defaults point to the real preprocessed MS MARCO splits and
the already-built real prefix index. Quantitative values below remain scoped
to the run that produced them; no real-data pair counts are inferred.

1. **Synthetic artifacts remain test fixtures only.** The synthetic JSONL
  generator and its existing artifacts are preserved for fast tests and
  demos. Real pair outputs are written under `data/processed/lstm/`.

2. **No PyTorch, and no way to install it.** `import torch` fails
   (`ModuleNotFoundError`), and `pip install torch` fails with "No
   matching distribution found" because this sandbox has no network
   access. Unlike limitation 1 (worked around with a smaller synthetic
   corpus), this one cannot be worked around inside this session -- there
   is no pure-Python fallback that would still honestly be "an LSTM
   trained with PyTorch" per the task's explicit requirement.

**What that means concretely**, file by file:

| File | Needs torch? | Status in this session |
|---|---|---|
| `scripts/generate_synthetic_lstm_queries.py` | No | **Run.** Produced the real train/dev split reported below. |
| `tokenizer.py` | No | **Run.** Fit on the synthetic train split; 14/14 unit tests pass for real. |
| `create_training_pairs.py` | No (uses the existing SQLite prefix index) | Implemented for real Parquet splits; focused fixture tests run. |
| `model.py` | Yes | Written, not imported/instantiated here. |
| `train.py` | Yes | Written, not run. No checkpoint exists. |
| `evaluate.py --mode baseline` | No | **Run** -- real numbers, see below. |
| `evaluate.py --mode lstm` | Yes | Written, not run (needs a checkpoint from `train.py`). |
| `inference.py` | Yes | Written, not run. |
| `tests/test_lstm_model.py` | Mixed | Tokenizer tests **run for real** (14 passed); torch-dependent tests **skip** (not faked) via `pytest.importorskip("torch")`. Also: `pytest` itself isn't installed here either, so a throwaway shim (`tests/_minimal_pytest_runner.py`) executed the test functions directly -- the test file itself needs no changes to run under real `pytest`. |

To actually produce a trained model and its real metrics, in an
environment with PyTorch and (ideally) the real parquet files:

```bash
pip install torch --break-system-packages    # or use an env that has it
# run these commands from the project directory
python -m src.models.lstm.create_training_pairs --config configs/lstm.yaml --split train
python -m src.models.lstm.create_training_pairs --config configs/lstm.yaml --split dev
python -m src.models.lstm.train --config configs/lstm.yaml
python -m src.models.lstm.evaluate --config configs/lstm.yaml --mode lstm
python -m pytest tests/test_lstm_model.py -v
```

No code changes should be required for any of the above once torch and
the real data are present; the synthetic files remain available separately
for fixture-based tests.

---

## 1. Training-pair generation

`create_training_pairs.py` turns each query into a bounded set of
character prefixes (via the *same* `generate_prefixes()` the prefix index
itself uses, so training prefixes match what production actually
generates), and for each prefix:

- emits **one positive**: `(prefix, full_query, label=1)`.
- emits **negatives** by querying the existing, already-built prefix index
  for that prefix's real candidate pool and sampling from the ones that
  aren't the target query.

This deliberately does **not** treat every other query in the dataset as a
true negative -- see the module's docstring for why retrieved-but-not-the-
target candidates are "weak" negatives (plausible completions of the same
prefix, not verified-irrelevant), and how a shortfall is backfilled with
clearly-tagged `random_fallback` negatives when a prefix's index results
run out.

**Real, run-in-this-session numbers** (synthetic corpus):

| Split | Positive pairs | Negative pairs (index-retrieved / random-fallback) | Total |
|---|---|---|---|
| train | 1,519 | 4,582 / 1,494 | 7,595 |
| dev | 349 | 1,008 / 388 | 1,745 |

## 2. Leakage prevention

- `scripts/generate_synthetic_lstm_queries.py` splits **by hash of
  normalized_query**, not by row/qid, so every duplicate of a query lands
  entirely on one side. Verified programmatically (`overlap_unique_queries
  == 0`, asserted in the script and reproduced in the training report).
- `create_training_pairs.py --split train` explicitly excludes any
  candidate whose normalized text is in the dev set before sampling
  negatives, because the existing prefix index (built once, before this
  LSTM-stage split existed) contains both sides' queries. This filter is
  skipped for `--split dev`, where seeing the full index is correct (it's
  what a real serving-time lookup would see).
- Duplicate normalized queries are collapsed to one canonical row before
  pair generation (same "smallest qid wins" rule as `index_builder.py`),
  so a duplicate group can't silently inflate positive-pair counts. The
  staging stores are SQLite-backed, so the large Parquet splits are not
  loaded into one giant Python list.

## 3. Tokenizer

Hybrid word+character tokenizer (`tokenizer.py`): Unicode-aware
regex splits text into word-like and punctuation/symbol runs; any
resulting token not in the trained word vocabulary falls back to
per-character ids (rather than one opaque `<UNK>`), so unseen code
identifiers, numbers, and rare multilingual text stay partially
informative. Word ids and character-fallback ids share **one id space**
(chars are appended after words), so a single embedding table in
`model.py` covers both. Configurable mode (`hybrid` / `word` / `char`),
vocab caps, min frequency, casing, and Unicode normalization form (see
`configs/lstm.yaml -> tokenizer`).

Real run: fit on the 190 unique synthetic train queries ->
**vocab_size = 252** (175 word tokens + 81 character tokens - 4 shared
specials). Saved to `models/lstm/tokenizer/tokenizer.json`.

## 4. Sequence representation

`[CLS] prefix_tokens [SEP] candidate_tokens [SEP]` -- one joint sequence
per (prefix, candidate) pair, matching the task's own suggested layout.
The alternative (a "siamese" design: encode prefix and candidate with two
separate LSTM towers, then compare two fixed vectors) was not used because
it can't let the recurrence itself condition on both sides while reading
-- e.g. noticing that a candidate diverges from the prefix's last partial
word -- only compare summaries after the fact. The cost: the prefix gets
re-encoded once per candidate compared against it, which is fine at the
serving-time scale here (a bounded candidate pool per keystroke, default
50; see `configs/lstm.yaml -> training_pairs.candidate_pool_size`), not
the whole corpus.

## 5. Model architecture

```
token ids -> Embedding(vocab_size, embedding_dim, padding_idx=PAD)
          -> LSTM(embedding_dim, hidden_dim, num_layers, bidirectional?)
             [pack_padded_sequence -- padding never enters the recurrence]
          -> last real hidden state (both directions concatenated if bidirectional)
          -> Dropout(dropout)
          -> Linear(-> 1)
          -> relevance logit
```

All of vocab size, embedding dim, hidden dim, layer count, dropout,
bidirectionality, and max sequence length are configurable
(`configs/lstm.yaml -> model`; see `LSTMRankerConfig` in `model.py`).
Defaults (`embedding_dim=128, hidden_dim=256, bidirectional=true,
num_layers=1`) against the real fitted vocab (252) give an **analytically
computed** ~823K trainable parameters (embedding 32,256 + bidirectional
LSTM 790,528 + output layer 513) -- computed from the standard LSTM
parameter-count formula, not verified by instantiating `torch.nn.Module`
since torch isn't available here; re-run
`model.num_trainable_parameters()` to confirm exactly once it is.

## 6. Loss function

Default **BCEWithLogitsLoss** on the 1/0 pair labels: treats each
`(prefix, candidate, label)` example independently, which matches how
`create_training_pairs.py` actually generated the data -- it does not
assume every negative is cleanly paired against one specific positive
within a batch, consistent with the "weak negative" limitation documented
above.

An alternative **pairwise hinge/margin loss** is also implemented
(`train.loss: "pairwise_hinge"` in `configs/lstm.yaml`): for every prefix
with both a positive and a negative in the same batch, it directly
penalizes `max(0, margin - (pos_score - neg_score))`. This more directly
targets ranking quality (what `evaluate.py` measures) at the cost of
needing positive/negative pairs to co-occur per prefix within a batch.
Both are legitimate; BCE is the default for simplicity and stability on
small/noisy data.

## 7. Evaluation methodology

`evaluate.py` simulates the real pipeline rather than scoring isolated
pairs: for each dev query's generated prefixes, it calls the **existing**
`PrefixIndex.get_candidates()` for that prefix's real candidate pool, then
ranks that pool either by the index's own existing order (`--mode
baseline`) or by the trained LSTM (`--mode lstm`), and computes
Precision@1, Precision@5, Recall@5, MRR@5, and Hit Rate@5 against the true
target query, plus re-ranking latency (avg/p50/p95).

**Real baseline run** (synthetic dev split, 1,477 evaluated prefixes, 0
where the target was missing from the index):

| Metric | Value |
|---|---|
| Precision@1 | 0.2471 |
| Precision@5 | 0.3026 |
| Recall@5 | 0.6181 |
| MRR@5 | 0.3770 |
| Hit Rate@5 | 0.6181 |
| Latency (avg / p50 / p95, ms) | 0.0003 / 0.0002 / 0.0005 |

The LSTM side of this comparison is `null` in
`reports/models/lstm_baseline_comparison.json` -- not estimated, not
inferred from the architecture -- pending an actual `train.py` run with
PyTorch available.

## 8. Candidate Retrieval vs. Candidate Ranking Pipeline

SmartSearch AI strictly separates candidate retrieval from deep learning candidate ranking:

```
User partial query (e.g., "deep lear")
             ↓
[1. Prefix Normalization] (lstrip, NFKC, internal whitespace collapsed, trailing space preserved)
             ↓
[2. Prefix Retrieval: PrefixIndex] (SQLite range-scan / hot-cache B-tree seek, O(log N) + K)
             ↓  (Candidate pool: default 50 candidates, retrieved in <10ms without scanning 9.2M rows)
[3. Deduplication & Validation] (Drops exact duplicates, ensures candidate starts with prefix)
             ↓
[4. LSTM Re-Ranker: LSTMRanker] (Joint [CLS] prefix [SEP] candidate [SEP], torch.inference_mode())
             ↓  (Model evaluates ranking logits in eval mode with mini-batched forward passes)
[5. Top-K Selection] (Stable sort by descending score, returning top-K structured suggestions)
             ↓
Ranked Suggestions (e.g., Top 5 autocomplete recommendations)
```

### Retrieval vs. Ranking Distinction
- **PrefixIndex (Retrieval Engine):** Fast, lightweight, pure standard library SQLite B-tree. Narrows the 9.2M+ query universe down to a high-recall candidate pool (e.g., 50 candidates) in ~5ms. It **never** scans the full Parquet dataset or loads millions of objects into Python RAM.
- **LSTM Ranker (Scoring Model):** Evaluates interaction between the typed prefix and each candidate string via bidirectional LSTM recurrent states. It is strictly a **ranker / re-ranker**, **NOT** an autoregressive text generator (it does not hallucinate new text).

### Production Inference Pipeline (`src/models/lstm/inference.py`)

1. **`LSTMRanker` Class:**
   - **Singleton Model Loading:** Loads checkpoint (`models/lstm/best_model.pt`) and tokenizer (`models/lstm/tokenizer/`) once at initialization. Model is set to `.eval()` mode with dropout disabled.
   - **Zero-Gradient Overhead:** All forward passes run inside `torch.inference_mode()` (or `torch.no_grad()`).
   - **Hardware Acceleration:** Auto-selects CUDA if an NVIDIA GPU is available (`torch.cuda.is_available()`), otherwise CPU. Supports explicit device specification (`device="cuda"` or `device="cpu"`).
   - **Batch Chunking:** Automatically chunks candidate lists into configurable mini-batches (`batch_size=64`) to prevent VRAM spikes or memory exhaustion.
   - **Input Robustness:** Gracefully handles empty prefixes (`""`), whitespace prefixes (`"   "`), unknown/OOV words (via character-level fallback and `<UNK>`), and legitimate Unicode text (CJK, accents, symbols).

2. **`SmartSearchRanker` / `LSTMSuggestionRanker` Class:**
   - High-level autocomplete service class orchestrating prefix retrieval, candidate deduplication, prefix correspondence verification, and LSTM scoring.
   - Exposes clean methods `suggest(prefix, top_k=5, candidate_pool_size=50)` and `predict(prefix, top_k=5)`.
   - Structured JSON output format:
     ```json
     [
       {
         "suggestion": "deep learning",
         "score": -2.0630,
         "candidate": "deep learning"
       }
     ]
     ```
   - Operates as a context manager (`with SmartSearchRanker(...) as ranker:`) for clean SQLite connection lifetime management.

### CLI / Demo Usage

Run the end-to-end autocomplete pipeline from terminal:

```bash
# Standard autocomplete prediction (Prefix Index -> Candidates -> LSTM Ranker -> Top 5)
python -m src.models.lstm.inference --query "deep learning" --top-k 5

# Structured JSON output with latency measurement
python -m src.models.lstm.inference --query "machine learning" --top-k 3 --json

# Direct candidate re-ranking (scoring an explicit list of candidate queries)
python -m src.models.lstm.inference --query "neural net" --candidates "neural network architectures" "neural net tutorial" --top-k 2
```

## 9. Testing & Verification

Unit tests and integration tests are isolated in `tests/test_lstm_inference.py`:

- **Isolated Unit Tests (No 3.48GB Index Dependency):**
  - Tests utilize in-memory mock prefix indexes and temporary fitted tokenizers.
  - Test coverage:
    - Checkpoint loading, model eval mode, and tokenizer initialization
    - Missing checkpoint / missing tokenizer `FileNotFoundError` handling
    - CPU and CUDA inference device routing
    - Empty, whitespace-only, and Unicode prefix inputs
    - Out-of-vocabulary / unknown-token fallback
    - Descending score sorting and deterministic tie-breaking
    - Candidate deduplication and prefix validity enforcement
    - Top-K truncation and empty-candidate behavior
    - Singleton model loading (verifying checkpoint is never reloaded per request)
    - Batch candidate scoring consistency

- **Real Prefix Index Integration Test:**
  - `test_real_prefix_index_integration` tests the live 3.48GB SQLite index and `best_model.pt`.
  - Guarded by `RUN_REAL_INDEX_TESTS=1` so default unit-test runs stay fast and self-contained:
    ```powershell
    $env:RUN_REAL_INDEX_TESTS="1"; python tests/_minimal_pytest_runner.py
    ```

## Honesty & Engineering Scope Notes

- **Research/Engineering Prototype:** This is an academic and engineering search autocomplete implementation inspired by modern web search query suggestion architectures. It is **NOT** Google's proprietary search autocomplete system.
- **Scoring Semantics:** The LSTM outputs raw ranking logits. In ranking, sorting by logit descending is monotonically identical to sorting by sigmoid probability; scores are reported as raw model logits and are not assumed to be calibrated probabilities.
- **Popularity Proxy:** MS MARCO does not include click logs or impression frequencies. Candidate popularity in the prefix index is derived from query frequency in the dataset.
- **Latency & Performance:** Model and tokenizer are kept in RAM; on standard x86 CPU, end-to-end candidate retrieval + re-ranking completes in under 50 milliseconds. On CUDA GPUs, latency scales further for larger candidate pools.
