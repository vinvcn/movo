"""Materialize inline file payloads on external tool results into MOVO artifacts.

External tools may return generated files inline as base64 (for example
gift-mcp's ``gift_brochure`` returns ``data.file.content_base64``). The user
web delivers files through the artifact card pipeline, which consumes
``artifact`` / ``documents`` entries on the tool result
(``app/dsh_runtime/events/projection.py``), and the model context must not
carry hundreds of kilobytes of base64. This module bridges the two: the bytes
are uploaded into MOVO object storage, the artifact metadata is attached to
the result, and the inline base64 is scrubbed.

Two result shapes are recognized:

- direct envelopes (``result["data"]["file"]`` / ``result["file"]``);
- MOVO's MCP call outcome, which wraps the MCP CallToolResult under ``raw``
  (``raw.content[i].text`` carries the JSON envelope, and FastMCP mirrors it
  under ``raw.structuredContent.result``). Both copies are scrubbed.

Degradation contract (gift-collaborator reply UI design §6.2): a malformed
payload, an oversized file or a storage failure keeps the original result
untouched and logs a warning — the tool call still succeeds and the inline
payload survives.
"""

from __future__ import annotations

import base64
import binascii
import copy
import json
import logging
from typing import Any, Callable

from app.enterprise_capabilities.artifacts.storage import upload_derived_artifact
from app.enterprise_capabilities.runtime.contracts import CapabilityExecutionContext

logger = logging.getLogger(__name__)

# The brochure artifacts are ~100 KB; 25 MB leaves generous headroom without
# letting a misbehaving MCP server exhaust memory or storage.
MAX_INLINE_FILE_BYTES = 25 * 1024 * 1024

# Direct result shapes carrying an inline file (non-MCP envelopes first; the
# MCP outcome shape is handled through ``raw`` below).
_INLINE_FILE_PATHS: tuple[tuple[str, ...], ...] = (("data", "file"), ("file",))
_MCP_FILE_PATH: tuple[str, ...] = ("data", "file")


class _Job:
    """One inline file plus the closure that scrubs it on the rewritten copy."""

    def __init__(
        self,
        file_info: dict[str, Any],
        apply: Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], None],
    ) -> None:
        self.file_info = file_info
        self.apply = apply


def _node_at(root: dict[str, Any], path: tuple[str, ...]) -> dict[str, Any] | None:
    node: Any = root
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node if isinstance(node, dict) else None


def _set_node(root: dict[str, Any], path: tuple[str, ...], value: dict[str, Any]) -> None:
    node = root
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value


def _has_inline_file(node: Any) -> bool:
    return isinstance(node, dict) and bool(str(node.get("content_base64") or "").strip())


def _collect_jobs(result: dict[str, Any]) -> list[_Job]:
    jobs: list[_Job] = []
    for path in _INLINE_FILE_PATHS:
        info = _node_at(result, path)
        if _has_inline_file(info):
            jobs.append(_Job(info, _direct_apply(path)))
    raw = result.get("raw")
    if isinstance(raw, dict):
        blocks = raw.get("content")
        for index, block in enumerate(blocks if isinstance(blocks, list) else []):
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            payload = _parse_json_object(str(block.get("text") or ""))
            info = _node_at(payload, _MCP_FILE_PATH) if payload is not None else None
            if _has_inline_file(info):
                original_base64 = str(info.get("content_base64") or "")
                jobs.append(_Job(info, _mcp_apply(index, _MCP_FILE_PATH, original_base64)))
    return jobs


def _direct_apply(path: tuple[str, ...]) -> Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], None]:
    def apply(rewritten: dict[str, Any], scrubbed: dict[str, Any], artifact: dict[str, Any]) -> None:
        _set_node(rewritten, path, scrubbed)

    return apply


def _mcp_apply(
    block_index: int,
    path: tuple[str, ...],
    original_base64: str,
) -> Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], None]:
    """Rewrite the JSON envelope inside the CallToolResult text block and keep
    the FastMCP ``structuredContent.result`` mirror in step.

    The artifact is embedded in the envelope because ``canonical_tool_result``
    forwards only the structuredContent wrapper to DSH — top-level receipt
    fields (where the artifact also lives for direct consumers) never reach
    the tool event.
    """

    def apply(rewritten: dict[str, Any], scrubbed: dict[str, Any], artifact: dict[str, Any]) -> None:
        raw = rewritten.get("raw")
        if not isinstance(raw, dict):
            return
        blocks = raw.get("content")
        if not isinstance(blocks, list) or block_index >= len(blocks):
            return
        block = blocks[block_index]
        if not isinstance(block, dict):
            return
        payload = _parse_json_object(str(block.get("text") or ""))
        if payload is None or _node_at(payload, path) is None:
            return
        _set_node(payload, path, scrubbed)
        payload["artifact"] = artifact
        new_text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        block["text"] = new_text
        structured = raw.get("structuredContent")
        if (
            isinstance(structured, dict)
            and isinstance(structured.get("result"), str)
            and original_base64 in structured["result"]
        ):
            structured["result"] = new_text

    return apply


def _parse_json_object(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _materialize_one(
    file_info: dict[str, Any],
    *,
    context: CapabilityExecutionContext,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Decode + upload one inline file. Returns (artifact, scrubbed file dict)
    or None when the file cannot be materialized (original stays untouched)."""
    filename = str(file_info.get("filename") or "artifact.bin")[:255]
    content_type = str(file_info.get("content_type") or "application/octet-stream")
    encoded = str(file_info.get("content_base64") or "")
    try:
        content = base64.b64decode(encoded, validate=False)
    except (binascii.Error, ValueError):
        logger.warning("inline_file_artifact_skipped reason=invalid_base64 filename=%s", filename)
        return None
    if not content:
        logger.warning("inline_file_artifact_skipped reason=empty_payload filename=%s", filename)
        return None
    if len(content) > MAX_INLINE_FILE_BYTES:
        logger.warning(
            "inline_file_artifact_skipped reason=too_large filename=%s bytes=%s",
            filename,
            len(content),
        )
        return None
    try:
        artifact = upload_derived_artifact(
            content,
            context=context,
            filename=filename,
            content_type=content_type,
        )
    except Exception:
        logger.warning(
            "inline_file_artifact_failed filename=%s bytes=%s; keeping the inline payload",
            filename,
            len(content),
            exc_info=True,
        )
        return None
    artifact = {**artifact, "lifecycle": "final", "visibility": "user"}
    scrubbed = {key: value for key, value in file_info.items() if key != "content_base64"}
    scrubbed["materialized"] = True
    return artifact, scrubbed


def _refresh_mcp_response_summary(rewritten: dict[str, Any]) -> None:
    """Re-derive ``responseSummary`` from the scrubbed text blocks so the
    truncated base64 does not survive in the summary copy."""
    raw = rewritten.get("raw")
    if not isinstance(raw, dict):
        return
    blocks = raw.get("content")
    texts = [
        str(block.get("text") or "")
        for block in (blocks if isinstance(blocks, list) else [])
        if isinstance(block, dict) and block.get("type") == "text" and str(block.get("text") or "").strip()
    ]
    if not texts or not isinstance(rewritten.get("responseSummary"), str):
        return
    summary = "\n".join(texts)
    rewritten["responseSummary"] = summary if len(summary) <= 4000 else summary[:4000] + "...[truncated]"


def materialize_inline_file_results(
    result: Any,
    *,
    context: CapabilityExecutionContext,
) -> tuple[Any, list[dict[str, Any]]]:
    """Attach materialized artifacts to an external tool result.

    Returns ``(result, artifacts)``. When nothing was materialized the input
    object is returned unchanged, so callers can compare identity.
    """
    if not isinstance(result, dict):
        return result, []
    jobs = _collect_jobs(result)
    if not jobs:
        return result, []

    rewritten = copy.deepcopy(result)
    artifacts: list[dict[str, Any]] = []
    applied = False
    for job in jobs:
        materialized = _materialize_one(job.file_info, context=context)
        if materialized is None:
            continue
        artifact, scrubbed = materialized
        job.apply(rewritten, scrubbed, artifact)
        artifacts.append(artifact)
        applied = True

    if not applied:
        return result, []
    # The projection reads the singular ``artifact`` key (and ``documents``
    # for multiples); both survive the size-bounded compaction untouched.
    rewritten["artifact"] = artifacts[0]
    if len(artifacts) > 1:
        rewritten["documents"] = artifacts
    _refresh_mcp_response_summary(rewritten)
    return rewritten, artifacts


__all__ = ["MAX_INLINE_FILE_BYTES", "materialize_inline_file_results"]
