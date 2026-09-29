#!/usr/bin/env python3
"""Stream an existing pair JSONL into a control-character-free copy."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from pathlib import Path


def contains_disallowed_control(text: str) -> bool:
    return any(ord(character) < 32 and character not in "\t\n\r" for character in text)


def sanitize_file(source_path: Path, output_path: Path, report_path: Path) -> dict:
    if source_path.resolve() == output_path.resolve():
        raise ValueError("source and output must be different files")

    started = time.time()
    stats = {
        
        "input_path": str(source_path),
        "output_path": str(output_path),
        "source_pairs_scanned": 0,
        "pairs_written": 0,
        "rows_skipped_control": 0,
        "prefix_control_rows_skipped": 0,
        "candidate_control_rows_skipped": 0,
        "fresh_source_regeneration": False,
        "sanitization_of_existing_real_pairs": True,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_output:
            temporary_path = Path(temporary_output.name)
            with source_path.open("r", encoding="utf-8") as source:
                for line in source:
                    row = json.loads(line)
                    stats["source_pairs_scanned"] += 1
                    prefix_invalid = contains_disallowed_control(row["prefix"])
                    candidate_invalid = contains_disallowed_control(row["candidate"])
                    if prefix_invalid or candidate_invalid:
                        stats["rows_skipped_control"] += 1
                        stats["prefix_control_rows_skipped"] += int(prefix_invalid)
                        stats["candidate_control_rows_skipped"] += int(candidate_invalid)
                        continue
                    temporary_output.write(json.dumps(row, ensure_ascii=False) + "\n")
                    stats["pairs_written"] += 1

        actual_lines = 0
        with temporary_path.open("r", encoding="utf-8") as written_output:
            for line in written_output:
                row = json.loads(line)
                if contains_disallowed_control(row["prefix"]) or contains_disallowed_control(row["candidate"]):
                    raise ValueError("temporary output contains a disallowed control character")
                actual_lines += 1
        if actual_lines != stats["pairs_written"]:
            raise RuntimeError(
                f"wrote {actual_lines} JSONL lines but counted {stats['pairs_written']} pairs"
            )
        if actual_lines == 0:
            raise RuntimeError("refusing to replace output with an empty clean JSONL file")
        os.replace(temporary_path, output_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    stats["generation_seconds"] = round(time.time() - started, 3)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8", newline="\n") as report:
        json.dump(stats, report, indent=2)
        report.write("\n")
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()

    stats = sanitize_file(args.source, args.output, args.report)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
