from __future__ import annotations

import base64

import pytest

import app.enterprise_capabilities.tools.file_results as file_results
from app.dsh_runtime.events.projection import KernelEventProjector
from app.enterprise_capabilities.runtime.contracts import CapabilityExecutionContext

PPTX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def _context() -> CapabilityExecutionContext:
    return CapabilityExecutionContext(
        tenant_id="tenant-a",
        user_id="user-a",
        conversation_id="conv-a",
        kernel_session_id="kernel-a",
        profile_version="rp-a",
        action_id="action-a",
        message_id="msg-a",
    )


def _brochure_result(*, filename: str = "小礼宣传册_20261009.pptx", content: bytes = b"pptx-bytes") -> dict:
    """The gift-mcp gift_brochure success envelope."""
    return {
        "success": True,
        "data": {
            "file": {
                "filename": filename,
                "content_type": PPTX_CONTENT_TYPE,
                "size_bytes": len(content),
                "content_base64": base64.b64encode(content).decode("ascii"),
            },
            "brochure_meta": {"page_count": 3, "skipped_gift_ids": [], "template": "blue"},
        },
        "meta": {"source": ["maoxiu.maoxiu_products"], "warnings": []},
        "errors": [],
    }


@pytest.fixture()
def captured_uploads(monkeypatch):
    uploads: list[tuple[bytes, str, str, str]] = []

    def fake_upload(content, *, context, filename, content_type):
        uploads.append((content, filename, content_type, context.user_id))
        return {
            "object_path": f"2026/10/10/{filename}",
            "filename": filename,
            "content_type": content_type,
            "size": len(content),
        }

    monkeypatch.setattr(file_results, "upload_derived_artifact", fake_upload)
    return uploads


def test_materializes_inline_file_into_an_artifact(captured_uploads):
    content = b"payload-123"

    result, artifacts = file_results.materialize_inline_file_results(
        _brochure_result(content=content), context=_context()
    )

    assert captured_uploads == [(content, "小礼宣传册_20261009.pptx", PPTX_CONTENT_TYPE, "user-a")]
    assert len(artifacts) == 1
    assert artifacts[0]["object_path"].endswith("小礼宣传册_20261009.pptx")
    assert artifacts[0]["lifecycle"] == "final"
    assert artifacts[0]["visibility"] == "user"
    assert result["artifact"] == artifacts[0]
    # The inline base64 is scrubbed; everything else survives verbatim.
    assert "content_base64" not in result["data"]["file"]
    assert result["data"]["file"]["materialized"] is True
    assert result["data"]["file"]["size_bytes"] == len(content)
    assert result["data"]["brochure_meta"]["page_count"] == 3
    assert result["success"] is True


def test_materialized_artifact_survives_the_ui_projection(captured_uploads):
    result, _ = file_results.materialize_inline_file_results(_brochure_result(), context=_context())

    projected = KernelEventProjector._result_artifacts(result)

    assert len(projected) == 1
    assert projected[0]["filename"] == "小礼宣传册_20261009.pptx"
    assert projected[0]["object_path"] == result["artifact"]["object_path"]


def test_projection_extracts_artifacts_from_the_mcp_wrapper(captured_uploads):
    import json as _json

    envelope = _brochure_result(content=b"wrapper-projection")
    outcome = _mcp_outcome(envelope, _json.dumps(envelope, ensure_ascii=False))
    result, _ = file_results.materialize_inline_file_results(outcome, context=_context())
    # What DSH echoes into the tool event is exactly the structuredContent
    # wrapper: {"result": "<envelope json string>"}.
    wrapped = {"result": result["raw"]["structuredContent"]["result"]}

    projected = KernelEventProjector._result_artifacts(wrapped)

    assert len(projected) == 1
    assert projected[0]["filename"] == "小礼宣传册_20261009.pptx"
    assert projected[0]["object_path"].endswith("小礼宣传册_20261009.pptx")


def test_mcp_outcome_embeds_the_artifact_inside_the_envelope(captured_uploads):
    import json as _json

    envelope = _brochure_result(content=b"envelope-embed")
    outcome = _mcp_outcome(envelope, _json.dumps(envelope, ensure_ascii=False))

    result, artifacts = file_results.materialize_inline_file_results(outcome, context=_context())

    reparsed = _json.loads(result["raw"]["structuredContent"]["result"])
    assert reparsed["artifact"]["filename"] == "小礼宣传册_20261009.pptx"
    assert reparsed["artifact"] == artifacts[0]
    assert "content_base64" not in reparsed["data"]["file"]


def test_upload_failure_keeps_the_inline_payload(monkeypatch):
    def fail_upload(*args, **kwargs):
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(file_results, "upload_derived_artifact", fail_upload)
    payload = _brochure_result()

    result, artifacts = file_results.materialize_inline_file_results(payload, context=_context())

    assert result is payload
    assert artifacts == []
    assert result["data"]["file"]["content_base64"]
    assert "materialized" not in result["data"]["file"]
    assert "artifact" not in result


def test_invalid_base64_keeps_the_inline_payload(captured_uploads):
    payload = _brochure_result()
    payload["data"]["file"]["content_base64"] = "%%%not-base64%%%"

    result, artifacts = file_results.materialize_inline_file_results(payload, context=_context())

    assert result is payload
    assert artifacts == []
    assert captured_uploads == []


def test_oversized_payload_is_skipped(captured_uploads, monkeypatch):
    monkeypatch.setattr(file_results, "MAX_INLINE_FILE_BYTES", 4)
    payload = _brochure_result(content=b"way-too-large")

    result, artifacts = file_results.materialize_inline_file_results(payload, context=_context())

    assert result is payload
    assert artifacts == []
    assert captured_uploads == []


def test_results_without_inline_files_pass_through_untouched(captured_uploads):
    payload = {"success": True, "data": {"items": [{"gift_id": "197235"}]}, "meta": {}, "errors": []}

    result, artifacts = file_results.materialize_inline_file_results(payload, context=_context())

    assert result is payload
    assert artifacts == []
    assert captured_uploads == []


def test_root_level_file_shape_is_supported(captured_uploads):
    payload = {
        "success": True,
        "file": {
            "filename": "report.pptx",
            "content_type": PPTX_CONTENT_TYPE,
            "content_base64": base64.b64encode(b"root-shape").decode("ascii"),
        },
    }

    result, artifacts = file_results.materialize_inline_file_results(payload, context=_context())

    assert len(artifacts) == 1
    assert "content_base64" not in result["file"]
    assert result["artifact"]["filename"] == "report.pptx"


def _mcp_outcome(envelope: dict, envelope_json: str) -> dict:
    """MOVO's MCP call outcome: the CallToolResult lives under raw, the
    envelope JSON is mirrored in content[].text and structuredContent.result."""
    return {
        "success": True,
        "status": "passed",
        "message": "MCP tool gift_brochure 调用成功",
        "responseSummary": envelope_json[:4000],
        "raw": {
            "isError": False,
            "content": [{"type": "text", "text": envelope_json}],
            "structuredContent": {"result": envelope_json},
        },
    }


def test_mcp_outcome_shape_is_materialized_and_scrubbed(captured_uploads):
    import json as _json

    envelope = _brochure_result(content=b"mcp-shape-payload")
    envelope_json = _json.dumps(envelope, ensure_ascii=False)
    outcome = _mcp_outcome(envelope, envelope_json)
    original_base64 = envelope["data"]["file"]["content_base64"]

    result, artifacts = file_results.materialize_inline_file_results(outcome, context=_context())

    assert len(artifacts) == 1
    assert result["artifact"] == artifacts[0]
    # Both envelope copies are scrubbed; no base64 survives anywhere.
    text = result["raw"]["content"][0]["text"]
    assert original_base64 not in text
    assert original_base64 not in result["raw"]["structuredContent"]["result"]
    assert original_base64 not in result["responseSummary"]
    reparsed = _json.loads(text)
    assert reparsed["data"]["file"]["materialized"] is True
    assert "content_base64" not in reparsed["data"]["file"]
    assert reparsed["data"]["brochure_meta"]["page_count"] == 3
    # structuredContent mirrors the rewritten text.
    assert result["raw"]["structuredContent"]["result"] == text
    # The original input is untouched.
    assert original_base64 in outcome["raw"]["content"][0]["text"]


def test_mcp_outcome_passthrough_without_inline_file(captured_uploads):
    import json as _json

    envelope_json = _json.dumps({"success": True, "items": []}, ensure_ascii=False)
    outcome = _mcp_outcome({"success": True, "items": []}, envelope_json)

    result, artifacts = file_results.materialize_inline_file_results(outcome, context=_context())

    assert result is outcome
    assert artifacts == []
