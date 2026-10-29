import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "scripts"))
import grok_transcript


def update(kind, text="", timestamp=1800000000, session="sample-session", **params):
    return {
        "timestamp": timestamp,
        "params": {"sessionId": session, "update": {"sessionUpdate": kind, "content": {"type": "text", "text": text}}, **params},
    }


def write(path, *events):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
    return path


def test_find_transcript_uses_exact_session_and_home_override(tmp_path, monkeypatch):
    home = tmp_path / "home"
    expected = write(home / ".grok/sessions/encoded/sample-session/updates.jsonl", update("turn_completed"))
    write(home / ".grok/sessions/encoded/sample-session-extra/updates.jsonl", update("turn_completed"))
    monkeypatch.delenv("GROK_HOME", raising=False)
    assert grok_transcript.find_transcript("sample-session", str(home)) == str(expected)
    override = tmp_path / "override"
    other = write(override / "sessions/encoded/sample-session/updates.jsonl", update("turn_completed"))
    monkeypatch.setenv("GROK_HOME", str(override))
    assert grok_transcript.find_transcript("sample-session", str(home)) == str(other)


@pytest.mark.parametrize("session", ["*", "../sample-session", "a/b", "", None])
def test_find_transcript_rejects_unsafe_ids(session):
    assert grok_transcript.find_transcript(session) == ""


def test_detection_requires_acp_update_not_generic_filename(tmp_path):
    unrelated = write(tmp_path / "updates.jsonl", {"timestamp": 1800000000})
    assert not grok_transcript.is_transcript(unrelated)
    path = write(tmp_path / "copy.jsonl", {"params": {"update": {}}}, update("turn_completed"))
    assert grok_transcript.is_transcript(path)
    assert grok_transcript.session_id(path) == "sample-session"


def test_session_fallback_uses_directory_not_common_filename(tmp_path):
    event = update("turn_completed")
    event["params"].pop("sessionId")
    path = write(tmp_path / "distinct-session/updates.jsonl", event)
    assert grok_transcript.session_id(path) == "distinct-session"


def test_streaming_messages_preserve_prompts_and_assistant_across_tools(tmp_path):
    path = write(tmp_path / "updates.jsonl",
        update("user_message_chunk", "Make "), update("user_message_chunk", "it blue", timestamp=1800000001),
        update("agent_thought_chunk", "hidden thought"),
        update("agent_message_chunk", "Changed "), update("tool_call", "hidden tool"),
        update("agent_message_chunk", "the color."), update("turn_completed"),
        update("user_message_chunk", "Use green instead", timestamp=1800000002), update("turn_completed"),
        update("user_message_chunk", "Keep the size", timestamp=1800000003),
    )
    messages = grok_transcript.read_messages(path)
    assert [(row["role"], row["text"]) for row in messages] == [
        ("user", "Make it blue"), ("assistant", "Changed the color."),
        ("user", "Use green instead"), ("user", "Keep the size"),
    ]
    assert messages[0]["timestamp"] == "2027-01-15T08:00:01.000Z"


def test_numeric_timestamps_and_malformed_lines_are_safe(tmp_path):
    path = write(tmp_path / "updates.jsonl", [], {"params": []},
        update("user_message_chunk", "Prompt", _meta={"agentTimestampMs": 1800000000123}),
        update("turn_completed", timestamp="bad"),
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{broken\n")
    events = grok_transcript.read_events(path)
    assert events[0]["timestamp"] == "2027-01-15T08:00:00.123Z"
    assert events[1]["timestamp"] == ""


def test_non_text_content_and_system_updates_never_become_messages(tmp_path):
    image = update("user_message_chunk", "hidden image")
    image["params"]["update"]["content"]["type"] = "image"
    path = write(tmp_path / "updates.jsonl", image, update("system_message", "hidden instruction"),
        update("agent_thought_chunk", "hidden thought"), update("tool_call_update", "hidden result"),
    )
    assert grok_transcript.read_messages(path) == []
    assert all(not row["text"] for row in grok_transcript.read_events(path))


def test_overflowing_agent_timestamp_is_ignored_without_losing_message(tmp_path):
    path = write(tmp_path / "updates.jsonl", update("user_message_chunk", "Prompt", _meta={"agentTimestampMs": 10**400}))
    messages = grok_transcript.read_messages(path)
    assert len(messages) == 1
    assert messages[0]["text"] == "Prompt" and messages[0]["timestamp"] == ""


@pytest.mark.parametrize("final_kind,completed", [("turn_completed", True), ("user_message_chunk", False)])
def test_activity_reads_tail_after_split_utf8_line_and_ignores_partial_final_event(tmp_path, final_kind, completed):
    large = update("user_message_chunk", "가" * grok_transcript.ACTIVITY_TAIL_BYTES)
    final = update(final_kind, "Continue", timestamp=1800000002)
    path = tmp_path / "updates.jsonl"
    while True:
        raw = (json.dumps(large, ensure_ascii=False) + "\n" + json.dumps(final) + "\n").encode("utf-8") + b'{"params":{"update":'
        if raw[len(raw) - grok_transcript.ACTIVITY_TAIL_BYTES] & 0xC0 == 0x80:
            break
        final["params"]["update"]["content"]["text"] += "x"
    path.write_bytes(raw)
    events = list(grok_transcript.iter_events(path, grok_transcript.ACTIVITY_TAIL_BYTES))
    assert len(events) == 1 and events[0]["update"] == final_kind
    state = grok_transcript.activity(path)
    assert state["completed"] == completed
    assert state["last_timestamp"] == "2027-01-15T08:00:02.000Z"
    messages = grok_transcript.read_messages(path)
    assert messages[0]["text"].startswith("가" * 100)
    assert len(grok_transcript.read_events(path)) == 2


def test_tail_reader_keeps_event_starting_exactly_at_boundary(tmp_path):
    first = update("user_message_chunk", "Earlier")
    final = update("turn_completed", timestamp=1800000002)
    path = tmp_path / "updates.jsonl"
    final_line = (json.dumps(final) + "\n").encode("utf-8")
    path.write_bytes((json.dumps(first) + "\n").encode("utf-8") + final_line)
    tail_bytes = len(final_line)
    events = list(grok_transcript.iter_events(path, tail_bytes))
    assert len(events) == 1 and events[0]["update"] == "turn_completed"


def test_activity_recovers_completed_before_oversized_metadata(tmp_path):
    path = write(tmp_path / "updates.jsonl", update("turn_completed", timestamp=1800000001),
        update("session_info_update", "x" * grok_transcript.ACTIVITY_TAIL_BYTES, timestamp=1800000002))
    state = grok_transcript.activity(path)
    assert state["completed"] and state["last_timestamp"] == "2027-01-15T08:00:01.000Z"


def test_activity_recovers_oversized_final_tool_activity(tmp_path):
    path = write(tmp_path / "updates.jsonl", update("turn_completed", timestamp=1800000001),
        update("tool_call_update", "x" * grok_transcript.ACTIVITY_TAIL_BYTES, timestamp=1800000002))
    state = grok_transcript.activity(path)
    assert not state["completed"] and state["last_timestamp"] == "2027-01-15T08:00:02.000Z"


def test_activity_with_valid_tail_event_never_falls_back_to_full_read(tmp_path, monkeypatch):
    path = write(tmp_path / "updates.jsonl", update("user_message_chunk", "x" * grok_transcript.ACTIVITY_TAIL_BYTES),
        update("turn_completed", timestamp=1800000002))
    original = grok_transcript.iter_events
    reads = []

    def tracked(path, tail_bytes=0):
        reads.append(tail_bytes)
        return original(path, tail_bytes)

    monkeypatch.setattr(grok_transcript, "iter_events", tracked)
    assert grok_transcript.activity(path)["completed"]
    assert reads == [grok_transcript.ACTIVITY_TAIL_BYTES]


def test_completion_survives_metadata_until_next_activity(tmp_path):
    path = write(tmp_path / "updates.jsonl", update("user_message_chunk", "Prompt"),
        update("turn_completed"), update("session_info_update", timestamp=1800000002),
    )
    assert grok_transcript.activity(path) == {
        "session_id": "sample-session", "last_timestamp": "2027-01-15T08:00:00.000Z", "last_role": "completed", "completed": True,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(update("user_message_chunk", "Continue", timestamp=1800000003)) + "\n")
    assert not grok_transcript.activity(path)["completed"]


def child_meta(**changes):
    return {"subagent_id": "worker-a", "parent_session_id": "sample-session", "child_session_id": "child-session",
        "attempt_id": "attempt-a", "status": "running", "started_at": "2027-01-15T08:00:01Z", **changes}


def write_meta(path, **changes):
    meta = child_meta(**changes)
    folder = path.parent / "subagents" / meta["subagent_id"]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return folder / "meta.json"


def child_event(kind, timestamp=1800000001, session="sample-session", **changes):
    event = update(kind, timestamp=timestamp, session=session)
    event["params"]["update"].update({"subagent_id": "worker-a", "child_session_id": "child-session", "attempt_id": "attempt-a", **changes})
    return event


def test_subagents_metadata_finds_child_in_different_cwd_and_preserves_running(tmp_path, monkeypatch):
    monkeypatch.setenv("GROK_HOME", str(tmp_path))
    parent = write(tmp_path / "sessions/parent-cwd/sample-session/updates.jsonl", update("turn_completed"))
    child = write(tmp_path / "sessions/child-cwd/child-session/updates.jsonl", update("turn_completed", session="child-session"))
    write_meta(parent, child_cwd="/different/worktree")
    records = grok_transcript.subagent_records(parent)
    assert len(records) == 1
    assert records[0]["transcript"] == str(child)
    assert records[0]["status"] == "running"
    assert records[0]["started_at"] == "2027-01-15T08:00:01.000Z"
    assert grok_transcript.subagent_transcripts(parent) == [str(child)]


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_terminal_metadata_wins_over_running_lifecycle(tmp_path, status):
    parent = write(tmp_path / "sample-session/updates.jsonl", child_event("subagent_spawned"))
    write_meta(parent, status=status, completed_at="2027-01-15T08:00:02Z")
    record = grok_transcript.subagent_records(parent)[0]
    assert record["status"] == status
    assert record["completed_at"] == "2027-01-15T08:00:02.000Z"
    assert record["transcript"] == ""


def test_lifecycle_fallback_completes_matching_attempt_without_metadata(tmp_path):
    parent = write(tmp_path / "sample-session/updates.jsonl", child_event("subagent_spawned"),
        child_event("subagent_finished", 1800000003, status="completed"))
    record = grok_transcript.subagent_records(parent)[0]
    assert record["status"] == "completed"
    assert record["started_at"] == "2027-01-15T08:00:01.000Z"
    assert record["completed_at"] == "2027-01-15T08:00:03.000Z"


def test_old_attempt_finish_does_not_stop_new_attempt(tmp_path):
    parent = write(tmp_path / "sample-session/updates.jsonl", child_event("subagent_spawned"),
        child_event("subagent_spawned", 1800000002, attempt_id="attempt-b", child_session_id="child-b"),
        child_event("subagent_finished", 1800000003, status="completed"))
    records = grok_transcript.subagent_records(parent)
    assert len(records) == 1
    assert records[0]["attempt_id"] == "attempt-b" and records[0]["status"] == "running"


def test_stale_running_metadata_cannot_reverse_terminal_lifecycle(tmp_path):
    parent = write(tmp_path / "sample-session/updates.jsonl", child_event("subagent_spawned"),
        child_event("subagent_finished", 1800000003, status="completed"))
    write_meta(parent, status="running")
    record = grok_transcript.subagent_records(parent)[0]
    assert record["status"] == "completed" and record["completed_at"] == "2027-01-15T08:00:03.000Z"


@pytest.mark.parametrize("meta_started", ["2027-01-15T08:00:01Z", "", "invalid"])
def test_stale_or_undated_metadata_cannot_replace_newer_lifecycle_attempt(tmp_path, meta_started):
    parent = write(tmp_path / "sample-session/updates.jsonl",
        child_event("subagent_spawned", 1800000002, attempt_id="attempt-b", child_session_id="child-b"))
    write_meta(parent, status="completed", started_at=meta_started, completed_at="2027-01-15T08:00:03Z")
    record = grok_transcript.subagent_records(parent)[0]
    assert record["attempt_id"] == "attempt-b" and record["status"] == "running"


def test_newer_dated_metadata_can_replace_earlier_lifecycle_attempt(tmp_path):
    parent = write(tmp_path / "sample-session/updates.jsonl", child_event("subagent_spawned"))
    write_meta(parent, attempt_id="attempt-b", child_session_id="child-b", started_at="2027-01-15T08:00:02Z")
    assert grok_transcript.subagent_records(parent)[0]["attempt_id"] == "attempt-b"


def test_missing_lifecycle_start_time_keeps_known_attempt(tmp_path):
    parent = write(tmp_path / "sample-session/updates.jsonl", child_event("subagent_spawned", timestamp="invalid"))
    write_meta(parent, attempt_id="attempt-b", child_session_id="child-b", started_at="2027-01-15T08:00:02Z")
    assert grok_transcript.subagent_records(parent)[0]["attempt_id"] == "attempt-a"


def test_subagent_lifecycle_is_not_lost_outside_activity_tail(tmp_path):
    parent = write(tmp_path / "sample-session/updates.jsonl", child_event("subagent_spawned"),
        update("session_info_update", "x" * grok_transcript.ACTIVITY_TAIL_BYTES), update("turn_completed"))
    records = grok_transcript.subagent_records(parent)
    assert len(records) == 1 and records[0]["status"] == "running"


@pytest.mark.parametrize("changes", [
    {"parent_session_id": "different-parent"}, {"child_session_id": "../escape"},
    {"status": "unknown"}, {"status": []}, {"attempt_id": []}, {"child_session_id": "sample-session"},
])
def test_invalid_metadata_is_rejected(tmp_path, changes):
    parent = write(tmp_path / "sample-session/updates.jsonl", update("turn_completed"))
    write_meta(parent, **changes)
    assert grok_transcript.subagent_records(parent) == []


def test_malformed_metadata_falls_back_to_parent_lifecycle_and_ignores_foreign_events(tmp_path):
    parent = write(tmp_path / "sample-session/updates.jsonl", child_event("subagent_spawned"),
        child_event("subagent_finished", session="other-parent", status="completed"),
        child_event("subagent_finished", parent_session_id="other-parent", status="completed"))
    meta = write_meta(parent)
    meta.write_text("{broken", encoding="utf-8")
    assert grok_transcript.subagent_records(parent)[0]["status"] == "running"


def test_optional_attempt_id_and_missing_child_transcript_remain_visible(tmp_path):
    parent = write(tmp_path / "sample-session/updates.jsonl", update("turn_completed"))
    write_meta(parent, attempt_id=None)
    record = grok_transcript.subagent_records(parent)[0]
    assert record["attempt_id"] == "" and record["transcript"] == "" and record["status"] == "running"


def test_subagent_metadata_and_session_symlinks_cannot_escape_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("GROK_HOME", str(tmp_path / "store"))
    parent = write(tmp_path / "store/sessions/parent/sample-session/updates.jsonl", update("turn_completed"))
    outside = tmp_path / "outside"
    write(outside / "child-session/updates.jsonl", update("turn_completed", session="child-session"))
    (outside / "worker-a").mkdir()
    (outside / "worker-a/meta.json").write_text(json.dumps(child_meta()), encoding="utf-8")
    try:
        (parent.parent / "subagents").symlink_to(outside, target_is_directory=True)
        (tmp_path / "store/sessions/escape").symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"Directory symlinks unavailable: {error}")
    assert grok_transcript.subagent_records(parent) == []
    assert grok_transcript.find_transcript("child-session") == ""


def test_summary_parent_references_do_not_invent_subagent_relationships(tmp_path, monkeypatch):
    monkeypatch.setenv("GROK_HOME", str(tmp_path))
    parent = write(tmp_path / "sessions/parent/sample-session/updates.jsonl", update("turn_completed"))
    child = write(tmp_path / "sessions/child/forked-session/updates.jsonl", update("turn_completed", session="forked-session"))
    child.with_name("summary.json").write_text(json.dumps({"parent_session_id": "sample-session"}), encoding="utf-8")
    assert grok_transcript.subagent_records(parent) == []
