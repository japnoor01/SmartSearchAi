"""Regression tests for streamed sanitizer output and report consistency."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.sanitize_training_pairs import sanitize_file  # noqa: E402


def test_sanitizer_writes_nonempty_output_and_matching_report(tmp_path: Path) -> None:
    source = tmp_path / "source.jsonl"
    output = tmp_path / "clean.jsonl"
    report = tmp_path / "clean_report.json"
    rows = [
        {"prefix": "machine", "candidate": "machine learning", "label": 1, "source": "positive"},
        {"prefix": "\x08", "candidate": "\x0812路进4路出混音器", "label": 1, "source": "positive"},
        {"prefix": "中", "candidate": "中文查询", "label": 1, "source": "positive"},
    ]
    with source.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    stats = sanitize_file(source, output, report)

    assert output.exists()
    assert output.stat().st_size > 0
    written = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    saved_report = json.loads(report.read_text(encoding="utf-8"))
    assert len(written) == 2
    assert stats["pairs_written"] == 2
    assert saved_report["pairs_written"] == len(written)
    assert all("\x08" not in row["prefix"] + row["candidate"] for row in written)
    assert any(row["candidate"] == "中文查询" for row in written)
