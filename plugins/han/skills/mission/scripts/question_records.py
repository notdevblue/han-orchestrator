import copy
import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path

from question_summary import QuestionItem, QuestionSummary


class QuestionRecordError(ValueError):
    pass


DOCUMENT_ID = re.compile(r"(?:ask|confirm)-[A-Za-z0-9][A-Za-z0-9_.-]*")
RECORD_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
ITEM_STATUSES = ("pending", "answered", "withdrawn")
APPROVAL_STATUSES = ("none", "pending", "complete")
STATUS_LABELS = {"pending": "답변 대기", "answered": "답변 완료", "withdrawn": "철회"}
APPROVAL_LABELS = {"none": "없음", "pending": "승인 대기", "complete": "승인 완료"}
DOCUMENT_FIELDS = ("schema", "id", "revision", "title", "intro", "items", "body", "approval", "answers")
ITEM_FIELDS = ("id", "title", "body", "status", "answer")
UPDATE_FIELDS = ("id", "status", "answer")
ANSWER_FIELDS = ("schema", "id", "revision", "at", "text", "updates")


def validate_identifier(value, label, pattern=RECORD_ID):
    if not isinstance(value, str) or not pattern.fullmatch(value) or ".." in value or value.endswith("."):
        raise QuestionRecordError(f"{label} 형식 오류: {value}")
    return value


def document_id(name):
    if not isinstance(name, str) or not name.endswith(".md"):
        raise QuestionRecordError(f"질문 문서는 루트 Markdown 이름이어야 함: {name}")
    return validate_identifier(name[:-3], "문서 id", DOCUMENT_ID)


def contained_path(folder, *parts):
    root = Path(folder).resolve()
    path = root.joinpath(*parts)
    try:
        resolved = path.resolve()
        if not resolved.is_relative_to(root) or resolved == root:
            raise QuestionRecordError(f"미션 밖 질문 경로: {path}")
        if any(part.startswith(".") for part in resolved.relative_to(root).parts):
            raise QuestionRecordError(f"숨김 질문 경로: {path}")
    except (OSError, RuntimeError) as error:
        raise QuestionRecordError(f"질문 경로 확인 실패: {path}: {error}") from error
    return path


def source_path(folder, name):
    identifier = document_id(name)
    return contained_path(folder, "questions", f"{identifier}.json")


def document_names(folder):
    directory = contained_path(folder, "questions")
    if not directory.exists():
        return []
    if not directory.is_dir():
        raise QuestionRecordError(f"질문 저장소가 폴더가 아님: {directory}")
    names = []
    for path in sorted(directory.glob("*.json")):
        name = f"{path.stem}.md"
        source_path(folder, name)
        names.append(name)
    return names


def required_text(value, field, nonempty=False):
    if not isinstance(value, str) or (nonempty and not value.strip()):
        raise QuestionRecordError(f"{field} 문자열이 필요함")
    return value


def validate_fields(value, required, optional=(), label="기록"):
    if not isinstance(value, dict):
        raise QuestionRecordError(f"{label} 객체가 필요함")
    missing = set(required) - set(value)
    unknown = set(value) - set(required) - set(optional)
    if missing:
        raise QuestionRecordError(f"{label} 필수 필드 없음: {', '.join(sorted(missing))}")
    if unknown:
        raise QuestionRecordError(f"{label} 알 수 없는 필드: {', '.join(sorted(str(field) for field in unknown))}")


def validate_revision(value):
    if type(value) is not int or value < 1:
        raise QuestionRecordError("revision은 양의 정수여야 함")
    return value


def validate_approval(value):
    validate_fields(value, ("status", "reason"), label="approval")
    if value.get("status") not in APPROVAL_STATUSES:
        raise QuestionRecordError("approval.status 형식 오류")
    required_text(value.get("reason"), "approval.reason", value["status"] == "pending")


def validate_item(value, label="문항"):
    if not isinstance(value, dict):
        raise QuestionRecordError(f"{label} 객체가 필요함")
    validate_identifier(value.get("id"), f"{label} id")
    if value.get("status") not in ITEM_STATUSES:
        raise QuestionRecordError(f"{label} status 형식 오류")
    required_text(value.get("answer"), f"{label} answer", value["status"] != "pending")


def validate_answer(value):
    validate_fields(value, ANSWER_FIELDS, ("approval",), "답 기록")
    if type(value.get("schema")) is not int or value["schema"] != 1:
        raise QuestionRecordError("답 기록 schema는 1이어야 함")
    validate_identifier(value.get("id"), "답 기록 id")
    validate_revision(value.get("revision"))
    at = required_text(value.get("at"), "답 기록 at", True)
    try:
        datetime.fromisoformat(at.replace("Z", "+00:00"))
    except ValueError as error:
        raise QuestionRecordError("답 기록 at은 ISO 날짜/시각이어야 함") from error
    required_text(value.get("text"), "답 원문", True)
    updates = value.get("updates")
    if not isinstance(updates, list):
        raise QuestionRecordError("답 기록 updates 배열이 필요함")
    identifiers = set()
    for update in updates:
        validate_fields(update, UPDATE_FIELDS, label="답 갱신")
        validate_item(update, "답 갱신")
        if update["id"] in identifiers:
            raise QuestionRecordError(f"답 갱신 id 중복: {update['id']}")
        identifiers.add(update["id"])
    if "approval" in value:
        validate_approval(value["approval"])
    return value


def validate_document(record):
    validate_fields(record, DOCUMENT_FIELDS, ("legacy_markdown",), "질문")
    if type(record.get("schema")) is not int or record["schema"] != 1:
        raise QuestionRecordError("질문 schema는 1이어야 함")
    validate_identifier(record.get("id"), "문서 id", DOCUMENT_ID)
    validate_revision(record.get("revision"))
    required_text(record.get("title"), "title", True)
    required_text(record.get("intro"), "intro")
    required_text(record.get("body"), "body")
    validate_approval(record.get("approval"))
    items = record.get("items")
    if not isinstance(items, list) or not items:
        raise QuestionRecordError("items는 비어 있지 않은 배열이어야 함")
    identifiers = set()
    for item in items:
        validate_fields(item, ITEM_FIELDS, ("heading",), "문항")
        validate_item(item)
        required_text(item.get("title"), "문항 title", True)
        required_text(item.get("body"), "문항 body")
        if "heading" in item:
            heading = required_text(item["heading"], "문항 heading", True)
            if heading.splitlines() != [heading]:
                raise QuestionRecordError("문항 heading은 한 줄이어야 함")
        if item["id"] in identifiers:
            raise QuestionRecordError(f"문항 id 중복: {item['id']}")
        identifiers.add(item["id"])
    answers = record.get("answers")
    if not isinstance(answers, list):
        raise QuestionRecordError("answers 배열이 필요함")
    operations = set()
    for answer in answers:
        validate_answer(answer)
        if answer["id"] in operations:
            raise QuestionRecordError(f"답 기록 id 중복: {answer['id']}")
        if answer["revision"] > record["revision"]:
            raise QuestionRecordError("답 기록 revision이 문서 revision보다 큼")
        if answer["revision"] == record["revision"]:
            for update in answer["updates"]:
                if update["id"] not in identifiers:
                    raise QuestionRecordError(f"현재 개정 답 대상 문항이 없음: {update['id']}")
        operations.add(answer["id"])
    if "legacy_markdown" in record:
        required_text(record["legacy_markdown"], "legacy_markdown")
    return copy.deepcopy(record)


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise QuestionRecordError(f"질문 JSON 읽기 실패: {path}: {error}") from error


def read_document(folder, name):
    record = validate_document(read_json(source_path(folder, name)))
    if record["id"] != document_id(name):
        raise QuestionRecordError(f"질문 문서 id와 파일 이름 불일치: {name}")
    return record


def summarize_document(record):
    record = validate_document(record)
    items = []
    for item in record["items"]:
        status = "answered"
        if item["status"] == "pending":
            status = "pending"
        items.append(QuestionItem(item["id"], item["title"], status))
    pending = any(item.status == "pending" for item in items) or record["approval"]["status"] == "pending"
    return QuestionSummary(f"{record['id']}.md", record["title"], items, pending, True, True)


def render_document(record):
    record = validate_document(record)
    summary = summarize_document(record)
    approval = record["approval"]
    status = "답변 완료"
    if summary.pending:
        status = "답변 대기"
    sections = [f"# {record['title']}", f"문서: `{record['id']}` · 개정: {record['revision']}",
                f"상태: {status} · 미답 {summary.pending_count}/{len(summary.items)}",
                f"별도 승인 요청: {APPROVAL_LABELS[approval['status']]}"]
    if approval["reason"]:
        sections.append(approval["reason"])
    if record["intro"]:
        sections.append(record["intro"])
    sections.append("## 결정할 것")
    for item in record["items"]:
        heading = item.get("heading", f"{item['id']}. {item['title']}")
        sections.append(f"### {heading}")
        if item["body"]:
            sections.append(item["body"])
        sections.append(f"- 상태: {STATUS_LABELS[item['status']]}")
        if item["answer"]:
            sections.extend(["답 내용:", item["answer"]])
    if record["body"]:
        sections.append(record["body"])
    sections.append("## 답 원문 이력")
    for answer in record["answers"]:
        sections.extend([f"### {answer['id']} · {answer['at']} · 개정 {answer['revision']}", answer["text"]])
        for update in answer["updates"]:
            sections.extend([f"- 문항 {update['id']}: {STATUS_LABELS[update['status']]}", update["answer"]])
        if "approval" in answer:
            sections.extend([f"별도 승인 요청: {APPROVAL_LABELS[answer['approval']['status']]}", answer["approval"]["reason"]])
    return "\n\n".join(sections) + "\n"


def normalize_markdown(text):
    return text.replace("\r\n", "\n").replace("\r", "\n")


def inspect_document(folder, name, text_snapshot):
    try:
        record = read_document(folder, name)
        contained_path(folder, name)
        if not isinstance(text_snapshot, str) or normalize_markdown(text_snapshot) != normalize_markdown(render_document(record)):
            raise QuestionRecordError(f"생성 Markdown과 질문 JSON 불일치: {name}")
        return summarize_document(record), ""
    except (QuestionRecordError, OSError) as error:
        return QuestionSummary(name, name, [], False, False, False), str(error)


def write_text(path, text):
    temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path, value):
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def render_saved_document(folder, name):
    record = read_document(folder, name)
    path = contained_path(folder, name)
    try:
        write_text(path, render_document(record))
        snapshot = path.read_text(encoding="utf-8")
    except OSError as error:
        raise QuestionRecordError(f"질문 Markdown 저장 실패: {path}: {error}") from error
    if read_document(folder, name) != record:
        raise QuestionRecordError(f"질문 렌더 중 JSON이 바뀜: {name}")
    _, issue = inspect_document(folder, name, snapshot)
    if issue:
        raise QuestionRecordError(issue)
    return record


def validate_publication(current, record, editorial):
    current_items = {item["id"]: item for item in current["items"]}
    items = {item["id"]: item for item in record["items"]}
    if editorial:
        if record.get("legacy_markdown", "") != current.get("legacy_markdown", "") or ("legacy_markdown" in record) != ("legacy_markdown" in current):
            raise QuestionRecordError("editorial 발행은 legacy_markdown을 변경할 수 없음")
        if set(current_items) != set(items):
            raise QuestionRecordError("editorial 발행은 문항 id 집합을 변경할 수 없음")
        for identifier, item in items.items():
            previous = current_items[identifier]
            if item["status"] != previous["status"] or item["answer"] != previous["answer"]:
                raise QuestionRecordError("editorial 발행은 문항 상태나 답 내용을 변경할 수 없음")
        if record["approval"] != current["approval"] or record["answers"] != current["answers"]:
            raise QuestionRecordError("editorial 발행은 승인이나 답 이력을 변경할 수 없음")
        return
    if record["approval"] != current["approval"] and record["approval"]["status"] != "pending":
        raise QuestionRecordError("승인 대기 해소는 answer로 기록해야 함")
    for identifier, item in items.items():
        if identifier not in current_items:
            if item["status"] == "answered":
                raise QuestionRecordError(f"새 문항은 answered로 발행할 수 없음: {identifier}")
            continue
        previous = current_items[identifier]
        definition_changed = any(item.get(field, "") != previous.get(field, "") for field in ("title", "body", "heading"))
        if definition_changed:
            if item["status"] == "answered":
                raise QuestionRecordError(f"내용이 바뀐 문항은 answered로 발행할 수 없음: {identifier}")
            continue
        answer_changed = item["status"] != previous["status"] or item["answer"] != previous["answer"]
        if answer_changed and (item["status"] != "pending" or item["answer"] != ""):
            raise QuestionRecordError(f"기존 문항 답 변경은 answer로 기록해야 함: {identifier}")
    scope_changed = record["intro"] != current["intro"] or record["body"] != current["body"]
    if scope_changed and any(item["status"] != "pending" for item in record["items"]):
        if record["approval"]["status"] != "pending":
            raise QuestionRecordError("전체 범위가 바뀌면 해결된 문항을 유지하기 전에 approval 대기가 필요함")


def publish_document(folder, record, expected_revision="", editorial=False):
    if type(editorial) is not bool:
        raise QuestionRecordError("editorial은 bool이어야 함")
    record = validate_document(record)
    name = f"{record['id']}.md"
    path = source_path(folder, name)
    markdown_path = contained_path(folder, name)
    try:
        if path.exists():
            current = read_document(folder, name)
            if record == current:
                return render_saved_document(folder, name)
            if type(expected_revision) is not int or expected_revision != current["revision"]:
                raise QuestionRecordError("질문 expected_revision이 현재 revision과 다름")
            if record["revision"] <= current["revision"]:
                raise QuestionRecordError("변경한 질문 revision은 현재보다 커야 함")
            if record["answers"] != current["answers"]:
                raise QuestionRecordError("답 원문 이력 변경은 answer로 기록해야 함")
            if "legacy_markdown" in current and record.get("legacy_markdown") != current["legacy_markdown"]:
                raise QuestionRecordError("기존 legacy_markdown을 삭제하거나 변경할 수 없음")
            validate_publication(current, record, editorial)
            history = contained_path(folder, "questions", "history", record["id"], f"{current['revision']}.json")
            history.parent.mkdir(parents=True, exist_ok=True)
            if history.exists():
                if read_json(history) != current:
                    raise QuestionRecordError(f"질문 개정 이력 충돌: {history}")
            else:
                write_json(history, current)
        else:
            if editorial:
                raise QuestionRecordError("editorial 발행에는 기존 질문이 필요함")
            if expected_revision != "":
                raise QuestionRecordError("새 질문에는 expected_revision을 지정할 수 없음")
            if markdown_path.exists():
                try:
                    legacy_markdown = markdown_path.read_bytes().decode("utf-8")
                except UnicodeError as error:
                    raise QuestionRecordError(f"기존 Markdown UTF-8 형식 오류: {name}") from error
                if record.get("legacy_markdown") != legacy_markdown:
                    raise QuestionRecordError(f"기존 Markdown 보호: legacy_markdown이 없거나 원문이 바뀜: {name}")
        path.parent.mkdir(parents=True, exist_ok=True)
        source_path(folder, name)
        write_json(path, record)
    except OSError as error:
        raise QuestionRecordError(f"질문 JSON 저장 실패: {path}: {error}") from error
    return render_saved_document(folder, name)


def answer_document(folder, name, payload):
    payload = copy.deepcopy(validate_answer(payload))
    record = read_document(folder, name)
    for previous in record["answers"]:
        if previous["id"] == payload["id"]:
            if previous != payload:
                raise QuestionRecordError(f"같은 답 기록 id의 내용이 다름: {payload['id']}")
            return render_saved_document(folder, name)
    if payload["revision"] != record["revision"]:
        raise QuestionRecordError("답 기록 revision이 현재 질문 revision과 다름")
    items = {item["id"]: item for item in record["items"]}
    for update in payload["updates"]:
        if update["id"] not in items:
            raise QuestionRecordError(f"답 대상 문항이 없음: {update['id']}")
        items[update["id"]].update(status=update["status"], answer=update["answer"])
    if "approval" in payload:
        record["approval"] = copy.deepcopy(payload["approval"])
    record["answers"].append(payload)
    path = source_path(folder, name)
    try:
        write_json(path, record)
    except OSError as error:
        raise QuestionRecordError(f"답 기록 JSON 저장 실패: {path}: {error}") from error
    return render_saved_document(folder, name)
