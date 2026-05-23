from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agentic_rag.config import Settings, get_settings
from agentic_rag.ingestion.adapters.mineru_adapter import MinerUAdapter
from agentic_rag.ingestion.adapters.mineru_result_parser import parse_mineru_markdown
from agentic_rag.ingestion.inspection import (
    DOC_LIKE_SUFFIXES,
    IngestionInspectionRecorder,
    collect_input_files,
    empty_mineru_raw,
    failure_result,
)
from agentic_rag.ingestion.multimodal_orchestrator import MultiModalOrchestrator
from agentic_rag.ingestion.node_schema import IngestionFailure, MultimodalIngestionResult


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect multimodal ingestion nodes without writing Qdrant",
    )
    parser.add_argument("input_path", type=str, help="Single file or directory to inspect")
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output root directory, default from INGESTION_INSPECT_OUTPUT_DIR",
    )
    parser.add_argument(
        "--render-pages",
        action="store_true",
        default=None,
        help="Render PDF page screenshots when PyMuPDF is installed",
    )
    parser.add_argument(
        "--max-text-chars",
        type=int,
        default=None,
        help="Max chars stored per node text field",
    )
    parser.add_argument(
        "--include-mineru-raw",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Save MinerU raw markdown and structured content",
    )
    parser.add_argument(
        "--embedding-preview",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Write embedding_preview.jsonl without full vectors",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON summary")
    args = parser.parse_args()

    settings = get_settings()
    settings = _settings_with_cli_overrides(settings, args)

    input_path = Path(args.input_path)
    output_dir = Path(args.output or settings.ingestion_inspect_output_dir)
    render_pages = bool(args.render_pages) if args.render_pages is not None else settings.ingestion_inspect_render_pdf_pages

    try:
        result, parser_name, mineru_raw = _inspect_input(input_path, settings)
        recorder = IngestionInspectionRecorder(
            settings=settings,
            output_dir=output_dir,
            max_text_chars=args.max_text_chars or settings.ingestion_inspect_max_text_chars,
        )
        run_dir = recorder.write_report(
            input_path=input_path,
            result=result,
            parser=parser_name,
            render_pages=render_pages,
            mineru_raw=mineru_raw,
        )
    except Exception as exc:
        print(f"[ERROR] Ingestion inspect failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        import json

        print(
            json.dumps(
                {
                    "run_dir": str(run_dir),
                    "nodes": len(result.nodes),
                    "failed_files": len(result.failures),
                    "wrote_qdrant": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    print("Ingestion inspection completed")
    print(f"- run_dir: {run_dir}")
    print(f"- nodes: {len(result.nodes)}")
    print(f"- failed_files: {len(result.failures)}")
    print("- wrote_qdrant: false")
    print("- open: chunks.html / document_map.html")
    return 0


def _settings_with_cli_overrides(settings: Settings, args: argparse.Namespace) -> Settings:
    updates = {}
    if args.include_mineru_raw is not None:
        updates["ingestion_inspect_include_mineru_raw"] = bool(args.include_mineru_raw)
    if args.embedding_preview is not None:
        updates["ingestion_inspect_include_embedding_preview"] = bool(args.embedding_preview)
    if args.max_text_chars is not None:
        updates["ingestion_inspect_max_text_chars"] = args.max_text_chars
    if args.render_pages is not None:
        updates["ingestion_inspect_render_pdf_pages"] = bool(args.render_pages)
    return settings.model_copy(update=updates) if updates else settings


def _inspect_input(input_path: Path, settings: Settings) -> tuple[MultimodalIngestionResult, str, dict | None]:
    files = collect_input_files(input_path)
    orchestrator = MultiModalOrchestrator(settings)
    all_nodes = []
    failures: list[IngestionFailure] = []
    raw_markdown_parts: list[str] = []
    parsed_markdown_parts: list[str] = []
    structured_by_file = []
    raw_file_manifest = []
    parser_names = set()

    for file_path in files:
        result, parser_name, mineru_raw = _inspect_file(file_path, settings, orchestrator)
        all_nodes.extend(result.nodes)
        failures.extend(result.failures)
        parser_names.add(parser_name)
        if mineru_raw:
            raw_markdown_parts.append(f"\n\n<!-- source: {file_path} -->\n\n{mineru_raw.get('raw_markdown') or ''}")
            parsed_markdown_parts.append(f"\n\n<!-- source: {file_path} -->\n\n{mineru_raw.get('parsed_markdown') or ''}")
            structured_by_file.append(
                {
                    "source": str(file_path),
                    "structured_content": mineru_raw.get("structured_content") or [],
                }
            )
            raw_file_manifest.append(mineru_raw.get("raw_result_manifest") or {"source": str(file_path)})

    parser_name = "mixed" if len(parser_names) > 1 else next(iter(parser_names), "unknown")
    combined = MultimodalIngestionResult(nodes=all_nodes, failures=failures)
    raw = None
    if raw_markdown_parts or structured_by_file:
        raw = {
            "raw_markdown": "".join(raw_markdown_parts),
            "parsed_markdown": "".join(parsed_markdown_parts),
            "structured_content": structured_by_file,
            "raw_result_manifest": {"files": raw_file_manifest},
        }
    elif settings.enable_mineru and input_path.suffix.lower() in DOC_LIKE_SUFFIXES:
        raw = empty_mineru_raw(str(input_path))
    return combined, parser_name, raw


def _inspect_file(
    file_path: Path,
    settings: Settings,
    orchestrator: MultiModalOrchestrator,
) -> tuple[MultimodalIngestionResult, str, dict | None]:
    suffix = file_path.suffix.lower()
    if settings.enable_mineru and suffix in DOC_LIKE_SUFFIXES:
        result, raw = _parse_mineru_file_for_inspection(file_path, settings)
        if result.nodes or not settings.mineru_fallback_to_existing:
            return result, "mineru", raw
        fallback = orchestrator._parse_with_fallback(file_path, orchestrator._adapter_for(file_path))  # noqa: SLF001
        return fallback, "mineru_fallback", raw

    adapter = orchestrator._adapter_for(file_path)  # noqa: SLF001
    return adapter.parse_file(file_path), adapter.__class__.__name__, None


def _parse_mineru_file_for_inspection(file_path: Path, settings: Settings) -> tuple[MultimodalIngestionResult, dict]:
    adapter = MinerUAdapter(settings)
    try:
        if settings.mineru_mode == "agent":
            parse_result = adapter._parse_agent(file_path)  # noqa: SLF001
        else:
            parse_result = adapter._parse_precise(file_path)  # noqa: SLF001
        parsed_markdown = parse_mineru_markdown(parse_result, settings)
        nodes = adapter._build_nodes_from_markdown(  # noqa: SLF001
            parsed_markdown,
            file_path,
            structured_content=parse_result.structured_content or [],
        )
        result = MultimodalIngestionResult(nodes=nodes, failures=[])
        raw = {
            "raw_markdown": parse_result.markdown_content,
            "parsed_markdown": parsed_markdown,
            "structured_content": parse_result.structured_content or [],
            "raw_result_manifest": {
                "source": str(file_path),
                "task_id": parse_result.task_id,
                "state": parse_result.state,
                "source_url": parse_result.source_url,
                "raw": parse_result.raw,
            },
        }
        return result, raw
    except Exception as exc:
        raw = empty_mineru_raw(str(file_path))
        raw["raw_result_manifest"]["error"] = f"{type(exc).__name__}: {exc}"
        return failure_result(str(file_path), f"MinerU inspect failed: {exc}"), raw


if __name__ == "__main__":
    raise SystemExit(main())
