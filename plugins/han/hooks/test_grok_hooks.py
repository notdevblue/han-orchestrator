import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import hook_runtime
import mission_guard
import question_integrity
import session_rules


@pytest.fixture
def project(tmp_path, monkeypatch):
    for key in ("GROK_HOOK_EVENT", "GROK_SESSION_ID", "CLAUDE_PROJECT_DIR", "HAN_REPO", "HAN_GROK_INJECT_RULES"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HAN_ADAPTERS_DIR", str(tmp_path / "no-registry"))
    root = tmp_path / "sample-project"
    adapter = root / ".grok/orchestrator/adapter.json"
    adapter.parent.mkdir(parents=True)
    adapter.write_text(json.dumps({"schema": 1, "paths": {"stateDir": "state"},
                                  "guard": {"deny": [{"pattern": "^release-tool publish$", "reason": "release blocked"}],
                                            "coordinatorWrite": ["state/**"]}}), encoding="utf-8")
    return root


def payload(project, command="echo ready", tool="run_terminal_command", session="owner", **fields):
    return {"hookEventName": "pre_tool_use", "sessionId": session, "cwd": str(project),
            "toolName": tool, "toolInput": {"command": command}, **fields}


def run_hook(script, value, **environment):
    env = {key: value for key, value in os.environ.items() if key not in ("GROK_HOOK_EVENT", "GROK_SESSION_ID", "CLAUDE_PROJECT_DIR")}
    result = subprocess.run([sys.executable, "-B", str(Path(__file__).parent / script)],
                            input=json.dumps(value).encode("utf-8"), capture_output=True,
                            env={**env, **environment}, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout) if result.stdout else {}


@pytest.mark.parametrize("tool,shell,marker", [("run_terminal_command", "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe", "Write-Output"),
                                              ("Shell", "C:/Program Files/PowerShell/7/pwsh.exe", "Write-Host"),
                                              ("run_terminal_command", "C:/Program Files/Git/bin/bash.exe", "echo")])
def test_native_marker_lifecycle_and_init_boundary(project, tool, shell, marker):
    script = Path(__file__).parents[1] / "skills/mission/scripts/mission_state.py"
    init = [sys.executable, "-B", str(script), "--runtime", "grok", "--project", str(project), "--mission", "garden", "init", "--tier", "M", "--goal", "Build a fictional garden"]
    before = subprocess.run(init, capture_output=True, timeout=30)
    assert before.returncode != 0
    def call(action, session="owner"):
        value = payload(project, tool=tool, session=session,
                        toolInput={"command": f"{marker} han-mission {action} garden", "shell": shell})
        return run_hook("mission_guard.py", value)
    assert "permissionDecision" not in call("open").get("hookSpecificOutput", {})
    active_path = project / "state/ACTIVE"
    assert json.loads(active_path.read_text())["session"] == "owner"
    initialized = subprocess.run(init, capture_output=True, timeout=30)
    assert initialized.returncode == 0, initialized.stderr
    assert (project / "state/garden/mission.md").is_file()
    assert call("close", "stranger")["hookSpecificOutput"]["permissionDecision"] == "deny"
    call("resume", "next-owner")
    assert json.loads(active_path.read_text())["session"] == "next-owner"
    call("close", "next-owner")
    assert not active_path.exists()


def open_mission(project):
    assert not mission_guard.decide(payload(project, "echo han-mission open garden")).deny


def test_native_deny_command_and_unrelated_session(project):
    open_mission(project)
    assert mission_guard.decide(payload(project, "release-tool publish")).deny
    assert not mission_guard.decide(payload(project, "release-tool publish", session="unrelated")).deny
    assert mission_guard.decide(payload(project, "release-tool publish", session="child", subagentType="developer")).deny
    assert mission_guard.decide(payload(project, "echo ready", toolInputTruncated=True)).deny
    assert not mission_guard.decide(payload(project, "echo ready", session="unrelated", toolInputTruncated=True)).deny


def test_native_marker_requires_owner_and_records_transcript(project):
    missing = payload(project, "echo han-mission open garden", session="")
    assert "session_id" in mission_guard.decide(missing).deny
    transcript = str(project / "session.json")
    assert not mission_guard.decide(payload(project, "echo han-mission open garden", transcriptPath=transcript)).deny
    sessions = (project / "state/garden/sessions.tsv").read_text(encoding="utf-8")
    assert f"open\towner\t{transcript}" in sessions


@pytest.mark.parametrize("action", ["open", "resume", "close"])
def test_subagent_cannot_change_mission_owner(project, action):
    open_mission(project)
    before = (project / "state/ACTIVE").read_bytes()
    assert mission_guard.decide(payload(project, f"echo han-mission {action} garden", session="child", subagentType="developer")).deny
    assert (project / "state/ACTIVE").read_bytes() == before


@pytest.mark.parametrize("tool,key", [("write", "path"), ("Write", "path"), ("search_replace", "file_path"),
                                     ("hashline_edit", "path"), ("StrReplace", "path"), ("EditNotebook", "target_notebook")])
def test_native_guarded_writes_and_subagent_identity(project, tool, key):
    open_mission(project)
    value = payload(project, tool=tool, toolInput={key: "src/garden.py", "contents": "garden = []"})
    assert mission_guard.decide(value).deny
    assert mission_guard.decide({**value, "agent_id": "forged-child"}).deny
    assert not mission_guard.decide({**value, "subagentType": "developer", "sessionId": "child"}).deny
    question = {**value, "toolInput": {key: "state/garden/questions/ask-colors.json", "contents": "{}"}, "subagentType": "developer"}
    assert "직접 쓰지 않음" in mission_guard.decide(question).deny


def test_grok_event_cwd_already_includes_tool_cwd(project):
    open_mission(project)
    value = payload(project, tool="write", toolInput={"path": "src/garden.py", "cwd": "elsewhere"})
    assert mission_guard.decide(value).deny


@pytest.mark.parametrize("event", ["post_tool_use", "stop"])
def test_native_integrity_checks_current_and_child_session(project, event):
    open_mission(project)
    question = project / "state/garden/ask-colors.md"
    question.write_text("Untracked answer", encoding="utf-8")
    value = payload(project, tool="write", hookEventName=event)
    assert question_integrity.decide(value)["decision"] == "block"
    assert question_integrity.decide({**value, "sessionId": "child", "subagentType": "developer"})["decision"] == "block"
    assert question_integrity.decide({**value, "sessionId": "unrelated"}) == {}


def test_native_session_context_does_not_inherit_claude_rule_opt_in(project, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_INJECT_RULES", "on")
    monkeypatch.setenv("GROK_HOOK_EVENT", "session_start")
    value = {"hookEventName": "session_start", "sessionId": "owner", "source": "load", "transcriptPath": "C:/sessions/garden.json"}
    output = session_rules.decide(value)
    context = output["hookSpecificOutput"]["additionalContext"]
    assert "C:/sessions/garden.json" in context
    assert session_rules.HEADER not in context


@pytest.mark.parametrize("runtime", ["claude", "codex"])
def test_explicit_other_runtime_is_preserved(project, runtime, monkeypatch):
    monkeypatch.setenv("GROK_HOOK_EVENT", "pre_tool_use")
    value = hook_runtime.normalize_payload({"tool_name": "Bash", "tool_input": {"command": "echo ready", "shell": "bash"}}, runtime)
    assert value["han_runtime"] == runtime
    assert value["tool_name"] == "Bash"
    assert hook_runtime.normalize_payload(value)["han_runtime"] == runtime


def test_native_matchers_cover_current_grok_contract():
    hooks = json.loads((Path(__file__).parent / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    assert set(("new", "load")).issubset(hooks["SessionStart"][0]["matcher"].split("|"))
    for event in ("PreToolUse", "PostToolUse"):
        assert set(("run_terminal_command", "Shell", "search_replace", "write")).issubset(hooks[event][0]["matcher"].split("|"))


def test_native_manifest_exposes_only_grok_skills_and_agents():
    root = Path(__file__).parents[1]
    manifest = json.loads((root / ".grok-plugin/plugin.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "han"
    assert manifest["agents"]
    native_agent_root = (root / "grok/agents").resolve()
    claude_agent_root = (root / "agents").resolve()
    discovered = []
    for path in manifest["agents"]:
        agent_dir = (root / path).resolve()
        assert agent_dir == native_agent_root
        assert not agent_dir.is_relative_to(claude_agent_root)
        assert agent_dir.is_dir()
        for agent in agent_dir.glob("*.md"):
            text = agent.read_text(encoding="utf-8")
            assert text.startswith("---\n")
            fields = dict(line.split(": ", 1) for line in text.split("---", 2)[1].strip().splitlines())
            discovered.append(f"{manifest['name']}:{fields['name']}")
            assert fields["model"] == "inherit"
    assert len(discovered) == 5
    assert set(discovered) == {"han:researcher", "han:dev-small", "han:dev-medium", "han:verifier", "han:reviewer"}
    assert len(manifest["skills"]) == 8
    assert len(set(manifest["skills"])) == 8
    for skill in manifest["skills"]:
        assert skill.startswith("./grok/skills/")
        assert (root / skill / "SKILL.md").is_file()


def test_session_start_sources_use_explicit_grok_rule_opt_in(project, monkeypatch, tmp_path):
    rules = tmp_path / "bundle/rules"
    rules.mkdir(parents=True)
    (rules / "general.md").write_text("Shared rule", encoding="utf-8")
    (rules / "main-session.md").write_text("Main rule", encoding="utf-8")
    monkeypatch.setenv("GROK_PLUGIN_ROOT", str(rules.parent))
    monkeypatch.setenv("HAN_GROK_INJECT_RULES", "on")
    for source in ("new", "load"):
        output = session_rules.decide({"hookEventName": "session_start", "source": source})
        assert "Main rule" in output["hookSpecificOutput"]["additionalContext"]
    output = session_rules.decide({"hookEventName": "subagent_start", "subagentType": "developer"})
    assert output["hookSpecificOutput"]["hookEventName"] == "SubagentStart"
    assert "Shared rule" in output["hookSpecificOutput"]["additionalContext"]
    assert "Main rule" not in output["hookSpecificOutput"]["additionalContext"]
