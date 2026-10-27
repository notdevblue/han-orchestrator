#!/usr/bin/env python3
import argparse
import contextlib
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime

SKILLS_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
HOOKS_DIR = os.path.normpath(os.path.join(SKILLS_DIR, "..", "hooks"))
sys.path.insert(0, HOOKS_DIR)
sys.path.insert(0, os.path.join(SKILLS_DIR, "slots", "scripts"))
import mission_guard  # noqa: E402
import slots  # noqa: E402
import verification
import question_records
from question_summary import QuestionSummary, is_answer_section, markdown_lines, plain_text, summarize_question

OK = 0
FAILED = 1

TEMPLATES_DIR = os.path.join(SKILLS_DIR, "mission", "references", "templates")
TEMPLATE_FILES = ("mission.md", "units.tsv", "ledger.tsv", "decisions.tsv", "report.md")
ID_TEMPLATES = ("mission.md", "report.md")
ID_PLACEHOLDER = "<id>"

UNITS_FILE = "units.tsv"
LEDGER_FILE = "ledger.tsv"
DECISIONS_FILE = "decisions.tsv"
SHARED_DOCUMENTS_FILE = "shared-documents.tsv"
UNIT_HEADER = ("id", "goal", "files", "done", "verify", "timebox", "forbid", "human", "after", "unit", "started", "status")
LEDGER_HEADER = ("at", "unit", "status", "grade", "evidence", "files")
DECISIONS_HEADER = ("at", "id", "what", "chosen", "why", "affects")
SHARED_DOCUMENTS_HEADER = ("path", "sha256", "at")
SYSTEM_DOCUMENTS = {"mission.md", UNITS_FILE, LEDGER_FILE, DECISIONS_FILE, "sessions.tsv", SHARED_DOCUMENTS_FILE}

BRIEF_FIELDS = ("goal", "files", "done", "verify", "timebox", "forbid")
EDITABLE_FIELDS = BRIEF_FIELDS + ("human", "after")
UNIT_STATUSES = ("todo", "doing", "done", "held")
LEDGER_STATUSES = ("pass", "fail", "retry", "blocked")
MISSION_STATES = ("진행", "중단", "닫힘")
NEEDS_HUMAN = "needs-human"
NONE_CELL = "-"
VERDICT_LIMIT = 4

UNIT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
CONFIRM_KEY = re.compile(r"[A-Za-z]\d*")
SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
DECISION_ID = re.compile(r"D(\d+)")
HEADER_FIELD = re.compile(r"^(상태|현재 단계|PoC|확인 [A-Za-z]\d*):[ \t]*(.*?)[ \t]*$")
INSERTABLE_KEYS = ("PoC",)
HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
QUESTION_FILE = re.compile(r"^(?:confirm|ask)-.+\.md$")
CONFIRM_FILE = re.compile(r"^confirm-([A-Za-z])(\d*)\.md$")
ASK_FILE = re.compile(r"^ask-.+\.md$")
ANSWER_DATE = re.compile(r"\b\d{4}[-./]\d{1,2}[-./]\d{1,2}\b")
APPROVED = "승인"
SKIPPED = "생략"


class StateError(Exception):
    pass


class OneLineParser(argparse.ArgumentParser):
    # 사용법 전문 대신 한 줄 오류로 끝냄(하위 파서도 이 클래스를 물려받음)
    def error(self, message):
        raise StateError(f"{self.prog}: {message} (-h 로 사용법 확인)")


@dataclass
class Context:
    adapter: dict
    state_dir: str
    mission_id: str
    runtime: str
    project: str

    @property
    def folder(self):
        return os.path.join(self.state_dir, self.mission_id)

    def path(self, name):
        return os.path.join(self.folder, name)


def clean(value):
    return mission_guard.CELL_BREAKS.sub(" ", str(value)).strip()


def read_text(path):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return f.read()
    except FileNotFoundError:
        raise StateError(f"파일 없음: {path} (init 이 빠진 템플릿 파일을 채움, 있는 파일은 안 덮음)")


def write_text(path, text):
    temp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(temp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(temp, path)
    except BaseException:
        mission_guard.remove_quietly(temp)
        raise


def read_table(path, header):
    lines = read_text(path).split("\n")
    if tuple(lines[0].rstrip().split("\t")) != header:
        raise StateError(f"{os.path.basename(path)} 헤더가 다름 (기대: {' '.join(header)}): {path}")
    rows = []
    for number, line in enumerate(lines[1:], start=2):
        if not line.strip():
            continue
        cells = line.split("\t")
        if len(cells) != len(header):
            raise StateError(
                f"{os.path.basename(path)} {number}행 칸 수 {len(cells)} (기대 {len(header)}): 탭이 빠졌거나 넘친 행을 고칠 것"
            )
        rows.append(dict(zip(header, cells)))
    return rows


def write_table(path, header, rows):
    lines = ["\t".join(header)] + ["\t".join(row[name] for name in header) for row in rows]
    write_text(path, "\n".join(lines) + "\n")


def canonical_document_path(folder, name):
    raw = str(name).replace("\\", "/")
    parts = raw.split("/")
    if not raw or raw.startswith("/") or os.path.isabs(str(name)) or re.match(r"^[A-Za-z]:", raw):
        raise StateError(f"문서 경로는 미션 폴더 안의 상대 경로여야 함: {name}")
    if any(part in ("", ".", "..") or ":" in part or any(ord(char) < 32 for char in part) for part in parts):
        raise StateError(f"문서 경로 형식 오류: {name}")
    if any(part.startswith(".") for part in parts):
        raise StateError(f"숨김 경로는 공유할 수 없음: {name}")
    canonical = "/".join(parts)
    if canonical in SYSTEM_DOCUMENTS:
        raise StateError(f"시스템 파일은 공유할 수 없음: {name}")
    root = os.path.realpath(folder)
    target = os.path.realpath(os.path.join(folder, *parts))
    if not slots.inside_root(root, target):
        raise StateError(f"문서 경로가 미션 폴더 밖이거나 폴더 자체임: {name}")
    resolved = os.path.relpath(target, root).replace(os.sep, "/")
    resolved_parts = resolved.split("/")
    if any(part.startswith(".") for part in resolved_parts):
        raise StateError(f"숨김 경로는 공유할 수 없음: {name}")
    if resolved in SYSTEM_DOCUMENTS:
        raise StateError(f"시스템 파일은 공유할 수 없음: {name}")
    return canonical, target


def shared_document_rows(folder):
    path = os.path.join(folder, SHARED_DOCUMENTS_FILE)
    if not os.path.lexists(path):
        return []
    if not slots.inside_root(os.path.realpath(folder), os.path.realpath(path)) or not os.path.isfile(path):
        raise StateError(f"{SHARED_DOCUMENTS_FILE} 경로가 잘못됨: {path}")
    try:
        text = read_text(path)
    except UnicodeError as error:
        raise StateError(f"{SHARED_DOCUMENTS_FILE} 인코딩 오류: {error}")
    if not text.splitlines():
        raise StateError(f"{SHARED_DOCUMENTS_FILE} 비어 있음: {path}")
    try:
        rows = read_table(path, SHARED_DOCUMENTS_HEADER)
    except UnicodeError as error:
        raise StateError(f"{SHARED_DOCUMENTS_FILE} 인코딩 오류: {error}")
    seen = set()
    for row in rows:
        canonical, _ = canonical_document_path(folder, row["path"])
        if canonical != row["path"] or canonical in seen or not re.fullmatch(r"[0-9a-f]{64}", row["sha256"]) or not row["at"].strip():
            raise StateError(f"{SHARED_DOCUMENTS_FILE} 유효하지 않은 행: {row['path']}")
        seen.add(canonical)
    return rows


def shared_documents(folder):
    rows = shared_document_rows(folder)
    shared = set()
    for row in rows:
        _, target = canonical_document_path(folder, row["path"])
        try:
            with open(target, "rb") as stream:
                digest = hashlib.sha256(stream.read()).hexdigest()
        except OSError:
            continue
        if digest == row["sha256"]:
            shared.add(row["path"])
    return shared


def auto_question_document(folder, name):
    try:
        canonical, target = canonical_document_path(folder, name)
        root = os.path.realpath(folder)
        resolved = os.path.relpath(target, root).replace("\\", "/")
        if os.path.normcase(canonical) != os.path.normcase(resolved) or not os.path.isfile(target):
            return False
        parts = canonical.split("/")
        if len(parts) == 1 and QUESTION_FILE.fullmatch(canonical):
            return True
        if len(parts) == 2 and parts[0] == "details" and QUESTION_FILE.fullmatch(parts[1]):
            return auto_question_document(folder, parts[1])
        return False
    except (OSError, ValueError, StateError):
        return False


def auto_question_documents(folder):
    names = set()
    for name in os.listdir(folder):
        if QUESTION_FILE.fullmatch(name) and auto_question_document(folder, name):
            names.add(name)
            detail = f"details/{name}"
            if auto_question_document(folder, detail):
                names.add(detail)
    return names


def share_documents(folder, names, expected_hashes=()):
    requested = list(dict.fromkeys(canonical_document_path(folder, name)[0] for name in names))
    contents = {}
    for name in requested:
        _, target = canonical_document_path(folder, name)
        try:
            with open(target, "rb") as stream:
                content = stream.read()
        except OSError as error:
            raise StateError(f"공유할 파일을 읽을 수 없음: {name} ({error})")
        if not os.path.isfile(target):
            raise StateError(f"공유할 파일이 아님: {name}")
        contents[name] = hashlib.sha256(content).hexdigest()
        if QUESTION_FILE.fullmatch(name):
            try:
                text = content.decode("utf-8-sig")
            except UnicodeError as error:
                raise StateError(f"질문 문서 인코딩 오류: {name} ({error})") from error
            _, issue = question_records.inspect_document(folder, name, text)
            if issue:
                raise StateError(f"{name} 기록 확인 필요: {issue}")
    try:
        expected = dict(expected_hashes)
    except (TypeError, ValueError) as error:
        raise StateError(f"기대 해시 형식 오류: {error}")
    for name, digest in expected.items():
        if name not in contents or digest != contents[name]:
            raise StateError(f"문서가 바뀌었습니다: {name}")
    path = os.path.join(folder, SHARED_DOCUMENTS_FILE)
    rows = shared_document_rows(folder)
    by_name = {row["path"]: row for row in rows}
    for name in requested:
        by_name[name] = {"path": name, "sha256": contents[name], "at": mission_guard.now_iso()}
    ordered = [by_name[row["path"]] for row in rows]
    ordered.extend(by_name[name] for name in requested if name not in {row["path"] for row in rows})
    write_table(path, SHARED_DOCUMENTS_HEADER, ordered)
    return requested


def hide_documents(folder, names):
    requested = list(dict.fromkeys(canonical_document_path(folder, name)[0] for name in names))
    path = os.path.join(folder, SHARED_DOCUMENTS_FILE)
    rows = shared_document_rows(folder)
    hidden = {row["path"] for row in rows} & set(requested)
    if hidden:
        write_table(path, SHARED_DOCUMENTS_HEADER, [row for row in rows if row["path"] not in hidden])
    return [name for name in requested if name in hidden]


def append_row(path, header, cells):
    # 기존 행은 다시 직렬화하지 않고 본문 끝에만 덧붙임(append only)
    text = read_text(path)
    if not text.endswith("\n"):
        text += "\n"
    write_text(path, text + "\t".join(clean(cell) for cell in cells) + "\n")


def find_row(rows, key, value):
    for row in rows:
        if row[key] == value:
            return row
    return None


def require_unit(rows, unit_id):
    row = find_row(rows, "id", unit_id)
    if row is None:
        known = ", ".join(r["id"] for r in rows) or "없음"
        raise StateError(f"마일스톤 {unit_id} 없음 (있는 것: {known})")
    return row


def require_text(label, value):
    cleaned = clean(value)
    if not cleaned:
        raise StateError(f"{label} 비어 있음")
    return cleaned


def parse_timebox(value):
    if not value.isascii() or not value.isdigit() or int(value) <= 0:
        raise StateError(f"timebox 는 양의 정수(분): {value}")
    return str(int(value))


def normalize_after(value, rows, own_id=""):
    items = list(dict.fromkeys(clean(item) for item in value.split(",") if clean(item)))
    if not items or items == [NONE_CELL]:
        return NONE_CELL
    for item in items:
        if item == own_id:
            raise StateError(f"after 에 자기 자신({item})을 둘 수 없음")
        if find_row(rows, "id", item) is None:
            raise StateError(f"after 의 {item} 는 units.tsv 에 없음 (먼저 unit add, 없으면 {NONE_CELL})")
    return ",".join(items)


def elapsed_minutes(row):
    try:
        started = datetime.fromisoformat(row["started"])
    except ValueError:
        return -1
    if started.tzinfo is None:
        started = started.astimezone()
    return max(0, int((datetime.now().astimezone() - started).total_seconds() // 60))


def is_overdue(row):
    elapsed = elapsed_minutes(row)
    return row["status"] == "doing" and row["timebox"].isdigit() and elapsed > int(row["timebox"])


def ratio_of(row):
    elapsed = elapsed_minutes(row)
    return f"{'?' if elapsed < 0 else elapsed}/{row['timebox']}분"


def progress_of(row):
    if row["status"] != "doing":
        return f"{NONE_CELL}/{row['timebox']}분"
    return f"{ratio_of(row)} 넘김" if is_overdue(row) else ratio_of(row)


def header_end(lines):
    for index, line in enumerate(lines):
        if line.startswith("## "):
            return index
    return len(lines)


def header_fields(lines):
    fields = {}
    for line in lines[: header_end(lines)]:
        match = HEADER_FIELD.match(line)
        if match:
            fields[match.group(1)] = match.group(2)
    return fields


def header_index(lines, key):
    for index in range(header_end(lines)):
        match = HEADER_FIELD.match(lines[index])
        if match and match.group(1) == key:
            return index
    return -1


def stage_and_confirms(fields):
    confirms = ", ".join(f"{key[3:]} {value}" for key, value in fields.items() if key.startswith("확인 "))
    return f"현재 단계 {fields.get('현재 단계', '?')} | 확인 {confirms or '없음'}"


def require_folder(ctx):
    if not os.path.isdir(ctx.folder):
        raise StateError(f"미션 폴더 없음: {ctx.folder} — `init` 먼저")


MISSION_TIERS = {
    "S": "한두 파일·단일 수정. 미션 대상 아님(직접 처리하거나 한 번 파견)",
    "M": "여러 파일·한 레이어 이상, 검증 게이트 1개 이상",
    "L": "여러 레이어·PoC·사람 확인이 필요한 다단계 작업",
}


def tier_messages(args, mission_id):
    tier = (getattr(args, "tier", None) or "").strip().upper()
    if not tier:
        return [
            "경고: --tier 없음. 미션은 M·L 티어용이다(S: 한두 파일 수정은 미션 없이). 다음부터 `init --tier M|L` 로 연다"
        ], ""
    if tier not in MISSION_TIERS:
        raise StateError(f"--tier 는 S·M·L 중 하나: {tier}")
    if tier == "S":
        if not getattr(args, "force_tier", False):
            raise StateError(
                f"S 티어는 미션으로 열지 않음({MISSION_TIERS['S']}). `echo han-mission close {mission_id}` 로 닫고 직접 처리할 것. "
                "사용자가 미션을 원하면 `--force-tier`"
            )
        return ["경고: S 티어를 --force-tier 로 미션으로 엶"], tier
    return [], tier


def cmd_init(ctx, args):
    active = mission_guard.read_active(ctx.state_dir)
    if active.corrupt or active.id != ctx.mission_id or not active.session:
        raise StateError(
            f"가드 안 켜짐: ACTIVE 가 미션 {ctx.mission_id} 의 세션을 가리키지 않음 — `echo han-mission open {ctx.mission_id}` 를 단독으로 먼저 실행할 것"
        )
    warnings, tier = tier_messages(args, ctx.mission_id)
    os.makedirs(ctx.folder, exist_ok=True)
    created = []
    kept = []
    for name in TEMPLATE_FILES:
        target = ctx.path(name)
        if os.path.exists(target):
            kept.append(name)
            continue
        text = read_text(os.path.join(TEMPLATES_DIR, name))
        if name in ID_TEMPLATES:
            text = text.replace(ID_PLACEHOLDER, ctx.mission_id)
        if name == "mission.md":
            text = text.replace("상태: 준비", "상태: 진행", 1)
            if tier:
                text = text.replace("현재 단계:", f"티어: {tier}\n현재 단계:", 1)
            goal = (args.goal or "").strip()
            if goal:
                if "## 목표\n" not in text:
                    raise StateError("mission.md 템플릿에 '## 목표' 제목이 없음")
                text = text.replace("## 목표\n", f"## 목표\n\n{goal}\n", 1)
        write_text(target, text)
        created.append(name)
    return [*warnings, f"init {ctx.mission_id} 만듦: {' '.join(created) or '없음'} / 건너뜀: {' '.join(kept) or '없음'}"]


def poc_relative_path(project, value):
    given = require_text("--poc", value)
    root = os.path.abspath(project)
    target = os.path.abspath(os.path.join(root, given))
    if not slots.inside_root(os.path.realpath(root), os.path.realpath(target)):
        raise StateError(f"--poc 가 작업 공간({root}) 밖: {given}")
    if not os.path.isfile(target):
        raise StateError(f"--poc 파일 없음: {target}")
    return os.path.relpath(target, root).replace(os.sep, "/")


def cmd_mission_set(ctx, args):
    require_folder(ctx)
    path = ctx.path("mission.md")
    lines = read_text(path).split("\n")
    changes = []
    if args.stage is not None:
        stage = require_text("--stage", args.stage)
        known = [s.get("id") for s in ctx.adapter.get("stages") or [] if isinstance(s, dict) and s.get("id")]
        if known and stage not in known:
            raise StateError(f"--stage 허용 값: {', '.join(known)} (어댑터 stages)")
        changes.append(("현재 단계", stage))
    for item in args.confirm:
        key, separator, value = item.partition("=")
        if not separator or not CONFIRM_KEY.fullmatch(key.strip()):
            raise StateError(f"--confirm 형식은 K=V (K 는 A·B·C·A1 처럼): {item}")
        changes.append((f"확인 {key.strip()}", require_text(f"--confirm {key.strip()} 값", value)))
    if args.state:
        changes.append(("상태", args.state))
    if args.poc is not None:
        changes.append(("PoC", poc_relative_path(ctx.project, args.poc)))
    if not changes:
        raise StateError("바꿀 값 없음: --stage, --confirm K=V, --state, --poc 중 하나 이상")
    for key, value in changes:
        index = header_index(lines, key)
        if index >= 0:
            lines[index] = f"{key}: {value}"
            continue
        if not (key.startswith("확인 ") or key in INSERTABLE_KEYS):
            raise StateError(f"mission.md 헤더에 '{key}:' 줄이 없음: {path}")
        # 확인 줄이 있으면 그 마지막 뒤, 없으면 현재 단계 줄 뒤
        confirm_lines = [i for i in range(header_end(lines)) if HEADER_FIELD.match(lines[i]) and lines[i].startswith("확인 ")]
        anchor = max(confirm_lines + [header_index(lines, "현재 단계")])
        if anchor < 0:
            raise StateError(f"mission.md 헤더에 '확인 ' 줄도 '현재 단계:' 줄도 없음: {path}")
        lines.insert(anchor + 1, f"{key}: {value}")
    write_text(path, "\n".join(lines))
    fields = header_fields(lines)
    return [f"상태 {fields.get('상태', '?')} | {stage_and_confirms(fields)}"]


def cmd_unit_add(ctx, args):
    require_folder(ctx)
    path = ctx.path(UNITS_FILE)
    rows = read_table(path, UNIT_HEADER)
    unit_id = clean(args.id)
    if not UNIT_ID.fullmatch(unit_id):
        raise StateError(f"마일스톤 id 형식 오류 (영문·숫자·._- 만, 쉼표·공백 불가): {args.id}")
    if find_row(rows, "id", unit_id) is not None:
        raise StateError(f"마일스톤 {unit_id} 가 이미 있음 (바꾸려면 unit set)")
    brief = {name: clean(getattr(args, name)) for name in BRIEF_FIELDS}
    empty = [name for name, value in brief.items() if not value]
    if empty:
        raise StateError(f"브리프 빈 칸: {', '.join(empty)}, 마일스톤을 다시 쪼갤 것")
    row = {
        "id": unit_id,
        **brief,
        "timebox": parse_timebox(brief["timebox"]),
        "human": clean(args.human) or NONE_CELL,
        "after": normalize_after(args.after, rows),
        "unit": "?",
        "started": "",
        "status": "todo",
    }
    write_table(path, UNIT_HEADER, rows + [row])
    return [f"{unit_id} 추가 (todo, timebox {row['timebox']}분, after {row['after']})"]


def cmd_unit_set(ctx, args):
    require_folder(ctx)
    path = ctx.path(UNITS_FILE)
    rows = read_table(path, UNIT_HEADER)
    row = require_unit(rows, clean(args.id))
    changed = []
    for name in EDITABLE_FIELDS:
        value = getattr(args, name)
        if value is None:
            continue
        cleaned = clean(value)
        if not cleaned:
            hint = f" ({NONE_CELL} 로 비움 표시)" if name in ("human", "after") else ""
            raise StateError(f"브리프 칸 {name} 을 빈 값으로 바꿀 수 없음{hint}")
        if name == "timebox":
            cleaned = parse_timebox(cleaned)
        if name == "after":
            cleaned = normalize_after(cleaned, rows, row["id"])
        row[name] = cleaned
        changed.append(name)
    if args.unit is not None:
        row["unit"] = require_text("--unit", args.unit)
        changed.append(f"unit={row['unit']}")
    if changed and row["status"] == "done" and args.status != "doing":
        raise StateError("완료한 단위의 브리프를 바꾸려면 --status doing 으로 재개할 것")
    if args.status and args.status != row["status"]:
        if args.status == "doing":
            pending = []
            for dependency in [d for d in row["after"].split(",") if d != NONE_CELL]:
                other = find_row(rows, "id", dependency)
                if other is not None and other["status"] != "done":
                    pending.append(f"{dependency}({other['status']})")
            if pending:
                raise StateError(f"after 가 아직 done 이 아님: {', '.join(pending)} — 먼저 끝낼 것")
            row["started"] = mission_guard.now_iso()
        if args.status == "done":
            raise StateError("완료는 complete --receipt --review --done-evidence 로 기록할 것")
        row["status"] = args.status
        changed.append(f"status={args.status}")
    if not changed:
        raise StateError("바꿀 칸 없음: --status, --unit, --goal 같은 옵션을 줄 것")
    write_table(path, UNIT_HEADER, rows)
    return [f"{row['id']} 갱신: {', '.join(changed)}"]


def cmd_unit_list(ctx, args):
    require_folder(ctx)
    rows = read_table(ctx.path(UNITS_FILE), UNIT_HEADER)
    lines = [
        f"{row['id']} {row['status']} {progress_of(row)} {row['unit']} {row['goal']}"
        for row in rows
        if not args.status or row["status"] == args.status
    ]
    return lines or ["마일스톤 없음"]


def cmd_ledger_record(ctx, args):
    require_folder(ctx)
    require_unit(read_table(ctx.path(UNITS_FILE), UNIT_HEADER), clean(args.unit))
    configured = (ctx.adapter.get("verify") or {}).get("grades")
    grades = [g for g in configured if isinstance(g, str)] if isinstance(configured, list) else []
    grades.append(NEEDS_HUMAN)
    grade = clean(args.grade)
    if grade not in grades:
        raise StateError(f"grade 허용 값: {', '.join(grades)} (어댑터 verify.grades + {NEEDS_HUMAN}): {grade}")
    evidence = require_text("--evidence", args.evidence)
    files = clean(args.files) or NONE_CELL
    append_row(
        ctx.path(LEDGER_FILE),
        LEDGER_HEADER,
        [mission_guard.now_iso(), clean(args.unit), args.status, grade, evidence, files],
    )
    return [f"ledger {clean(args.unit)} {args.status} {grade}"]


def cmd_decision_add(ctx, args):
    require_folder(ctx)
    path = ctx.path(DECISIONS_FILE)
    rows = read_table(path, DECISIONS_HEADER)
    what = require_text("--what", args.what)
    cells = [require_text(f"--{name}", getattr(args, name)) for name in ("chosen", "why", "affects")]
    fixes = clean(args.fixes or "")
    if args.fixes is not None:
        if find_row(rows, "id", fixes) is None:
            raise StateError(f"--fixes {fixes} 는 decisions.tsv 에 없음 (있는 것: {', '.join(r['id'] for r in rows) or '없음'})")
        what = f"{fixes} 정정: {what}"
    numbers = [int(m.group(1)) for m in (DECISION_ID.fullmatch(r["id"]) for r in rows) if m]
    decision_id = f"D{max(numbers, default=0) + 1}"
    append_row(path, DECISIONS_HEADER, [mission_guard.now_iso(), decision_id, what, *cells])
    return [f"{decision_id} 추가" + (f" ({fixes} 정정)" if fixes else "")]


def confirm_approved(fields, letter, number):
    whole = fields.get(f"확인 {letter}", "")
    if whole.startswith((APPROVED, SKIPPED)):
        return True
    key = f"{letter}{number}"
    return bool(number) and (fields.get(f"확인 {key}", "").startswith(APPROVED) or re.search(rf"\b{key}\s*{APPROVED}", whole) is not None)


def summarize_question_document(name, text, fields) -> QuestionSummary:
    pending = True
    confirm = CONFIRM_FILE.match(name)
    if confirm:
        pending = not confirm_approved(fields, confirm.group(1).upper(), confirm.group(2))
    elif ASK_FILE.match(name):
        body = markdown_lines(text)
        heading = -1
        for index, line in enumerate(body):
            match = re.match(r"^#{2,3}[ \t]+(.+?)[ \t]*$", line)
            if match and is_answer_section(plain_text(re.sub(r"[ \t]+#+$", "", match.group(1)))):
                heading = index
                break
        below = []
        dated = False
        if heading >= 0:
            for line in body[heading + 1 :]:
                if line.startswith("## "):
                    break
                below.append(line)
            dated = any(ANSWER_DATE.search(line) for line in [body[heading], *below])
        pending = not dated or not any(line.strip() for line in below)
    return summarize_question(name, text, pending)


def question_inspections(folder):
    inspected = []
    try:
        names = set(question_records.document_names(folder))
    except question_records.QuestionRecordError as error:
        names = set()
        inspected.append(("questions", QuestionSummary("questions", "", [], False, False, False), str(error)))
    names.update(name for name in auto_question_documents(folder) if QUESTION_FILE.fullmatch(name))
    for name in sorted(names):
        try:
            _, target = canonical_document_path(folder, name)
            with open(target, "rb") as stream:
                text = stream.read().decode("utf-8-sig")
        except FileNotFoundError:
            text = ""
        except (OSError, UnicodeError, StateError) as error:
            inspected.append((name, QuestionSummary(name, "", [], False, False, False),
                              f"{name} 읽기 실패: {error}; question render {name} 으로 복구할 것"))
            continue
        summary, issue = question_records.inspect_document(folder, name, text)
        inspected.append((name, summary, issue))
    return inspected


def unanswered_questions(folder, fields):
    return [name for name, summary, issue in question_inspections(folder) if not issue and summary.pending]


def active_lines(ctx, active, state):
    opened = active.opened_at or "?"
    session = active.session or "?"
    mark = mission_guard.stale_text(state) if state.stale else f"살아 있음: 마지막 활동 {mission_guard.age_text(state.hours)} 전"
    return [f"ACTIVE {active.id} | 세션 {session} | 열림 {opened}", mark]


def cmd_active(ctx, args):
    active = mission_guard.read_active(ctx.state_dir)
    path = mission_guard.active_path_of(ctx.state_dir)
    if active.corrupt:
        return [f"ACTIVE 손상: {path}"]
    if not active.id:
        return [f"ACTIVE 없음: {path}"]
    limit = args.hours if args.hours is not None else mission_guard.stale_hours_of(ctx.adapter)
    return [*active_lines(ctx, active, mission_guard.staleness(ctx.state_dir, active, limit)), f"파일: {path}"]


def cmd_close_stale(ctx, args):
    # 끊긴(마지막 활동이 기준 시간을 넘긴) ACTIVE 만, --force-close-stale 이 있을 때만 닫음. 없으면 미리보기
    active = mission_guard.read_active(ctx.state_dir)
    path = mission_guard.active_path_of(ctx.state_dir)
    if not active.id:
        raise StateError(f"ACTIVE 없음: {path}")
    if active.id != ctx.mission_id:
        raise StateError(f"ACTIVE 는 {active.id} — --mission {ctx.mission_id} 와 다름")
    limit = args.hours if args.hours is not None else mission_guard.stale_hours_of(ctx.adapter)
    if not limit:
        raise StateError("끊김 기준 0(꺼짐) — --hours N 으로 기준을 줄 것")
    state = mission_guard.staleness(ctx.state_dir, active, limit)
    lines = active_lines(ctx, active, state)
    if not state.stale:
        raise StateError(f"아직 끊김 아님(기준 {limit:g}시간): {lines[1]} — 그 세션에서 `echo han-mission close {active.id}`")
    if not args.force_close_stale:
        args.exit_code = FAILED
        return [*lines, f"미리보기: --force-close-stale 을 붙이면 ACTIVE 를 지우고 mission.md 상태를 {'유지' if args.keep_state else '중단'}으로 바꿈"]
    os.remove(path)
    mission_guard.clear_allow_once(ctx.state_dir, active.id)
    done = [*lines, f"ACTIVE 지움: {path}"]
    mission_md = ctx.path("mission.md")
    if not args.keep_state and os.path.isfile(mission_md):
        text_lines = read_text(mission_md).split("\n")
        index = header_index(text_lines, "상태")
        if index >= 0 and header_fields(text_lines).get("상태") == "진행":
            text_lines[index] = "상태: 중단"
            write_text(mission_md, "\n".join(text_lines))
            done.append("mission.md 상태: 진행 → 중단")
    if os.path.isdir(ctx.folder):
        note = clean(args.reason or "") or NONE_CELL
        last = datetime.fromtimestamp(state.last).isoformat(timespec="seconds") if state.last else NONE_CELL
        with open(ctx.path("force-close.log"), "a", encoding="utf-8", newline="\n") as f:
            f.write(f"{mission_guard.now_iso()}\tsession={active.session or NONE_CELL}\topenedAt={active.opened_at or NONE_CELL}"
                    f"\tlast={last}\tlimit={limit:g}h\treason={note}\n")
        done.append(f"기록: {ctx.path('force-close.log')}")
    done.append(f"되살리기: `echo han-mission open {active.id}` 후 `mission set --state 진행`")
    return done


def cmd_status(ctx, args):
    require_folder(ctx)
    rows = read_table(ctx.path(UNITS_FILE), UNIT_HEADER)
    verdicts = read_table(ctx.path(LEDGER_FILE), LEDGER_HEADER)
    fields = header_fields(read_text(ctx.path("mission.md")).split("\n"))

    counts = {name: 0 for name in UNIT_STATUSES}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    summary = ", ".join(f"{name} {count}" for name, count in counts.items())

    latest = {}
    for verdict in verdicts:
        latest.pop(verdict["unit"], None)
        latest[verdict["unit"]] = verdict
    recent = list(latest.values())[-VERDICT_LIMIT:]
    rest = len(latest) - len(recent)
    judged = ", ".join(f"{v['unit']} {v['status']} {v['grade']}" for v in recent) + (f" 외 {rest}개" if rest > 0 else "")

    stuck = [f"{row['id']} timebox 넘김({ratio_of(row)})" for row in rows if is_overdue(row)]
    for name, question, issue in question_inspections(ctx.folder):
        if issue:
            stuck.append(f"{name} 기록 확인 필요")
        elif question.pending:
            stuck.append(f"{name} 답 없음")
    active = mission_guard.read_active(ctx.state_dir)
    if active.id == ctx.mission_id and not active.corrupt:
        state = mission_guard.staleness(ctx.state_dir, active, mission_guard.stale_hours_of(ctx.adapter))
        if state.stale:
            stuck.append(f"ACTIVE {mission_guard.stale_text(state)}")
    return [
        f"마일스톤 {summary} | {stage_and_confirms(fields)}",
        f"판정: {judged or '없음'}",
        f"걸린 것: {', '.join(stuck) or '없음'}",
    ]


def cmd_context(ctx, args):
    lines = cmd_status(ctx, args)
    rows = read_table(ctx.path(UNITS_FILE), UNIT_HEADER)
    active = [row for row in rows if row["status"] == "doing"]
    ready = [row for row in rows if row["status"] == "todo" and all(
        require_unit(rows, dependency)["status"] == "done"
        for dependency in row["after"].split(",") if dependency != NONE_CELL
    )]
    for row in (active + ready)[:6]:
        lines.append(f"{row['id']} {row['status']} {row['goal'][:160]} | 브리프: {ctx.path(UNITS_FILE)} #{row['id']}")
    if len(active + ready) > 6:
        lines.append(f"다음 단위 외 {len(active + ready) - 6}개: unit list")
    decisions = read_table(ctx.path(DECISIONS_FILE), DECISIONS_HEADER)
    for row in decisions[-4:]:
        lines.append(f"{row['id']}: {row['what'][:120]} → {row['chosen'][:180]}")
    lines.append(f"승인·명세·지시 원본: {ctx.path('mission.md')} (필요한 절만 읽기)")
    lines.append(f"전체 판정·결정·질문 원본: {ctx.folder} (관련 단위·번호만 조회)")
    return lines


def verification_plan(ctx, args):
    require_folder(ctx)
    row = require_unit(read_table(ctx.path(UNITS_FILE), UNIT_HEADER), args.id)
    plan = verification.load_plan(ctx.project, args.plan, (ctx.adapter.get("verify") or {}).get("grades", []))
    plan["unitBrief"] = {name: row[name] for name in EDITABLE_FIELDS}
    return plan


def cmd_verify(ctx, args):
    plan = verification_plan(ctx, args)

    def guard(command):
        problems = []
        verdict = mission_guard.judge_command(ctx.adapter, command, "PowerShell" if os.name == "nt" else "Bash", problems)
        if verdict.kind != "allow" or problems:
            raise StateError(f"검증 명령 거부: {verdict.reason or problems}")

    path, receipt, reused = verification.run(ctx.project, ctx.folder, args.id, plan, guard, args.force)
    args.exit_code = OK if receipt["status"] == "pass" else FAILED
    return [
        f"검증 {receipt['status']} {receipt['grade']} {'재사용' if reused else '실행'} | {path}",
        f"로그: {receipt['log']}" + (f" | {receipt['error']}" if receipt["error"] else ""),
    ]


def cmd_review(ctx, args):
    require_folder(ctx)
    require_unit(read_table(ctx.path(UNITS_FILE), UNIT_HEADER), args.id)
    path = verification.record_review(
        ctx.project, ctx.folder, args.id, args.receipt, args.evidence, args.reviewer, args.kind, args.status,
        args.findings_count, args.fixed_count,
    )
    return [f"리뷰 {args.id} {args.status} {args.kind} | {path}"]


def cmd_review_scope(ctx, args):
    plan = verification_plan(ctx, args)
    paths = verification.review_scope(ctx.project, ctx.folder, args.id, plan)
    return [f"리뷰 변경 범위 {args.id}: {len(paths)}개", *paths]


def cmd_complete(ctx, args):
    require_folder(ctx)
    rows = read_table(ctx.path(UNITS_FILE), UNIT_HEADER)
    row = require_unit(rows, args.id)
    if row["status"] not in ("doing", "done"):
        raise StateError("doing 마일스톤만 완료할 수 있음")
    require_text("--done-evidence", args.done_evidence)
    receipt = verification.read_json(args.receipt)
    verification.validate_receipt(ctx.project, ctx.folder, args.id, receipt)
    verification.validate_latest(ctx.folder, args.id, args.receipt)
    if receipt.get("plan", {}).get("unitBrief") != {name: row[name] for name in EDITABLE_FIELDS}:
        raise StateError("검증 이후 브리프·완료 조건이 바뀜")
    if receipt.get("grade") not in (ctx.adapter.get("verify") or {}).get("grades", []):
        raise StateError("검증 등급이 현재 어댑터와 다름")
    verification.validate_review(ctx.folder, args.id, args.receipt, args.review)
    evidence = clean(f"receipt={os.path.abspath(args.receipt)} review={os.path.abspath(args.review)} done={args.done_evidence}")
    ledger_path = ctx.path(LEDGER_FILE)
    verdicts = read_table(ledger_path, LEDGER_HEADER)
    latest = [verdict for verdict in verdicts if verdict["unit"] == args.id]
    if not latest or latest[-1]["evidence"] != evidence:
        append_row(ledger_path, LEDGER_HEADER, [
            mission_guard.now_iso(), args.id, "pass", receipt["grade"], evidence,
            ",".join(receipt["snapshot"]),
        ])
    row["status"] = "done"
    write_table(ctx.path(UNITS_FILE), UNIT_HEADER, rows)
    return [f"{args.id} 완료 {receipt['grade']}", *cmd_context(ctx, args)]


def search_claude(session, home):
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(home, ".claude")
    projects = os.path.join(base, "projects")
    if not os.path.isdir(projects):
        return ""
    for name in sorted(os.listdir(projects)):
        candidate = os.path.join(projects, name, f"{session}.jsonl")
        if os.path.isfile(candidate):
            return candidate
    return ""


def search_codex(session, home):
    base = os.environ.get("CODEX_HOME") or os.path.join(home, ".codex")
    # 보관한 세션은 archived_sessions 로 옮겨져서 sessions.tsv 경로가 낡음
    for root in ("sessions", "archived_sessions"):
        for folder, _, names in os.walk(os.path.join(base, root)):
            for name in sorted(names):
                if name.startswith("rollout-") and name.endswith(f"-{session}.jsonl"):
                    return os.path.join(folder, name)
    return ""


def cmd_transcripts(ctx, args):
    require_folder(ctx)
    path = ctx.path(mission_guard.SESSIONS_FILE)
    if not os.path.isfile(path):
        return [f"세션 기록 없음 ({mission_guard.SESSIONS_FILE} 은 open·resume 마커가 남김)"]
    recorded = {}
    for row in read_table(path, mission_guard.SESSIONS_HEADER):
        if not recorded.get(row["session"]):
            recorded[row["session"]] = row["transcript"]
    searches = (search_claude, search_codex) if ctx.runtime == "claude" else (search_codex, search_claude)
    home = os.path.expanduser("~")
    lines = []
    for session, transcript in recorded.items():
        found = transcript if transcript and os.path.isfile(transcript) else ""
        # 세션 id 는 파일 이름 일치로만 찾음(와일드카드·경로 구분자가 든 값은 찾지 않음)
        if not found and SESSION_ID.fullmatch(session):
            for search in searches:
                found = found or search(session, home)
        if not found:
            lines.append(f"없음: {session}")
            continue
        lines.append(f"{session} {found}")
        subagents = os.path.join(os.path.splitext(found)[0], "subagents")
        if os.path.isdir(subagents):
            lines.extend(f"  {os.path.join(subagents, n)}" for n in sorted(os.listdir(subagents)) if n.endswith(".jsonl"))
    return lines or ["세션 기록 없음"]


def cmd_question_preview(ctx, args):
    record = question_records.validate_document(verification.read_json(args.input))
    return [question_records.render_document(record).rstrip("\n")]


def cmd_question_publish(ctx, args):
    require_folder(ctx)
    record = question_records.publish_document(
        ctx.folder, verification.read_json(args.input), getattr(args, "expect_revision", ""), editorial=args.editorial,
    )
    return [f"질문 저장: {record['id']}.md (revision {record['revision']})"]


def cmd_question_answer(ctx, args):
    require_folder(ctx)
    record = question_records.answer_document(ctx.folder, args.name, verification.read_json(args.input))
    return [f"답변 저장: {record['id']}.md (revision {record['revision']})"]


def cmd_question_restore(ctx, args):
    require_folder(ctx)
    record = question_records.restore_document(ctx.folder, args.input, args.source, args.expect_revision, args.expect_sha256)
    return [f"질문 기록 복구: {record['id']}.md (revision {record['revision']})"]


def cmd_question_show(ctx, args):
    require_folder(ctx)
    return [json.dumps(question_records.read_document(ctx.folder, args.name), ensure_ascii=False, indent=2)]


def cmd_question_render(ctx, args):
    require_folder(ctx)
    record = question_records.render_saved_document(ctx.folder, args.name)
    return [f"질문 문서 생성: {record['id']}.md (revision {record['revision']})"]


def cmd_question_check(ctx, args):
    require_folder(ctx)
    inspected = question_inspections(ctx.folder)
    issues = []
    for name, _, issue in inspected:
        if not issue:
            continue
        if name == "questions":
            issues.append(f"questions 기록 확인 필요: {issue}; questions 폴더와 JSON 파일 이름을 확인할 것")
        else:
            source = os.path.join(ctx.folder, "questions", f"{name[:-3]}.json")
            issues.append(f"{name} 기록 확인 필요: {issue}; 정본 {source} 확인 후 question publish --input <JSONfile>, "
                          f"생성본 복구는 question render {name}")
    if issues:
        args.exit_code = FAILED
        return issues
    return [f"질문 기록 정상: {len(inspected)}개"]


def cmd_document_share(ctx, args):
    require_folder(ctx)
    names = share_documents(ctx.folder, args.paths)
    return [f"공유함: {', '.join(names)}"]


def cmd_document_hide(ctx, args):
    require_folder(ctx)
    names = hide_documents(ctx.folder, args.paths)
    automatic = sorted({canonical_document_path(ctx.folder, name)[0] for name in args.paths if auto_question_document(ctx.folder, name)})
    hidden = sorted(set(names) - set(automatic))
    result = [f"숨김: {', '.join(hidden) or '변경 없음'}"]
    if automatic:
        result.append(f"자동 표시 유지: {', '.join(automatic)}")
    return result


def cmd_document_list(ctx, args):
    require_folder(ctx)
    automatic = sorted(auto_question_documents(ctx.folder))
    result = []
    try:
        names = sorted(shared_documents(ctx.folder))
        result.append(f"공유 중: {', '.join(names) or '없음'}")
    except (OSError, UnicodeError, StateError) as error:
        result.append(f"읽기 실패: {SHARED_DOCUMENTS_FILE} ({error})")
    if automatic:
        result.append(f"자동 표시: {', '.join(automatic)}")
    return result


def build_parser():
    parser = OneLineParser(prog="mission_state.py", description="미션 상태 파일 쓰기")
    parser.add_argument("--project", help="어댑터를 찾기 시작할 폴더 (기본: 현재 폴더)")
    parser.add_argument("--mission", help="미션 id (기본: <stateDir>/ACTIVE 의 id)")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--project", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    common.add_argument("--mission", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)

    def leaf(parent, name, handler):
        child = parent.add_parser(name, parents=[common])
        child.set_defaults(handler=handler)
        return child

    init = leaf(sub, "init", cmd_init)
    init.add_argument("--goal")
    init.add_argument("--tier", help="S|M|L. S 는 --force-tier 없이는 거부, 없으면 경고")
    init.add_argument("--force-tier", action="store_true", help="S 티어도 미션으로 연다(사용자가 원할 때만)")
    leaf(sub, "status", cmd_status)
    leaf(sub, "context", cmd_context)
    leaf(sub, "transcripts", cmd_transcripts)
    leaf(sub, "active", cmd_active).add_argument("--hours", type=float, help="끊김 기준 시간 (기본: 어댑터 mission.staleHours, 24)")
    close_stale = leaf(sub, "close-stale", cmd_close_stale)
    close_stale.add_argument("--force-close-stale", action="store_true", help="실제로 닫음. 없으면 미리보기")
    close_stale.add_argument("--hours", type=float, help="끊김 기준 시간 (기본: 어댑터 mission.staleHours, 24)")
    close_stale.add_argument("--keep-state", action="store_true", help="mission.md 상태를 진행으로 둠")
    close_stale.add_argument("--reason", default="")

    question = sub.add_parser("question").add_subparsers(dest="action", required=True)
    preview = leaf(question, "preview", cmd_question_preview)
    preview.add_argument("--input", required=True)
    publish = leaf(question, "publish", cmd_question_publish)
    publish.add_argument("--input", required=True)
    publish.add_argument("--expect-revision", type=int, default=argparse.SUPPRESS)
    publish.add_argument("--editorial", action="store_true")
    answer = leaf(question, "answer", cmd_question_answer)
    answer.add_argument("name")
    answer.add_argument("--input", required=True)
    restore = leaf(question, "restore", cmd_question_restore)
    restore.add_argument("--input", required=True)
    restore.add_argument("--source", required=True)
    restore.add_argument("--expect-revision", type=int, required=True)
    restore.add_argument("--expect-sha256", required=True)
    leaf(question, "show", cmd_question_show).add_argument("name")
    leaf(question, "render", cmd_question_render).add_argument("name")
    leaf(question, "check", cmd_question_check)

    document = sub.add_parser("document").add_subparsers(dest="action", required=True)
    leaf(document, "share", cmd_document_share).add_argument("paths", nargs="+")
    leaf(document, "hide", cmd_document_hide).add_argument("paths", nargs="+")
    leaf(document, "list", cmd_document_list)

    verify = leaf(sub, "verify", cmd_verify)
    verify.add_argument("id")
    verify.add_argument("--plan", required=True)
    verify.add_argument("--force", action="store_true")
    review = leaf(sub, "review", cmd_review)
    review.add_argument("id")
    for field in ("receipt", "evidence", "reviewer"):
        review.add_argument(f"--{field}", required=True)
    review.add_argument("--kind", required=True, choices=("poc", "unit", "integration"))
    review.add_argument("--status", required=True, choices=("clean", "findings"))
    review.add_argument("--findings-count", type=int, default=-1, help="남은 지적 수. 생략하면 미기록")
    review.add_argument("--fixed-count", type=int, default=-1, help="직전 지적 중 이번에 해소를 확인한 수")
    scope = leaf(sub, "review-scope", cmd_review_scope)
    scope.add_argument("id")
    scope.add_argument("--plan", required=True)
    complete = leaf(sub, "complete", cmd_complete)
    complete.add_argument("id")
    for field in ("receipt", "review", "done-evidence"):
        complete.add_argument(f"--{field}", required=True)

    mission = sub.add_parser("mission").add_subparsers(dest="action", required=True)
    mission_set = leaf(mission, "set", cmd_mission_set)
    mission_set.add_argument("--stage")
    mission_set.add_argument("--confirm", action="append", default=[], metavar="K=V")
    mission_set.add_argument("--state", choices=MISSION_STATES)
    mission_set.add_argument("--poc", metavar="PATH")

    unit = sub.add_parser("unit").add_subparsers(dest="action", required=True)
    add = leaf(unit, "add", cmd_unit_add)
    add.add_argument("id")
    for name in BRIEF_FIELDS:
        add.add_argument(f"--{name}", default="")
    add.add_argument("--human", default=NONE_CELL)
    add.add_argument("--after", default=NONE_CELL)
    unit_set = leaf(unit, "set", cmd_unit_set)
    unit_set.add_argument("id")
    unit_set.add_argument("--status", choices=UNIT_STATUSES)
    unit_set.add_argument("--unit")
    for name in EDITABLE_FIELDS:
        unit_set.add_argument(f"--{name}")
    leaf(unit, "list", cmd_unit_list).add_argument("--status", choices=UNIT_STATUSES)

    ledger = sub.add_parser("ledger").add_subparsers(dest="action", required=True)
    record = leaf(ledger, "record", cmd_ledger_record)
    record.add_argument("unit")
    record.add_argument("--status", required=True, choices=LEDGER_STATUSES)
    record.add_argument("--grade", required=True)
    record.add_argument("--evidence", default="")
    record.add_argument("--files", default="")

    decision = sub.add_parser("decision").add_subparsers(dest="action", required=True)
    decide = leaf(decision, "add", cmd_decision_add)
    for name in ("what", "chosen", "why", "affects"):
        decide.add_argument(f"--{name}", default="")
    decide.add_argument("--fixes")
    return parser


def main(argv=None, runtime="claude"):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        try:
            args = build_parser().parse_args(argv)
        except SystemExit:
            return OK
        try:
            located = slots.locate(args.project, runtime)
            adapter = slots.read_adapter(located.adapter_path, runtime)
            state_dir = mission_guard.state_dir_of(located.project, adapter)
        except (slots.UsageError, mission_guard.GuardOff) as e:
            raise StateError(str(e))
        mission_id = args.mission or ""
        if args.handler == cmd_active:
            mission_id = "-"
        elif not mission_id:
            active = mission_guard.read_active(state_dir)
            if active.corrupt:
                raise StateError(f"ACTIVE 손상: {mission_guard.active_path_of(state_dir)}, --mission <id> 로 지정하거나 확인 후 지울 것")
            if not active.id:
                raise StateError("미션 id 없음: --mission <id> 를 주거나 `echo han-mission open <id>` 로 먼저 열 것")
            mission_id = active.id
        if args.handler != cmd_active and not mission_guard.valid_mission_id(mission_id):
            raise StateError(f"미션 id 형식 오류: {mission_id}")
        readonly = args.handler in (
            cmd_status, cmd_context, cmd_transcripts, cmd_unit_list, cmd_review_scope, cmd_document_list, cmd_active,
            cmd_question_preview, cmd_question_show, cmd_question_check,
        )
        lock_name = mission_id
        if args.handler == cmd_verify:
            if not UNIT_ID.fullmatch(args.id):
                raise StateError("검증 단위 id 형식 오류")
            lock_name = f"{mission_id}-verify-{args.id}"
        lock = contextlib.nullcontext() if readonly else slots.FileLock(
            os.path.join(state_dir, ".locks", f"{lock_name}.lock"), "미션 상태", timeout=2,
        )
        with lock:
            lines = args.handler(Context(adapter, state_dir, mission_id, runtime, located.project), args)
    except (StateError, OSError, verification.VerificationError, question_records.QuestionRecordError, slots.LockBusy) as e:
        print(f"오류: {e}", file=sys.stderr)
        return FAILED
    for line in lines:
        print(line)
    return getattr(args, "exit_code", OK)


if __name__ == "__main__":
    sys.exit(main())
