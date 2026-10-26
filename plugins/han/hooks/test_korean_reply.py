import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent
sys.path.insert(0, str(HOOKS_DIR))

import korean_reply  # noqa: E402

CODEX_SCRIPT = HOOKS_DIR.parent / "codex" / "korean_reply.py"
SCRIPT = HOOKS_DIR / "korean_reply.py"
KOREAN = "이 변경은 세션 시작 훅에서 규칙을 읽어 넣는 방식으로 동작하고 실패하면 알림만 남긴다. " * 3
ENGLISH = "This change reads the rules in the session start hook and only leaves a warning when it fails. " * 2


@pytest.fixture(autouse=True)
def korean_settings(tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.mkdir()
    (config / "settings.json").write_text(json.dumps({"language": "한국어 (Korean)"}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv(korean_reply.CONFIG_DIR_ENV, str(config))
    monkeypatch.setenv(korean_reply.PROJECT_DIR_ENV, str(tmp_path / "no-project"))
    monkeypatch.delenv(korean_reply.DISABLE_ENV, raising=False)
    monkeypatch.delenv(korean_reply.CODEX_ENABLE_ENV, raising=False)
    return config


def stop(message, **fields):
    return {"hook_event_name": "Stop", "session_id": "s-1", "stop_hook_active": False, "last_assistant_message": message, **fields}


def blocked(output):
    return output.get("decision") == "block" and bool(output.get("reason"))


def write_transcript(path, *entries):
    path.write_text("\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries) + "\n", encoding="utf-8")
    return str(path)


def assistant_line(text):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def user_line(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def test_korean_reply_passes():
    assert korean_reply.decide(stop(KOREAN)) == {}


def test_english_reply_is_blocked_with_korean_reason():
    output = korean_reply.decide(stop(ENGLISH))
    assert blocked(output)
    assert output["reason"] == korean_reply.REASON


def test_mostly_code_block_reply_passes():
    reply = "아래처럼 고친다.\n```python\n" + ENGLISH + "\n```\n"
    assert korean_reply.decide(stop(reply)) == {}


def test_unclosed_code_block_is_also_skipped():
    assert korean_reply.decide(stop("고쳤다.\n```\n" + ENGLISH)) == {}


def test_inline_code_quote_url_and_path_are_not_counted():
    english = "the quick brown fox jumps over the lazy dog and keeps running far away from here " * 2
    slashed = english.replace(" ", "/")
    backslashed = english.replace(" ", chr(92))
    reply = f"결과는 `{english}` 이고\n> {english}\n https://example.com/{slashed} C:{chr(92)}Users{chr(92)}example{chr(92)}{backslashed}"
    assert korean_reply.decide(stop(reply)) == {}


def test_dotted_identifiers_are_not_counted():
    reply = "한국어 문장이 충분히 길지는 않지만 " + " ".join(["session_rules.read_payload"] * 20)
    assert korean_reply.prose_of(reply).count("session_rules") == 0


def test_short_reply_passes_even_if_english():
    short = ("a" * (korean_reply.MIN_LETTERS - 1))
    assert korean_reply.decide(stop(short)) == {}
    assert blocked(korean_reply.decide(stop("a" * korean_reply.MIN_LETTERS)))


def test_hangul_ratio_boundary():
    letters = korean_reply.MIN_LETTERS
    hangul = int(letters * korean_reply.MIN_HANGUL_RATIO)
    at_limit = "가" * hangul + "a" * (letters - hangul)
    below_limit = "가" * (hangul - 1) + "a" * (letters - hangul + 1)
    assert hangul / letters >= korean_reply.MIN_HANGUL_RATIO
    assert korean_reply.decide(stop(at_limit)) == {}
    assert blocked(korean_reply.decide(stop(below_limit)))


def test_stop_hook_active_does_nothing():
    assert korean_reply.decide(stop(ENGLISH, stop_hook_active=True)) == {}


def test_disable_env_turns_it_off(monkeypatch):
    monkeypatch.setenv(korean_reply.DISABLE_ENV, "1")
    assert korean_reply.decide(stop(ENGLISH)) == {}


def test_other_disable_value_does_not_disable(monkeypatch):
    monkeypatch.setenv(korean_reply.DISABLE_ENV, "0")
    assert blocked(korean_reply.decide(stop(ENGLISH)))


@pytest.mark.parametrize("language", ["English", "", "일본어", None])
def test_non_korean_language_setting_is_not_checked(korean_settings, language):
    (korean_settings / "settings.json").write_text(json.dumps({"language": language}), encoding="utf-8")
    assert korean_reply.decide(stop(ENGLISH)) == {}


def test_missing_settings_is_not_checked(korean_settings):
    (korean_settings / "settings.json").unlink()
    assert korean_reply.decide(stop(ENGLISH)) == {}


@pytest.mark.parametrize("language", ["Korean", "korean", "한국어", "한국어 (Korean)"])
def test_korean_language_values_turn_it_on(korean_settings, language):
    (korean_settings / "settings.json").write_text(json.dumps({"language": language}, ensure_ascii=False), encoding="utf-8")
    assert blocked(korean_reply.decide(stop(ENGLISH)))


def test_project_setting_overrides_user_setting(tmp_path, monkeypatch):
    project = tmp_path / "project"
    (project / ".claude").mkdir(parents=True)
    (project / ".claude" / "settings.json").write_text(json.dumps({"language": "English"}), encoding="utf-8")
    monkeypatch.setenv(korean_reply.PROJECT_DIR_ENV, str(project))
    assert korean_reply.decide(stop(ENGLISH)) == {}
    (project / ".claude" / "settings.local.json").write_text(json.dumps({"language": "Korean"}), encoding="utf-8")
    assert blocked(korean_reply.decide(stop(ENGLISH)))


def test_cwd_in_payload_finds_project_settings(tmp_path, monkeypatch):
    project = tmp_path / "project"
    (project / ".claude").mkdir(parents=True)
    (project / ".claude" / "settings.json").write_text(json.dumps({"language": "English"}), encoding="utf-8")
    assert korean_reply.decide(stop(ENGLISH, cwd=str(project))) == {}


def test_reads_last_reply_from_transcript_when_message_field_is_missing(tmp_path):
    transcript = write_transcript(tmp_path / "t.jsonl", user_line("질문"), assistant_line(KOREAN), assistant_line(ENGLISH))
    payload = {"hook_event_name": "Stop", "transcript_path": transcript, "stop_hook_active": False}
    assert blocked(korean_reply.decide(payload))
    transcript = write_transcript(tmp_path / "t2.jsonl", user_line("질문"), assistant_line(ENGLISH), assistant_line(KOREAN))
    assert korean_reply.decide({**payload, "transcript_path": transcript}) == {}


def test_transcript_does_not_reach_back_to_the_previous_turn(tmp_path):
    transcript = write_transcript(tmp_path / "t.jsonl", assistant_line(ENGLISH), user_line("다음 질문"))
    assert korean_reply.decide({"hook_event_name": "Stop", "transcript_path": transcript}) == {}


def test_missing_message_and_transcript_does_nothing():
    assert korean_reply.decide({"hook_event_name": "Stop"}) == {}
    assert korean_reply.decide({"hook_event_name": "Stop", "transcript_path": "no-such-file.jsonl"}) == {}


def run_script(script, payload, env=None, raw=None):
    base = {k: v for k, v in os.environ.items() if k != korean_reply.DISABLE_ENV}
    base.update(env or {})
    stdin = raw if raw is not None else json.dumps(payload).encode("utf-8")
    result = subprocess.run([sys.executable, "-B", str(script)], input=stdin, capture_output=True, env=base, timeout=30)
    assert result.returncode == 0
    text = result.stdout.decode("ascii").strip()
    return json.loads(text) if text else {}


def test_script_prints_block_json_as_ascii(korean_settings):
    env = {korean_reply.CONFIG_DIR_ENV: str(korean_settings)}
    output = run_script(SCRIPT, stop(ENGLISH), env)
    assert output == {"decision": "block", "reason": korean_reply.REASON}
    assert run_script(SCRIPT, stop(KOREAN), env) == {}


def test_script_survives_garbage_input():
    assert run_script(SCRIPT, None, raw=b"not json") == {}
    assert run_script(SCRIPT, None, raw=b"[]") == {}


def test_codex_entry_needs_its_own_opt_in_and_ignores_claude_settings(korean_settings):
    env = {korean_reply.CONFIG_DIR_ENV: str(korean_settings)}
    assert run_script(CODEX_SCRIPT, stop(ENGLISH), env) == {}
    on = {**env, korean_reply.CODEX_ENABLE_ENV: "on"}
    assert run_script(CODEX_SCRIPT, stop(ENGLISH), on) == {"decision": "block", "reason": korean_reply.REASON}
    assert run_script(CODEX_SCRIPT, stop(KOREAN), on) == {}
    assert run_script(CODEX_SCRIPT, stop(ENGLISH, stop_hook_active=True), on) == {}
    assert run_script(CODEX_SCRIPT, stop(None), on) == {}


def commands_of(path, event):
    hooks = json.loads(path.read_text(encoding="utf-8"))["hooks"][event]
    return [(group.get("matcher"), json.dumps(hook)) for group in hooks for hook in group["hooks"]]


def test_hooks_json_registers_stop_and_memory_hooks_for_claude_only_stop_for_codex():
    claude = HOOKS_DIR / "hooks.json"
    codex = HOOKS_DIR.parent / "codex" / "hooks.json"
    assert [m for m, c in commands_of(claude, "Stop") if "hooks/korean_reply.py" in c] == [None]
    assert [m for m, c in commands_of(claude, "PreToolUse") if "hooks/memory_notice.py" in c] == ["Write|Edit"]
    assert [m for m, c in commands_of(codex, "Stop") if "codex/korean_reply.py" in c] == [None]
    assert not [c for _, c in commands_of(codex, "PreToolUse") if "memory_notice" in c]
