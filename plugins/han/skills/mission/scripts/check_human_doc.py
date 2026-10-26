import argparse
import json
import re
from datetime import datetime
from pathlib import Path

import question_records
from question_summary import answer_recorded, is_answer_section, markdown_lines, plain_text


def without_fences(text):
    lines = []
    fence = ""
    for line in text.splitlines():
        match = re.match(r"^\s*(`{3,}|~{3,})", line)
        if match:
            marker = match.group(1)[0]
            if not fence:
                fence = marker
            elif marker == fence:
                fence = ""
            continue
        if not fence:
            lines.append(line)
    return "\n".join(lines)


def answer(text):
    match = re.search(r"^## (?:답|답변)\s*\n", text, re.M)
    if match:
        return text[match.start():].strip()
    return ""


def answer_bodies(text):
    headings = []
    for match in re.finditer(r"(?m)^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*\r?$", text):
        visible = markdown_lines(text[:match.end()])
        if not visible or visible[-1] != match.group(0).rstrip("\r"):
            continue
        value = plain_text(re.sub(r"[ \t]+#+$", "", match.group(2)))
        headings.append((match.start(), match.end(), len(match.group(1)), value))
    bodies = []
    for index, (_, start, level, title) in enumerate(headings):
        if level not in (2, 3) or not is_answer_section(title):
            continue
        end = len(text)
        for next_start, _, next_level, _ in headings[index + 1:]:
            if next_level <= level:
                end = next_start
                break
        body = question_records.normalize_markdown(text[start:end]).strip("\n")
        visible = [plain_text(line).replace("답변대기", "답변 대기") for line in markdown_lines(body)]
        if answer_recorded(["## 답", *visible]):
            dates = re.findall(r"\d{4}[-./]\d{1,2}[-./]\d{1,2}", title)
            bodies.append((body, dates))
    return bodies


def history_date_preserved(value, answers):
    year, month, day = (int(part) for part in re.split(r"[-./]", value))
    normalized = f"{year:04}-{month:02}-{day:02}"
    for item in answers:
        if value in item["text"] or datetime.fromisoformat(item["at"].replace("Z", "+00:00")).date().isoformat() == normalized:
            return True
    return False


def read_document(path):
    if path.suffix.lower() == ".json":
        record = question_records.validate_document(question_records.read_json(path))
        return question_records.render_document(record), record
    return path.read_text(encoding="utf-8-sig"), {}


def check_record_history(record, source_record, issues):
    source_answers = source_record["answers"]
    if record["answers"][:len(source_answers)] != source_answers:
        issues.append("기존 답 원문 이력이 삭제되거나 달라졌습니다")
    if "legacy_markdown" in source_record and ("legacy_markdown" not in record or record["legacy_markdown"] != source_record["legacy_markdown"]):
        issues.append("기존 legacy_markdown이 삭제되거나 달라졌습니다")


def check_migration(record, text, source_path, issues):
    original = source_path.read_bytes().decode("utf-8")
    if "legacy_markdown" not in record or record["legacy_markdown"] != original:
        issues.append("legacy_markdown이 원문 Markdown과 다릅니다: BOM과 줄바꿈까지 그대로 보존하세요")
    rendered = question_records.normalize_markdown(text)
    for body, dates in answer_bodies(original):
        matching_answers = [item for item in record["answers"] if body in question_records.normalize_markdown(item["text"])]
        if not matching_answers or body not in rendered:
            issues.append("기존 답 절 본문이 답 원문 이력에서 빠지거나 달라졌습니다: 원문의 답과 날짜를 그대로 보존하세요")
        if any(not history_date_preserved(value, matching_answers) for value in dates):
            issues.append("기존 답 절 제목의 날짜가 답 원문 이력의 날짜나 원문에서 빠지거나 달라졌습니다")


def schema_tables(text):
    tables = []
    group = []
    for line in [*without_fences(text).splitlines(), ""]:
        if line.lstrip().startswith("|"):
            group.append(line.strip())
            continue
        if len(group) > 1:
            header = group[0]
            if re.search(r"타입|컬럼|요청|응답|필드|Enum|enum|값|자료형", header) and not re.search(r"파일|경로|검증 명령", header):
                tables.append("\n".join(group))
        group = []
    return tables


def prose_length(text):
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"(?m)^\s*\|.*(?:\n|$)", "", text)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]+\)", r"\1", text)
    return len(text.strip())


def check(path, source_path=None):
    text = ""
    issues = []
    try:
        text, record = read_document(path)
        if source_path:
            source, source_record = read_document(source_path)
            if path.suffix.lower() == ".json":
                if source_path.suffix.lower() == ".json":
                    check_record_history(record, source_record, issues)
                else:
                    check_migration(record, text, source_path, issues)
            elif source_path.suffix.lower() == ".json":
                if question_records.normalize_markdown(text) != question_records.normalize_markdown(source):
                    issues.append("생성 Markdown이 원본 질문 JSON의 렌더 결과와 다릅니다")
            elif answer(source) != answer(text):
                issues.append("기존 답 절이 달라졌습니다: 원문의 답과 날짜를 그대로 보존하세요")
            current_tables = schema_tables(text)
            for number, table in enumerate(schema_tables(source), 1):
                if table not in current_tables:
                    issues.append(f"원문 설계 표 {number}의 컬럼·타입·조건이 빠지거나 달라졌습니다")
    except (question_records.QuestionRecordError, OSError, UnicodeError) as error:
        issues.append(f"문서 검사 실패: {error}")
    return {"path": str(path), "prose_characters": prose_length(text), "issues": issues, "semantic_review_required": True}


def main(argv=None):
    parser = argparse.ArgumentParser(description="사람용 문서의 설계 표·기존 답을 검사합니다. 문체와 의미 대조는 별도입니다.")
    parser.add_argument("path", type=Path)
    parser.add_argument("--source", type=Path)
    args = parser.parse_args(argv)
    result = check(args.path, args.source)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(bool(result["issues"]))


if __name__ == "__main__":
    raise SystemExit(main())
