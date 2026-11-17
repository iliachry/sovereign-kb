"""Tests for Antigravity session initial-thinking and summary liveness classification."""

from __future__ import annotations

import json
import time
from pathlib import Path

from sovereign_kb.sessions import inspect_session


def test_inspect_session_initial_thinking_and_summary_liveness(tmp_path: Path) -> None:
    """Verify `inspect_session` classifies fresh USER_INPUT / EPHEMERAL_MESSAGE and running summary/child rows as ACTIVE_TOOL_LOOP."""
    agy_dir = tmp_path / "antigravity"
    brain_dir = agy_dir / "brain"
    cid = "11111111-2222-3333-4444-555555555555"
    conv_dir = brain_dir / cid
    logs_dir = conv_dir / ".system_generated" / "logs"
    logs_dir.mkdir(parents=True)
    tpath = logs_dir / "transcript.jsonl"
    now = time.time()

    # 1. Fresh USER_INPUT while model is initially thinking
    tpath.write_text(
        json.dumps({"step_index": 0, "type": "USER_INPUT", "status": "DONE", "content": "keep awake"}) + "\n",
        encoding="utf-8",
    )
    info_user = inspect_session(conv_dir, now=now)
    assert info_user is not None
    assert info_user["state"] == "ACTIVE_TOOL_LOOP"

    # 2. Fresh EPHEMERAL_MESSAGE injected before first PLANNER_RESPONSE
    tpath.write_text(
        "\n".join(
            [
                json.dumps({"step_index": 0, "type": "USER_INPUT", "status": "DONE", "content": "keep awake"}),
                json.dumps({"step_index": 1, "type": "EPHEMERAL_MESSAGE", "status": "DONE", "content": "router"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    info_eph = inspect_session(conv_dir, now=now)
    assert info_eph is not None
    assert info_eph["state"] == "ACTIVE_TOOL_LOOP"

    # 3. Completed transcript promoted to ACTIVE_TOOL_LOOP if summary or child subagent is CASCADE_RUN_STATUS_RUNNING
    tpath.write_text(
        "\n".join(
            [
                json.dumps({"step_index": 0, "type": "USER_INPUT", "status": "DONE", "content": "run browser"}),
                json.dumps({"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "content": "Done."}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    assert inspect_session(conv_dir, now=now, summaries_liveness={})["state"] == "IDLE_COMPLETE"
    child_running_map = {
        cid: ("CASCADE_RUN_STATUS_IDLE", 0, "Run browser", ""),
        "child-subagent-1": ("CASCADE_RUN_STATUS_RUNNING", 1, "", cid),
    }
    promoted = inspect_session(conv_dir, now=now, summaries_liveness=child_running_map)
    assert promoted is not None
    assert promoted["state"] == "ACTIVE_TOOL_LOOP"
