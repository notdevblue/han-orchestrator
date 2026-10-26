import copy
import json
import os
import sys

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
    assert payload["text"] in render_document(saved)
    assert "답 내용:\n\n권장" in render_document(saved)


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
    assert text.count("별도 승인 요청: 승인 완료") == 2
    assert "확인 A 전체 승인: 승인 완료" in text
    assert payload["text"] in text
    assert saved["answers"][0]["text"] == payload["text"]
    saved["approval"] = {"status": "none", "reason": ""}
    assert "별도 승인 요청: 없음" in render_document(saved)
    saved["approval"] = {"status": "pending", "reason": "추가 확인"}
    assert "별도 승인 요청: 승인 대기" in render_document(saved)


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
