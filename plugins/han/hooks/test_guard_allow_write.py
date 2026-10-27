import json
import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mission_guard  # noqa: E402

CL = r"(?:^|\s)-c\s*(?:\d+|\$\{?\w+\}?)(?=\s|$)"
ADAPTER = {
    "guard": {
        "allow": [r"tool\.bat submit \d+ --check-only"],
        "deny": [{"pattern": r"(?:^|\s)submit(?:\s|$)", "reason": "submit 금지"}],
        "p4Deny": [
            {"commands": ["obliterate"], "reason": "obliterate 금지"},
            {"commands": ["shelve"], "when": r"(?:^|\s)-d\b", "reason": "shelve 삭제 금지"},
            {"commands": ["revert"], "unless": r"^(?=.*(?:^|\s)-a\b)(?=.*" + CL + ")", "reason": "revert 제한"},
            {"commands": ["edit", "add"], "unless": CL, "reason": "번호 CL 만"},
        ],
    }
}


def reason(command, adapter=ADAPTER, problems=None):
    return mission_guard.judge_command(adapter, command, "Bash", [] if problems is None else problems, "D:/p").reason


@pytest.mark.parametrize(
    "command, expected",
    [
        ("tool.bat submit 12 --check-only", ""),
        ("tool.bat submit 12", "submit 금지"),
        ("tool.bat submit 12 --check-only && p4 submit", "submit 금지"),
        ("p4 obliterate -y //x/...", "obliterate 금지"),
        ("& p4 -u me -p ssl:h:1666 obliterate //x", "obliterate 금지"),
        ('"C:/Program Files/Perforce/p4.exe" obliterate //x', "obliterate 금지"),
        ("p4 shelve -c 1", ""),
        ("p4 shelve -d -c 1", "shelve 삭제 금지"),
        ("p4 revert -a -c 12 //x/...", ""),
        ("p4 revert -c 12 a.cs", "revert 제한"),
        ("p4 -c ws edit -c $CL a.cs", ""),
        ('p4 -c ws edit -c "$CL" a.cs', ""),
        ("p4 -c ws edit a.cs", "번호 CL 만"),
        ("p4 opened", ""),
        ("echo obliterate", ""),
    ],
)
def test_allow_list_and_p4_deny(command, expected):
    assert reason(command) == expected, command


def test_allow_does_not_skip_p4_client_check():
    adapter = {"guard": {"allow": [r"p4 sync"], "p4Client": {"require": True}}}
    assert reason("p4 sync", adapter) != ""


def test_bad_p4_deny_and_allow_are_reported_not_allowed():
    problems = []
    adapter = {"guard": {"allow": ["("], "p4Deny": [{"commands": []}, {"commands": "submit", "reason": "x"}]}}
    assert reason("p4 submit", adapter, problems) == "x"
    assert any("guard.allow" in p for p in problems) and any("commands" in p for p in problems)


@pytest.mark.parametrize(
    "pattern, path, ok",
    [
        ("_My/missions/**", "_My/missions/m1/mission.md", True),
        ("_My/missions/**", "_my/MISSIONS/m1/x.md", True),
        ("_My/missions/**", "Assets/a.cs", False),
        ("docs/*.md", "docs/a.md", True),
        ("docs/*.md", "docs/sub/a.md", False),
        ("**/*.tmp", "a/b/c.tmp", True),
        ("**/*.tmp", "c.tmp", True),
    ],
)
def test_glob_regex(pattern, path, ok):
    assert bool(mission_guard.glob_regex(pattern).fullmatch(path)) is ok


@pytest.fixture
def write_project(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    state = project / "_My" / "missions"
    state.mkdir(parents=True)
    adapter_dir = project / ".claude" / "orchestrator"
    adapter_dir.mkdir(parents=True)
    adapter = {"schema": 1, "project": "T", "paths": {"vcsRoot": ".", "stateDir": "_My/missions"},
               "guard": {"coordinatorWrite": ["_My/missions/**"]}}
    (adapter_dir / "adapter.json").write_text(json.dumps(adapter), encoding="utf-8")
    (state / "ACTIVE").write_text(json.dumps({"id": "m1", "session": "s1", "openedAt": "2026-10-26T00:00:00"}), encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    return project


def write(project, target, session="s1", **extra):
    payload = {"tool_name": "Write", "session_id": session, "cwd": str(project),
               "tool_input": {"file_path": str(target)}, "han_runtime": "claude", **extra}
    return mission_guard.decide(payload)


def test_coordinator_write_whitelist(write_project):
    p = write_project
    assert write(p, p / "_My" / "missions" / "m1" / "mission.md").deny == ""
    denied = write(p, p / "Assets" / "a.cs").deny
    assert "Assets/a.cs" in denied and "roles.implement" in denied
    assert write(p, "Assets/b.cs").deny  # 상대 경로도 프로젝트 기준
    assert write(p, p.parent / "memory" / "x.md").deny == ""  # 프로젝트 밖
    assert write(p, p / "Assets" / "a.cs", agent_id="a1").deny == ""  # 서브에이전트
    assert write(p, p / "Assets" / "a.cs", session="s2").deny == ""  # 다른 세션


def test_coordinator_write_off_without_active_or_setting(write_project):
    p = write_project
    (p / "_My" / "missions" / "ACTIVE").unlink()
    assert write(p, p / "Assets" / "a.cs").deny == ""
    adapter_path = p / ".claude" / "orchestrator" / "adapter.json"
    adapter = json.loads(adapter_path.read_text(encoding="utf-8"))
    del adapter["guard"]
    adapter_path.write_text(json.dumps(adapter), encoding="utf-8")
    (p / "_My" / "missions" / "ACTIVE").write_text(json.dumps({"id": "m1", "session": "s1"}), encoding="utf-8")
    assert write(p, p / "Assets" / "a.cs").deny == ""


def test_hooks_json_routes_write_tools_to_guard():
    hooks = json.loads((Path(__file__).parent / "hooks.json").read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    matchers = [group["matcher"] for group in hooks for hook in group["hooks"]
                if hook.get("type") == "command" and "${CLAUDE_PLUGIN_ROOT}/hooks/mission_guard.py" in hook.get("args", [])]
    for tool in ("Bash", "PowerShell", "Write", "Edit", "MultiEdit", "NotebookEdit", "apply_patch", "ApplyPatch"):
        assert any(re.fullmatch(matcher, tool) for matcher in matchers), tool
