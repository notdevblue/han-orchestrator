import json

import pytest

import session_title


@pytest.fixture(autouse=True)
def done_dir(tmp_path, monkeypatch):
    folder = tmp_path / "done"
    monkeypatch.setattr(session_title, "DONE_DIR", str(folder))
    monkeypatch.delenv(session_title.DISABLE_ENV, raising=False)
    return folder


def payload(prompt, session="s-1", transcript=""):
    return {"hook_event_name": "UserPromptSubmit", "session_id": session, "prompt": prompt, "transcript_path": transcript}


def title(output):
    return output.get("hookSpecificOutput", {}).get("sessionTitle", "")


def write_transcript(path, *entries):
    path.write_text("\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries) + "\n", encoding="utf-8")
    return str(path)


def user_line(text):
    return {"type": "user", "promptId": "p", "message": {"role": "user", "content": text}}


def test_first_prompt_becomes_title():
    assert title(session_title.decide(payload("  펫 가챠 서버 이관 계획 세워줘\n자세한 건 아래"))) == "펫 가챠 서버 이관 계획 세워줘"


def test_mission_command_is_shown_as_mission():
    assert title(session_title.decide(payload("/han:mission 던전 보상 구현"))) == "미션: 던전 보상 구현"


def test_long_prompt_is_cut_to_limit():
    result = title(session_title.decide(payload("가" * 100)))
    assert len(result) == session_title.TITLE_LIMIT and result.endswith("…")


def test_only_first_prompt_of_session_names_it():
    assert title(session_title.decide(payload("첫 작업")))
    assert session_title.decide(payload("ㅇㅇ ㄱㄱ")) == {}


def test_session_started_with_a_name_keeps_it():
    assert session_title.decide({**payload("/han:mission 작업"), "session_title": "slot1: 작업"}) == {}


def test_named_session_keeps_its_name(tmp_path):
    transcript = write_transcript(tmp_path / "t.jsonl", {"type": "custom-title", "customTitle": "slot1: 작업"})
    assert session_title.decide(payload("/han:mission 작업", transcript=transcript)) == {}


def test_resumed_session_with_earlier_prompts_keeps_its_name(tmp_path):
    transcript = write_transcript(tmp_path / "t.jsonl", user_line("예전 작업"))
    assert session_title.decide(payload("이어서 해줘", transcript=transcript)) == {}


def test_current_prompt_already_in_transcript_still_names(tmp_path):
    transcript = write_transcript(tmp_path / "t.jsonl", {"type": "attachment"}, user_line("새 작업"))
    assert title(session_title.decide(payload("새 작업", transcript=transcript))) == "새 작업"


def test_disable_env_turns_it_off(monkeypatch):
    monkeypatch.setenv(session_title.DISABLE_ENV, "1")
    assert session_title.decide(payload("작업")) == {}


def test_blank_prompt_or_odd_session_id_gives_nothing():
    assert session_title.decide(payload("   \n ")) == {}
    assert session_title.decide(payload("작업", session="../x")) == {}
