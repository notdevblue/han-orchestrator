import json
import os
import uuid
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent
sys.path.insert(0, str(HOOKS_DIR))

import memory_notice  # noqa: E402


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    config = tmp_path / "config"
    monkeypatch.setenv(memory_notice.CONFIG_DIR_ENV, str(config))
    monkeypatch.setattr(memory_notice, "DONE_DIR", str(tmp_path / "done"))
    monkeypatch.delenv(memory_notice.DISABLE_ENV, raising=False)
    return config


def payload(path, session="s-1", tool="Write"):
    return {"hook_event_name": "PreToolUse", "tool_name": tool, "session_id": session, "tool_input": {"file_path": str(path)}}


def context(output):
    return output["hookSpecificOutput"]["additionalContext"]


def test_memory_note_shows_notice_without_blocking(isolated):
    output = memory_notice.decide(payload(isolated / "projects" / "D--Work-Example" / "memory" / "rule.md"))
    assert context(output) == memory_notice.NOTICE
    assert output["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert "permissionDecision" not in output["hookSpecificOutput"]
    assert "decision" not in output


def test_memory_index_and_edit_tool_also_show_notice(isolated):
    memory = isolated / "projects" / "p" / "memory"
    assert memory_notice.decide(payload(memory / "MEMORY.md", tool="Edit"))


def test_default_user_folder_is_recognized_without_config_dir(monkeypatch):
    monkeypatch.delenv(memory_notice.CONFIG_DIR_ENV)
    path = Path(os.path.expanduser("~")) / ".claude" / "projects" / "p" / "memory" / "a.md"
    assert memory_notice.decide(payload(path))


def test_windows_style_separators_are_recognized(isolated):
    path = str(isolated / "projects" / "p" / "memory" / "a.md").replace("/", "\\")
    assert memory_notice.decide(payload(path))


def test_path_outside_memory_does_nothing(tmp_path, isolated):
    assert memory_notice.decide(payload(tmp_path / "project" / "Wiki" / "guide.md")) == {}
    assert memory_notice.decide(payload(isolated / "projects" / "p" / "notes.md")) == {}
    assert memory_notice.decide(payload(isolated / "projects" / "p" / "memory")) == {}
    assert memory_notice.decide(payload(isolated / "rules" / "memory" / "a.md")) == {}


def test_non_markdown_in_memory_does_nothing(isolated):
    assert memory_notice.decide(payload(isolated / "projects" / "p" / "memory" / "a.json")) == {}


def test_dotdot_escape_from_memory_does_nothing(isolated):
    path = str(isolated / "projects" / "p" / "memory" / ".." / "notes.md")
    assert memory_notice.decide(payload(path)) == {}


def test_shows_once_per_session(isolated):
    path = isolated / "projects" / "p" / "memory" / "a.md"
    assert memory_notice.decide(payload(path))
    assert memory_notice.decide(payload(path)) == {}
    assert memory_notice.decide(payload(path, session="s-2"))


def test_outside_path_does_not_use_up_the_notice(tmp_path, isolated):
    assert memory_notice.decide(payload(tmp_path / "guide.md")) == {}
    assert memory_notice.decide(payload(isolated / "projects" / "p" / "memory" / "a.md"))


def test_disable_env_turns_it_off(isolated, monkeypatch):
    monkeypatch.setenv(memory_notice.DISABLE_ENV, "1")
    assert memory_notice.decide(payload(isolated / "projects" / "p" / "memory" / "a.md")) == {}


def test_missing_or_odd_fields_give_nothing(isolated):
    path = isolated / "projects" / "p" / "memory" / "a.md"
    assert memory_notice.decide({"session_id": "s-1"}) == {}
    assert memory_notice.decide({"session_id": "s-1", "tool_input": {"file_path": 3}}) == {}
    assert memory_notice.decide(payload(path, session="../x")) == {}
    assert memory_notice.decide(payload(path, session="")) == {}


def test_script_prints_ascii_json_and_survives_garbage(tmp_path, isolated):
    path = isolated / "projects" / "p" / "memory" / "a.md"
    env = {**os.environ, memory_notice.CONFIG_DIR_ENV: str(isolated)}
    env.pop(memory_notice.DISABLE_ENV, None)
    script = HOOKS_DIR / "memory_notice.py"
    session = "script-" + uuid.uuid4().hex
    result = subprocess.run([sys.executable, "-B", str(script)], input=json.dumps(payload(path, session=session)).encode(), capture_output=True, env=env, timeout=30)
    assert result.returncode == 0
    output = json.loads(result.stdout.decode("ascii"))
    assert context(output) == memory_notice.NOTICE
    garbage = subprocess.run([sys.executable, "-B", str(script)], input=b"not json", capture_output=True, env=env, timeout=30)
    assert garbage.returncode == 0 and garbage.stdout == b""
    done = Path(memory_notice.tempfile.gettempdir()) / "han-memory-notice" / session
    done.unlink(missing_ok=True)
