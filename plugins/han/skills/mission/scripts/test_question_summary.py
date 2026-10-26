import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from question_summary import summarize_question


def question_text(state="답변 대기", answer=""):
    return (
        "# [해금 **클라**](ask-3.md): `CL2` 제출\n\n"
        f"{state} · 2026-10-22\n\n"
        "## 결정할 것\n\n"
        "### 1. CL1(427720)과 CL2(427727)를 지금 제출할까요?\n\n"
        "- 권장: 제출합니다.\n\n"
        "### 2. CL2 제출 뒤 M2b를 M18 대기에서 풀까요?\n\n"
        "- 권장: 풉니다.\n\n"
        "## 답\n\n"
        f"{answer}\n"
    )


def test_numbered_metadata_preserves_question_titles_and_remaining_item():
    result = summarize_question("ask-3.md", question_text("질문 1 답변(제출 보류) · 질문 2 대기"), True)
    assert result.name == "ask-3.md"
    assert result.title == "해금 클라: CL2 제출"
    assert [(item.number, item.status) for item in result.items] == [("1", "answered"), ("2", "pending")]
    assert result.items[1].title == "CL2 제출 뒤 M2b를 M18 대기에서 풀까요?"
    assert result.pending and result.count_known


def test_dated_answer_does_not_remove_explicit_remaining_question():
    text = question_text("질문 1 답변 · 질문 2 대기", "2026-10-22 질문 1: 제출 보류")
    result = summarize_question("ask-3.md", text, False)
    assert result.pending and result.count_known
    assert [item.status for item in result.items] == ["answered", "pending"]


@pytest.mark.parametrize("name", ["ask-3.md", "confirm-A3.md"])
@pytest.mark.parametrize("state", ["질문 1 답변 · 질문 2 답변 완료", "답변 완료"])
def test_all_explicit_answers_remove_document_pending(name, state):
    result = summarize_question(name, question_text(state), True)
    assert not result.pending and result.count_known
    assert all(item.status == "answered" for item in result.items)


def test_approved_confirm_overrides_old_waiting_metadata():
    result = summarize_question("confirm-A3.md", question_text(), False)
    assert not result.pending and result.count_known
    assert all(item.status == "answered" for item in result.items)


@pytest.mark.parametrize("marker", ["승인 대기", "전체 승인 확인 대기"])
@pytest.mark.parametrize("name", ["ask-3.md", "confirm-A3.md"])
def test_explicit_approval_wait_preserves_document_pending(name, marker):
    result = summarize_question(name, question_text(f"답변 완료 · {marker}"), True)
    assert result.pending and result.count_known
    assert all(item.status == "answered" for item in result.items)


@pytest.mark.parametrize("marker", ["승인 대기", "전체 승인 확인 대기"])
def test_approved_confirm_overrides_explicit_approval_wait(marker):
    result = summarize_question("confirm-A3.md", question_text(f"답변 완료 · {marker}"), False)
    assert not result.pending and result.count_known
    assert all(item.status == "answered" for item in result.items)


@pytest.mark.parametrize("name", ["ask-3.md", "confirm-A3.md"])
def test_global_answer_complete_without_decision_items_clears_pending(name):
    result = summarize_question(name, "# 후속 범위\n\n답변 완료\n\n## 답\n\n2026-10-23 권장대로\n", True)
    assert not result.pending and not result.total_known and not result.count_known


def test_duplicate_confirm_items_do_not_claim_complete_document():
    text = question_text("답변 완료").replace("### 2.", "### 1.")
    result = summarize_question("confirm-A3.md", text, True)
    assert result.pending and not result.total_known and not result.count_known


def test_unnumbered_decision_heading_counts_without_inventing_number():
    result = summarize_question("ask-1.md", "# **다음 작업**\n\n답변 대기\n\n## 결정할 것\n\n### 제출할까요?\n", True)
    assert result.title == "다음 작업"
    assert [(item.number, item.title, item.status) for item in result.items] == [("", "제출할까요?", "pending")]
    assert result.pending and result.count_known and result.total_known


@pytest.mark.parametrize("state", ["답변 접수", "답변 전달", "부분 답변", "답변 검토중"])
def test_receipt_partial_or_unrecognized_status_does_not_claim_answers(state):
    result = summarize_question("confirm-A3.md", question_text(f"질문 1 {state} · 질문 2 대기"), True)
    assert [item.status for item in result.items] == ["unknown", "pending"]
    assert result.pending and not result.count_known


def test_standard_waiting_document_without_answers_counts_all_items():
    result = summarize_question("ask-3.md", question_text("초안 · 답변 대기", "<!-- 날짜와 답 -->"), True)
    assert result.pending and result.count_known
    assert all(item.status == "pending" for item in result.items)


def test_unstructured_answer_is_not_guessed_from_numbers_or_approval_words():
    text = question_text("답변 대기", "2026-10-22 1번은 승인. 2번은 나중에 생각할게요.")
    result = summarize_question("ask-3.md", text, False)
    assert not result.pending and not result.count_known
    assert all(item.status == "unknown" for item in result.items)


def test_partial_explicit_metadata_does_not_fill_missing_statuses():
    result = summarize_question("ask-3.md", question_text("답변 대기 · 질문 1 답변"), True)
    assert [item.status for item in result.items] == ["answered", "unknown"]
    assert result.pending and not result.count_known


def test_comments_and_code_do_not_provide_headings_states_or_answers():
    fake = (
        "<!-- # 가짜 제목\n질문 1 답변 · 질문 2 답변\n-->\n"
        "```markdown\n# 코드 제목\n질문 1 답변\n## 결정할 것\n### 9. 가짜 질문\n```\n"
    )
    text = fake + question_text("답변 대기", "~~~text\n2026-10-22 승인\n~~~")
    result = summarize_question("ask-3.md", text, True)
    assert result.title == "해금 클라: CL2 제출"
    assert [item.number for item in result.items] == ["1", "2"]
    assert result.pending and result.count_known


def test_approval_words_in_question_content_are_not_status():
    text = question_text().replace("- 권장: 제출합니다.", "- 권장: 승인합니다. 질문 1 답변 · 질문 2 답변")
    result = summarize_question("confirm-A3.md", text, True)
    assert result.pending and result.count_known
    assert all(item.status == "pending" for item in result.items)


def test_explicit_item_answer_fields_are_counted_without_parsing_prose():
    text = question_text("").replace("- 권장: 제출합니다.", "- 답: 답변 완료").replace("- 권장: 풉니다.", "- 답: 미결")
    result = summarize_question("ask-3.md", text, False)
    assert [item.status for item in result.items] == ["answered", "pending"]
    assert result.pending and result.count_known


def test_conflicting_explicit_metadata_remains_unknown():
    result = summarize_question("ask-3.md", question_text("질문 1 답변 · 질문 1 대기 · 질문 2 대기"), True)
    assert [item.status for item in result.items] == ["unknown", "pending"]
    assert result.pending and not result.count_known


def test_only_decision_section_questions_are_counted():
    text = question_text() + "\n## 이전 이력\n\n### 3. 이전 질문\n"
    result = summarize_question("ask-3.md", text, True)
    assert [item.number for item in result.items] == ["1", "2"]
    assert result.count_known


def test_missing_title_uses_name_and_duplicate_numbers_make_count_unknown():
    text = question_text().replace("# [해금 **클라**](ask-3.md): `CL2` 제출\n", "").replace("### 2.", "### 1.")
    result = summarize_question("ask-3.md", text, True)
    assert result.title == "ask-3.md"
    assert not result.count_known and not result.total_known


def test_title_markup_becomes_plain_text():
    result = summarize_question("ask-3.md", "# <b>상태</b> &amp; **[기준](https://example.com)**\n", True)
    assert result.title == "상태 & 기준"
    assert not result.count_known


def test_title_preserves_literal_code_conditions_and_language_suffix():
    result = summarize_question("ask-3.md", "# `List<T>`에서 값 < 10 조건을 쓸까요? C#\n", True)
    assert result.title == "List<T>에서 값 < 10 조건을 쓸까요? C#"


def test_partial_global_status_does_not_expand_old_waiting_into_all_pending():
    result = summarize_question("ask-3.md", question_text("답변 대기 · 부분 답변"), True)
    assert all(item.status == "unknown" for item in result.items)
    assert not result.count_known


def test_partial_answer_annotation_is_unknown():
    result = summarize_question("ask-3.md", question_text("질문 1 답변(일부) · 질문 2 대기"), True)
    assert [item.status for item in result.items] == ["unknown", "pending"]
    assert not result.count_known


def test_metadata_for_missing_question_makes_count_unknown():
    result = summarize_question("ask-3.md", question_text("질문 1 답변 · 질문 2 답변 · 질문 3 대기"), False)
    assert result.pending and not result.count_known and not result.total_known


def test_comment_markers_in_code_and_fences_in_comments_do_not_hide_real_content():
    hidden = "<!--\n```\n# 가짜 제목\n-->\n```text\n<!--\n```\n"
    result = summarize_question("ask-3.md", hidden + question_text(), True)
    assert result.title == "해금 클라: CL2 제출"
    assert len(result.items) == 2 and result.count_known


def test_unclosed_comment_hides_its_remaining_headings_and_states():
    result = summarize_question("ask-3.md", "# 원래 제목\n<!--\n" + question_text("답변 완료"), True)
    assert result.title == "원래 제목"
    assert result.items == [] and not result.count_known and result.pending


@pytest.mark.parametrize("indent", ["    ", "\t", "  \t"])
def test_indented_code_does_not_supply_states_headings_or_dates(indent):
    text = question_text().replace(
        "답변 대기 · 2026-10-22\n",
        f"답변 대기 · 2026-10-22\n\n{indent}질문 1 답변 · 질문 2 답변\n\n{indent}### 3. 가짜 질문\n",
    )
    text = text.replace("- 권장: 제출합니다.", f"{indent}- 답: 답변 완료")
    text += f"\n{indent}2026-10-22 승인\n"
    result = summarize_question("ask-3.md", text, True)
    assert [item.status for item in result.items] == ["pending", "pending"]
    assert result.pending and result.count_known


def test_three_known_questions_with_unknown_answers_keep_total_separate():
    text = (
        "# 단축키 도구 역할 확장\n\n답변 대기\n\n## 결정할 것\n\n"
        "### 1) 도구 범위를 넓힐까요?\n\n### 2. 서버 코드를 포함할까요?\n\n### 3) 진행 순서를 바꿀까요?\n\n"
        "## 답\n\n2026-10-23 사용자가 범위를 답변함\n"
    )
    result = summarize_question("ask-1.md", text, False)
    assert result.total_known and not result.count_known
    assert len(result.items) == 3 and result.pending_count == 0
    assert [item.status for item in result.items] == ["unknown"] * 3
    assert not result.pending


def test_legacy_a2_headings_preserve_labels_and_context_for_six_questions():
    titles = [
        "(정의 데이터 위치). 어디에 둘까요?",
        "(입장 조건). 어떤 값을 쓸까요?",
        "(응답 구조). 무엇을 내려줄까요?",
        "(보상 지급). 어느 시점에 줄까요?",
        "(오류 처리). 어떤 규약을 쓸까요?",
        "(검증 범위). 어디까지 확인할까요?",
    ]
    headings = "\n\n".join(f"### A2 결정 {number} {title}" for number, title in enumerate(titles, 1))
    result = summarize_question("confirm-A2.md", f"# 던전 설계 확인\n\n답변 대기\n\n## 결정할 것\n\n{headings}\n", True)
    assert result.total_known and result.count_known and result.pending_count == 6
    assert [item.number for item in result.items] == [f"A2 결정 {number}" for number in range(1, 7)]
    assert [item.title for item in result.items] == titles


def test_legacy_numbers_match_metadata_and_collide_with_plain_number_ids():
    text = "# 던전 설계\n\n질문 1 답변 · 질문 2 대기\n\n## 결정할 것\n\n### A2 결정 1 (정의 데이터 위치). 어디에 둘까요?\n\n### 2. 입장 조건\n"
    result = summarize_question("confirm-A2.md", text, True)
    assert result.total_known and result.count_known
    assert [item.status for item in result.items] == ["answered", "pending"]
    duplicate = summarize_question("confirm-A2.md", text.replace("### 2.", "### 1."), True)
    assert not duplicate.total_known and not duplicate.count_known


def test_current_numbered_and_sync_questions_exclude_resolved_history():
    text = (
        "# 던전 질문\n\n답변 대기 · 부분 답변\n\n## 결정할 것\n\n"
        "### 4. 기존 오류를 정리할까요?\n\n### 5) 코드를 최신에 맞출까요?\n\n"
        "### sync 승인: slot6 를 head 로 받을까요?\n\n"
        "## 답변 완료한 질문\n\n### 1. 이전 결정\n\n### 2. 이전 계약\n\n### 3. 이전 범위\n"
    )
    result = summarize_question("ask-1.md", text, True)
    assert result.total_known and not result.count_known and result.pending
    assert [item.number for item in result.items] == ["4", "5", ""]
    assert result.items[2].title == "sync 승인: slot6 를 head 로 받을까요?"


def test_several_unnumbered_headings_keep_independent_item_states():
    text = (
        "# 승인 질문\n\n## 결정할 것\n\n"
        "### sync 승인: 최신으로 받을까요?\n\n- 답: 답변 완료\n\n"
        "### resolve 승인: 충돌을 풀까요?\n\n- 답: 대기\n\n"
        "### 제출 승인: 제출할까요?\n\n- 답: 답변 접수\n"
    )
    result = summarize_question("ask-1.md", text, True)
    assert result.total_known and not result.count_known and result.pending_count == 1
    assert [item.number for item in result.items] == ["", "", ""]
    assert [item.status for item in result.items] == ["answered", "pending", "unknown"]


def test_multiple_decision_sections_do_not_claim_a_confident_total():
    result = summarize_question("ask-1.md", question_text() + "\n## 결정할 것\n\n### 추가 승인\n", True)
    assert len(result.items) == 3
    assert not result.total_known and not result.count_known


def test_unnumbered_headings_in_code_or_comments_do_not_supply_questions():
    text = (
        "# 질문\n\n답변 대기\n\n## 결정할 것\n\n"
        "<!-- ### 숨겨진 승인 -->\n\n```markdown\n### 가짜 sync 승인\n```\n\n"
        "    ### 들여쓴 승인\n\n### 실제 sync 승인\n"
    )
    result = summarize_question("ask-1.md", text, True)
    assert result.total_known and result.count_known
    assert [(item.number, item.title) for item in result.items] == [("", "실제 sync 승인")]


@pytest.mark.parametrize("pending", [True, False])
def test_explicit_unknown_item_states_keep_document_pending(pending):
    result = summarize_question("ask-1.md", question_text("답변 검토중"), pending)
    assert result.total_known and not result.count_known
    assert result.pending


@pytest.mark.parametrize("state", ["답변 접수", "답변 전달", "부분 답변", "질문 1 답변 · 질문 2 답변 접수", "질문 1 답변"])
def test_dated_answer_cannot_clear_explicit_partial_or_unknown_states(state):
    result = summarize_question("ask-1.md", question_text(state, "2026-10-23 사용자 답변"), False)
    assert result.pending and not result.count_known


@pytest.mark.parametrize("heading", ["답 없이 진행 중인 정리", "답변 완료한 질문", "답변 범위를 넓힐까요?"])
def test_answer_word_in_other_section_does_not_record_an_answer(heading):
    text = question_text().replace("## 답\n", f"## {heading}\n\n2026-10-23 답이 아닌 기록\n")
    result = summarize_question("ask-1.md", text, True)
    assert result.pending and result.count_known
    assert [item.status for item in result.items] == ["pending", "pending"]


def test_real_answer_after_fake_answer_heading_records_an_answer():
    text = question_text().replace("## 답\n", "## 답 없이 진행 중인 정리\n\n아직 답이 없음\n\n## 답\n\n2026-10-23 사용자 답변\n")
    result = summarize_question("ask-1.md", text, False)
    assert not result.pending and result.total_known and not result.count_known


@pytest.mark.parametrize("heading", ["답", "답변", "답 (2026-10-23)", "답변: 2026-10-23", "답변 2026-10-23", "답변 (2026.10.23 사용자)"])
def test_level_three_answer_sections_do_not_add_decisions(heading):
    text = (
        "# 질문\n\n답변 대기\n\n## 결정할 것\n\n### 1. 제출할까요?\n\n"
        f"### {heading}\n\n2026-10-23 사용자: 승인\n"
    )
    result = summarize_question("ask-1.md", text, False)
    assert result.total_known and not result.count_known
    assert [(item.number, item.title) for item in result.items] == [("1", "제출할까요?")]
    assert not result.pending


def test_level_three_answer_body_headings_do_not_resume_question_collection():
    text = (
        "# 질문\n\n답변 대기\n\n## 결정할 것\n\n### 1. 제출할까요?\n\n"
        "### 답\n\n2026-10-23 사용자: 승인\n\n"
        "#### 근거\n\n### 참고한 자료\n\n### 9. 답변의 예시 번호\n"
    )
    result = summarize_question("ask-1.md", text, False)
    assert result.total_known and len(result.items) == 1
    assert result.items[0].number == "1" and not result.count_known


@pytest.mark.parametrize("title", ["답 없이 진행해도 될까요?", "답변 범위를 넓힐까요?"])
def test_question_starting_with_answer_word_is_still_a_decision(title):
    text = (
        "# 질문\n\n답변 대기\n\n## 결정할 것\n\n### 1. 제출할까요?\n\n"
        f"### {title}\n\n- 권장: 대기합니다.\n\n"
        "### 답\n\n2026-10-23 사용자: 승인\n\n### 답변의 참고 자료\n"
    )
    result = summarize_question("ask-1.md", text, False)
    assert result.total_known and not result.count_known
    assert [(item.number, item.title) for item in result.items] == [("1", "제출할까요?"), ("", title)]
    assert not result.pending
