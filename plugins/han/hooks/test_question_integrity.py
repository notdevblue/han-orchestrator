import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import question_integrity
import question_records


@pytest.fixture
def project(tmp_path, monkeypatch):
    for key in ("HAN_ADAPTERS_DIR", "CLAUDE_PLUGIN_OPTION_ADAPTERS_DIR", "CLAUDE_PROJECT_DIR", "HAN_REPO", "HAN_GUARD_LOG"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    folder = tmp_path / "project"
    adapter = folder / ".claude/orchestrator/adapter.json"
    adapter.parent.mkdir(parents=True)
    adapter.write_text(json.dumps({"schema": 1, "paths": {"stateDir": "state"}}), encoding="utf-8")
    mission = folder / "state/example-mission"
    mission.mkdir(parents=True)
    (mission.parent / "ACTIVE").write_text(json.dumps({"id": mission.name, "session": "owner", "openedAt": "2026-01-01"}), encoding="utf-8")
    return folder, mission


def document():
    return {"schema": 1, "id": "ask-palette", "revision": 1, "title": "Choose a palette",
            "intro": "Choose colors for a fictional garden.",
            "items": [{"id": "1", "title": "Use blue?", "body": "Blue or green", "status": "pending", "answer": ""}],
            "body": "", "approval": {"status": "none", "reason": ""}, "answers": []}


def answer():
    return {"schema": 1, "id": "reply-palette", "revision": 1, "at": "2026-01-01", "text": "Use blue for the fictional garden.",
            "updates": [{"id": "1", "status": "answered", "answer": "Use blue."}]}


def payload(project, event="Stop", **fields):
    folder, _ = project
    return {"hook_event_name": event, "session_id": "owner", "cwd": str(folder), "tool_name": "Bash",
            "tool_input": {"command": "python opaque-writer.py"}, **fields}


def snapshot(folder):
    return {str(path.relative_to(folder)): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in folder.rglob("*") if path.is_file()}


@pytest.mark.parametrize("event", ["PostToolUse", "Stop"])
@pytest.mark.parametrize("state", ["pending", "answered", "approval-pending"])
def test_consistent_questions_pass_without_changing_records(project, event, state):
    _, mission = project
    question_records.publish_document(mission, document())
    if state != "pending":
        reply = answer()
        if state == "approval-pending":
            reply["approval"] = {"status": "pending", "reason": "The fictional border still needs approval."}
        question_records.answer_document(mission, "ask-palette.md", reply)
    before = snapshot(project[0])
    assert question_integrity.decide(payload(project, event)) == {}
    assert snapshot(project[0]) == before


@pytest.mark.parametrize("event", ["PostToolUse", "Stop"])
@pytest.mark.parametrize("mutation", ["markdown-answer", "markdown-status", "stale-json", "missing-json", "corrupt-json", "missing-markdown", "invalid-utf8"])
def test_opaque_writes_are_detected_without_rewriting(project, event, mutation):
    _, mission = project
    question_records.publish_document(mission, document())
    markdown = mission / "ask-palette.md"
    source = mission / "questions/ask-palette.json"
    if mutation == "markdown-answer":
        markdown.write_text(markdown.read_text(encoding="utf-8") + "\n## Past answer\nUse blue.\n", encoding="utf-8")
    elif mutation == "markdown-status":
        markdown.write_text(markdown.read_text(encoding="utf-8").replace("답변 대기", "답변 완료"), encoding="utf-8")
    elif mutation == "stale-json":
        before = source.read_bytes()
        question_records.answer_document(mission, markdown.name, answer())
        source.write_bytes(before)
    elif mutation == "missing-json":
        source.unlink()
    elif mutation == "corrupt-json":
        source.write_text("{broken", encoding="utf-8")
    elif mutation == "missing-markdown":
        markdown.unlink()
    else:
        source.write_bytes(b"\xff")
    before = snapshot(project[0])
    output = question_integrity.decide(payload(project, event, stop_hook_active=True))
    assert output["decision"] == "block"
    assert "ask-palette.md" in output["reason"] or "검사 실패" in output["reason"]
    assert "question check" in output["reason"] and "question restore" in output["reason"]
    assert "permissionDecision" not in output.get("hookSpecificOutput", {})
    if event == "PostToolUse":
        assert output["hookSpecificOutput"] == {"hookEventName": event, "additionalContext": output["reason"]}
    else:
        assert "hookSpecificOutput" not in output
        assert question_integrity.decide(payload(project, event, stop_hook_active=True))["decision"] == "block"
    assert snapshot(project[0]) == before


@pytest.mark.parametrize("fields,blocked", [({}, True), ({"agent_id": "child", "agent_type": "default"}, True),
                                            ({"session_id": "unrelated"}, False), ({"session_id": "child-session", "parent_session_id": "owner"}, False)])
def test_owner_and_common_parent_child_session_scope(project, fields, blocked):
    _, mission = project
    (mission / "ask-palette.md").write_text("# Unregistered fictional question", encoding="utf-8")
    output = question_integrity.decide(payload(project, **fields))
    assert (output.get("decision") == "block") is blocked


def test_no_active_or_adapter_does_not_audit_archived_questions(project):
    _, mission = project
    (mission / "ask-palette.md").write_text("# Archived fictional question", encoding="utf-8")
    active = mission.parent / "ACTIVE"
    active.unlink()
    assert question_integrity.decide(payload(project)) == {}
    (project[0] / ".claude/orchestrator/adapter.json").unlink()
    assert not question_integrity.decide(payload(project)).get("decision")


@pytest.mark.parametrize("invalid", ["{broken", '{"id":"../outside","session":"owner"}', '{"id":"example-mission"}'])
def test_unowned_active_is_reported_as_unverified(project, invalid):
    (project[1].parent / "ACTIVE").write_text(invalid, encoding="utf-8")
    output = question_integrity.decide(payload(project))
    assert "미실행" in output["systemMessage"]
    assert "decision" not in output


def test_missing_owned_mission_and_inspector_errors_block(project, monkeypatch):
    project[1].rmdir()
    assert question_integrity.decide(payload(project))["decision"] == "block"
    project[1].mkdir()
    monkeypatch.setattr(question_integrity.mission_state, "question_inspections", lambda folder: (_ for _ in ()).throw(OSError("unreadable")))
    output = question_integrity.decide(payload(project))
    assert output["decision"] == "block" and "unreadable" in output["reason"]


def test_unavailable_adapter_and_missing_session_report_unverified(project):
    adapter = project[0] / ".claude/orchestrator/adapter.json"
    adapter.write_text("{broken", encoding="utf-8")
    output = question_integrity.decide(payload(project))
    assert "미실행" in output["systemMessage"] and "decision" not in output
    adapter.write_text(json.dumps({"schema": 1, "paths": {"stateDir": "state"}}), encoding="utf-8")
    output = question_integrity.decide(payload(project, session_id=""))
    assert "미실행" in output["systemMessage"] and "decision" not in output


def test_unsupported_events_and_tools_are_ignored(project):
    assert question_integrity.decide(payload(project, "PreToolUse")) == {}
    assert question_integrity.decide(payload(project, "PostToolUse", tool_name="Read")) == {}
    assert "미실행" in question_integrity.decide([])["systemMessage"]
    assert "미실행" in question_integrity.decide({})["systemMessage"]


@pytest.mark.parametrize("tool_input", ["*** Begin Patch\n*** End Patch", [], 42])
def test_patch_payload_shape_does_not_bypass_owned_inspection(project, tool_input):
    (project[1] / "ask-palette.md").write_text("# Unregistered fictional question", encoding="utf-8")
    output = question_integrity.decide(payload(project, "PostToolUse", tool_name="apply_patch", tool_input=tool_input))
    assert output["decision"] == "block" and "ask-palette.md" in output["reason"]


def test_codex_relative_command_cwd_is_resolved_once(project):
    unrelated = project[0].parent
    (project[1] / "ask-palette.md").write_text("# Unregistered fictional question", encoding="utf-8")
    fields = payload(project, "PostToolUse", cwd=str(unrelated), tool_input={"command": "opaque-writer", "cwd": project[0].name})
    assert question_integrity.decide(fields, "codex")["decision"] == "block"


def test_malformed_stdin_is_explicit_without_writes(project):
    before = snapshot(project[0])
    done = subprocess.run([sys.executable, "-B", question_integrity.__file__], input=b"{invalid", capture_output=True, check=True)
    output = json.loads(done.stdout)
    assert "미실행" in output["systemMessage"] and "decision" not in output
    assert snapshot(project[0]) == before


def run_cli(project, *args):
    done = subprocess.run([sys.executable, "-B", str(Path(question_integrity.SCRIPTS) / "mission_state.py"),
                           "question", *args, "--project", str(project[0]), "--mission", project[1].name], capture_output=True, check=False)
    assert done.returncode == 0, done.stderr.decode("utf-8")


def test_real_cli_publish_answer_and_reviewed_restore_pass(project, tmp_path):
    draft = tmp_path / "draft.json"
    draft.write_text(json.dumps(document()), encoding="utf-8")
    run_cli(project, "publish", "--input", str(draft))
    assert question_integrity.decide(payload(project)) == {}
    reply = tmp_path / "reply.json"
    reply.write_text(json.dumps(answer()), encoding="utf-8")
    run_cli(project, "answer", "ask-palette.md", "--input", str(reply))
    assert question_integrity.decide(payload(project)) == {}
    record = question_records.read_document(project[1], "ask-palette.md")
    restored = copy.deepcopy(record)
    extra = {**answer(), "id": "reply-border", "text": "Use a white fictional border.",
             "updates": [{"id": "1", "status": "answered", "answer": "Blue with a white border."}]}
    restored["answers"].append(extra)
    restored["items"][0].update(status="answered", answer=extra["updates"][0]["answer"])
    markdown = project[1] / "ask-palette.md"
    markdown.write_text(markdown.read_text(encoding="utf-8") + "\n" + extra["text"] + "\n", encoding="utf-8")
    assert question_integrity.decide(payload(project))["decision"] == "block"
    evidence = tmp_path / "original.md"
    evidence.write_bytes(markdown.read_bytes())
    draft.write_text(json.dumps(restored), encoding="utf-8")
    digest = hashlib.sha256((project[1] / "questions/ask-palette.json").read_bytes()).hexdigest()
    run_cli(project, "restore", "--input", str(draft), "--source", str(evidence), "--expect-revision", "1", "--expect-sha256", digest)
    run_cli(project, "check")
    assert question_integrity.decide(payload(project, "PostToolUse")) == {}
    assert question_integrity.decide(payload(project)) == {}


def test_cli_answer_repairs_markdown_only_answer_without_question_changes(project, tmp_path):
    question_records.publish_document(project[1], document())
    source = project[1] / "questions/ask-palette.json"
    before = source.read_bytes()
    question_records.answer_document(project[1], "ask-palette.md", answer())
    source.write_bytes(before)
    output = question_integrity.decide(payload(project))
    assert output["decision"] == "block"
    assert output["reason"].index("question answer") < output["reason"].index("question restore")
    reply = tmp_path / "confirmed-reply.json"
    reply.write_text(json.dumps(answer()), encoding="utf-8")
    run_cli(project, "answer", "ask-palette.md", "--input", str(reply))
    run_cli(project, "check")
    assert question_integrity.decide(payload(project)) == {}
