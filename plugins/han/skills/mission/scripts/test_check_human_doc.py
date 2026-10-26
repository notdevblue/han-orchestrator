from pathlib import Path
import copy
import json
import re
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import check_human_doc


SCHEMA_TABLE = "| 컬럼 | 타입 |\n| --- | --- |\n| Id | long |"
ANSWER_TEXT = "2026-10-20 승인\n\n1번은 권장대로 진행.\n날짜와 원문을 유지."


def document():
    return {
        "schema": 1,
        "id": "confirm-A",
        "revision": 1,
        "title": "설계 확인",
        "intro": "결정할 내용을 확인합니다.",
        "items": [{"id": "1", "title": "방향", "body": "권장대로 진행할까요?", "status": "pending", "answer": ""}],
        "body": SCHEMA_TABLE,
        "approval": {"status": "none", "reason": ""},
        "answers": [{"schema": 1, "id": "legacy-1", "revision": 1, "at": "2026-10-20T12:00:00+09:00", "text": ANSWER_TEXT, "updates": []}],
    }


def write_record(path, record):
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return path


def migration(tmp_path):
    raw = "\ufeff# 원문\r\n\r\n" + SCHEMA_TABLE.replace("\n", "\r\n")
    raw += "\r\n\r\n```md\r\n## 답\r\n예시 답은 이관 대상 아님\r\n```\r\n\r\n## 답변 (2026-10-20)\r\n\r\n"
    raw += ANSWER_TEXT.replace("\n", "\r\n") + "\r\n\r\n## 후속\r\n\r\n후속 설명\r\n"
    source = tmp_path / "source.md"
    source.write_bytes(raw.encode("utf-8"))
    record = document()
    record["legacy_markdown"] = raw
    target = write_record(tmp_path / "draft.json", record)
    return source, target, record


def test_large_design_table_remains_allowed_in_short_report(tmp_path):
    table = "| 컬럼 | 타입 | 제약 |\n| --- | --- | --- |\n" + "".join(f"| Field{number} | int | 0 이상 |\n" for number in range(100))
    original = tmp_path / "original.md"
    original.write_text("# 원문\n" + table, encoding="utf-8")
    report = tmp_path / "report.md"
    report.write_text("# 설계 결과\n\n## 지금\n\n설계 표를 확인받습니다.\n\n" + table, encoding="utf-8")
    assert not check_human_doc.check(report, original)["issues"]


def test_rewrite_cannot_change_approved_answer_or_schema(tmp_path):
    original = tmp_path / "original.md"
    original.write_text("# 확인\n\n| 컬럼 | 타입 |\n| --- | --- |\n| Id | long |\n\n## 답\n\n2026-10-20 승인\n", encoding="utf-8")
    result = tmp_path / "confirm-A.md"
    result.write_text("# 확인\n\n| 컬럼 | 타입 |\n| --- | --- |\n| Id | int |\n\n## 답\n", encoding="utf-8")
    assert len(check_human_doc.check(result, original)["issues"]) == 2


def test_length_alone_does_not_decide_readability(tmp_path):
    report = tmp_path / "report.md"
    report.write_text("# 진행 보고\n\n## 지금\n\n" + "상세 실행 기록입니다. " * 150, encoding="utf-8")
    result = check_human_doc.check(report)
    assert not result["issues"]
    assert result["semantic_review_required"]


def test_markdown_migration_preserves_raw_source_and_answer_under_generated_history_heading(tmp_path):
    source, target, _ = migration(tmp_path)
    result = check_human_doc.check(target, source)
    assert not result["issues"]
    assert result["semantic_review_required"]
    assert result["prose_characters"] > len(ANSWER_TEXT)


@pytest.mark.parametrize("body", [
    "<!-- 답 작성 예시 -->",
    "```text\n답 작성 예시\n```",
    "    답 작성 예시",
    "(대기)",
    "대기",
    "답변 대기",
    "답변대기",
    "<!-- 안내 -->\n\n```text\n예시\n```\n\n(대기)",
])
def test_markdown_migration_does_not_require_hidden_examples_or_placeholder_answers(tmp_path, body):
    raw = "# 원문\n\n## 답\n\n" + body + "\n\n## 후속\n\n설명\n"
    source = tmp_path / "source.md"
    source.write_bytes(raw.encode("utf-8"))
    record = document()
    record.update(answers=[], legacy_markdown=raw)
    target = write_record(tmp_path / "draft.json", record)
    assert not check_human_doc.check(target, source)["issues"]


def test_real_answer_preserves_raw_body_including_surrounding_examples_and_comments(tmp_path):
    body = "<!-- 안내 -->\n\n```text\n예시\n```\n\n권장대로 진행."
    raw = "# 원문\n\n## 답\n\n" + body + "\n"
    source = tmp_path / "source.md"
    source.write_bytes(raw.encode("utf-8"))
    record = document()
    record["legacy_markdown"] = raw
    record["answers"][0]["text"] = body
    target = write_record(tmp_path / "draft.json", record)
    assert not check_human_doc.check(target, source)["issues"]
    record["answers"][0]["text"] = "권장대로 진행."
    write_record(target, record)
    assert any("답 절 본문" in issue for issue in check_human_doc.check(target, source)["issues"])


@pytest.mark.parametrize("date", ["2026-10-20", "2026.10.20", "2026/10/20", "2026.1.2"])
@pytest.mark.parametrize("preservation", ["at", "text", "missing"])
def test_markdown_migration_preserves_answer_heading_date_in_history(tmp_path, date, preservation):
    body = "권장대로 진행."
    raw = f"# 원문\n\n## 답({date})\n\n{body}\n"
    source = tmp_path / "source.md"
    source.write_bytes(raw.encode("utf-8"))
    record = document()
    record["legacy_markdown"] = raw
    history = record["answers"][0]
    history.update(at="2026-10-26T16:00:00+09:00", text=body)
    if preservation == "at":
        year, month, day = (int(part) for part in re.split(r"[-./]", date))
        history["at"] = f"{year:04}-{month:02}-{day:02}T12:00:00+09:00"
    if preservation == "text":
        history["text"] = f"{date}\n\n{body}"
    target = write_record(tmp_path / "draft.json", record)
    issues = check_human_doc.check(target, source)["issues"]
    if preservation == "missing":
        assert any("제목의 날짜" in issue for issue in issues)
    else:
        assert not issues


def test_migration_can_group_past_answers_with_heading_dates_in_one_history_text(tmp_path):
    raw = "# 원문\n\n## 답변 (2026-10-20)\n\n첫 답.\n\n## 답: 2026/10/21\n\n둘째 답.\n"
    source = tmp_path / "source.md"
    source.write_bytes(raw.encode("utf-8"))
    record = document()
    record["legacy_markdown"] = raw
    record["answers"][0].update(at="2026-10-26T16:00:00+09:00", text="2026-10-20\n\n첫 답.\n\n2026/10/21\n\n둘째 답.")
    target = write_record(tmp_path / "draft.json", record)
    assert not check_human_doc.check(target, source)["issues"]


@pytest.mark.parametrize("change", [
    lambda record: record.pop("legacy_markdown"),
    lambda record: record.update(legacy_markdown=record["legacy_markdown"].lstrip("\ufeff")),
    lambda record: record.update(legacy_markdown=record["legacy_markdown"].replace("\r\n", "\n")),
    lambda record: record.update(legacy_markdown=record["legacy_markdown"].replace("후속 설명", "변경 설명")),
])
def test_markdown_migration_rejects_any_raw_source_change(tmp_path, change):
    source, target, record = migration(tmp_path)
    change(record)
    write_record(target, record)
    assert any("legacy_markdown" in issue for issue in check_human_doc.check(target, source)["issues"])


@pytest.mark.parametrize("change", [
    lambda record: record.update(answers=[]),
    lambda record: record["answers"][0].update(text=ANSWER_TEXT.replace("2026-10-20", "2026-10-21")),
    lambda record: record["answers"][0].update(text=ANSWER_TEXT.replace("권장대로", "대안대로")),
])
def test_markdown_migration_requires_original_answer_text_in_history(tmp_path, change):
    source, target, record = migration(tmp_path)
    change(record)
    write_record(target, record)
    assert any("답 절 본문" in issue for issue in check_human_doc.check(target, source)["issues"])


@pytest.mark.parametrize("change", [
    lambda record: record.update(answers=[]),
    lambda record: record["answers"][0].update(text="바뀐 답"),
    lambda record: record["answers"][0].update(at="2026-10-21T12:00:00+09:00"),
])
def test_json_revision_rejects_deleted_or_changed_answer_history(tmp_path, change):
    original = document()
    source = write_record(tmp_path / "source.json", original)
    revised = copy.deepcopy(original)
    revised["revision"] = 2
    change(revised)
    target = write_record(tmp_path / "draft.json", revised)
    assert any("답 원문 이력" in issue for issue in check_human_doc.check(target, source)["issues"])


@pytest.mark.parametrize("change", [
    lambda record: record.pop("legacy_markdown"),
    lambda record: record.update(legacy_markdown="변경 원문"),
])
def test_json_revision_preserves_original_legacy_markdown(tmp_path, change):
    original = document()
    original["legacy_markdown"] = "\ufeff# 원문\r\n"
    source = write_record(tmp_path / "source.json", original)
    revised = copy.deepcopy(original)
    change(revised)
    target = write_record(tmp_path / "draft.json", revised)
    assert any("legacy_markdown" in issue for issue in check_human_doc.check(target, source)["issues"])


def test_json_revision_accepts_appended_history_and_unchanged_schema(tmp_path):
    original = document()
    source = write_record(tmp_path / "source.json", original)
    revised = copy.deepcopy(original)
    revised["revision"] = 2
    appended = copy.deepcopy(revised["answers"][0])
    appended.update(id="reply-2", revision=2, text="새 개정의 추가 답")
    revised["answers"].append(appended)
    target = write_record(tmp_path / "draft.json", revised)
    assert not check_human_doc.check(target, source)["issues"]


@pytest.mark.parametrize("source_type", ["markdown", "json"])
def test_structured_document_rejects_schema_column_type_change(tmp_path, source_type):
    source, target, record = migration(tmp_path)
    if source_type == "json":
        source = write_record(tmp_path / "source.json", record)
    record["body"] = SCHEMA_TABLE.replace("long", "int")
    write_record(target, record)
    assert any("설계 표" in issue for issue in check_human_doc.check(target, source)["issues"])


def test_generated_markdown_must_match_source_json(tmp_path):
    record = document()
    source = write_record(tmp_path / "source.json", record)
    target = tmp_path / "preview.md"
    target.write_bytes(check_human_doc.question_records.render_document(record).replace("\n", "\r\n").encode("utf-8"))
    assert not check_human_doc.check(target, source)["issues"]
    target.write_text("# 임의로 바꾼 화면", encoding="utf-8")
    assert any("렌더 결과" in issue for issue in check_human_doc.check(target, source)["issues"])


def test_json_without_source_checks_structure_and_rendered_prose(tmp_path):
    record = document()
    target = write_record(tmp_path / "draft.json", record)
    result = check_human_doc.check(target)
    assert not result["issues"]
    assert result["prose_characters"] == check_human_doc.prose_length(check_human_doc.question_records.render_document(record))
    record["items"] = []
    write_record(target, record)
    assert check_human_doc.check(target)["issues"]


def test_damaged_json_is_reported_as_cli_failure_without_traceback(tmp_path, capsys):
    target = tmp_path / "broken.json"
    target.write_text('{"schema":', encoding="utf-8")
    assert check_human_doc.main([str(target)]) == 1
    output = capsys.readouterr()
    assert json.loads(output.out)["issues"]
    assert not output.err
