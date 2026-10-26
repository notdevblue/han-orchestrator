import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import distill  # noqa: E402


def user(text, ts, **flags):
    return {"type": "user", "timestamp": ts, "message": {"role": "user", "content": text}, **flags}


def assistant(content, ts="2026-10-19T00:00:00.000Z"):
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    return {"type": "assistant", "timestamp": ts, "message": {"role": "assistant", "content": content}}


def write_transcript(path, entries, raw=()):
    lines = [json.dumps(e, ensure_ascii=False) for e in entries]
    lines.extend(raw)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def env(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("HAN_DISTILL_LOG", str(tmp_path / "log.tsv"))
    monkeypatch.delenv("HAN_TRANSCRIPT", raising=False)

    class Env:
        root = tmp_path
        log = tmp_path / "log.tsv"
        out = tmp_path / "out.md"

        def run(self, *args):
            capsys.readouterr()
            code = distill.main(list(args))
            captured = capsys.readouterr()
            return code, captured.out.rstrip("\n"), captured.err.rstrip("\n")

        def ok(self, *args):
            code, out, err = self.run(*args)
            assert code == 0, err
            return out

        def fail(self, *args):
            code, out, err = self.run(*args)
            assert code == 1 and out == "" and "\n" not in err, (code, out, err)
            return err

        def extract(self, transcript, *extra):
            self.ok("extract", "--transcript", str(transcript), "--out", str(self.out), *extra)
            return self.out.read_text(encoding="utf-8")

    return Env()


def test_string_and_list_content_become_messages(env):
    path = write_transcript(
        env.root / "s1.jsonl",
        [
            user("문자열 메시지", "2026-10-19T01:00:00.000Z"),
            user(
                [
                    {"type": "image", "source": {}},
                    {"type": "text", "text": "이미지 설명"},
                    {"type": "tool_result", "content": "무시"},
                    {"type": "text", "text": "이어서"},
                ],
                "2026-10-19T02:00:00.000Z",
            ),
            user([{"type": "tool_result", "content": "결과만"}], "2026-10-19T03:00:00.000Z"),
        ],
    )
    text = env.extract(path)
    assert "## s1" in text
    assert "### 1 · 2026-10-19T01:00:00.000Z\n사용자: 문자열 메시지" in text
    assert "### 2 · 2026-10-19T02:00:00.000Z\n사용자: 이미지 설명\n이어서" in text
    assert "결과만" not in text and "무시" not in text and "### 3" not in text


@pytest.mark.parametrize("flag", ["isMeta", "isCompactSummary", "isSidechain", "isVisibleInTranscriptOnly"])
def test_flagged_user_entries_are_dropped(env, flag):
    path = write_transcript(
        env.root / "s1.jsonl",
        [user("버릴 것", "2026-10-19T01:00:00.000Z", **{flag: True}), user("남길 것", "2026-10-19T02:00:00.000Z")],
    )
    text = env.extract(path)
    assert "버릴 것" not in text and "사용자: 남길 것" in text


def test_non_user_types_and_broken_lines_are_skipped(env):
    path = write_transcript(
        env.root / "s1.jsonl",
        [{"type": "system", "message": {"content": "시스템"}}, user("진짜", "2026-10-19T01:00:00.000Z")],
        raw=["{깨진 줄", "[1, 2]"],
    )
    text = env.extract(path)
    assert "시스템" not in text and "사용자: 진짜" in text


def test_system_reminder_blocks_are_stripped(env):
    path = write_transcript(
        env.root / "s1.jsonl",
        [
            user("앞<system-reminder>\n숨김\n</system-reminder>뒤", "2026-10-19T01:00:00.000Z"),
            user("<system-reminder>전부 숨김</system-reminder>", "2026-10-19T02:00:00.000Z"),
        ],
    )
    text = env.extract(path)
    assert "사용자: 앞뒤" in text and "숨김" not in text and "### 2" not in text


@pytest.mark.parametrize(
    "body",
    [
        "<task-notification>끝남</task-notification>",
        "<local-command-stdout>out</local-command-stdout>",
        "<local-command-stderr>err</local-command-stderr>",
        "<local-command-caveat>c</local-command-caveat>",
        "[Request interrupted by user for tool use]",
        "   ",
    ],
)
def test_discard_prefixes(env, body):
    path = write_transcript(env.root / "s1.jsonl", [user(body, "2026-10-19T01:00:00.000Z")])
    assert "###" not in env.extract(path)


def test_slash_command_kept_only_with_args(env):
    path = write_transcript(
        env.root / "s1.jsonl",
        [
            user("<command-message>x</command-message>\n<command-name>/x</command-name>\n<command-args>Y 인자</command-args>", "2026-10-19T01:00:00.000Z"),
            user("<command-message>x</command-message>\n<command-name>/x</command-name>\n<command-args></command-args>", "2026-10-19T02:00:00.000Z"),
            user("<command-name>/clear</command-name>", "2026-10-19T03:00:00.000Z"),
        ],
    )
    text = env.extract(path)
    assert "사용자: /x Y 인자" in text and "### 2" not in text


def test_previous_assistant_text_tail_folds_and_resets(env):
    long = "가" * 400
    path = write_transcript(
        env.root / "s1.jsonl",
        [
            assistant(f"처음\n\n말  {long}"),
            assistant([{"type": "tool_use", "id": "t", "name": "Bash", "input": {}}]),
            user("첫", "2026-10-19T01:00:00.000Z"),
            user("둘", "2026-10-19T02:00:00.000Z"),
            assistant([{"type": "text", "text": "a\nb"}, {"type": "text", "text": "c"}]),
            user("셋", "2026-10-19T03:00:00.000Z"),
        ],
    )
    lines = env.extract(path).splitlines()
    first = lines.index("### 1 · 2026-10-19T01:00:00.000Z")
    assert lines[first + 1] == "앞 답: > " + "가" * 300
    assert lines[first + 2] == "사용자: 첫"
    second = lines.index("### 2 · 2026-10-19T02:00:00.000Z")
    assert lines[second + 1] == "사용자: 둘"
    third = lines.index("### 3 · 2026-10-19T03:00:00.000Z")
    assert lines[third + 1] == "앞 답: > a b c"


def test_tool_only_assistant_keeps_previous_text(env):
    path = write_transcript(
        env.root / "s1.jsonl",
        [
            assistant("텍스트 답"),
            assistant([{"type": "tool_use", "id": "t", "name": "Bash", "input": {}}]),
            user("질문", "2026-10-19T01:00:00.000Z"),
        ],
    )
    assert "앞 답: > 텍스트 답" in env.extract(path)


def test_numbering_spans_transcripts_and_stdout_summary(env):
    a = write_transcript(env.root / "a.jsonl", [user("a1", "2026-10-19T01:00:00.000Z"), user("a2", "2026-10-19T02:00:00.000Z")])
    b = write_transcript(env.root / "b.jsonl", [user("b1", "2026-10-19T03:00:00.000Z")])
    out = env.ok("extract", "--transcript", str(a), "--transcript", str(b), "--out", str(env.out))
    assert out.splitlines() == [
        "a: 메시지 2개, until=2026-10-19T02:00:00.000Z",
        "b: 메시지 1개, until=2026-10-19T03:00:00.000Z",
        str(env.out),
    ]
    text = env.out.read_text(encoding="utf-8")
    assert "## a" in text and "## b" in text and "### 3 · 2026-10-19T03:00:00.000Z" in text


def test_transcript_comes_from_environment(env, monkeypatch):
    path = write_transcript(env.root / "envs.jsonl", [user("환경", "2026-10-19T01:00:00.000Z")])
    monkeypatch.setenv("HAN_TRANSCRIPT", str(path))
    env.ok("extract", "--out", str(env.out))
    assert "## envs" in env.out.read_text(encoding="utf-8")
    monkeypatch.delenv("HAN_TRANSCRIPT")
    assert "--transcript 나 HAN_TRANSCRIPT 필요" in env.fail("extract", "--out", str(env.out))


def test_missing_transcript_fails(env):
    assert "대화 기록 없음" in env.fail("extract", "--transcript", str(env.root / "none.jsonl"), "--out", str(env.out))


def test_watermark_skips_processed_and_all_overrides(env):
    path = write_transcript(
        env.root / "s1.jsonl",
        [
            user("옛 것", "2026-10-19T01:00:00.000Z"),
            user("경계", "2026-10-19T02:00:00.000Z"),
            user("새 것", "2026-10-19T03:00:00.000Z"),
        ],
    )
    env.ok("record", "--session", "s1", "--until", "2026-10-19T02:00:00.000Z", "--verdict", "확인")
    env.ok("record", "--session", "other", "--until", "2026-10-19T09:00:00.000Z", "--verdict", "확인")
    text = env.extract(path)
    assert "옛 것" not in text and "경계" not in text and "사용자: 새 것" in text and "### 1 · 2026-10-19T03" in text
    everything = env.extract(path, "--all")
    assert "옛 것" in everything and "경계" in everything and "새 것" in everything


def test_watermark_uses_largest_until(env):
    path = write_transcript(env.root / "s1.jsonl", [user("m", "2026-10-19T02:00:00.000Z"), user("n", "2026-10-19T05:00:00.000Z")])
    env.ok("record", "--session", "s1", "--until", "2026-10-19T05:00:00.000Z", "--verdict", "확인")
    env.ok("record", "--session", "s1", "--until", "2026-10-19T01:00:00.000Z", "--verdict", "확인")
    assert "###" not in env.extract(path)


def test_empty_extract_reports_dash(env):
    path = write_transcript(env.root / "s1.jsonl", [assistant("답만")])
    out = env.ok("extract", "--transcript", str(path), "--out", str(env.out))
    assert out.splitlines()[0] == "s1: 메시지 0개, until=-"


def test_record_writes_header_and_row(env):
    env.ok(
        "record", "--session", "s1", "--until", "2026-10-19T02:00:00.000Z",
        "--verdict", "반영", "--dest", "guide", "--summary", "탭\t줄\n바꿈",
    )
    env.ok("record", "--session", "s1", "--until", "2026-10-19T03:00:00.000Z", "--verdict", "확인")
    rows = [line.split("\t") for line in env.log.read_text(encoding="utf-8").splitlines()]
    assert rows[0] == list(distill.LOG_HEADER)
    assert rows[1][1:] == ["s1", "2026-10-19T02:00:00.000Z", "반영", "guide", "탭 줄 바꿈"]
    assert rows[2][1:] == ["s1", "2026-10-19T03:00:00.000Z", "확인", "-", "-"]
    assert all(len(row) == 6 for row in rows)


def test_record_creates_missing_folder(env, monkeypatch):
    nested = env.root / "a" / "b" / "log.tsv"
    monkeypatch.setenv("HAN_DISTILL_LOG", str(nested))
    env.ok("record", "--session", "s1", "--until", "t", "--verdict", "확인")
    assert nested.is_file()


@pytest.mark.parametrize(
    "extra",
    [
        ["--verdict", "반영", "--dest", "guide"],
        ["--verdict", "반영", "--summary", "요약"],
        ["--verdict", "기각"],
        ["--verdict", "기각", "--summary", "  "],
        ["--verdict", "없는판정"],
        ["--verdict", "반영", "--dest", "elsewhere", "--summary", "요약"],
        [],
    ],
)
def test_record_rejects_bad_arguments(env, extra):
    env.fail("record", "--session", "s1", "--until", "t", *extra)
    assert not env.log.exists()


def test_record_rejects_bad_log_header(env):
    env.log.write_text("엉뚱\t헤더\n", encoding="utf-8")
    path = write_transcript(env.root / "s1.jsonl", [user("m", "2026-10-19T01:00:00.000Z")])
    assert "헤더가 다름" in env.fail("extract", "--transcript", str(path), "--out", str(env.out))


def codex_message(role, text, ts="2026-10-20T01:00:00.000Z", content_type="text"):
    return {
        "type": "response_item",
        "timestamp": ts,
        "payload": {"type": "message", "role": role, "content": [{"type": content_type, "text": text}]},
    }


@pytest.mark.parametrize("content_type", ["text", "input_text", "output_text"])
def test_codex_reads_canonical_messages_without_duplicate_events(env, content_type):
    path = write_transcript(env.root / "codex.jsonl", [
        {"type": "session_meta", "payload": {"id": "codex-session"}},
        codex_message("developer", "시스템 지시"),
        codex_message("assistant", "앞\n답", content_type=content_type),
        {"type": "response_item", "payload": {"type": "reasoning", "summary": "숨김"}},
        {"type": "response_item", "payload": {"type": "function_call_output", "output": "도구"}},
        {"type": "event_msg", "payload": {"type": "user_message", "message": "교정"}},
        codex_message("user", "교정", content_type=content_type),
        {"type": "compacted", "replacement_history": [codex_message("user", "요약")]},
    ])
    text = env.extract(path)
    assert "## codex-session" in text
    assert "앞 답: > 앞 답" in text
    assert text.count("사용자: 교정") == 1
    assert all(value not in text for value in ("시스템 지시", "숨김", "도구", "요약"))


@pytest.mark.parametrize("prefix", distill.CODEX_CONTEXT_PREFIXES)
@pytest.mark.parametrize("as_list", [True, False])
def test_codex_injected_context_is_not_feedback(env, prefix, as_list):
    entry = codex_message("user", prefix + "\n주입된 내용")
    if not as_list:
        entry["payload"]["content"] = prefix + "\n주입된 내용"
    path = write_transcript(env.root / "codex.jsonl", [entry, codex_message("user", "사용자 교정")])
    text = env.extract(path)
    assert "주입된 내용" not in text
    assert "사용자: 사용자 교정" in text
    assert "### 2" not in text


def test_codex_context_part_does_not_drop_feedback_in_other_parts(env):
    entry = codex_message("user", "# AGENTS.md instructions\n규칙")
    entry["payload"]["content"].append({"type": "input_text", "text": "실제 피드백"})
    path = write_transcript(env.root / "codex.jsonl", [entry])
    text = env.extract(path)
    assert "사용자: 실제 피드백" in text and "규칙" not in text


def test_codex_watermark_uses_session_metadata_across_rollout_names(env):
    entries = [
        {"type": "session_meta", "payload": {"id": "same-session"}},
        codex_message("user", "처리한 교정", "2026-10-20T01:00:00.000Z"),
        codex_message("user", "새 교정", "2026-10-20T02:00:00.000Z"),
    ]
    first = write_transcript(env.root / "rollout-first.jsonl", entries)
    second = write_transcript(env.root / "rollout-second.jsonl", entries)
    env.ok("record", "--session", "same-session", "--until", "2026-10-20T01:00:00.000Z", "--verdict", "확인")
    for path in (first, second):
        text = env.extract(path)
        assert "처리한 교정" not in text and "사용자: 새 교정" in text
        assert "처리한 교정" in env.extract(path, "--all")


def test_claude_and_codex_transcripts_can_be_extracted_together(env):
    claude = write_transcript(env.root / "claude.jsonl", [user("Claude 교정", "2026-10-19T01:00:00.000Z")])
    codex = write_transcript(env.root / "codex.jsonl", [codex_message("user", "Codex 교정")])
    env.ok("extract", "--transcript", str(claude), "--transcript", str(codex), "--out", str(env.out))
    text = env.out.read_text(encoding="utf-8")
    assert "사용자: Claude 교정" in text and "사용자: Codex 교정" in text and "### 2" in text


def test_runtime_log_defaults_and_override(env, monkeypatch):
    monkeypatch.delenv("HAN_DISTILL_LOG")
    monkeypatch.setenv("CODEX_HOME", str(env.root / "codex-home"))
    assert Path(distill.log_path("codex")) == env.root / "codex-home/han/distill/log.tsv"
    assert Path(distill.log_path()) == Path.home() / ".claude/han/distill/log.tsv"
    monkeypatch.delenv("CODEX_HOME")
    assert Path(distill.log_path("codex")) == Path.home() / ".codex/han/distill/log.tsv"
    monkeypatch.setenv("HAN_DISTILL_LOG", str(env.log))
    assert distill.log_path("codex") == distill.log_path() == str(env.log)


def test_codex_requires_explicit_transcript_instead_of_inherited_claude_path(env, monkeypatch, capsys):
    path = write_transcript(env.root / "claude.jsonl", [user("다른 세션", "2026-10-19T01:00:00.000Z")])
    monkeypatch.setenv("HAN_TRANSCRIPT", str(path))
    capsys.readouterr()
    assert distill.main(["extract", "--out", str(env.out)], runtime="codex") == 1
    assert "--transcript 필요" in capsys.readouterr().err
    assert not env.out.exists()


def test_codex_entrypoint_records_and_extracts_from_another_cwd(env, monkeypatch):
    monkeypatch.delenv("HAN_DISTILL_LOG")
    monkeypatch.setenv("CODEX_HOME", str(env.root / "codex-home"))
    script = Path(__file__).resolve().parents[3] / "codex/skills/han-distill/scripts/distill.py"
    path = write_transcript(env.root / "rollout.jsonl", [
        {"type": "session_meta", "payload": {"id": "codex-session"}},
        codex_message("user", "교정"),
    ])
    commands = [
        ["extract", "--transcript", str(path), "--out", str(env.out)],
        ["record", "--session", "codex-session", "--until", "2026-10-20T01:00:00.000Z", "--verdict", "확인"],
        ["extract", "--transcript", str(path), "--out", str(env.out)],
    ]
    outputs = []
    for arguments in commands:
        result = subprocess.run([sys.executable, str(script), *arguments], cwd=env.root, capture_output=True, timeout=15)
        assert result.returncode == 0, result.stderr.decode("utf-8")
        outputs.append(result.stdout.decode("utf-8"))
    assert "메시지 1개" in outputs[0] and "메시지 0개" in outputs[-1]
    assert (env.root / "codex-home/han/distill/log.tsv").is_file()
