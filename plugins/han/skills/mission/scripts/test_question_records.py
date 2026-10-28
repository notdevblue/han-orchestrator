import copy
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import question_records
from question_records import (
    QuestionRecordError,
    answer_document,
    document_names,
    inspect_document,
    publish_document,
    read_document,
    render_document,
    render_saved_document,
    restore_document,
    source_path,
    summarize_document,
)


def document(identifier="ask-1"):
    return {
        "schema": 1,
        "id": identifier,
        "revision": 1,
        "title": "제출과 후속 범위",
        "intro": "답변 대기라는 과거 문장을 상태로 읽지 않음.",
        "items": [
            {"id": "1", "title": "제출할까요?", "body": "- 권장: 제출\n- 대안: 보류", "status": "pending", "answer": ""},
            {"id": "2", "title": "후속 작업할까요?", "body": "- 권장: 계속", "status": "pending", "answer": ""},
        ],
        "body": "## 상세\n\n상세 검토 내용",
        "approval": {"status": "none", "reason": ""},
        "answers": [],
    }


def answer(operation="reply-1", revision=1, updates=()):
    return {
        "schema": 1,
        "id": operation,
        "revision": revision,
        "at": "2026-10-26T16:00:00+09:00",
        "text": "1번 권장, 2번 대안으로 해줘.\n원문 두 번째 줄.",
        "updates": list(updates),
    }


def test_recommended_and_alternative_answers_preserve_raw_text_and_explicit_state(tmp_path):
    record = publish_document(tmp_path, document())
    payload = answer(updates=[
        {"id": "1", "status": "answered", "answer": "권장"},
        {"id": "2", "status": "answered", "answer": "대안"},
    ])
    saved = answer_document(tmp_path, "ask-1.md", payload)
    summary, issue = inspect_document(tmp_path, "ask-1.md", (tmp_path / "ask-1.md").read_text(encoding="utf-8"))
    assert not issue and not summary.pending and summary.count_known and summary.total_known
    assert [(item.number, item.status) for item in summary.items] == [("1", "answered"), ("2", "answered")]
    assert saved["revision"] == record["revision"] == 1
    assert saved["answers"] == [payload]
    assert payload["text"] not in render_document(saved)
    assert payload["text"] in question_records.history_path(tmp_path, "ask-1.md").read_text(encoding="utf-8")
    assert "## 현재 결과" not in render_document(saved)
    assert "완료한 결정" not in render_document(saved)


def test_partial_answer_withdrawal_and_separate_approval(tmp_path):
    publish_document(tmp_path, document("confirm-A"))
    partial = answer(updates=[{"id": "1", "status": "answered", "answer": "권장"}])
    first = answer_document(tmp_path, "confirm-A.md", partial)
    assert summarize_document(first).pending_count == 1
    rest = answer("reply-2", updates=[{"id": "2", "status": "withdrawn", "answer": "범위 취소"}])
    rest["approval"] = {"status": "pending", "reason": "전체 범위는 확인 필요"}
    second = answer_document(tmp_path, "confirm-A.md", rest)
    summary = summarize_document(second)
    assert summary.pending and summary.pending_count == 0
    assert [item.status for item in summary.items] == ["answered", "answered"]
    final = answer("reply-3")
    final["approval"] = {"status": "complete", "reason": "사용자가 전체 범위 승인"}
    saved = answer_document(tmp_path, "confirm-A.md", final)
    assert not summarize_document(saved).pending
    assert not (tmp_path / "mission.md").exists()


def test_render_labels_separate_approval_request_without_changing_original_approval_text(tmp_path):
    record = document("confirm-A")
    record["intro"] = "확인 A 전체 승인: 승인 완료"
    publish_document(tmp_path, record)
    payload = answer()
    payload["text"] = "전체 승인: 기존 확인 A 승인 유지"
    payload["approval"] = {"status": "complete", "reason": "추가 요청 승인"}
    saved = answer_document(tmp_path, "confirm-A.md", payload)
    text = render_document(saved)
    assert text.count("별도 승인 요청: 승인 완료") == 1
    assert "확인 A 전체 승인: 승인 완료" in text
    assert payload["text"] not in text
    assert payload["text"] in question_records.history_path(tmp_path, "confirm-A.md").read_text(encoding="utf-8")
    assert saved["answers"][0]["text"] == payload["text"]
    saved["approval"] = {"status": "none", "reason": ""}
    assert "별도 승인 요청: 없음" in render_document(saved)
    saved["approval"] = {"status": "pending", "reason": "추가 확인"}
    assert "별도 승인 요청: 승인 대기" in render_document(saved)


def test_current_view_keeps_pending_options_and_current_design_before_completed_results(tmp_path):
    record = document()
    record["items"][0].update(status="answered", answer="Keep the blue border.")
    record["items"][0]["body"] = "Past choices: blue or green."
    record["items"][1]["body"] = "Pending choices: circle or square."
    record["body"] = "## Current contract\n\n| 필드 | 타입 |\n| --- | --- |\n| Shape | string |"
    record["legacy_markdown"] = "# Original palette\r\n"
    publish_document(tmp_path, record)
    current = render_document(record)
    assert "Past choices" not in current and record["legacy_markdown"] not in current
    assert current.index(record["intro"]) < current.index("Pending choices") < current.index(record["body"]) < current.index("## 전체 이력")
    assert "Keep the blue border." not in current and "## 현재 결과" not in current
    assert "details/history/ask-1.md" in current
    archive = question_records.history_path(tmp_path, "ask-1.md").read_bytes().decode("utf-8")
    assert "Past choices: blue or green." in archive and record["legacy_markdown"] in archive
    assert question_records.validate_history_archive(question_records.history_path(tmp_path, "ask-1.md"), record)


def test_history_deduplicates_raw_answers_and_legacy_while_linking_each_revision(tmp_path):
    record = document()
    record["legacy_markdown"] = "# Original fictional palette\r\nExact legacy text.\r\n"
    publish_document(tmp_path, record)
    saved = answer_document(tmp_path, "ask-1.md", answer(updates=[{"id": "1", "status": "answered", "answer": "Blue."}]))
    revised = copy.deepcopy(saved)
    revised.update(revision=2, title="Reworded palette")
    publish_document(tmp_path, revised, 1, editorial=True)
    archive = question_records.history_path(tmp_path, "ask-1.md").read_bytes().decode("utf-8")
    assert archive.count(record["legacy_markdown"]) == 1
    assert archive.count(saved["answers"][0]["text"]) == 1
    assert archive.count("이 개정의 답 기록: `reply-1` (대상 개정 1)") == 2
    assert "## 개정 1" in archive and "## 개정 2" in archive


def test_history_revisions_are_ordered_by_number_instead_of_filename(tmp_path):
    record = document()
    record["revision"] = 2
    publish_document(tmp_path, record)
    later = copy.deepcopy(record)
    later.update(revision=10, title="Tenth fictional revision")
    publish_document(tmp_path, later, 2, editorial=True)
    current = copy.deepcopy(later)
    current.update(revision=11, title="Eleventh fictional revision")
    publish_document(tmp_path, current, 10, editorial=True)
    assert [snapshot["revision"] for snapshot in question_records.history_records(tmp_path, current)] == [2, 10, 11]
    archive = question_records.render_history_document(tmp_path, current)
    assert archive.index("## 개정 2") < archive.index("## 개정 10") < archive.index("## 개정 11")


def test_completed_and_withdrawn_answers_are_only_in_history_unless_author_puts_results_in_body(tmp_path):
    record = document()
    record["items"][0].update(title="1. Blue border", status="answered", answer="Recommended.\nRetain the curved edge.")
    record["items"][1].update(status="withdrawn", answer="Drop the optional flag.")
    record["body"] = "## Current design\n\nUse blue with the curved edge."
    publish_document(tmp_path, record)
    text = render_document(record)
    assert "1. 1." not in text
    assert record["body"] in text
    assert record["items"][0]["answer"] not in text and record["items"][1]["answer"] not in text
    history = question_records.render_history_document(tmp_path, record)
    assert record["items"][0]["answer"] in history and record["items"][1]["answer"] in history


@pytest.mark.parametrize("change", ["missing", "tampered"])
def test_inspection_rejects_missing_or_changed_generated_history_and_render_repairs_it(tmp_path, change):
    record = publish_document(tmp_path, document())
    archive = question_records.history_path(tmp_path, "ask-1.md")
    if change == "missing":
        archive.unlink()
    else:
        archive.write_text("Changed archived answer", encoding="utf-8")
    assert inspect_document(tmp_path, "ask-1.md", render_document(record))[1]
    render_saved_document(tmp_path, "ask-1.md")
    assert not inspect_document(tmp_path, "ask-1.md", render_document(record))[1]


@pytest.mark.parametrize("change", [
    lambda record: record.update(revision=True),
    lambda record: record.update(schema=True),
    lambda record: record["items"].append(copy.deepcopy(record["items"][0])),
    lambda record: record["items"][0].update(status="unknown"),
    lambda record: record["items"][0].update(status="answered"),
    lambda record: record["items"][0].update(status="withdrawn"),
    lambda record: record["items"][0].update(id="../1"),
    lambda record: record.update(approval={"status": "pending", "reason": ""}),
    lambda record: record.update(answers={}),
    lambda record: record.update(body=None),
])
def test_invalid_records_are_rejected_without_writing(tmp_path, change):
    record = document()
    change(record)
    with pytest.raises(QuestionRecordError):
        publish_document(tmp_path, record)
    assert not (tmp_path / "ask-1.md").exists()


@pytest.mark.parametrize("change", [
    lambda record: record.update(approvals={"status": "pending", "reason": "오타"}),
    lambda record: record["items"][0].update(statuz="answered"),
    lambda record: record["approval"].update(reasons="오타"),
    lambda record: record.update(answers=[{**answer(), "approvals": {"status": "pending", "reason": "오타"}}]),
    lambda record: record.update(answers=[answer(updates=[{"id": "1", "status": "answered", "answer": "권장", "title": "잘못된 키"}])]),
])
def test_unknown_schema_fields_are_rejected_before_publication(tmp_path, change):
    record = document()
    change(record)
    with pytest.raises(QuestionRecordError, match="알 수 없는 필드"):
        publish_document(tmp_path, record)
    assert not source_path(tmp_path, "ask-1.md").exists()
    assert not (tmp_path / "ask-1.md").exists()


def test_unknown_answer_payload_fields_are_rejected_before_writing(tmp_path):
    original = publish_document(tmp_path, document())
    payload = {**answer(), "approvals": {"status": "pending", "reason": "오타"}}
    with pytest.raises(QuestionRecordError, match="알 수 없는 필드"):
        answer_document(tmp_path, "ask-1.md", payload)
    assert read_document(tmp_path, "ask-1.md") == original


def test_empty_items_cannot_claim_a_known_complete_document(tmp_path):
    record = document()
    record["items"] = []
    with pytest.raises(QuestionRecordError, match="비어 있지 않은 배열"):
        publish_document(tmp_path, record)


def test_current_revision_history_rejects_unknown_item_but_old_revision_preserves_it(tmp_path):
    publish_document(tmp_path, document())
    saved = answer_document(tmp_path, "ask-1.md", answer(updates=[{"id": "1", "status": "answered", "answer": "권장"}]))
    removed = copy.deepcopy(saved)
    removed["items"] = [removed["items"][1]]
    with pytest.raises(QuestionRecordError, match="현재 개정 답 대상 문항이 없음"):
        publish_document(tmp_path, removed, 1)
    removed["revision"] = 2
    assert publish_document(tmp_path, removed, 1)["answers"] == saved["answers"]


def test_publish_cannot_change_existing_answer_but_can_reset_for_reconfirmation(tmp_path):
    publish_document(tmp_path, document())
    saved = answer_document(tmp_path, "ask-1.md", answer(updates=[{"id": "1", "status": "answered", "answer": "권장"}]))
    changed = copy.deepcopy(saved)
    changed["revision"] = 2
    changed["items"][0]["answer"] = "대안"
    with pytest.raises(QuestionRecordError, match="기존 문항 답 변경"):
        publish_document(tmp_path, changed, 1)
    changed["items"][0].update(status="pending", answer="")
    assert publish_document(tmp_path, changed, 1)["answers"] == saved["answers"]


@pytest.mark.parametrize("approval", [
    {"status": "none", "reason": ""},
    {"status": "complete", "reason": "사용자 승인"},
])
def test_publish_cannot_resolve_approval_wait(tmp_path, approval):
    record = document()
    record["approval"] = {"status": "pending", "reason": "승인 대기"}
    saved = publish_document(tmp_path, record)
    changed = {**saved, "revision": 2, "approval": approval}
    with pytest.raises(QuestionRecordError, match="승인 대기 해소"):
        publish_document(tmp_path, changed, 1)
    assert read_document(tmp_path, "ask-1.md") == saved


def test_publish_cannot_append_answer_history(tmp_path):
    saved = publish_document(tmp_path, document())
    changed = {**saved, "revision": 2, "answers": [answer()]}
    with pytest.raises(QuestionRecordError, match="이력 변경은 answer"):
        publish_document(tmp_path, changed, 1)
    assert read_document(tmp_path, "ask-1.md") == saved


@pytest.mark.parametrize("field", ["title", "body", "heading"])
def test_changed_item_definition_cannot_inherit_answered_status(tmp_path, field):
    publish_document(tmp_path, document())
    saved = answer_document(tmp_path, "ask-1.md", answer(updates=[{"id": "1", "status": "answered", "answer": "권장"}]))
    changed = copy.deepcopy(saved)
    changed["revision"] = 2
    changed["items"][0][field] = "새로운 결정 범위"
    with pytest.raises(QuestionRecordError, match="내용이 바뀐 문항"):
        publish_document(tmp_path, changed, 1)
    assert read_document(tmp_path, "ask-1.md") == saved
    changed["items"][0].update(status="pending", answer="")
    assert publish_document(tmp_path, changed, 1)["answers"] == saved["answers"]
    answered = answer_document(tmp_path, "ask-1.md", answer("reply-2", 2, [{"id": "1", "status": "answered", "answer": "새 범위의 대안"}]))
    assert answered["items"][0]["status"] == "answered"
    assert answered["answers"][-1]["revision"] == 2


@pytest.mark.parametrize("status", ["pending", "withdrawn", "answered"])
def test_new_item_accepts_pending_or_explicit_withdrawal_but_requires_new_answer(tmp_path, status):
    original = publish_document(tmp_path, document())
    changed = copy.deepcopy(original)
    changed["revision"] = 2
    changed["items"].append({"id": "3", "title": "추가 결정", "body": "새 선택지", "status": status, "answer": "새 범위 제외"})
    if status == "answered":
        with pytest.raises(QuestionRecordError, match="새 문항"):
            publish_document(tmp_path, changed, 1)
        assert read_document(tmp_path, "ask-1.md") == original
    else:
        assert publish_document(tmp_path, changed, 1)["items"][-1]["status"] == status


@pytest.mark.parametrize("field", ["intro", "body"])
def test_document_scope_change_requires_explicit_approval_wait_to_preserve_answers(tmp_path, field):
    publish_document(tmp_path, document())
    saved = answer_document(tmp_path, "ask-1.md", answer(updates=[{"id": "1", "status": "answered", "answer": "권장"}]))
    changed = copy.deepcopy(saved)
    changed["revision"] = 2
    changed[field] = "승인 대상이 바뀐 범위"
    with pytest.raises(QuestionRecordError, match="approval 대기"):
        publish_document(tmp_path, changed, 1)
    changed["approval"] = {"status": "pending", "reason": "전체 범위 변경 재확인"}
    assert publish_document(tmp_path, changed, 1)["answers"] == saved["answers"]
    summary = summarize_document(changed)
    assert summary.pending and summary.pending_count == 1


def test_document_scope_change_can_reset_items_while_retaining_raw_history(tmp_path):
    publish_document(tmp_path, document())
    saved = answer_document(tmp_path, "ask-1.md", answer(updates=[{"id": "1", "status": "answered", "answer": "권장"}]))
    changed = copy.deepcopy(saved)
    changed["revision"] = 2
    changed["body"] = "다시 판단할 범위"
    changed["items"][0].update(status="pending", answer="")
    assert publish_document(tmp_path, changed, 1)["answers"] == saved["answers"]


def test_editorial_declaration_preserves_answer_scope_and_allows_text_edits(tmp_path):
    publish_document(tmp_path, document())
    saved = answer_document(tmp_path, "ask-1.md", answer(updates=[{"id": "1", "status": "answered", "answer": "권장"}]))
    changed = copy.deepcopy(saved)
    changed.update(revision=2, title="제목 문체", intro="소개 문체", body="상세 문체")
    changed["items"][0].update(title="문항 문체", body="선택지 문체", heading="1. 원래 승인 범위")
    with pytest.raises(QuestionRecordError):
        publish_document(tmp_path, changed, 1)
    assert publish_document(tmp_path, changed, 1, editorial=True) == changed
    assert changed["answers"] == saved["answers"]


@pytest.mark.parametrize("change", [
    lambda record: record["items"][0].update(status="pending"),
    lambda record: record["items"][0].update(answer="다른 결정"),
    lambda record: record.update(approval={"status": "pending", "reason": "승인 변경"}),
    lambda record: record["items"].append({"id": "3", "title": "추가", "body": "", "status": "pending", "answer": ""}),
    lambda record: record.update(legacy_markdown="새 원문"),
])
def test_editorial_declaration_cannot_change_states_approval_or_item_identity(tmp_path, change):
    publish_document(tmp_path, document())
    saved = answer_document(tmp_path, "ask-1.md", answer(updates=[{"id": "1", "status": "answered", "answer": "권장"}]))
    changed = copy.deepcopy(saved)
    changed["revision"] = 2
    change(changed)
    with pytest.raises(QuestionRecordError, match="editorial"):
        publish_document(tmp_path, changed, 1, editorial=True)
    assert read_document(tmp_path, "ask-1.md") == saved


def test_editorial_declaration_requires_an_existing_document(tmp_path):
    with pytest.raises(QuestionRecordError, match="기존 질문"):
        publish_document(tmp_path, document(), editorial=True)


def test_new_publication_preserves_reviewed_revision_and_legacy_original(tmp_path):
    record = document()
    record["revision"] = 2
    record["legacy_markdown"] = "# 기존 원문\r\n\r\n- 답: 권장\r\n"
    assert publish_document(tmp_path, record) == record
    assert read_document(tmp_path, "ask-1.md")["legacy_markdown"] == record["legacy_markdown"]
    assert document_names(tmp_path) == ["ask-1.md"]


def test_initial_publication_without_legacy_original_does_not_overwrite_markdown(tmp_path):
    original = "# 기존 답변\r\n\r\n권장대로\r\n".encode("utf-8")
    markdown_path = tmp_path / "ask-1.md"
    markdown_path.write_bytes(original)
    with pytest.raises(QuestionRecordError, match="기존 Markdown 보호"):
        publish_document(tmp_path, document())
    assert markdown_path.read_bytes() == original
    assert not source_path(tmp_path, "ask-1.md").exists()


def test_initial_migration_rejects_changed_original_and_accepts_matching_legacy(tmp_path):
    record = document()
    record["legacy_markdown"] = "\ufeff# 이전 답변\r\n\r\n권장대로\r\n"
    markdown_path = tmp_path / "ask-1.md"
    changed = record["legacy_markdown"] + "다른 세션의 추가 답변\r\n"
    markdown_path.write_bytes(changed.encode("utf-8"))
    with pytest.raises(QuestionRecordError, match="기존 Markdown 보호"):
        publish_document(tmp_path, record)
    assert markdown_path.read_bytes() == changed.encode("utf-8")
    assert not source_path(tmp_path, "ask-1.md").exists()
    record["legacy_markdown"] = changed
    assert publish_document(tmp_path, record) == record
    assert read_document(tmp_path, "ask-1.md")["legacy_markdown"] == changed
    markdown_path.write_text("손편집", encoding="utf-8")
    assert publish_document(tmp_path, record) == record
    assert markdown_path.read_text(encoding="utf-8") == render_document(record)


def test_legacy_heading_preserves_anchor_separately_from_item_identity(tmp_path):
    record = document()
    record["items"][0]["heading"] = "A2 결정 5 해금 기준을 선택할까요?"
    record["items"][1]["heading"] = "결정 a. 답변 완료 범위는?"
    saved = publish_document(tmp_path, record)
    text = render_document(saved)
    assert "### A2 결정 5 해금 기준을 선택할까요?\n" in text
    assert "### 결정 a. 답변 완료 범위는?\n" in text
    summary = summarize_document(saved)
    assert [(item.number, item.status) for item in summary.items] == [("1", "pending"), ("2", "pending")]
    assert summary.pending


@pytest.mark.parametrize("heading", ["", "   ", "첫 줄\n둘째 줄", "한 줄\n", None])
def test_invalid_legacy_heading_is_rejected(tmp_path, heading):
    record = document()
    record["items"][0]["heading"] = heading
    with pytest.raises(QuestionRecordError, match="heading"):
        publish_document(tmp_path, record)


def test_publish_requires_new_revision_and_current_expectation_and_preserves_history(tmp_path):
    original = publish_document(tmp_path, document())
    changed = copy.deepcopy(original)
    changed["title"] = "수정된 질문"
    with pytest.raises(QuestionRecordError, match="revision"):
        publish_document(tmp_path, changed, 1)
    changed["revision"] = 2
    with pytest.raises(QuestionRecordError, match="expected_revision"):
        publish_document(tmp_path, changed)
    published = publish_document(tmp_path, changed, 1)
    history = tmp_path / "questions" / "history" / "ask-1" / "1.json"
    assert json.loads(history.read_text(encoding="utf-8")) == original
    assert published == changed and document_names(tmp_path) == ["ask-1.md"]
    with pytest.raises(QuestionRecordError, match="expected_revision"):
        publish_document(tmp_path, {**published, "revision": 3, "title": "지연된 작성"}, 1)
    assert read_document(tmp_path, "ask-1.md") == changed


def test_publish_preserves_prior_raw_answer_and_legacy_bytes(tmp_path):
    record = document()
    record["legacy_markdown"] = "원래 문서\r\n"
    publish_document(tmp_path, record)
    saved = answer_document(tmp_path, "ask-1.md", answer())
    changed = {**saved, "revision": 2, "answers": []}
    with pytest.raises(QuestionRecordError, match="이력"):
        publish_document(tmp_path, changed, 1)
    changed["answers"] = saved["answers"]
    changed["legacy_markdown"] = "고친 원문"
    with pytest.raises(QuestionRecordError, match="legacy_markdown"):
        publish_document(tmp_path, changed, 1)


def test_publish_history_collision_is_rejected(tmp_path):
    publish_document(tmp_path, document())
    history = tmp_path / "questions" / "history" / "ask-1" / "1.json"
    history.parent.mkdir(parents=True)
    history.write_text("{}", encoding="utf-8")
    with pytest.raises(QuestionRecordError, match="이력 충돌"):
        publish_document(tmp_path, {**document(), "revision": 2}, 1)
    assert read_document(tmp_path, "ask-1.md")["revision"] == 1


def test_answer_operation_retry_is_idempotent_after_later_revision(tmp_path):
    publish_document(tmp_path, document())
    payload = answer(updates=[{"id": "1", "status": "answered", "answer": "권장"}])
    saved = answer_document(tmp_path, "ask-1.md", payload)
    updated = copy.deepcopy(saved)
    updated["revision"] = 2
    updated["items"][0].update(status="pending", answer="")
    publish_document(tmp_path, updated, 1)
    (tmp_path / "ask-1.md").unlink()
    assert answer_document(tmp_path, "ask-1.md", payload) == updated
    assert read_document(tmp_path, "ask-1.md")["items"][0]["status"] == "pending"
    with pytest.raises(QuestionRecordError, match="내용이 다름"):
        answer_document(tmp_path, "ask-1.md", {**payload, "text": "다른 원문"})
    with pytest.raises(QuestionRecordError, match="revision"):
        answer_document(tmp_path, "ask-1.md", answer("late-reply", 1))


@pytest.mark.parametrize("updates", [
    [{"id": "missing", "status": "answered", "answer": "권장"}],
    [{"id": "1", "status": "answered", "answer": ""}],
    [{"id": "1", "status": "bogus", "answer": "권장"}],
    [{"id": "1", "status": "answered", "answer": "권장"}] * 2,
])
def test_bad_answer_does_not_modify_source(tmp_path, updates):
    saved = publish_document(tmp_path, document())
    with pytest.raises(QuestionRecordError):
        answer_document(tmp_path, "ask-1.md", answer(updates=updates))
    assert read_document(tmp_path, "ask-1.md") == saved


def test_inspection_never_falls_back_to_markdown_status(tmp_path):
    text = "# 이미 답변\n\n답변 완료\n\n## 답\n\n권장대로\n"
    summary, issue = inspect_document(tmp_path, "ask-1.md", text)
    assert issue and not summary.pending and not summary.count_known and not summary.total_known
    publish_document(tmp_path, document())
    summary, issue = inspect_document(tmp_path, "ask-1.md", text)
    assert issue and summary.items == []
    source_path(tmp_path, "ask-1.md").write_text("{", encoding="utf-8")
    summary, issue = inspect_document(tmp_path, "ask-1.md", text)
    assert issue and not summary.count_known


def test_markdown_and_direct_source_edits_require_explicit_render_repair(tmp_path):
    record = publish_document(tmp_path, document())
    text = render_document(record)
    assert not inspect_document(tmp_path, "ask-1.md", text.replace("\n", "\r\n"))[1]
    assert inspect_document(tmp_path, "ask-1.md", text + "수기 답변 완료\n")[1]
    record["items"][0].update(status="answered", answer="대안")
    source_path(tmp_path, "ask-1.md").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    assert inspect_document(tmp_path, "ask-1.md", text)[1]
    assert render_saved_document(tmp_path, "ask-1.md") == record
    assert not inspect_document(tmp_path, "ask-1.md", (tmp_path / "ask-1.md").read_text(encoding="utf-8"))[1]


def test_atomic_source_survives_markdown_failure_and_retry_repairs_it(tmp_path, monkeypatch):
    write_text = question_records.write_text

    def fail_markdown(path, text):
        if path.suffix == ".md":
            raise OSError("Markdown 쓰기 중단")
        write_text(path, text)

    monkeypatch.setattr(question_records, "write_text", fail_markdown)
    with pytest.raises(QuestionRecordError, match="Markdown"):
        publish_document(tmp_path, document())
    assert read_document(tmp_path, "ask-1.md") == document()
    monkeypatch.setattr(question_records, "write_text", write_text)
    publish_document(tmp_path, document())
    payload = answer(updates=[{"id": "1", "status": "answered", "answer": "권장"}])
    monkeypatch.setattr(question_records, "write_text", fail_markdown)
    with pytest.raises(QuestionRecordError, match="Markdown"):
        answer_document(tmp_path, "ask-1.md", payload)
    assert read_document(tmp_path, "ask-1.md")["answers"] == [payload]
    monkeypatch.setattr(question_records, "write_text", write_text)
    saved = answer_document(tmp_path, "ask-1.md", payload)
    assert len(saved["answers"]) == 1
    assert not inspect_document(tmp_path, "ask-1.md", (tmp_path / "ask-1.md").read_text(encoding="utf-8"))[1]


@pytest.mark.parametrize("name", ["../ask-1.md", ".ask-1.md", "sub/ask-1.md", "ask-../x.md", "C:\\ask-1.md", "ask-1.json", "mission.md"])
def test_hidden_or_external_names_are_rejected(tmp_path, name):
    with pytest.raises(QuestionRecordError):
        source_path(tmp_path, name)


@pytest.mark.parametrize("target", ["questions", "ask-1.md"])
def test_symlink_escape_is_rejected(tmp_path, target):
    mission = tmp_path / "mission"
    outside = tmp_path / "outside"
    mission.mkdir()
    outside.mkdir()
    link = mission / target
    destination = outside / "other.md"
    if target == "questions":
        destination = outside
    try:
        link.symlink_to(destination, target_is_directory=target == "questions")
    except OSError:
        pytest.skip("symlink 생성 권한 없음")
    with pytest.raises(QuestionRecordError, match="미션 밖"):
        publish_document(mission, document())
    assert not list(outside.iterdir())


@pytest.fixture
def recovery(tmp_path):
    earlier = {"schema": 1, "id": "first-choice", "revision": 1, "at": "2041-02-03",
               "text": "Keep the author filter.",
               "updates": [{"id": "author", "status": "answered", "answer": "Author filter retained."}]}
    current = {
        "schema": 1, "id": "ask-library", "revision": 3, "title": "Library filters", "intro": "Choose filters.",
        "items": [{"id": "author", "title": "Author", "body": "", "status": "answered", "answer": "Author filter retained."},
                  {"id": "year", "title": "Year", "body": "", "status": "pending", "answer": ""}],
        "body": "", "approval": {"status": "pending", "reason": "Review remaining filters."},
        "answers": [earlier], "legacy_markdown": "# Initial library choices\r\n",
    }
    publish_document(tmp_path, current)
    original_json = b"\xef\xbb\xbf" + json.dumps(current, ensure_ascii=False).encode("utf-8") + b"\r\n"
    source_path(tmp_path, "ask-library.md").write_bytes(original_json)
    restored = copy.deepcopy(current)
    restored["items"][1].update(status="answered", answer="Year filter retained.")
    restored["items"].append({"id": "publisher", "title": "Publisher", "body": "", "status": "withdrawn",
                              "answer": "Publisher filter excluded."})
    restored["body"] = "## Approved filter design\n\nAuthor and year filters."
    restored["approval"] = {"status": "complete", "reason": "Filter design approved."}
    restored["answers"].extend([
        {"schema": 1, "id": "year-choice", "revision": 2, "at": "2041-02-05T10:15:00Z",
         "text": "Keep the year filter.\nShow the selected year.",
         "updates": [{"id": "year", "status": "answered", "answer": "Year filter retained."}]},
        {"schema": 1, "id": "final-choice", "revision": 3, "at": "2041-02-07",
         "text": "Exclude publisher. Approve the filter design.",
         "updates": [{"id": "publisher", "status": "withdrawn", "answer": "Publisher filter excluded."}],
         "approval": restored["approval"]},
    ])
    original_markdown = ("\ufeff# Recorded library discussion\r\n\r\n" +
                         "\r\n\r\n".join(item["text"].replace("\n", "\r\n") for item in restored["answers"]) +
                         "\r\n\r\n## Approved filter design\r\nAuthor and year filters.\r\n").encode("utf-8")
    (tmp_path / "ask-library.md").write_bytes(original_markdown)
    evidence = tmp_path / "saved-original.md"
    evidence.write_bytes(original_markdown)
    reviewed = tmp_path / "reviewed.json"

    def run(record=restored, **options):
        reviewed.write_bytes(json.dumps(record, ensure_ascii=False).encode("utf-8"))
        arguments = {"input_path": reviewed, "source": evidence, "expected_revision": 3,
                     "expected_sha256": hashlib.sha256(original_json).hexdigest()}
        arguments.update(options)
        return restore_document(tmp_path, **arguments)

    return current, restored, original_json, original_markdown, evidence, reviewed, run


def assert_recovery_originals(tmp_path, recovery):
    assert source_path(tmp_path, "ask-library.md").read_bytes() == recovery[2]
    assert (tmp_path / "ask-library.md").read_bytes() == recovery[3]


def test_restore_preserves_historical_dates_revisions_and_byte_snapshots(tmp_path, recovery):
    current, restored, original_json, original_markdown, _, reviewed, run = recovery
    assert run() == restored
    saved = read_document(tmp_path, "ask-library.md")
    assert saved["revision"] == 3 and saved["answers"][:1] == current["answers"]
    assert [(entry["revision"], entry["at"]) for entry in saved["answers"]] == [
        (1, "2041-02-03"), (2, "2041-02-05T10:15:00Z"), (3, "2041-02-07")]
    history = tmp_path / "questions" / "history" / "ask-library" / "recovery"
    snapshot, = history.iterdir()
    assert (snapshot / "before.json").read_bytes() == original_json
    assert (snapshot / "before.md").read_bytes() == original_markdown
    assert (snapshot / "input.json").read_bytes() == reviewed.read_bytes()
    receipt = json.loads((snapshot / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "complete" and receipt["repaired_at"]
    for name, digest in receipt["sha256"].items():
        assert hashlib.sha256((snapshot / name).read_bytes()).hexdigest() == digest
    assert (snapshot / "after.json").read_bytes() == source_path(tmp_path, "ask-library.md").read_bytes()
    assert not inspect_document(tmp_path, "ask-library.md", (tmp_path / "ask-library.md").read_text(encoding="utf-8"))[1]
    with pytest.raises(QuestionRecordError, match="expected_sha256"):
        run()
    assert list(history.iterdir()) == [snapshot]
    with pytest.raises(QuestionRecordError, match="현재 정본과 같음"):
        run(expected_sha256=receipt["sha256"]["after.json"], source=snapshot / "after.md")


@pytest.mark.parametrize("change, message", [
    (lambda record: record["answers"][0].update(at="2041-02-04"), "앞부분"),
    (lambda record: record["answers"].pop(0), "앞부분"),
    (lambda record: record.pop("legacy_markdown"), "legacy_markdown"),
    (lambda record: record.update(legacy_markdown="Changed original"), "legacy_markdown"),
    (lambda record: record["items"].pop(0), "문항 id"),
    (lambda record: record.update(revision=4), "id와 revision"),
    (lambda record: record["answers"][1].update(text="Use a subject filter instead."), "원문"),
    (lambda record: record["answers"][1].update(updates=[]), "추가 이력"),
    (lambda record: record["answers"][2].update(updates=[]), "추가 이력"),
    (lambda record: record["answers"][2].pop("approval"), "승인 변경"),
    (lambda record: record["items"][0].update(status="pending", answer=""), "추가 이력"),
    (lambda record: record["items"][0].update(answer="Author filter excluded."), "추가 이력"),
    (lambda record: record["answers"][1]["updates"].append({"id": "missing", "status": "answered", "answer": "Include."}), "대상 문항"),
])
def test_restore_rejects_lost_history_and_unbacked_outcomes(tmp_path, recovery, change, message):
    restored = copy.deepcopy(recovery[1])
    change(restored)
    with pytest.raises(QuestionRecordError, match=message):
        recovery[-1](restored)
    assert_recovery_originals(tmp_path, recovery)
    assert not (tmp_path / "questions" / "history").exists()


@pytest.mark.parametrize("options, message", [
    ({"expected_revision": 2}, "expected_revision"),
    ({"expected_sha256": "0" * 64}, "expected_sha256"),
])
def test_restore_snapshot_preconditions_reject_before_backups(tmp_path, recovery, options, message):
    with pytest.raises(QuestionRecordError, match=message):
        recovery[-1](**options)
    assert_recovery_originals(tmp_path, recovery)
    assert not (tmp_path / "questions" / "history").exists()


def test_restore_source_requires_exact_bytes(tmp_path, recovery):
    recovery[4].write_bytes(recovery[3].replace(b"\r\n", b"\n"))
    with pytest.raises(QuestionRecordError, match="source"):
        recovery[-1]()
    assert_recovery_originals(tmp_path, recovery)


@pytest.mark.parametrize("target", ["ask-library.md", "questions/ask-library.json"])
def test_restore_requires_both_live_files(tmp_path, recovery, target):
    (tmp_path / target).unlink()
    with pytest.raises(QuestionRecordError, match="기존 JSON과 Markdown"):
        recovery[-1]()


@pytest.mark.parametrize("target", ["ask-library.md", "questions/ask-library.json"])
def test_restore_rechecks_snapshot_after_backups(tmp_path, recovery, monkeypatch, target):
    write_json = question_records.write_json
    live = tmp_path / target
    external = b"External writer changed this file.\n"

    def change_live_after_receipt(path, value):
        write_json(path, value)
        if path.name == "receipt.json":
            live.write_bytes(external)

    monkeypatch.setattr(question_records, "write_json", change_live_after_receipt)
    with pytest.raises(QuestionRecordError, match="준비 중"):
        recovery[-1]()
    assert live.read_bytes() == external
    unchanged = tmp_path / ("questions/ask-library.json" if target.endswith(".md") else "ask-library.md")
    assert unchanged.read_bytes() == recovery[2 if target.endswith(".md") else 3]


@pytest.mark.parametrize("target", ["ask-library.md", "ask-library.json"])
def test_restore_write_failure_rolls_back_pair_and_retains_backups(tmp_path, recovery, monkeypatch, target):
    write_bytes = question_records.write_bytes

    def fail_live_once(path, value):
        if path.name == target and "recovery" not in path.parts:
            raise OSError("Simulated live write failure")
        write_bytes(path, value)

    monkeypatch.setattr(question_records, "write_bytes", fail_live_once)
    with pytest.raises(QuestionRecordError, match="원본 복원됨"):
        recovery[-1]()
    assert_recovery_originals(tmp_path, recovery)
    snapshot, = (tmp_path / "questions" / "history" / "ask-library" / "recovery").iterdir()
    assert (snapshot / "before.json").read_bytes() == recovery[2]
    assert (snapshot / "before.md").read_bytes() == recovery[3]
    assert json.loads((snapshot / "receipt.json").read_text(encoding="utf-8"))["status"] == "rolled_back"


def test_restore_does_not_overwrite_external_edit_during_rollback(tmp_path, recovery, monkeypatch):
    write_bytes = question_records.write_bytes
    path = source_path(tmp_path, "ask-library.md")

    def fail_markdown_after_external_edit(target, value):
        if target == tmp_path / "ask-library.md":
            path.write_bytes(b"External change\n")
            raise OSError("Simulated write failure")
        write_bytes(target, value)

    monkeypatch.setattr(question_records, "write_bytes", fail_markdown_after_external_edit)
    with pytest.raises(QuestionRecordError, match="롤백 확인 필요"):
        recovery[-1]()
    assert path.read_bytes() == b"External change\n"
    assert (tmp_path / "ask-library.md").read_bytes() == recovery[3]


def test_restore_preserves_publish_and_answer_protections(tmp_path, recovery):
    restored = recovery[1]
    with pytest.raises(QuestionRecordError, match="revision"):
        publish_document(tmp_path, restored, 3)
    newer = {**restored, "revision": 4}
    with pytest.raises(QuestionRecordError, match="이력 변경은 answer"):
        publish_document(tmp_path, newer, 3)
    with pytest.raises(QuestionRecordError, match="revision"):
        answer_document(tmp_path, "ask-library.md", restored["answers"][1])
    with pytest.raises(QuestionRecordError, match="대상 문항이 없음"):
        answer_document(tmp_path, "ask-library.md", restored["answers"][2])
    assert_recovery_originals(tmp_path, recovery)


def test_restore_backup_failure_keeps_live_pair_and_retry_uses_new_history(tmp_path, recovery, monkeypatch):
    write_bytes = question_records.write_bytes

    def fail_before_markdown(path, value):
        if path.name == "before.md":
            raise OSError("Simulated backup write failure")
        write_bytes(path, value)

    monkeypatch.setattr(question_records, "write_bytes", fail_before_markdown)
    with pytest.raises(QuestionRecordError, match="백업 저장 실패"):
        recovery[-1]()
    assert_recovery_originals(tmp_path, recovery)
    history = tmp_path / "questions" / "history" / "ask-library" / "recovery"
    failed, = history.iterdir()
    assert (failed / "before.json").read_bytes() == recovery[2]
    monkeypatch.setattr(question_records, "write_bytes", write_bytes)
    recovery[-1]()
    assert len(list(history.iterdir())) == 2
    assert (failed / "before.json").read_bytes() == recovery[2]


def test_restore_cannot_add_legacy_markdown_when_absent(tmp_path, recovery):
    current = copy.deepcopy(recovery[0])
    current.pop("legacy_markdown")
    path = source_path(tmp_path, "ask-library.md")
    path.write_bytes(json.dumps(current).encode("utf-8"))
    before = path.read_bytes()
    with pytest.raises(QuestionRecordError, match="legacy_markdown"):
        recovery[-1](expected_sha256=hashlib.sha256(before).hexdigest())
    assert path.read_bytes() == before
    assert (tmp_path / "ask-library.md").read_bytes() == recovery[3]


def test_restore_accepts_evidenced_downgrade_and_rejects_mismatched_final_approval(tmp_path, recovery):
    restored = copy.deepcopy(recovery[1])
    restored["items"][0].update(status="pending", answer="")
    restored["answers"][-1]["updates"].append({"id": "author", "status": "pending", "answer": ""})
    restored["answers"][-1]["text"] += " Reconsider the author filter."
    evidence = recovery[3] + b"\r\nExclude publisher. Approve the filter design. Reconsider the author filter.\r\n"
    recovery[4].write_bytes(evidence)
    (tmp_path / "ask-library.md").write_bytes(evidence)
    wrong = copy.deepcopy(restored)
    wrong["approval"] = {"status": "pending", "reason": "Another review."}
    with pytest.raises(QuestionRecordError, match="승인 변경"):
        recovery[-1](wrong)
    assert recovery[-1](restored) == restored


def test_restore_history_collision_never_overwrites_snapshot(tmp_path, recovery, monkeypatch):
    fixed = datetime(2041, 2, 8, tzinfo=timezone.utc)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz):
            return fixed

    monkeypatch.setattr(question_records, "datetime", FixedDatetime)
    monkeypatch.setattr(question_records.uuid, "uuid4", lambda: SimpleNamespace(hex="fixed-id"))
    history = tmp_path / "questions" / "history" / "ask-library" / "recovery" / "20410208T000000000000Z-fixed-id"
    history.mkdir(parents=True)
    snapshot = history / "before.json"
    snapshot.write_bytes(b"Previously preserved bytes\n")
    with pytest.raises(QuestionRecordError, match="백업 저장 실패"):
        recovery[-1]()
    assert snapshot.read_bytes() == b"Previously preserved bytes\n"
    assert list(history.iterdir()) == [snapshot]
    assert_recovery_originals(tmp_path, recovery)


def test_restore_receipt_completion_failure_rolls_back_pair(tmp_path, recovery, monkeypatch):
    write_json = question_records.write_json

    def fail_completion(path, value):
        if path.name == "receipt.json" and value["status"] == "complete":
            raise OSError("Simulated receipt failure")
        write_json(path, value)

    monkeypatch.setattr(question_records, "write_json", fail_completion)
    with pytest.raises(QuestionRecordError, match="원본 복원됨"):
        recovery[-1]()
    assert_recovery_originals(tmp_path, recovery)
    snapshot, = (tmp_path / "questions" / "history" / "ask-library" / "recovery").iterdir()
    assert json.loads((snapshot / "receipt.json").read_text(encoding="utf-8"))["status"] == "rolled_back"


def test_restore_preserves_markdown_edit_between_live_replacements(tmp_path, recovery, monkeypatch):
    write_bytes = question_records.write_bytes
    path = source_path(tmp_path, "ask-library.md")
    markdown = tmp_path / "ask-library.md"
    external_markdown = b"# Library filters\n\nKeep the subject filter too.\n"

    def edit_markdown_after_json(target, value):
        write_bytes(target, value)
        if target == path and value != recovery[2]:
            markdown.write_bytes(external_markdown)

    monkeypatch.setattr(question_records, "write_bytes", edit_markdown_after_json)
    with pytest.raises(QuestionRecordError, match="현재 파일이 바뀜"):
        recovery[-1]()
    assert path.read_bytes() == recovery[2]
    assert markdown.read_bytes() == external_markdown
    snapshot, = (tmp_path / "questions" / "history" / "ask-library" / "recovery").iterdir()
    assert (snapshot / "before.md").read_bytes() == recovery[3]
    assert json.loads((snapshot / "receipt.json").read_text(encoding="utf-8"))["status"] == "rollback_failed"


def test_restore_revalidates_containment_before_replacement_and_rollback(tmp_path, recovery, monkeypatch):
    contained_path = question_records.contained_path
    write_bytes = question_records.write_bytes
    path = source_path(tmp_path, "ask-library.md")
    json_replaced = False
    rechecked = []

    def invalidate_markdown_after_json(folder, *parts):
        if json_replaced:
            rechecked.append(parts)
            if parts == ("ask-library.md",):
                raise QuestionRecordError("미션 밖 질문 경로: changed Markdown target")
        return contained_path(folder, *parts)

    def track_json_replacement(target, value):
        nonlocal json_replaced
        write_bytes(target, value)
        if target == path:
            json_replaced = True

    monkeypatch.setattr(question_records, "contained_path", invalidate_markdown_after_json)
    monkeypatch.setattr(question_records, "write_bytes", track_json_replacement)
    with pytest.raises(QuestionRecordError, match="미션 밖 질문 경로"):
        recovery[-1]()
    assert_recovery_originals(tmp_path, recovery)
    assert ("questions", "ask-library.json") in rechecked
    assert ("ask-library.md",) in rechecked


@pytest.mark.parametrize("archive_existed", [True, False])
def test_restore_backs_up_generated_history_and_can_create_it_for_older_records(tmp_path, recovery, archive_existed):
    archive = question_records.history_path(tmp_path, "ask-library.md")
    original = archive.read_bytes()
    if not archive_existed:
        archive.unlink()
        original = b""
    recovery[-1]()
    snapshot, = (tmp_path / "questions/history/ask-library/recovery").iterdir()
    receipt = json.loads((snapshot / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["before_history_existed"] == archive_existed
    assert (snapshot / "before-history.md").read_bytes() == original
    assert (snapshot / "after-history.md").read_bytes() == archive.read_bytes()
    assert question_records.validate_history_archive(archive, recovery[0])
    assert question_records.validate_history_archive(archive, recovery[1])
    assert "## Approved filter design" in archive.read_text(encoding="utf-8")


@pytest.mark.parametrize("archive_existed", [True, False])
def test_restore_completion_failure_restores_history_bytes_or_removes_new_file(tmp_path, recovery, monkeypatch, archive_existed):
    archive = question_records.history_path(tmp_path, "ask-library.md")
    original = archive.read_bytes()
    if not archive_existed:
        archive.unlink()
    write_json = question_records.write_json

    def fail_completion(path, value):
        if path.name == "receipt.json" and value["status"] == "complete":
            raise OSError("Simulated completion failure")
        write_json(path, value)

    monkeypatch.setattr(question_records, "write_json", fail_completion)
    with pytest.raises(QuestionRecordError, match="원본 복원됨"):
        recovery[-1]()
    assert_recovery_originals(tmp_path, recovery)
    assert archive.exists() == archive_existed
    if archive_existed:
        assert archive.read_bytes() == original


def test_restore_detects_external_history_change_after_backups(tmp_path, recovery, monkeypatch):
    archive = question_records.history_path(tmp_path, "ask-library.md")
    write_json = question_records.write_json

    def change_archive(path, value):
        write_json(path, value)
        if path.name == "receipt.json":
            archive.write_bytes(b"External history edit")

    monkeypatch.setattr(question_records, "write_json", change_archive)
    with pytest.raises(QuestionRecordError, match="준비 중 현재 전체 이력"):
        recovery[-1]()
    assert archive.read_bytes() == b"External history edit"
    assert_recovery_originals(tmp_path, recovery)


def test_restore_history_write_failure_rolls_back_other_targets(tmp_path, recovery, monkeypatch):
    archive = question_records.history_path(tmp_path, "ask-library.md")
    original = archive.read_bytes()
    write_bytes = question_records.write_bytes

    def fail_history(path, value):
        if path == archive:
            raise OSError("Simulated history write failure")
        write_bytes(path, value)

    monkeypatch.setattr(question_records, "write_bytes", fail_history)
    with pytest.raises(QuestionRecordError, match="원본 복원됨"):
        recovery[-1]()
    assert_recovery_originals(tmp_path, recovery)
    assert archive.read_bytes() == original
