import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mission_state  # noqa: E402

SCRIPT = Path(__file__).with_name("mission_state.py")
ADAPTER = {
    "schema": 1,
    "paths": {"stateDir": "state"},
    "stages": [{"id": "content"}, {"id": "poc"}, {"id": "impl"}, {"id": "review"}],
    "verify": {"grades": ["editmode-pass", "pytest-pass"]},
}
BRIEF = ["--goal", "목표", "--files", "a.cs", "--done", "테스트 1개", "--verify", "pytest", "--timebox", "60", "--forbid", "submit"]


class Mission:
    def __init__(self, root, capsys):
        self.root = root
        self.capsys = capsys
        self.folder = root / "state" / "m1"
        adapter_dir = root / ".claude" / "orchestrator"
        adapter_dir.mkdir(parents=True)
        (adapter_dir / "adapter.json").write_text(json.dumps(ADAPTER), encoding="utf-8")
        (root / "state").mkdir()
        (root / "state" / "ACTIVE").write_text(
            json.dumps({"id": "m1", "session": "s1", "openedAt": "2026-10-19T09:00:00+09:00"}), encoding="utf-8"
        )

    def run(self, *args):
        self.capsys.readouterr()
        code = mission_state.main(["--project", str(self.root), *args])
        captured = self.capsys.readouterr()
        return code, captured.out.rstrip("\n"), captured.err.rstrip("\n")

    def ok(self, *args):
        code, out, err = self.run(*args)
        assert code == 0, err
        return out

    def fail(self, *args):
        code, out, err = self.run(*args)
        assert code == 1 and out == "" and "\n" not in err, (code, out, err)
        return err

    def question_record(self, name="ask-1.md", statuses=("pending",), approval="none", **changes):
        record = {
            "schema": 1,
            "id": name.removesuffix(".md"),
            "revision": 1,
            "title": "질문",
            "intro": "",
            "items": [{"id": str(index), "title": f"선택 {index}", "body": "", "status": status,
                       "answer": "진행" if status == "answered" else ""}
                      for index, status in enumerate(statuses, start=1)],
            "body": "",
            "approval": {"status": approval, "reason": "전체 승인 필요" if approval == "pending" else ""},
            "answers": [],
        }
        record.update(changes)
        return record

    def publish_question(self, *args, **kwargs):
        return mission_state.question_records.publish_document(self.folder, self.question_record(*args, **kwargs))

    def json_input(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def add_unit(self, unit_id="M1", *extra):
        return self.ok("unit", "add", unit_id, *BRIEF, *extra)

    def read(self, name):
        return (self.folder / name).read_text(encoding="utf-8")

    def table(self, name):
        return [line.split("\t") for line in self.read(name).splitlines()]

    def backdate(self, unit_id, minutes):
        started = (datetime.now().astimezone() - timedelta(minutes=minutes)).isoformat(timespec="seconds")
        rows = self.table("units.tsv")
        for row in rows[1:]:
            if row[0] == unit_id:
                row[10] = started
        (self.folder / "units.tsv").write_text("\n".join("\t".join(r) for r in rows) + "\n", encoding="utf-8")


@pytest.fixture
def mission(tmp_path, capsys, monkeypatch):
    for name in ("CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_OPTION_ADAPTERS_DIR", "CLAUDE_CONFIG_DIR", "CODEX_HOME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HAN_ADAPTERS_DIR", str(tmp_path / "no-registry"))
    return Mission(tmp_path / "project", capsys)


@pytest.fixture
def opened(mission):
    mission.ok("init", "--goal", "서버 이관")
    return mission


def test_init_requires_guard_to_be_on(mission):
    (mission.root / "state" / "ACTIVE").unlink()
    assert "han-mission open m1" in mission.fail("--mission", "m1", "init")
    (mission.root / "state" / "ACTIVE").write_text(json.dumps({"id": "m1", "session": ""}), encoding="utf-8")
    assert "가드 안 켜짐" in mission.fail("init")
    (mission.root / "state" / "ACTIVE").write_text(json.dumps({"id": "other", "session": "s1"}), encoding="utf-8")
    assert "가드 안 켜짐" in mission.fail("--mission", "m1", "init")
    assert not mission.folder.exists()


def test_commands_need_an_id_from_active_or_flag(mission):
    (mission.root / "state" / "ACTIVE").unlink()
    assert "미션 id 없음" in mission.fail("status")


def test_init_copies_templates_fills_id_state_and_goal(mission):
    out = mission.ok("init", "--goal", "서버 이관")
    assert "만듦: mission.md units.tsv ledger.tsv decisions.tsv report.md" in out
    text = mission.read("mission.md")
    assert "# 미션 m1" in text and "<id>" not in text
    assert "상태: 진행" in text and "상태: 준비" not in text
    assert "## 목표\n\n서버 이관\n\n## " in text
    assert "<id>" not in mission.read("report.md")
    assert mission.read("units.tsv").startswith("id\tgoal\tfiles\tdone\tverify\ttimebox\tforbid\thuman\tafter\tunit\tstarted\tstatus")


def test_init_without_tier_warns_but_opens(mission):
    out = mission.ok("init", "--goal", "서버 이관")
    assert "--tier 없음" in out
    assert "티어:" not in mission.read("mission.md")


def test_init_records_tier(mission):
    out = mission.ok("init", "--tier", "m", "--goal", "서버 이관")
    assert "경고" not in out
    assert "상태: 진행\n티어: M\n현재 단계:" in mission.read("mission.md")


def test_init_refuses_small_tier_unless_forced(mission):
    err = mission.fail("init", "--tier", "S")
    assert "S 티어는 미션으로 열지 않음" in err and "han-mission close m1" in err
    assert not mission.folder.exists()
    assert "S·M·L" in mission.fail("init", "--tier", "XL")
    out = mission.ok("init", "--tier", "S", "--force-tier")
    assert "--force-tier" in out
    assert "티어: S" in mission.read("mission.md")


def test_init_does_not_overwrite_existing_files(opened):
    (opened.folder / "mission.md").write_text("손으로 고친 것\n", encoding="utf-8")
    (opened.folder / "ledger.tsv").unlink()
    out = opened.ok("init", "--goal", "다른 목표")
    assert "만듦: ledger.tsv" in out and "건너뜀: mission.md" in out
    assert opened.read("mission.md") == "손으로 고친 것\n"


def test_mission_set_changes_only_header_values(opened):
    out = opened.ok("mission", "set", "--stage", "impl", "--confirm", "A=승인", "--confirm", "B=대기", "--state", "중단")
    assert out == "상태 중단 | 현재 단계 impl | 확인 A 승인, B 대기, C 대기"
    text = opened.read("mission.md")
    assert "현재 단계: impl\n" in text and "확인 A: 승인\n" in text and "상태: 중단\n" in text
    assert "## 목표\n\n서버 이관\n" in text


def test_mission_set_adds_missing_confirm_line_after_last_confirm(opened):
    out = opened.ok("mission", "set", "--confirm", "A1=승인")
    assert out.endswith("확인 A 대기, B 대기, C 대기, A1 승인")
    lines = opened.read("mission.md").splitlines()
    assert lines[lines.index("확인 C: 대기") + 1] == "확인 A1: 승인"


def test_mission_set_rejects_bad_values(opened):
    before = opened.read("mission.md")
    assert "허용 값" in opened.fail("mission", "set", "--stage", "nope")
    assert "K=V" in opened.fail("mission", "set", "--confirm", "A승인")
    assert "바꿀 값 없음" in opened.fail("mission", "set")
    assert "invalid choice" in opened.fail("mission", "set", "--state", "준비")
    assert opened.read("mission.md") == before


def make_poc(mission, name="x"):
    poc = mission.root / "Wiki" / "design" / "prototypes" / name / "index.html"
    poc.parent.mkdir(parents=True)
    poc.write_text("<html></html>", encoding="utf-8")
    return poc


def test_mission_set_poc_stores_root_relative_path_for_relative_and_absolute_input(opened):
    poc = make_poc(opened)
    assert opened.ok("mission", "set", "--poc", "Wiki\\design\\prototypes\\x\\index.html").startswith("상태 진행")
    assert "PoC: Wiki/design/prototypes/x/index.html\n" in opened.read("mission.md")
    opened.ok("mission", "set", "--poc", "Wiki/design/../design/prototypes/x/index.html")
    assert opened.read("mission.md").count("PoC: Wiki/design/prototypes/x/index.html\n") == 1
    other = make_poc(opened, "y")
    opened.ok("mission", "set", "--poc", str(other))
    assert "PoC: Wiki/design/prototypes/y/index.html\n" in opened.read("mission.md")
    assert str(poc) not in opened.read("mission.md")


def test_mission_set_poc_rejects_missing_file_and_paths_outside_workspace(opened):
    make_poc(opened)
    before = opened.read("mission.md")
    assert "파일 없음" in opened.fail("mission", "set", "--poc", "Wiki/design/prototypes/none/index.html")
    assert "파일 없음" in opened.fail("mission", "set", "--poc", "Wiki")
    outside = opened.root.parent / "outside.html"
    outside.write_text("x", encoding="utf-8")
    assert "밖" in opened.fail("mission", "set", "--poc", str(outside))
    assert "밖" in opened.fail("mission", "set", "--poc", "../outside.html")
    assert "비어 있음" in opened.fail("mission", "set", "--poc", " ")
    assert opened.read("mission.md") == before


def test_mission_set_poc_inserts_after_last_confirm_or_stage_line(opened):
    make_poc(opened)
    opened.ok("mission", "set", "--poc", "Wiki/design/prototypes/x/index.html")
    lines = opened.read("mission.md").splitlines()
    assert lines[lines.index("확인 C: 대기") + 1] == "PoC: Wiki/design/prototypes/x/index.html"
    stripped = [line for line in lines if not line.startswith(("PoC:", "확인 "))]
    (opened.folder / "mission.md").write_text("\n".join(stripped) + "\n", encoding="utf-8")
    opened.ok("mission", "set", "--poc", "Wiki/design/prototypes/x/index.html")
    lines = opened.read("mission.md").splitlines()
    assert lines[lines.index("현재 단계: content") + 1] == "PoC: Wiki/design/prototypes/x/index.html"


def test_mission_set_poc_works_with_other_values_and_new_confirm_stays_before_it(opened):
    make_poc(opened)
    opened.ok("mission", "set", "--stage", "impl", "--poc", "Wiki/design/prototypes/x/index.html", "--confirm", "A1=승인")
    lines = opened.read("mission.md").splitlines()
    assert "현재 단계: impl" in lines and "확인 A1: 승인" in lines
    assert lines.index("확인 A1: 승인") < lines.index("PoC: Wiki/design/prototypes/x/index.html")
    assert mission_state.header_fields(lines)["PoC"] == "Wiki/design/prototypes/x/index.html"


def test_unit_add_writes_todo_row_with_defaults(opened):
    assert opened.add_unit("M1") == "M1 추가 (todo, timebox 60분, after -)"
    assert opened.table("units.tsv")[1] == ["M1", "목표", "a.cs", "테스트 1개", "pytest", "60", "submit", "-", "-", "?", "", "todo"]


def test_unit_add_flattens_tabs_and_newlines(opened):
    opened.ok("unit", "add", "M1", "--goal", "한 줄\n두 줄\t탭", *BRIEF[2:])
    row = opened.table("units.tsv")[1]
    assert len(row) == 12 and row[1] == "한 줄 두 줄 탭"


def test_unit_add_rejects_empty_brief_fields(opened):
    err = opened.fail("unit", "add", "M1", "--goal", "목표", "--timebox", "60")
    assert err == "오류: 브리프 빈 칸: files, done, verify, forbid, 마일스톤을 다시 쪼갤 것"
    assert len(opened.table("units.tsv")) == 1


def test_unit_add_rejects_duplicate_bad_timebox_and_unknown_after(opened):
    opened.add_unit("M1")
    assert "이미 있음" in opened.fail("unit", "add", "M1", *BRIEF)
    for bad in ("0", "-5", "1.5", "abc"):
        assert "양의 정수" in opened.fail("unit", "add", "M2", *BRIEF[:8], "--timebox", bad, *BRIEF[10:])
    assert "M9" in opened.fail("unit", "add", "M2", *BRIEF, "--after", "M9")
    opened.ok("unit", "add", "M2", *BRIEF, "--after", "M1, M1", "--human", "화면 확인")
    assert opened.table("units.tsv")[2][7:9] == ["화면 확인", "M1"]


def test_usage_errors_are_one_line_with_exit_code_1(opened):
    assert "required" in opened.fail("ledger", "record", "M1", "--status", "pass")
    assert "required" in opened.fail("unit")
    assert "invalid choice" in opened.fail("nope")
    assert "invalid choice" in opened.fail("unit", "set", "M1", "--status", "bogus")
    assert opened.run("-h")[0] == 0


def test_unit_set_doing_needs_finished_after_and_stamps_started(opened):
    opened.add_unit("M1")
    opened.add_unit("M2", "--after", "M1")
    assert "M1(todo)" in opened.fail("unit", "set", "M2", "--status", "doing")
    opened.ok("unit", "set", "M1", "--status", "doing", "--unit", "CL123")
    row = opened.table("units.tsv")[1]
    assert row[9] == "CL123" and row[11] == "doing"
    assert datetime.fromisoformat(row[10]).tzinfo is not None


def test_legacy_ledger_pass_cannot_bypass_verified_complete(opened):
    opened.add_unit("M1")
    assert "complete" in opened.fail("unit", "set", "M1", "--status", "done")
    opened.ok("ledger", "record", "M1", "--status", "pass", "--grade", "pytest-pass", "--evidence", "3 passed")
    opened.ok("ledger", "record", "M1", "--status", "fail", "--grade", "pytest-pass", "--evidence", "1 failed")
    assert "complete" in opened.fail("unit", "set", "M1", "--status", "done")
    opened.ok("ledger", "record", "M1", "--status", "pass", "--grade", "pytest-pass", "--evidence", "4 passed")
    assert "complete" in opened.fail("unit", "set", "M1", "--status", "done")
    assert opened.table("units.tsv")[1][11] == "todo"


def test_unit_set_edits_brief_but_never_blanks_it(opened):
    opened.add_unit("M1")
    assert opened.ok("unit", "set", "M1", "--goal", "새 목표", "--timebox", "30") == "M1 갱신: goal, timebox"
    assert opened.table("units.tsv")[1][1:6:4] == ["새 목표", "30"]
    assert "빈 값" in opened.fail("unit", "set", "M1", "--done", " ")
    assert "빈 값" in opened.fail("unit", "set", "M1", "--after", "")
    assert "바꿀 칸 없음" in opened.fail("unit", "set", "M1")
    assert "없음" in opened.fail("unit", "set", "M7", "--goal", "x")


def test_unit_list_shows_progress_and_overrun(opened):
    opened.add_unit("M1")
    opened.add_unit("M2")
    opened.ok("unit", "set", "M1", "--status", "doing", "--unit", "CL123")
    opened.backdate("M1", 12)
    assert opened.ok("unit", "list").splitlines() == ["M1 doing 12/60분 CL123 목표", "M2 todo -/60분 ? 목표"]
    opened.backdate("M1", 75)
    assert opened.ok("unit", "list", "--status", "doing") == "M1 doing 75/60분 넘김 CL123 목표"
    assert opened.ok("unit", "list", "--status", "held") == "마일스톤 없음"


def test_ledger_record_appends_row_with_adapter_grade(opened):
    opened.add_unit("M1")
    assert opened.ok("ledger", "record", "M1", "--status", "pass", "--grade", "needs-human", "--evidence", "화면") == "ledger M1 pass needs-human"
    opened.ok("ledger", "record", "M1", "--status", "retry", "--grade", "editmode-pass", "--evidence", "run 7", "--files", "a.cs,b.cs")
    rows = opened.table("ledger.tsv")
    assert rows[0] == ["at", "unit", "status", "grade", "evidence", "files"]
    assert rows[1][1:] == ["M1", "pass", "needs-human", "화면", "-"]
    assert rows[2][1:] == ["M1", "retry", "editmode-pass", "run 7", "a.cs,b.cs"]
    assert datetime.fromisoformat(rows[1][0]).tzinfo is not None


def test_ledger_record_rejects_unknown_unit_grade_and_blank_evidence(opened):
    opened.add_unit("M1")
    assert "M9 없음" in opened.fail("ledger", "record", "M9", "--status", "pass", "--grade", "pytest-pass", "--evidence", "x")
    err = opened.fail("ledger", "record", "M1", "--status", "pass", "--grade", "magic", "--evidence", "x")
    assert "editmode-pass, pytest-pass, needs-human" in err
    assert "--evidence" in opened.fail("ledger", "record", "M1", "--status", "pass", "--grade", "pytest-pass")
    assert len(opened.table("ledger.tsv")) == 1


def test_decision_add_numbers_ids_and_keeps_old_rows_byte_for_byte(opened):
    assert opened.ok("decision", "add", "--what", "범위", "--chosen", "좁게", "--why", "되돌리기 쉬움", "--affects", "M1") == "D1 추가"
    opened.ok("decision", "add", "--what", "순서", "--chosen", "A 먼저", "--why", "의존", "--affects", "M2")
    before = opened.read("decisions.tsv")
    assert opened.ok("decision", "add", "--what", "다시 판단", "--chosen", "B 먼저", "--why", "새 근거", "--affects", "M2", "--fixes", "D2") == "D3 추가 (D2 정정)"
    after = opened.read("decisions.tsv")
    assert after.startswith(before)
    last = after.splitlines()[-1].split("\t")
    assert last[1:] == ["D3", "D2 정정: 다시 판단", "B 먼저", "새 근거", "M2"]


def test_decision_add_rejects_unknown_fix_and_blank_fields(opened):
    assert "D3" in opened.fail("decision", "add", "--what", "a", "--chosen", "b", "--why", "c", "--affects", "d", "--fixes", "D3")
    assert "--why" in opened.fail("decision", "add", "--what", "a", "--chosen", "b", "--affects", "d")
    assert len(opened.table("decisions.tsv")) == 1


def test_malformed_table_is_reported_with_line_number(opened):
    (opened.folder / "units.tsv").write_text(opened.read("units.tsv") + "M1\t목표\n", encoding="utf-8")
    assert "2행 칸 수 2" in opened.fail("unit", "list")


def test_status_prints_three_lines(opened):
    for number in range(1, 7):
        opened.add_unit(f"M{number}")
        opened.ok("ledger", "record", f"M{number}", "--status", "pass", "--grade", "pytest-pass", "--evidence", "ok")
        rows = mission_state.read_table(str(opened.folder / "units.tsv"), mission_state.UNIT_HEADER)
        rows[-1]["status"] = "done"
        mission_state.write_table(str(opened.folder / "units.tsv"), mission_state.UNIT_HEADER, rows)
    opened.add_unit("M7")
    opened.ok("unit", "set", "M7", "--status", "doing")
    opened.backdate("M7", 90)
    opened.ok("mission", "set", "--stage", "impl", "--confirm", "A=승인")
    opened.publish_question("confirm-B.md", approval="pending")
    opened.publish_question("confirm-A.md", ("answered",), "complete")
    opened.publish_question("ask-1.md")
    opened.publish_question("ask-2.md")
    opened.ok("document", "share", "confirm-B.md", "ask-1.md", "ask-2.md")
    lines = opened.ok("status").splitlines()
    assert lines[0] == "마일스톤 todo 0, doing 1, done 6, held 0 | 현재 단계 impl | 확인 A 승인, B 대기, C 대기"
    assert lines[1] == "판정: M3 pass pytest-pass, M4 pass pytest-pass, M5 pass pytest-pass, M6 pass pytest-pass 외 2개"
    assert lines[2] == "걸린 것: M7 timebox 넘김(90/60분), ask-1.md 답 없음, ask-2.md 답 없음, confirm-B.md 답 없음"


def test_status_with_nothing_stuck(opened):
    lines = opened.ok("status").splitlines()
    assert lines[1:] == ["판정: 없음", "걸린 것: 없음"]


def test_unanswered_questions_uses_item_states_without_interpreting_prose(opened):
    record = opened.publish_question("ask-3.md", ("answered", "pending"), body="2026-10-22 답변 완료")
    opened.ok("document", "share", "ask-3.md")
    assert mission_state.unanswered_questions(str(opened.folder), {}) == ["ask-3.md"]
    mission_state.question_records.answer_document(opened.folder, "ask-3.md", {
        "schema": 1, "id": "reply-1", "revision": record["revision"], "at": "2026-10-26", "text": "2번 권장",
        "updates": [{"id": "2", "status": "answered", "answer": "권장"}],
    })
    assert mission_state.unanswered_questions(str(opened.folder), {}) == []
    opened.ok("document", "share", "ask-3.md")
    assert mission_state.unanswered_questions(str(opened.folder), {}) == []


@pytest.mark.parametrize(
    "name,state,answer,fields,pending,statuses,count_known",
    [
        ("ask-1.md", "질문 1 답변 · 질문 2 대기", "2026-10-22 1번 보류", {}, True, ["answered", "pending"], True),
        ("ask-1.md", "답변 대기", "2026-10-22 1번 보류", {}, False, ["unknown", "unknown"], False),
        ("ask-1.md", "답변 대기", "", {}, True, ["pending", "pending"], True),
        ("ask-1.md", "답변 접수", "2026-10-22 답변 접수", {}, True, ["unknown", "unknown"], False),
        ("ask-1.md", "답변 대기 · 부분 답변", "2026-10-22 일부만 답변", {}, True, ["unknown", "unknown"], False),
        ("ask-1.md", "질문 1 답변", "2026-10-22 일부만 답변", {}, True, ["answered", "unknown"], False),
        ("confirm-A3.md", "질문 1 답변 접수 · 질문 2 답변 전달", "2026-10-22 답변 접수", {}, True, ["unknown", "unknown"], False),
        ("confirm-A3.md", "질문 1 답변 · 질문 2 답변 완료", "", {"확인 A3": "대기"}, False, ["answered", "answered"], True),
        ("confirm-A3.md", "답변 완료", "", {"확인 A3": "대기"}, False, ["answered", "answered"], True),
        ("confirm-A3.md", "답변 완료 · 승인 대기", "", {"확인 A3": "대기"}, True, ["answered", "answered"], True),
        ("confirm-A3.md", "답변 완료 · 전체 승인 확인 대기", "", {"확인 A3": "대기"}, True, ["answered", "answered"], True),
        ("confirm-A3.md", "답변 완료 · 승인 대기", "", {"확인 A3": "승인"}, False, ["answered", "answered"], True),
        ("confirm-A3.md", "답변 대기", "", {"확인 A3": "승인"}, False, ["answered", "answered"], True),
    ],
)
def test_summarize_question_document_uses_only_supplied_snapshot(name, state, answer, fields, pending, statuses, count_known, monkeypatch):
    text = f"# 제출할 CL\n\n{state}\n\n## 결정할 것\n\n### 1. 제출할까요?\n\n### 2. 선행을 풀까요?\n\n## 답\n\n{answer}\n"

    def forbidden_read(*args, **kwargs):
        raise AssertionError("문서 snapshot 판정은 파일을 읽을 수 없음")

    monkeypatch.setattr(mission_state, "open", forbidden_read, raising=False)
    result = mission_state.summarize_question_document(name, text, fields)
    assert result.name == name and result.title == "제출할 CL"
    assert result.pending == pending and result.count_known == count_known
    assert result.total_known
    assert [item.status for item in result.items] == statuses


def test_unanswered_confirm_uses_explicit_approval_independently_of_headers(opened):
    record = opened.publish_question("confirm-A3.md", ("answered",), "pending")
    opened.ok("document", "share", "confirm-A3.md")
    assert mission_state.unanswered_questions(str(opened.folder), {}) == ["confirm-A3.md"]
    assert mission_state.unanswered_questions(str(opened.folder), {"확인 A3": "승인"}) == ["confirm-A3.md"]
    assert mission_state.unanswered_questions(str(opened.folder), {"확인 A": "생략"}) == ["confirm-A3.md"]
    mission_state.question_records.answer_document(opened.folder, "confirm-A3.md", {
        "schema": 1, "id": "reply-1", "revision": record["revision"], "at": "2026-10-26", "text": "전체 승인",
        "updates": [], "approval": {"status": "complete", "reason": "사용자 승인"},
    })
    assert mission_state.unanswered_questions(str(opened.folder), {}) == []


def test_legacy_question_is_a_record_issue_without_prose_fallback(opened):
    (opened.folder / "ask-1.md").write_text("# 질문\n\n## 답\n\n2026-10-22 권장\n", encoding="utf-8")
    assert mission_state.unanswered_questions(str(opened.folder), {}) == []
    assert opened.ok("status").endswith("걸린 것: ask-1.md 기록 확인 필요")
    assert "기록 확인 필요" in opened.fail("document", "share", "ask-1.md")


@pytest.mark.parametrize("answer_heading", ["## 답", "### 답 (2026-10-23)", "## **답변** ###"])
def test_historical_summary_uses_real_answer_after_similar_heading(opened, answer_heading):
    text = (
        "# 작업 범위\n\n답변 대기\n\n## 결정할 것\n\n### 1. 다음 작업을 할까요?\n\n"
        "## 답 없이 진행 중인 정리\n\n아직 결정 전\n\n"
        f"{answer_heading}\n\n2026-10-23 사용자: 진행해주세요\n"
    )
    result = mission_state.summarize_question_document("ask-1.md", text, {})
    assert not result.pending and result.total_known and not result.count_known


def test_similar_answer_heading_alone_cannot_clear_waiting(opened):
    text = (
        "# 작업 범위\n\n답변 대기\n\n## 결정할 것\n\n### 1. 다음 작업을 할까요?\n\n"
        "## 답 없이 진행 중인 정리\n\n2026-10-23 아직 결정 전\n"
    )
    result = mission_state.summarize_question_document("ask-1.md", text, {})
    assert result.pending and result.total_known and result.count_known
    assert result.pending_count == 1


def test_unanswered_questions_reads_unshared_sources_and_reports_changed_documents(opened):
    for name in ("ask-1.md", "ask-2.md"):
        opened.publish_question(name)
    opened.ok("document", "share", "ask-1.md")
    (opened.folder / "ask-1.md").write_text("# 변경된 질문\n", encoding="utf-8")
    assert mission_state.unanswered_questions(str(opened.folder), {}) == ["ask-2.md"]
    assert opened.ok("status").endswith("걸린 것: ask-1.md 기록 확인 필요, ask-2.md 답 없음")


def test_unanswered_questions_parse_a_single_current_snapshot(opened, monkeypatch):
    question = opened.folder / "ask-1.md"
    opened.publish_question(statuses=("answered",))
    original = question.read_text(encoding="utf-8")
    changed = original + "\n답변 대기\n"
    opened.ok("document", "share", "ask-1.md")
    original_open = open

    def swapping_open(path, mode="r", *args, **kwargs):
        stream = original_open(path, mode, *args, **kwargs)
        if Path(path) != question or mode != "rb":
            return stream
        with stream:
            snapshot = stream.read()
        question.write_text(changed, encoding="utf-8")
        return BytesIO(snapshot)

    monkeypatch.setattr(mission_state, "open", swapping_open, raising=False)
    assert mission_state.unanswered_questions(str(opened.folder), {}) == []
    assert question.read_text(encoding="utf-8") == changed


def test_questions_remain_visible_after_edits_hiding_and_markdown_deletion(opened):
    question = opened.folder / "ask-1.md"
    opened.publish_question()
    assert mission_state.shared_documents(str(opened.folder)) == set()
    assert opened.ok("status").endswith("걸린 것: ask-1.md 답 없음")
    assert opened.ok("document", "list") == "공유 중: 없음\n자동 표시: ask-1.md, details/history/ask-1.md"
    assert opened.ok("document", "share", "ask-1.md") == "공유함: ask-1.md"
    assert opened.ok("document", "list") == "공유 중: ask-1.md\n자동 표시: ask-1.md, details/history/ask-1.md"
    question.write_text("# 질문 수정\n", encoding="utf-8")
    assert mission_state.shared_documents(str(opened.folder)) == set()
    assert opened.ok("status").endswith("걸린 것: ask-1.md 기록 확인 필요")
    assert "기록 확인 필요" in opened.fail("document", "share", "ask-1.md")
    opened.ok("question", "render", "ask-1.md")
    opened.ok("document", "share", "ask-1.md")
    assert opened.ok("document", "hide", "ask-1.md") == "숨김: 변경 없음\n자동 표시 유지: ask-1.md"
    assert mission_state.shared_documents(str(opened.folder)) == set()
    assert opened.ok("status").endswith("걸린 것: ask-1.md 답 없음")
    question.unlink()
    assert opened.ok("status").endswith("걸린 것: ask-1.md 기록 확인 필요")
    assert mission_state.unanswered_questions(str(opened.folder), {}) == []
    opened.ok("question", "render", "ask-1.md")
    assert opened.ok("status").endswith("걸린 것: ask-1.md 답 없음")


def test_document_share_validates_all_paths_before_writing_manifest(opened):
    opened.publish_question()
    before = opened.folder / mission_state.SHARED_DOCUMENTS_FILE
    assert "파일을 읽을 수 없음" in opened.fail("document", "share", "ask-1.md", "missing.md")
    assert not before.exists()
    outside = opened.root.parent / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    for name in ("../outside.md", str(outside), ".secret.md", "details/.secret.md", "mission.md", "units.tsv", "ledger.tsv", "decisions.tsv", "sessions.tsv", mission_state.SHARED_DOCUMENTS_FILE):
        assert opened.fail("document", "share", name)
        assert not before.exists()
    nested = opened.folder / "details" / "ask-1.md"
    nested.parent.mkdir(exist_ok=True)
    nested.write_text("nested", encoding="utf-8")
    assert opened.ok("document", "share", "ask-1.md", "details/ask-1.md") == "공유함: ask-1.md, details/ask-1.md"
    assert mission_state.shared_documents(str(opened.folder)) == {"ask-1.md", "details/ask-1.md"}


def test_expected_document_hash_mismatch_preserves_manifest(opened):
    question = opened.folder / "note.md"
    previous_content = b"previous"
    question.write_bytes(previous_content)
    mission_state.share_documents(str(opened.folder), ["note.md"])
    manifest = opened.folder / mission_state.SHARED_DOCUMENTS_FILE
    before = manifest.read_bytes()
    current_content = b"current"
    question.write_bytes(current_content)
    with pytest.raises(mission_state.StateError, match="문서가 바뀌었습니다"):
        mission_state.share_documents(
            str(opened.folder),
            ["note.md"],
            expected_hashes={"note.md": hashlib.sha256(previous_content).hexdigest()},
        )
    assert manifest.read_bytes() == before
    assert mission_state.shared_documents(str(opened.folder)) == set()
    mission_state.share_documents(
        str(opened.folder),
        ["note.md"],
        expected_hashes={"note.md": hashlib.sha256(current_content).hexdigest()},
    )
    assert mission_state.shared_documents(str(opened.folder)) == {"note.md"}


def test_document_manifest_corruption_fails_closed_and_missing_entries_are_stale(opened):
    opened.publish_question()
    manifest = opened.folder / mission_state.SHARED_DOCUMENTS_FILE
    manifest.write_text("wrong\theader\n", encoding="utf-8")
    listed = opened.ok("document", "list")
    assert "헤더가 다름" in listed and "자동 표시: ask-1.md" in listed
    assert opened.ok("status").endswith("걸린 것: ask-1.md 답 없음")
    manifest.write_text("path\tsha256\tat\nmissing.md\t" + "0" * 64 + "\t2026-10-22T00:00:00+09:00\n", encoding="utf-8")
    assert mission_state.shared_documents(str(opened.folder)) == set()
    assert opened.ok("document", "list") == "공유 중: 없음\n자동 표시: ask-1.md, details/history/ask-1.md"


def test_auto_questions_include_only_root_documents_and_matching_details(opened):
    files = ["ask-1.md", "confirm-B.md", "details/ask-1.md", "details/confirm-B.md", "details/history/ask-1.md", "details/history/confirm-B.md", "details/history/ask-orphan.md", "details/history/notes.md", "questions/history/ask-1/1.json", "details/ask-orphan.md", "nested/ask-private.md", "ask-folder/private.md", ".ask-hidden.md"]
    for name in files:
        path = opened.folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# 질문\n", encoding="utf-8")
    expected = {"ask-1.md", "confirm-B.md", "details/ask-1.md", "details/confirm-B.md", "details/history/ask-1.md", "details/history/confirm-B.md"}
    assert mission_state.auto_question_documents(str(opened.folder)) == expected
    for name in files:
        assert mission_state.auto_question_document(str(opened.folder), name) == (name in expected)
    assert mission_state.unanswered_questions(str(opened.folder), {}) == []
    assert opened.ok("status").endswith("걸린 것: ask-1.md 기록 확인 필요, confirm-B.md 기록 확인 필요")


def test_hiding_question_details_uses_canonical_names_and_preserves_visibility(opened):
    (opened.folder / "ask-1.md").write_text("# 질문\n", encoding="utf-8")
    details = opened.folder / "details" / "ask-1.md"
    details.parent.mkdir()
    details.write_text("# 근거\n", encoding="utf-8")
    opened.ok("document", "share", "details/ask-1.md")
    assert opened.ok("document", "hide", "details\\ask-1.md") == "숨김: 변경 없음\n자동 표시 유지: details/ask-1.md"
    assert mission_state.shared_documents(str(opened.folder)) == set()
    assert mission_state.auto_question_documents(str(opened.folder)) == {"ask-1.md", "details/ask-1.md"}


def test_auto_questions_reject_private_symlink_aliases_and_escapes(opened):
    private = opened.folder / "private.md"
    private.write_text("private", encoding="utf-8")
    hidden = opened.folder / ".private.md"
    hidden.write_text("hidden", encoding="utf-8")
    outside = opened.root.parent / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    try:
        for number, target in enumerate((private, hidden, outside), start=1):
            (opened.folder / f"ask-{number}.md").symlink_to(target)
    except OSError:
        pytest.skip("symbolic links are unavailable")
    assert mission_state.auto_question_documents(str(opened.folder)) == set()
    assert mission_state.unanswered_questions(str(opened.folder), {}) == []


def test_document_share_rejects_symlink_escape(opened):
    outside = opened.root.parent / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    hidden_dir = opened.folder / ".dashboard-answers"
    hidden_dir.mkdir()
    hidden_file = hidden_dir / "secret.json"
    hidden_file.write_text("secret", encoding="utf-8")
    hidden_alias = opened.folder / "hidden-alias.md"
    system_alias = opened.folder / "system-alias.md"
    link = opened.folder / "linked.md"
    try:
        hidden_alias.symlink_to(hidden_file)
        system_alias.symlink_to(opened.folder / "mission.md")
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symbolic links are unavailable")
    assert "숨김 경로" in opened.fail("document", "share", "hidden-alias.md")
    assert "시스템 파일" in opened.fail("document", "share", "system-alias.md")
    assert "밖" in opened.fail("document", "share", "linked.md")
    manifest = opened.folder / mission_state.SHARED_DOCUMENTS_FILE
    assert not manifest.exists()
    manifest.symlink_to(outside)
    assert "경로가 잘못됨" in opened.fail("document", "list")


@pytest.fixture
def home(tmp_path, monkeypatch):
    folder = tmp_path / "home"
    folder.mkdir()
    monkeypatch.setenv("HOME", str(folder))
    monkeypatch.setenv("USERPROFILE", str(folder))
    return folder


def write_sessions(mission, *rows):
    lines = ["at\taction\tsession\ttranscript"] + [f"2026-10-19T09:00:00+09:00\t{a}\t{s}\t{t}" for a, s, t in rows]
    (mission.folder / "sessions.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_transcripts_prefers_recorded_path_then_searches_by_session(opened, home):
    recorded = home / "elsewhere.jsonl"
    recorded.write_text("{}", encoding="utf-8")
    claude = home / ".claude" / "projects" / "D--proj"
    (claude / "aaaa-1111" / "subagents").mkdir(parents=True)
    (claude / "aaaa-1111.jsonl").write_text("{}", encoding="utf-8")
    (claude / "aaaa-1111" / "subagents" / "agent-1.jsonl").write_text("{}", encoding="utf-8")
    write_sessions(opened, ("open", "s-rec", str(recorded)), ("open", "aaaa-1111", ""), ("resume", "aaaa-1111", ""))
    lines = opened.ok("transcripts").splitlines()
    assert lines[0] == f"s-rec {recorded}"
    assert lines[1] == f"aaaa-1111 {claude / 'aaaa-1111.jsonl'}"
    assert lines[2] == f"  {claude / 'aaaa-1111' / 'subagents' / 'agent-1.jsonl'}"
    assert len(lines) == 3


def test_transcripts_finds_codex_rollout_by_exact_session_suffix(opened, home):
    day = home / ".codex" / "sessions" / "2026" / "10" / "19"
    day.mkdir(parents=True)
    wanted = day / "rollout-2026-10-19T09-00-00-0199-aaaa.jsonl"
    wanted.write_text("{}", encoding="utf-8")
    (day / "rollout-2026-10-19T09-00-00-0199-aaaa-extra.jsonl").write_text("{}", encoding="utf-8")
    write_sessions(opened, ("open", "0199-aaaa", ""))
    assert opened.ok("transcripts") == f"0199-aaaa {wanted}"


def test_transcripts_reports_missing_and_never_globs_odd_ids(opened, home):
    (home / ".claude" / "projects" / "p").mkdir(parents=True)
    (home / ".claude" / "projects" / "p" / "secret.jsonl").write_text("{}", encoding="utf-8")
    write_sessions(opened, ("open", "gone-1", "C:/nope.jsonl"), ("open", "*", ""), ("open", "../p/secret", ""))
    assert opened.ok("transcripts").splitlines() == ["없음: gone-1", "없음: *", "없음: ../p/secret"]


def test_transcripts_without_sessions_file(opened):
    assert opened.ok("transcripts").startswith("세션 기록 없음")


def test_transcripts_finds_grok_session_and_ignores_claude_subagent_layout(opened, home, monkeypatch):
    base = home / "custom-grok"
    monkeypatch.setenv("GROK_HOME", str(base))
    folder = base / "sessions/encoded/sample-session"
    folder.mkdir(parents=True)
    transcript = folder / "updates.jsonl"
    transcript.write_text(json.dumps({"timestamp": 1800000000, "params": {"sessionId": "sample-session",
        "update": {"sessionUpdate": "turn_completed"}}}) + "\n", encoding="utf-8")
    fake_subagents = folder / "updates/subagents"
    fake_subagents.mkdir(parents=True)
    (fake_subagents / "agent.jsonl").write_text("{}", encoding="utf-8")
    write_sessions(opened, ("open", "sample-session", "stale-path.jsonl"))
    assert opened.ok("transcripts") == f"sample-session {transcript}"


def test_transcripts_prefers_grok_store_for_grok_runtime(opened, home, monkeypatch):
    monkeypatch.setenv("GROK_HOME", str(home / ".grok"))
    grok = home / ".grok/sessions/encoded/sample-session/updates.jsonl"
    grok.parent.mkdir(parents=True)
    grok.write_text(json.dumps({"params": {"sessionId": "sample-session", "update": {"sessionUpdate": "turn_completed"}}}), encoding="utf-8")
    claude = home / ".claude/projects/encoded/sample-session.jsonl"
    claude.parent.mkdir(parents=True)
    claude.write_text("{}", encoding="utf-8")
    write_sessions(opened, ("open", "sample-session", ""))
    ctx = mission_state.Context(ADAPTER, str(opened.root / "state"), "m1", "grok", str(opened.root))
    assert mission_state.cmd_transcripts(ctx, object()) == [f"sample-session {grok}"]


def test_cli_error_is_one_stderr_line_with_exit_code_1(tmp_path):
    result = subprocess.run(
        [sys.executable, "-B", str(SCRIPT), "--project", str(tmp_path), "status"],
        capture_output=True,
        cwd=tmp_path,
        timeout=20,
        env={**os.environ, "HAN_ADAPTERS_DIR": str(tmp_path / "none")},
    )
    assert result.returncode == 1
    assert result.stdout == b""
    assert result.stderr.decode("utf-8").count("\n") == 1


def test_question_preview_show_and_check_are_readonly(opened, monkeypatch):
    record = opened.question_record()
    draft = opened.json_input("draft.json", record)
    before = {str(path.relative_to(opened.folder)): path.read_bytes() for path in opened.folder.rglob("*") if path.is_file()}

    def forbidden_lock(*args, **kwargs):
        raise AssertionError("읽기 전용 명령은 미션 잠금을 만들 수 없음")

    monkeypatch.setattr(mission_state.slots, "FileLock", forbidden_lock)
    assert opened.ok("question", "preview", "--input", draft) == mission_state.question_records.render_document(record).rstrip("\n")
    assert opened.ok("question", "check") == "질문 기록 정상: 0개"
    after = {str(path.relative_to(opened.folder)): path.read_bytes() for path in opened.folder.rglob("*") if path.is_file()}
    assert after == before
    opened.publish_question()
    assert json.loads(opened.ok("question", "show", "ask-1.md")) == record
    assert opened.ok("question", "check") == "질문 기록 정상: 1개"


def test_question_cli_publish_answer_and_render_preserve_verbatim_answer_and_headers(opened, monkeypatch):
    record = opened.question_record("confirm-A.md", approval="pending")
    draft = opened.json_input("draft.json", record)
    header = (opened.folder / "mission.md").read_bytes()
    locks = []
    original_lock = mission_state.slots.FileLock

    def tracked_lock(path, *args, **kwargs):
        locks.append(Path(path).name)
        return original_lock(path, *args, **kwargs)

    monkeypatch.setattr(mission_state.slots, "FileLock", tracked_lock)
    assert "revision 1" in opened.ok("question", "publish", "--input", draft)
    answer = {"schema": 1, "id": "reply-1", "revision": 1, "at": "2026-10-26", "text": "권장\n원문 유지",
              "updates": [{"id": "1", "status": "answered", "answer": "권장"}],
              "approval": {"status": "complete", "reason": "사용자 승인"}}
    reply = opened.json_input("reply.json", answer)
    assert "답변 저장" in opened.ok("question", "answer", "confirm-A.md", "--input", reply)
    saved = mission_state.question_records.read_document(opened.folder, "confirm-A.md")
    assert saved["answers"] == [answer]
    assert saved["items"][0]["status"] == "answered" and saved["items"][0]["answer"] == "권장"
    assert (opened.folder / "mission.md").read_bytes() == header
    assert opened.ok("status").endswith("걸린 것: 없음")
    markdown = opened.folder / "confirm-A.md"
    expected = markdown.read_bytes()
    markdown.write_text("# 외부 편집\n", encoding="utf-8")
    assert "질문 문서 생성" in opened.ok("question", "render", "confirm-A.md")
    assert markdown.read_bytes() == expected
    assert locks == ["m1.lock", "m1.lock", "m1.lock"]
    assert json.loads(opened.ok("question", "show", "confirm-A.md")) == saved


def test_question_cli_check_reports_missing_invalid_and_changed_records_with_actions(opened):
    (opened.folder / "ask-legacy.md").write_text("# 예전 질문\n", encoding="utf-8")
    opened.publish_question("ask-missing.md", ("answered",))
    (opened.folder / "ask-missing.md").unlink()
    opened.publish_question("ask-corrupt.md")
    (opened.folder / "questions" / "ask-corrupt.json").write_text("{broken", encoding="utf-8")
    opened.publish_question("ask-edited.md")
    (opened.folder / "ask-edited.md").write_text("# 직접 편집\n", encoding="utf-8")
    code, out, err = opened.run("question", "check")
    assert code == 1 and not err
    for name in ("ask-legacy", "ask-missing", "ask-corrupt", "ask-edited"):
        line = next(line for line in out.splitlines() if line.startswith(f"{name}.md 기록 확인 필요"))
        assert str(opened.folder / "questions" / f"{name}.json") in line
        assert "question publish --input <JSONfile>" in line and f"question render {name}.md" in line
    assert mission_state.unanswered_questions(str(opened.folder), {}) == []
    status = opened.ok("context")
    assert "기록 확인 필요" in status and "답 없음" not in status


def test_question_cli_restore_uses_mission_lock_and_preserves_headers(opened, monkeypatch):
    record = opened.publish_question("ask-library.md", title="Library filters")
    source = opened.folder / "questions" / "ask-library.json"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    markdown = opened.folder / "ask-library.md"
    markdown.write_bytes(b"# Library filters\r\n\r\nKeep the author filter.\r\n")
    original = opened.root / "saved-original.md"
    original.write_bytes(markdown.read_bytes())
    record["items"][0].update(status="answered", answer="Author filter retained.")
    record["answers"].append({"schema": 1, "id": "library-choice", "revision": 1, "at": "2041-02-03",
                              "text": "Keep the author filter.",
                              "updates": [{"id": "1", "status": "answered", "answer": "Author filter retained."}]})
    draft = opened.json_input("reviewed-library.json", record)
    headers = {name: (opened.folder / name).read_bytes() for name in ("mission.md",)}
    active = (opened.root / "state" / "ACTIVE").read_bytes()
    locks = []
    original_lock = mission_state.slots.FileLock

    def tracked_lock(path, *args, **kwargs):
        locks.append(Path(path).name)
        return original_lock(path, *args, **kwargs)

    monkeypatch.setattr(mission_state.slots, "FileLock", tracked_lock)
    command = ("question", "restore", "--input", draft, "--source", str(original),
               "--expect-revision", "1", "--expect-sha256", digest)
    assert "질문 기록 복구" in opened.ok(*command)
    assert locks == ["m1.lock"]
    assert opened.ok("question", "check") == "질문 기록 정상: 1개"
    assert mission_state.question_records.read_document(opened.folder, "ask-library.md") == record
    assert (opened.root / "state" / "ACTIVE").read_bytes() == active
    assert all((opened.folder / name).read_bytes() == content for name, content in headers.items())
    saved = source.read_bytes(), markdown.read_bytes()
    assert "expected_sha256" in opened.fail(*command)
    assert (source.read_bytes(), markdown.read_bytes()) == saved


def test_question_cli_revision_and_duplicate_reply_conflicts_preserve_files(opened):
    record = opened.publish_question()
    record["revision"] = 2
    record["title"] = "개정 질문"
    draft = opened.json_input("draft.json", record)
    before = {str(path): path.read_bytes() for path in opened.folder.rglob("*") if path.is_file()}
    assert opened.fail("question", "publish", "--input", draft)
    assert {str(path): path.read_bytes() for path in opened.folder.rglob("*") if path.is_file()} == before
    assert "revision 2" in opened.ok("question", "publish", "--input", draft, "--expect-revision", "1")
    answer = {"schema": 1, "id": "reply-1", "revision": 2, "at": "2026-10-26", "text": "권장",
              "updates": [{"id": "1", "status": "answered", "answer": "권장"}]}
    reply = opened.json_input("reply.json", answer)
    opened.ok("question", "answer", "ask-1.md", "--input", reply)
    saved = (opened.folder / "questions" / "ask-1.json").read_bytes()
    (opened.folder / "ask-1.md").write_text("# 손상\n", encoding="utf-8")
    opened.ok("question", "answer", "ask-1.md", "--input", reply)
    assert (opened.folder / "questions" / "ask-1.json").read_bytes() == saved
    answer["text"] = "다른 답"
    opened.json_input("reply.json", answer)
    markdown = (opened.folder / "ask-1.md").read_bytes()
    assert opened.fail("question", "answer", "ask-1.md", "--input", reply)
    assert (opened.folder / "questions" / "ask-1.json").read_bytes() == saved
    assert (opened.folder / "ask-1.md").read_bytes() == markdown


@pytest.mark.parametrize("value", [[], {"schema": 1}, {"schema": 99}])
def test_question_cli_rejects_invalid_input_before_creating_records(opened, value):
    draft = opened.json_input("draft.json", value)
    assert opened.fail("question", "publish", "--input", draft)
    assert not (opened.folder / "questions").exists()
    assert not list(opened.folder.glob("ask-*.md"))


def test_question_cli_reports_json_input_and_parser_errors(opened):
    invalid = opened.root / "invalid.json"
    invalid.write_text("{broken", encoding="utf-8")
    assert "JSON 읽기 실패" in opened.fail("question", "publish", "--input", str(invalid))
    assert "JSON 읽기 실패" in opened.fail("question", "answer", "ask-1.md", "--input", str(opened.root / "missing.json"))
    assert "--input" in opened.fail("question", "publish")
    assert "--expect-revision" in opened.fail("question", "publish", "--input", str(invalid), "--expect-revision", "wrong")
    assert "형식 오류" in opened.fail("question", "show", "../ask-1.md")


def test_question_share_validates_all_records_before_changing_existing_manifest(opened):
    opened.publish_question("ask-1.md")
    opened.publish_question("ask-2.md")
    opened.ok("document", "share", "ask-1.md")
    manifest = opened.folder / mission_state.SHARED_DOCUMENTS_FILE
    before = manifest.read_bytes()
    (opened.folder / "ask-2.md").write_text("# 직접 편집\n", encoding="utf-8")
    assert "기록 확인 필요" in opened.fail("document", "share", "ask-1.md", "ask-2.md")
    assert manifest.read_bytes() == before


def test_question_store_shape_issue_remains_visible_in_status_and_check(opened):
    (opened.folder / "questions").write_text("잘못된 저장소", encoding="utf-8")
    assert opened.ok("status").endswith("걸린 것: questions 기록 확인 필요")
    code, out, err = opened.run("question", "check")
    assert code == 1 and not err
    assert str(opened.folder / "questions") in out and "폴더와 JSON 파일 이름을 확인" in out


def test_question_cli_editorial_requires_explicit_flag_and_preserves_answer_state(opened):
    record = opened.publish_question(statuses=("answered",))
    record["revision"] = 2
    record["title"] = "표현을 다듬은 질문"
    record["items"][0]["title"] = "선택 표현 정리"
    record["intro"] = "문체 수정"
    draft = opened.json_input("draft.json", record)
    source = opened.folder / "questions" / "ask-1.json"
    before = source.read_bytes()
    assert opened.fail("question", "publish", "--input", draft, "--expect-revision", "1")
    assert source.read_bytes() == before
    assert "revision 2" in opened.ok("question", "publish", "--input", draft, "--expect-revision", "1", "--editorial")
    saved = mission_state.question_records.read_document(opened.folder, "ask-1.md")
    assert saved == record and saved["items"][0]["answer"] == "진행"
    assert opened.ok("status").endswith("걸린 것: 없음")
    record["revision"] = 3
    record["items"][0].update(status="pending", answer="")
    opened.json_input("draft.json", record)
    before = source.read_bytes()
    assert opened.fail("question", "publish", "--input", draft, "--expect-revision", "2", "--editorial")
    assert source.read_bytes() == before


def test_runtime_grok_reads_grok_adapter(tmp_path, capsys, monkeypatch):
    for name in ("HAN_ADAPTERS_DIR", "HAN_REPO", "CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_OPTION_ADAPTERS_DIR"):
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "game"
    (root / ".grok" / "orchestrator").mkdir(parents=True)
    (root / ".grok" / "orchestrator" / "adapter.json").write_text(json.dumps(ADAPTER), encoding="utf-8")
    (root / "state").mkdir()
    code = mission_state.main(["--runtime", "grok", "--project", str(root), "active"])
    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert "ACTIVE 없음" in captured.out
