import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "hooks"))
sys.path.insert(0, str(PLUGIN / "skills/slots/scripts"))

import mission_guard
import session_rules
import slots


@pytest.fixture(autouse=True)
def isolated_codex_environment(monkeypatch, tmp_path):
    monkeypatch.delenv("HAN_ADAPTERS_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_ADAPTERS_DIR", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.delenv("HAN_ADAPTERS_DIR", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    folder = tmp_path / "project"
    adapter_dir = folder / ".codex/orchestrator"
    adapter_dir.mkdir(parents=True)
    adapter = {
        "schema": 1,
        "project": "test",
        "paths": {"stateDir": "state"},
        "guard": {"deny": [{"pattern": r"^p4\s+(submit|sync)\b", "reason": "blocked"}]},
        "slots": {
            "enabled": True,
            "root": str(tmp_path / "slots"),
            "client": "test_{name}",
            "names": ["slot1"],
            "prepare": [{"id": "claude", "run": "echo claude"}],
        },
        "runtimes": {"codex": {"slots": {"prepare": [{"id": "codex", "run": "echo codex"}]}}},
    }
    (adapter_dir / "adapter.json").write_text(json.dumps(adapter), encoding="utf-8")
    return folder


def hook(project, command, session="parent", **fields):
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "cwd": str(project),
        "session_id": session,
        "tool_input": {"command": command},
        **fields,
    }
    done = subprocess.run(
        [sys.executable, "-B", str(PLUGIN / "codex/mission_guard.py")],
        input=json.dumps(payload).encode(),
        capture_output=True,
        check=True,
        timeout=15,
    )
    return json.loads(done.stdout or b"{}")


def denied(output):
    return output.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


def test_codex_mission_lifecycle_and_session_scope(project, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project.parent / "other"))
    opened = hook(project, "echo han-mission open test")
    assert "켜짐" in opened["systemMessage"]
    active = project / "state/ACTIVE"
    assert json.loads(active.read_text())["session"] == "parent"
    assert denied(hook(project, "p4 submit -c 123", agent_id="child", agent_type="default"))
    assert not denied(hook(project, "p4 opened -c 123"))
    assert not denied(hook(project, "p4 submit -c 123", session="other"))
    allowance = project / "state/test/allow-once"
    allowance.parent.mkdir(exist_ok=True)
    allowance.write_text("p4 sync\n", encoding="utf-8")
    assert not denied(hook(project, "p4 sync"))
    assert denied(hook(project, "p4 sync"))
    assert not denied(hook(project, "echo han-mission close test"))
    assert not active.exists()
    assert not allowance.exists()


def test_codex_shell_normalization_and_command_cwd(project):
    hook(project, "echo han-mission open test")
    payload = {"command": "$tool = 'p4'\n& $tool `\n sync", "shell": "pwsh"}
    assert denied(hook(project, "", tool_input=payload))
    assert denied(hook(project.parent, "", tool_input={"command": "p4 sync", "cwd": str(project)}))
    assert denied(hook(project, "echo han-mission close test; p4 sync"))
    assert (project / "state/ACTIVE").exists()


def test_codex_adapter_precedence_and_shared_fallback(project, monkeypatch):
    claude = project / ".claude/orchestrator"
    claude.mkdir(parents=True)
    adapter = claude / "adapter.json"
    adapter.write_text('{"schema":1}', encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project.parent))
    payload = {"cwd": str(project), "han_runtime": "codex"}
    assert Path(mission_guard.find_adapter(payload).adapter_path).parent.name == "orchestrator"
    assert ".codex" in mission_guard.find_adapter(payload).adapter_path
    (project / ".codex/orchestrator/adapter.json").unlink()
    assert Path(mission_guard.find_adapter(payload).adapter_path) == adapter


def test_runtime_overlay_and_local_precedence(project, monkeypatch):
    path = project / ".codex/orchestrator/adapter.json"
    local = {"project": "local", "runtimes": {"codex": {"project": "codex-local"}}}
    (path.parent / "local.json").write_text(json.dumps(local), encoding="utf-8")
    claude = mission_guard.load_adapter(str(path))
    codex = mission_guard.load_adapter(str(path), runtime="codex")
    assert claude["project"] == "local"
    assert claude["slots"]["prepare"][0]["id"] == "claude"
    assert codex["project"] == "codex-local"
    assert codex["slots"]["prepare"][0]["id"] == "codex"
    config = slots.Config(mission_guard.Located(str(project), str(path)), codex, "codex")
    monkeypatch.setattr(slots.shutil, "which", lambda name: name)
    assert "memoryDir" not in config.values("slot1")
    command = slots.wt_command(config, "slot1", "task; more")
    assert command[-4:] == ["codex", "-c", "model_auto_compact_token_limit=200000", "$han-codex:han-mission task\\; more"]
    config = slots.Config(mission_guard.Located(str(project), str(path)), claude, "claude")
    command = slots.wt_command(config, "slot1", "task")
    assert command[command.index("claude"):][:5] == ["claude", "--autocompact", "500000", "-n", "slot1: task"]
    assert command[-2:] == ["--", "/han:mission task"]
    assert "--mcp-config" not in command and "--dangerously-load-development-channels" not in command


def test_codex_registry_ignores_claude_configuration(tmp_path, monkeypatch):
    registry = tmp_path / "registry"
    registry.mkdir()
    monkeypatch.setenv("HAN_ADAPTERS_DIR", str(registry))
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_ADAPTERS_DIR", "wrong")
    assert mission_guard.resolve_registry("codex").path == str(registry)
    monkeypatch.delenv("HAN_ADAPTERS_DIR")
    assert mission_guard.resolve_registry("codex").source != "userConfig"


def test_codex_registry_uses_config_after_environment(tmp_path, monkeypatch):
    env_registry = tmp_path / "env-registry"
    config_registry = tmp_path / "config-registry"
    config_home = tmp_path / "codex-home"
    config_home.mkdir()
    (config_home / "config.toml").write_text(
        f'[shell_environment_policy.set]\nHAN_ADAPTERS_DIR = "{config_registry.as_posix()}"\n', encoding="utf-8"
    )
    monkeypatch.setenv("CODEX_HOME", str(config_home))
    monkeypatch.setenv("HAN_ADAPTERS_DIR", str(env_registry))
    choice = mission_guard.resolve_registry("codex")
    assert choice == mission_guard.Registry(str(env_registry), "env")
    monkeypatch.delenv("HAN_ADAPTERS_DIR")
    choice = mission_guard.resolve_registry("codex")
    assert choice == mission_guard.Registry(str(config_registry), "codexConfig")


def test_codex_registry_reads_default_home_config(tmp_path, monkeypatch):
    config_home = tmp_path / "home"
    codex_home = config_home / ".codex"
    codex_home.mkdir(parents=True)
    registry = tmp_path / "registry"
    (codex_home / "config.toml").write_text(
        f'[shell_environment_policy.set]\nHAN_ADAPTERS_DIR = "{registry.as_posix()}"\n', encoding="utf-8"
    )
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("HAN_ADAPTERS_DIR", raising=False)

    def expand_home(path):
        if path == "~":
            return str(config_home)
        return path

    monkeypatch.setattr(os.path, "expanduser", expand_home)
    choice = mission_guard.resolve_registry("codex")
    assert choice == mission_guard.Registry(str(registry), "codexConfig")


def test_codex_registry_empty_or_missing_config_falls_back(tmp_path, monkeypatch):
    config_home = tmp_path / "codex-home"
    config_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(config_home))
    monkeypatch.delenv("HAN_ADAPTERS_DIR", raising=False)
    monkeypatch.setattr(mission_guard, "in_place_adapters_dir", lambda: str(tmp_path / "missing"))
    assert mission_guard.resolve_registry("codex") == mission_guard.Registry("", "none")
    config_path = config_home / "config.toml"
    for content in ("", "[shell_environment_policy.set]\n", '[shell_environment_policy.set]\nHAN_ADAPTERS_DIR = ""\n'):
        config_path.write_text(content, encoding="utf-8")
        assert mission_guard.resolve_registry("codex") == mission_guard.Registry("", "none")


def test_codex_environment_registry_bypasses_invalid_config(tmp_path, monkeypatch):
    config_home = tmp_path / "codex-home"
    config_home.mkdir()
    (config_home / "config.toml").write_text("invalid = [", encoding="utf-8")
    registry = tmp_path / "registry"
    monkeypatch.setenv("CODEX_HOME", str(config_home))
    monkeypatch.setenv("HAN_ADAPTERS_DIR", str(registry))
    assert mission_guard.resolve_registry("codex") == mission_guard.Registry(str(registry), "env")


@pytest.mark.parametrize(
    "config, message",
    [
        ("[shell_environment_policy.set\nHAN_ADAPTERS_DIR = \"x\"", "읽기 실패"),
        ('[shell_environment_policy.set]\nHAN_ADAPTERS_DIR = 7\n', "문자열이어야"),
        ('shell_environment_policy = "invalid"\n', "shell_environment_policy 는 객체"),
        ('[shell_environment_policy]\nset = "invalid"\n', "shell_environment_policy.set 는 객체"),
    ],
)
def test_codex_registry_invalid_config_raises_guard_off(tmp_path, monkeypatch, config, message):
    config_home = tmp_path / "codex-home"
    config_home.mkdir()
    (config_home / "config.toml").write_text(config, encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(config_home))
    monkeypatch.delenv("HAN_ADAPTERS_DIR", raising=False)
    with pytest.raises(mission_guard.GuardOff, match=message):
        mission_guard.resolve_registry("codex")


def test_codex_registry_unreadable_config_raises_guard_off(tmp_path, monkeypatch):
    config_home = tmp_path / "codex-home"
    config_home.mkdir()
    config_path = config_home / "config.toml"
    config_path.touch()
    original_open = open

    def unreadable(path, *args, **kwargs):
        if os.fspath(path) == str(config_path):
            raise PermissionError("denied")
        return original_open(path, *args, **kwargs)

    monkeypatch.setenv("CODEX_HOME", str(config_home))
    monkeypatch.delenv("HAN_ADAPTERS_DIR", raising=False)
    monkeypatch.setattr("builtins.open", unreadable)
    with pytest.raises(mission_guard.GuardOff, match="읽기 실패"):
        mission_guard.resolve_registry("codex")


def test_claude_registry_ignores_codex_config(tmp_path, monkeypatch):
    config_home = tmp_path / "codex-home"
    config_home.mkdir()
    registry = tmp_path / "registry"
    (config_home / "config.toml").write_text(
        f'[shell_environment_policy.set]\nHAN_ADAPTERS_DIR = "{registry.as_posix()}"\n', encoding="utf-8"
    )
    monkeypatch.setenv("CODEX_HOME", str(config_home))
    monkeypatch.delenv("HAN_ADAPTERS_DIR", raising=False)
    monkeypatch.setattr(mission_guard, "in_place_adapters_dir", lambda: str(tmp_path / "missing"))
    assert mission_guard.resolve_registry("claude") == mission_guard.Registry("", "none")


def test_installed_codex_hook_uses_config_registry_without_environment(project, tmp_path):
    (project / ".codex/orchestrator/adapter.json").unlink()
    installed = tmp_path / "installed" / "plugins" / "han"
    (installed / "codex").mkdir(parents=True)
    (installed / "hooks").mkdir()
    shutil.copy2(PLUGIN / "codex/mission_guard.py", installed / "codex/mission_guard.py")
    shutil.copy2(PLUGIN / "hooks/mission_guard.py", installed / "hooks/mission_guard.py")
    registry = tmp_path / "registry"
    adapter_dir = registry / "test"
    adapter_dir.mkdir(parents=True)
    (registry / "projects.json").write_text(
        json.dumps([{"adapter": "test", "roots": [str(project)]}]), encoding="utf-8"
    )
    (adapter_dir / "adapter.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "paths": {"stateDir": str(tmp_path / "state")},
                "guard": {"deny": [{"pattern": r"^p4\s+submit\b", "reason": "blocked"}]},
            }
        ),
        encoding="utf-8",
    )
    config_home = tmp_path / "codex-home"
    config_home.mkdir()
    (config_home / "config.toml").write_text(
        f'[shell_environment_policy.set]\nHAN_ADAPTERS_DIR = "{registry.as_posix()}"\n', encoding="utf-8"
    )
    env = {key: value for key, value in os.environ.items() if key != "HAN_ADAPTERS_DIR"}
    env["CODEX_HOME"] = str(config_home)
    hook_path = installed / "codex/mission_guard.py"
    payload = {"tool_name": "Bash", "cwd": str(project), "session_id": "installed-session"}
    opened = subprocess.run(
        [sys.executable, "-B", str(hook_path)],
        input=json.dumps({**payload, "tool_input": {"command": "echo han-mission open test"}}).encode(),
        capture_output=True,
        env=env,
        check=True,
        timeout=15,
    )
    assert "켜짐" in json.loads(opened.stdout)["systemMessage"]
    denied_submit = subprocess.run(
        [sys.executable, "-B", str(hook_path)],
        input=json.dumps({**payload, "tool_input": {"command": "p4 submit -c 1"}}).encode(),
        capture_output=True,
        env=env,
        check=True,
        timeout=15,
    )
    assert denied(json.loads(denied_submit.stdout))
    active = json.loads((tmp_path / "state/ACTIVE").read_text(encoding="utf-8"))
    assert active["id"] == "test" and active["session"] == "installed-session"


def test_codex_rules_are_opt_in_and_do_not_export_claude_env(tmp_path, monkeypatch):
    env_file = tmp_path / "env"
    monkeypatch.setenv("CLAUDE_ENV_FILE", str(env_file))
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_ADAPTERS_DIR", "wrong")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_INJECT_RULES", "on")
    monkeypatch.delenv("HAN_CODEX_INJECT_RULES", raising=False)
    monkeypatch.delenv("HAN_RULES_DISABLE", raising=False)
    payload = {"hook_event_name": "SessionStart"}
    assert not session_rules.decide(payload, "codex")
    monkeypatch.setenv("HAN_CODEX_INJECT_RULES", "on")
    assert session_rules.decide(payload, "codex")["hookSpecificOutput"]["additionalContext"]
    assert not env_file.exists()
    monkeypatch.setenv("HAN_RULES_DISABLE", "1")
    assert not session_rules.decide(payload, "codex")


@pytest.mark.parametrize("disable", [False, True])
def test_codex_transcript_context_is_available_without_personal_rules(tmp_path, monkeypatch, disable):
    env_file = tmp_path / "claude-env"
    monkeypatch.setenv("CLAUDE_ENV_FILE", str(env_file))
    monkeypatch.delenv("HAN_CODEX_INJECT_RULES", raising=False)
    monkeypatch.setenv("HAN_RULES_DISABLE", "1" if disable else "0")
    transcript = 'C:/대화 기록/quoted "session".jsonl'
    output = session_rules.decide({"hook_event_name": "SessionStart", "transcript_path": transcript}, "codex")
    context = output["hookSpecificOutput"]["additionalContext"]
    assert json.dumps(transcript, ensure_ascii=False) in context
    assert session_rules.HEADER not in context
    assert not env_file.exists()


def test_codex_transcript_context_survives_rules_injection(tmp_path, monkeypatch):
    monkeypatch.setenv("HAN_CODEX_INJECT_RULES", "on")
    monkeypatch.delenv("HAN_RULES_DISABLE", raising=False)
    payload = {"hook_event_name": "SessionStart", "transcript_path": str(tmp_path / "current.jsonl")}
    context = session_rules.decide(payload, "codex")["hookSpecificOutput"]["additionalContext"]
    assert "current.jsonl" in context and session_rules.HEADER in context
    subagent = {**payload, "hook_event_name": "SubagentStart"}
    context = session_rules.decide(subagent, "codex")["hookSpecificOutput"]["additionalContext"]
    assert "current.jsonl" not in context and session_rules.HEADER in context


def test_codex_entrypoints_resolve_shared_code_from_another_cwd(project):
    for relative, arguments in [
        ("han-slots/scripts/slots.py", ["--project", str(project), "create", "slot1", "--dry-run"]),
        ("han-adapter-init/scripts/check_guard.py", ["--doctor"]),
        ("han-slots/scripts/dashboard.py", ["--help"]),
    ]:
        result = subprocess.run(
            [sys.executable, str(PLUGIN / "codex/skills" / relative), *arguments],
            cwd=project,
            capture_output=True,
            timeout=20,
        )
        assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
        if relative == "han-slots/scripts/slots.py":
            assert "echo codex" in result.stdout.decode("utf-8")
            assert not (project.parent / "slots").exists()


def test_codex_mission_state_entrypoint_resolves_shared_code_from_another_cwd(project):
    hook(project, "echo han-mission open test", transcript_path="C:/t/parent.jsonl")
    script = PLUGIN / "codex/skills/han-mission/scripts/mission_state.py"
    outputs = []
    for arguments in (["init", "--goal", "목표"], ["mission", "set", "--stage", "impl"], ["status"]):
        result = subprocess.run(
            [sys.executable, "-B", str(script), "--project", str(project), *arguments],
            cwd=project.parent,
            capture_output=True,
            timeout=20,
        )
        assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
        outputs.append(result.stdout.decode("utf-8"))
    folder = project / "state/test"
    assert "상태: 진행" in (folder / "mission.md").read_text(encoding="utf-8")
    assert "현재 단계 impl" in outputs[2]
    assert "C:/t/parent.jsonl" in (folder / "sessions.tsv").read_text(encoding="utf-8")


def test_codex_status_reads_unshared_questions_without_a_valid_manifest(project):
    hook(project, "echo han-mission open test")
    script = PLUGIN / "codex/skills/han-mission/scripts/mission_state.py"
    command = [sys.executable, "-B", str(script), "--project", str(project)]
    initialized = subprocess.run([*command, "init"], cwd=project.parent, capture_output=True, timeout=20)
    assert initialized.returncode == 0, initialized.stderr.decode("utf-8", errors="replace")
    folder = project / "state/test"
    record = {
        "schema": 1, "id": "ask-7", "revision": 1, "title": "질문", "intro": "", "body": "",
        "items": [{"id": "1", "title": "진행할까요?", "body": "", "status": "pending", "answer": ""}],
        "approval": {"status": "none", "reason": ""}, "answers": [],
    }
    draft = project / "question.json"
    draft.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    published = subprocess.run([*command, "question", "publish", "--input", str(draft)],
                               cwd=project.parent, capture_output=True, timeout=20)
    assert published.returncode == 0, published.stderr.decode("utf-8", errors="replace")
    (folder / "shared-documents.tsv").write_text("잘못된 헤더\n", encoding="utf-8")
    pending = subprocess.run([*command, "status"], cwd=project.parent, capture_output=True, timeout=20)
    assert pending.returncode == 0, pending.stderr.decode("utf-8", errors="replace")
    assert "ask-7.md 답 없음" in pending.stdout.decode("utf-8")
    reply = project / "reply.json"
    reply.write_text(json.dumps({
        "schema": 1, "id": "reply-1", "revision": 1, "at": "2026-10-26", "text": "권장안 승인",
        "updates": [{"id": "1", "status": "answered", "answer": "권장안 승인"}],
    }, ensure_ascii=False), encoding="utf-8")
    saved = subprocess.run([*command, "question", "answer", "ask-7.md", "--input", str(reply)],
                           cwd=project.parent, capture_output=True, timeout=20)
    assert saved.returncode == 0, saved.stderr.decode("utf-8", errors="replace")
    answered = subprocess.run([*command, "status"], cwd=project.parent, capture_output=True, timeout=20)
    assert answered.returncode == 0, answered.stderr.decode("utf-8", errors="replace")
    assert "걸린 것: 없음" in answered.stdout.decode("utf-8")
    (folder / "ask-legacy.md").write_text("# 질문\n\n## 답\n\n2026-10-26 권장\n", encoding="utf-8")
    legacy = subprocess.run([*command, "status"], cwd=project.parent, capture_output=True, timeout=20)
    assert legacy.returncode == 0, legacy.stderr.decode("utf-8", errors="replace")
    assert "ask-legacy.md 기록 확인 필요" in legacy.stdout.decode("utf-8")
    assert "답 없음" not in legacy.stdout.decode("utf-8")


def test_shared_references_are_copies_of_claude_source():
    import importlib.util

    spec = importlib.util.spec_from_file_location("sync_shared", Path(__file__).with_name("sync_shared.py"))
    sync = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sync)
    files = list(sync.shared_files())
    assert len(files) >= 10
    assert any(t.endswith("human-writing.md") for _, t in files)
    assert sync.differing() == [], "python codex/sync_shared.py --write"
