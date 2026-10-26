import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent
sys.path.insert(0, str(HOOKS_DIR))

import session_rules  # noqa: E402

SCRIPT = HOOKS_DIR / "session_rules.py"
REAL_RULES = HOOKS_DIR.parent / "rules"
CLEARED_ENV = (
    "HAN_RULES_DISABLE",
    "CLAUDE_PLUGIN_ROOT",
    "CLAUDE_ENV_FILE",
    "CLAUDE_PLUGIN_OPTION_ADAPTERS_DIR",
    "HAN_ADAPTERS_DIR",
    "CLAUDE_PLUGIN_OPTION_INJECT_RULES",
    "HAN_CODEX_INJECT_RULES",
)
SUBAGENT_START = {"hook_event_name": "SubagentStart", "agent_id": "a1", "agent_type": "dev-medium"}


def make_root(folder, rules):
    rules_dir = folder / "rules"
    rules_dir.mkdir(parents=True)
    for name, text in rules.items():
        (rules_dir / name).write_text(text, encoding="utf-8")
    return folder


def run_hook(root=None, env=None, payload=None, raw=None, inject="on", runtime="claude"):
    base = {k: v for k, v in os.environ.items() if k not in CLEARED_ENV}
    if inject is not None:
        option = "HAN_CODEX_INJECT_RULES" if runtime == "codex" else "CLAUDE_PLUGIN_OPTION_INJECT_RULES"
        base[option] = inject
    if root is not None:
        base["CLAUDE_PLUGIN_ROOT"] = str(root)
    base.update(env or {})
    stdin = raw if raw is not None else json.dumps(payload or {}).encode("utf-8")
    script = HOOKS_DIR.parent / "codex" / "session_rules.py" if runtime == "codex" else SCRIPT
    result = subprocess.run(
        [sys.executable, "-B", str(script)], input=stdin, capture_output=True, env=base, timeout=30
    )
    assert result.returncode == 0
    text = result.stdout.decode("utf-8").strip()
    return json.loads(text) if text else {}


def context_of(output):
    return output["hookSpecificOutput"]["additionalContext"]


def test_injects_header_and_rules_in_name_order(tmp_path):
    root = make_root(tmp_path, {"b.md": "둘째", "a.md": "첫째\n", "note.txt": "무시"})
    output = run_hook(root)
    assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert context_of(output) == session_rules.HEADER + "\n\n첫째\n\n둘째"
    assert "systemMessage" not in output


def test_strips_bom_and_skips_empty_files(tmp_path):
    root = make_root(tmp_path, {"a.md": "﻿본문", "b.md": "  \n"})
    assert context_of(run_hook(root)) == session_rules.HEADER + "\n\n본문"


def test_disabled_outputs_nothing(tmp_path):
    root = make_root(tmp_path, {"a.md": "규칙"})
    assert run_hook(root, {"HAN_RULES_DISABLE": "1"}) == {}


@pytest.mark.parametrize("value", [None, "", "  ", "off", "no", "0", "false"])
def test_without_inject_option_nothing_is_injected(tmp_path, value):
    root = make_root(tmp_path, {"a.md": "규칙"})
    assert run_hook(root, inject=value) == {}
    assert run_hook(root, payload=SUBAGENT_START, inject=value) == {}


@pytest.mark.parametrize("value", ["on", "ON", "true", "True", "1", "yes", " Yes "])
def test_inject_option_values_enable_injection(tmp_path, value):
    root = make_root(tmp_path, {"a.md": "규칙"})
    assert context_of(run_hook(root, inject=value)).endswith("규칙")
    output = run_hook(root, payload=SUBAGENT_START, inject=value)
    assert output["hookSpecificOutput"]["hookEventName"] == "SubagentStart"


def test_disable_env_wins_over_inject_option(tmp_path):
    root = make_root(tmp_path, {"a.md": "규칙"})
    assert run_hook(root, {"HAN_RULES_DISABLE": "1"}, inject="on") == {}
    assert run_hook(root, {"HAN_RULES_DISABLE": "1"}, payload=SUBAGENT_START, inject="on") == {}


def test_without_inject_option_registry_export_still_happens(tmp_path):
    root = make_root(tmp_path / "plugin", {"a.md": "규칙"})
    output = run_hook(root, registry_env(tmp_path), inject=None)
    assert output == {}
    assert len(env_lines(tmp_path)) == 1


def test_other_disable_value_does_not_disable(tmp_path):
    root = make_root(tmp_path, {"a.md": "규칙"})
    assert "hookSpecificOutput" in run_hook(root, {"HAN_RULES_DISABLE": "0"})


def test_missing_rules_folder_outputs_nothing(tmp_path):
    assert run_hook(tmp_path) == {}


def test_empty_rules_folder_outputs_nothing(tmp_path):
    root = make_root(tmp_path, {})
    assert run_hook(root) == {}


def test_over_limit_is_cut_with_warning(tmp_path):
    root = make_root(tmp_path, {"a.md": "가" * 12000})
    output = run_hook(root)
    assert len(context_of(output)) == session_rules.CONTEXT_LIMIT
    assert context_of(output).startswith(session_rules.HEADER)
    total = len(session_rules.HEADER) + 2 + 12000
    assert str(total) in output["systemMessage"]
    assert str(total - session_rules.CONTEXT_LIMIT) in output["systemMessage"]


def test_exactly_at_limit_has_no_warning(tmp_path):
    body = "가" * (session_rules.CONTEXT_LIMIT - len(session_rules.HEADER) - 2)
    root = make_root(tmp_path, {"a.md": body})
    output = run_hook(root)
    assert len(context_of(output)) == session_rules.CONTEXT_LIMIT
    assert "systemMessage" not in output


def test_unreadable_rule_reports_failure_not_silence(tmp_path):
    root = make_root(tmp_path, {})
    (root / "rules" / "a.md").write_bytes(b"\xff\xfe\x00bad")
    output = run_hook(root)
    assert output["systemMessage"].startswith("개인 규칙 주입 실패: ")
    assert "hookSpecificOutput" not in output


def test_stdout_is_ascii_json_that_round_trips_korean(tmp_path):
    root = make_root(tmp_path, {"a.md": "한글 규칙"})
    base = {k: v for k, v in os.environ.items() if k not in CLEARED_ENV}
    base["CLAUDE_PLUGIN_ROOT"] = str(root)
    base["CLAUDE_PLUGIN_OPTION_INJECT_RULES"] = "on"
    result = subprocess.run([sys.executable, "-B", str(SCRIPT)], input=b"{}", capture_output=True, env=base, timeout=30)
    result.stdout.decode("ascii")
    assert "한글 규칙" in context_of(json.loads(result.stdout))


def test_root_falls_back_to_plugin_folder_next_to_hooks(monkeypatch):
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    assert Path(session_rules.plugin_root()).resolve() == HOOKS_DIR.parent.resolve()


def test_real_personal_rules_fit_within_limit():
    if not REAL_RULES.is_dir():
        pytest.skip("rules 폴더 없음")
    context = session_rules.build_context(
        [p.read_text(encoding="utf-8-sig").strip() for p in sorted(REAL_RULES.glob("*.md"))]
    )
    assert len(context) <= session_rules.CONTEXT_LIMIT


@pytest.mark.parametrize("runtime", ["claude", "codex"])
@pytest.mark.parametrize("event", session_rules.EVENTS)
def test_document_paths_are_readable_from_slot_cwd(tmp_path, monkeypatch, runtime, event):
    monkeypatch.chdir(tmp_path)
    plugin = HOOKS_DIR.parent.resolve()
    output = run_hook(plugin, payload={"hook_event_name": event}, runtime=runtime)
    context = context_of(output)
    root = plugin / "codex" if runtime == "codex" else plugin
    skill = "han-mission" if runtime == "codex" else "mission"
    paths = (root / "rules" / "human-documents.md", root / "skills" / skill / "references" / "human-writing.md")
    assert output["hookSpecificOutput"]["hookEventName"] == event
    assert "## 사람용 문서 자료" in context
    for path in paths:
        assert path.is_absolute()
        assert f"`{path}`" in context
        assert path.read_text(encoding="utf-8-sig").strip()


@pytest.mark.parametrize("runtime", ["claude", "codex"])
@pytest.mark.parametrize("event", session_rules.EVENTS)
@pytest.mark.parametrize("missing", ["rules", "writing", "both"])
def test_missing_document_materials_leave_fixture_context_unchanged(tmp_path, monkeypatch, runtime, event, missing):
    rules = {"a.md": "기존 규칙"}
    if missing == "writing":
        rules["human-documents.md"] = "문서 규칙"
    root = make_root(tmp_path, rules)
    if missing == "rules":
        skill = "han-mission" if runtime == "codex" else "mission"
        writing = root / "skills" / skill / "references" / "human-writing.md"
        writing.parent.mkdir(parents=True)
        writing.write_text("작성 안내", encoding="utf-8")
    monkeypatch.setattr(session_rules, "runtime_root", lambda value: str(root))
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_INJECT_RULES", "on")
    monkeypatch.setenv("HAN_CODEX_INJECT_RULES", "on")
    monkeypatch.delenv("HAN_RULES_DISABLE", raising=False)
    context = context_of(session_rules.decide({"hook_event_name": event}, runtime))
    assert context == session_rules.build_context(list(rules.values()))
    assert "사람용 문서 자료" not in context


@pytest.mark.parametrize("runtime", ["claude", "codex"])
@pytest.mark.parametrize("event", session_rules.EVENTS)
@pytest.mark.parametrize("disabled", [False, True])
def test_document_paths_follow_injection_settings(runtime, event, disabled):
    env = {"HAN_RULES_DISABLE": "1"} if disabled else {}
    inject = "on" if disabled else "off"
    assert run_hook(HOOKS_DIR.parent, env, {"hook_event_name": event}, inject=inject, runtime=runtime) == {}


def test_subagent_start_gets_same_rules_with_its_own_event_name(tmp_path):
    root = make_root(tmp_path, {"a.md": "규칙"})
    output = run_hook(root, payload=SUBAGENT_START)
    assert output["hookSpecificOutput"]["hookEventName"] == "SubagentStart"
    assert context_of(output) == session_rules.HEADER + "\n\n규칙"


def test_main_session_rules_go_last_to_session_start_only(tmp_path):
    root = make_root(tmp_path, {"a.md": "공통", "main-session.md": "메인 전용", "z.md": "끝"})
    assert context_of(run_hook(root)) == session_rules.HEADER + "\n\n공통\n\n끝\n\n메인 전용"
    assert context_of(run_hook(root, payload=SUBAGENT_START)) == session_rules.HEADER + "\n\n공통\n\n끝"


@pytest.mark.parametrize("runtime", ["claude", "codex"])
def test_real_subagent_rules_drop_main_session_sections(runtime):
    payload = {"hook_event_name": "SessionStart"}
    main = context_of(run_hook(HOOKS_DIR.parent, payload=payload, runtime=runtime))
    subagent = context_of(run_hook(HOOKS_DIR.parent, payload=SUBAGENT_START, runtime=runtime))
    for heading in ("## Answering", "## Plan Output", "## Subagent Dispatch"):
        assert heading in main
        assert heading not in subagent
    for heading in ("## Basic Attitude", "## Code Writing Rules", "## Root Cause First"):
        assert heading in subagent


def test_subagent_start_honors_disable_and_limit(tmp_path):
    root = make_root(tmp_path, {"a.md": "가" * 12000})
    assert run_hook(root, {"HAN_RULES_DISABLE": "yes"}, payload=SUBAGENT_START) == {}
    output = run_hook(root, payload=SUBAGENT_START)
    assert len(context_of(output)) == session_rules.CONTEXT_LIMIT
    assert "상한" in output["systemMessage"]


@pytest.mark.parametrize(
    "raw",
    [b"", b"not json", b"[1]", b'{"hook_event_name": 5}', b'{"hook_event_name": "Other"}', b'{"hook_event_name": ["x"]}'],
)
def test_empty_broken_or_unknown_payload_is_session_start(tmp_path, raw):
    root = make_root(tmp_path, {"a.md": "규칙"})
    assert run_hook(root, raw=raw)["hookSpecificOutput"]["hookEventName"] == "SessionStart"


def test_session_start_payload_keeps_session_start(tmp_path):
    root = make_root(tmp_path, {"a.md": "규칙"})
    output = run_hook(root, payload={"hook_event_name": "SessionStart", "source": "compact"})
    assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"


def registry_env(tmp_path, value=r"D:\Work\adapters"):
    return {"CLAUDE_ENV_FILE": str(tmp_path / "env.sh"), "CLAUDE_PLUGIN_OPTION_ADAPTERS_DIR": value}


def env_lines(tmp_path):
    path = tmp_path / "env.sh"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def test_session_start_exports_registry_dir_to_env_file(tmp_path):
    root = make_root(tmp_path / "plugin", {"a.md": "규칙"})
    output = run_hook(root, registry_env(tmp_path))
    assert env_lines(tmp_path) == ['export HAN_ADAPTERS_DIR="D:/Work/adapters"']
    assert "systemMessage" not in output


def test_export_appends_after_existing_lines_and_is_not_repeated(tmp_path):
    root = make_root(tmp_path / "plugin", {"a.md": "규칙"})
    (tmp_path / "env.sh").write_text("export OTHER=1\n", encoding="utf-8")
    run_hook(root, registry_env(tmp_path))
    run_hook(root, registry_env(tmp_path))
    assert env_lines(tmp_path) == ["export OTHER=1", 'export HAN_ADAPTERS_DIR="D:/Work/adapters"']


def test_export_escapes_quote_dollar_and_backtick(tmp_path):
    root = make_root(tmp_path / "plugin", {"a.md": "규칙"})
    run_hook(root, registry_env(tmp_path, r'C:\a "b"\$x\`y'))
    assert env_lines(tmp_path) == [r'export HAN_ADAPTERS_DIR="C:/a \"b\"/\$x/\`y"']


@pytest.mark.parametrize(
    "case",
    ["no_env_file", "empty_option", "han_env_already_set", "no_option"],
)
def test_export_is_skipped(tmp_path, case):
    root = make_root(tmp_path / "plugin", {"a.md": "규칙"})
    env = registry_env(tmp_path)
    if case == "no_env_file":
        del env["CLAUDE_ENV_FILE"]
    elif case == "empty_option":
        env["CLAUDE_PLUGIN_OPTION_ADAPTERS_DIR"] = "  "
    elif case == "han_env_already_set":
        env["HAN_ADAPTERS_DIR"] = "E:/already"
    else:
        del env["CLAUDE_PLUGIN_OPTION_ADAPTERS_DIR"]
    run_hook(root, env)
    assert env_lines(tmp_path) == []


def test_subagent_start_does_not_touch_env_file(tmp_path):
    root = make_root(tmp_path / "plugin", {"a.md": "규칙"})
    run_hook(root, registry_env(tmp_path), payload=SUBAGENT_START)
    assert env_lines(tmp_path) == []


def test_export_still_happens_when_rules_are_disabled(tmp_path):
    root = make_root(tmp_path / "plugin", {"a.md": "규칙"})
    output = run_hook(root, {**registry_env(tmp_path), "HAN_RULES_DISABLE": "1"})
    assert output == {}
    assert len(env_lines(tmp_path)) == 1


def test_export_write_failure_warns_and_keeps_rules(tmp_path):
    root = make_root(tmp_path / "plugin", {"a.md": "규칙"})
    folder = tmp_path / "env.sh"
    folder.mkdir()
    output = run_hook(root, registry_env(tmp_path))
    assert "HAN_ADAPTERS_DIR" in output["systemMessage"]
    assert "CLAUDE_ENV_FILE" in output["systemMessage"]
    assert context_of(output).endswith("규칙")


def transcript_payload(path=r"C:\Users\example\.claude\projects\p\s1.jsonl"):
    return {"hook_event_name": "SessionStart", "transcript_path": path}


def transcript_env(tmp_path):
    return {"CLAUDE_ENV_FILE": str(tmp_path / "env.sh")}


def test_session_start_exports_transcript_path_to_env_file(tmp_path):
    root = make_root(tmp_path / "plugin", {"a.md": "규칙"})
    output = run_hook(root, transcript_env(tmp_path), payload=transcript_payload())
    assert env_lines(tmp_path) == ['export HAN_TRANSCRIPT="C:/Users/example/.claude/projects/p/s1.jsonl"']
    assert "systemMessage" not in output


def test_transcript_export_is_not_repeated_for_same_path(tmp_path):
    root = make_root(tmp_path / "plugin", {"a.md": "규칙"})
    run_hook(root, transcript_env(tmp_path), payload=transcript_payload())
    run_hook(root, transcript_env(tmp_path), payload=transcript_payload())
    assert len(env_lines(tmp_path)) == 1


def test_transcript_export_appends_for_different_path_even_if_env_already_set(tmp_path):
    root = make_root(tmp_path / "plugin", {"a.md": "규칙"})
    env = {**transcript_env(tmp_path), "HAN_TRANSCRIPT": "C:/old.jsonl"}
    run_hook(root, env, payload=transcript_payload("C:/old.jsonl"))
    run_hook(root, env, payload=transcript_payload("C:/new.jsonl"))
    assert env_lines(tmp_path) == ['export HAN_TRANSCRIPT="C:/old.jsonl"', 'export HAN_TRANSCRIPT="C:/new.jsonl"']


@pytest.mark.parametrize("payload", [{}, {"transcript_path": ""}, {"transcript_path": "  "}, {"transcript_path": 5}])
def test_transcript_export_is_skipped_without_usable_path(tmp_path, payload):
    root = make_root(tmp_path / "plugin", {"a.md": "규칙"})
    run_hook(root, transcript_env(tmp_path), payload=payload)
    assert env_lines(tmp_path) == []


def test_transcript_export_is_skipped_without_env_file(tmp_path):
    root = make_root(tmp_path / "plugin", {"a.md": "규칙"})
    output = run_hook(root, payload=transcript_payload())
    assert "systemMessage" not in output
    assert env_lines(tmp_path) == []


def test_transcript_export_still_happens_when_rules_are_disabled(tmp_path):
    root = make_root(tmp_path / "plugin", {"a.md": "규칙"})
    output = run_hook(root, {**transcript_env(tmp_path), "HAN_RULES_DISABLE": "1"}, payload=transcript_payload())
    assert output == {}
    assert len(env_lines(tmp_path)) == 1


def test_transcript_export_still_happens_when_inject_is_off(tmp_path):
    root = make_root(tmp_path / "plugin", {"a.md": "규칙"})
    run_hook(root, transcript_env(tmp_path), payload=transcript_payload(), inject=None)
    assert len(env_lines(tmp_path)) == 1


def test_transcript_export_is_skipped_for_codex(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_ENV_FILE", str(tmp_path / "env.sh"))
    session_rules.decide(transcript_payload(), "codex")
    assert env_lines(tmp_path) == []


def test_subagent_start_does_not_export_transcript_path(tmp_path):
    root = make_root(tmp_path / "plugin", {"a.md": "규칙"})
    run_hook(root, transcript_env(tmp_path), payload={**SUBAGENT_START, "transcript_path": "C:/a.jsonl"})
    assert env_lines(tmp_path) == []


def test_transcript_export_write_failure_warns_and_keeps_rules(tmp_path):
    root = make_root(tmp_path / "plugin", {"a.md": "규칙"})
    (tmp_path / "env.sh").mkdir()
    output = run_hook(root, transcript_env(tmp_path), payload=transcript_payload())
    assert "HAN_TRANSCRIPT" in output["systemMessage"]
    assert "CLAUDE_ENV_FILE" in output["systemMessage"]
    assert context_of(output).endswith("규칙")


def test_hooks_json_registers_session_start_for_every_source():
    hooks = json.loads((HOOKS_DIR / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    group = hooks["SessionStart"][0]
    assert set(group["matcher"].split("|")) >= {"startup", "resume", "clear", "compact"}
    handler = group["hooks"][0]
    assert handler["command"] == "python"
    assert handler["args"] == ["${CLAUDE_PLUGIN_ROOT}/hooks/session_rules.py"]
    assert "PreToolUse" in hooks


def test_hooks_json_registers_subagent_start_for_every_agent_type():
    group = json.loads((HOOKS_DIR / "hooks.json").read_text(encoding="utf-8"))["hooks"]["SubagentStart"][0]
    assert "matcher" not in group
    handler = group["hooks"][0]
    assert handler["command"] == "python"
    assert handler["args"] == ["${CLAUDE_PLUGIN_ROOT}/hooks/session_rules.py"]


def test_plugin_json_declares_inject_rules_user_config():
    manifest = json.loads((HOOKS_DIR.parent / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    option = manifest["userConfig"]["inject_rules"]
    assert {"type", "title", "description"} <= set(option)
    assert option["type"] == "string"
    assert not option.get("sensitive")


def test_plugin_and_marketplace_versions_match():
    manifest = json.loads((HOOKS_DIR.parent / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    market = json.loads((HOOKS_DIR.parents[2] / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    assert market["plugins"][0]["version"] == manifest["version"]


def test_plugin_json_declares_adapters_dir_user_config():
    manifest = json.loads((HOOKS_DIR.parent / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    option = manifest["userConfig"]["adapters_dir"]
    assert {"type", "title", "description"} <= set(option)
    assert option["type"] == "string"
    assert not option.get("sensitive")
