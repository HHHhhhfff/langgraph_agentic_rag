from __future__ import annotations

import argparse
from pathlib import Path


DEFAULT_PATH = Path("storage/retrieval_eval/retrieval_history.jsonl")


def main() -> int:
    parser = argparse.ArgumentParser(description="Clear retrieval evaluation history JSONL file.")
    parser.add_argument("--path", default=str(DEFAULT_PATH), help="History JSONL path to clear")
    args = parser.parse_args()

    path = Path(args.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    print(f"Cleared retrieval history: {path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
