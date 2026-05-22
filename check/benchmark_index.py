from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"


def _extract_json(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark index build time through the existing build_index CLI.")
    parser.add_argument("--docs", required=True, help="Document directory passed to agentic_rag.cli.build_index")
    parser.add_argument("--output-dir", default="check/runs", help="Directory for benchmark outputs")
    parser.add_argument("--run-name", default=None, help="Optional run directory name")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    output_root = Path(args.output_dir)
    if not output_root.is_absolute():
        output_root = REPO_ROOT / output_root
    run_dir = output_root / (args.run_name or f"index_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    run_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(SRC_ROOT) if not existing_pythonpath else f"{SRC_ROOT}{os.pathsep}{existing_pythonpath}"
    command = [sys.executable, "-m", "agentic_rag.cli.build_index", "--docs", args.docs, "--json"]

    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=str(REPO_ROOT),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    index_build_time_ms = (time.perf_counter() - started) * 1000

    summary = {
        "docs": args.docs,
        "returncode": completed.returncode,
        "index_build_time_ms": index_build_time_ms,
        "index_summary": _extract_json(completed.stdout),
        "stdout_tail": (completed.stdout or "")[-2000:],
        "stderr_tail": (completed.stderr or "")[-2000:],
        "command": command,
    }
    (run_dir / "index_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Run directory: {run_dir}")
    print(f"index_build_time_ms: {index_build_time_ms:.2f}")
    print(f"returncode: {completed.returncode}")
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
