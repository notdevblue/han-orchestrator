import html
import re
from dataclasses import dataclass


@dataclass
class QuestionItem:
    number: str
    title: str
    status: str


@dataclass
class QuestionSummary:
    name: str
    title: str
    items: list[QuestionItem]
    pending: bool
    count_known: bool
    total_known: bool

    @property
    def pending_count(self):
        return sum(item.status == "pending" for item in self.items)


def markdown_lines(text):
    lines = []
    fence = ""
    comment = False
    for line in text.splitlines():
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence:
            if marker and marker.group(1)[0] == fence[0] and len(marker.group(1)) >= len(fence) and not marker.group(2).strip():
                fence = ""
            continue
        if not comment and line.expandtabs(4).startswith("    "):
            continue
        visible = ""
        cursor = 0
        while cursor < len(line):
            if comment:
                end = line.find("-->", cursor)
                if end < 0:
                    break
                comment = False
                cursor = end + 3
            else:
                start = line.find("<!--", cursor)
                if start < 0:
                    visible += line[cursor:]
                    break
                visible += line[cursor:start]
                comment = True
                cursor = start + 4
        if visible.expandtabs(4).startswith("    "):
            continue
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", visible)
        if marker:
            fence = marker.group(1)
            continue
        lines.append(visible)
    return lines


def plain_text(text):
    code = []

    def preserve_code(match):
        code.append(match.group(2))
        return f"\x00{len(code) - 1}\x00"

    text = re.sub(r"(`+)(.*?)\1", preserve_code, text)
    text = re.sub(r"</?[A-Za-z][^>]*>", "", text)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"!?\[([^\]]*)\]\[[^\]]*\]", r"\1", text)
    for marker in (r"\*\*", "__", "~~", r"\*"):
        text = re.sub(rf"{marker}(.+?){marker}", r"\1", text)
    text = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"\1", text)
    text = re.sub(r"\x00(\d+)\x00", lambda match: code[int(match.group(1))], text)
    return html.unescape(text).strip()


def explicit_status(text):
    value = plain_text(text).strip()
    if re.fullmatch(r"(?:답변\s*대기|대기|미답|미결)(?:\([^)]*\))?", value):
        return "pending"
    if re.fullmatch(r"(?:답변(?:\s*완료)?|답변됨|완료)(?:\([^)]*\))?", value) and not re.search(r"부분|일부|미결|미답|대기", value):
        return "answered"
    return "unknown"


def is_answer_section(value):
    date = r"\d{4}[-./]\d{1,2}[-./]\d{1,2}"
    return bool(re.fullmatch(rf"답(?:변)?(?:\s*[:：].*|\s*\({date}(?:\s+[^)]*)?\)|\s+{date})?", value))


def answer_recorded(lines):
    inside = False
    for line in lines:
        heading = re.match(r"^(#{1,6})[ \t]+(.+?)[ \t]*$", line)
        if heading:
            value = plain_text(re.sub(r"[ \t]+#+$", "", heading.group(2)))
            if is_answer_section(value) and len(heading.group(1)) in (2, 3):
                inside = True
                continue
            if len(heading.group(1)) <= 2:
                inside = False
        if inside and line.strip() and plain_text(line) not in ("-", "---", "대기", "(대기)", "답변 대기"):
            return True
    return False


def consistent_status(states):
    if states and len(set(states)) == 1:
        return states[0]
    return "unknown"


def summarize_question(name: str, text: str, pending: bool) -> QuestionSummary:
    lines = markdown_lines(text)
    title = name
    title_found = False
    items = []
    item_ids = []
    item_states = {}
    metadata = {}
    global_states = []
    approval_waiting = False
    preamble = True
    inside = False
    sections = 0
    current_key = 0
    for line in lines:
        heading = re.match(r"^(#{1,6})[ \t]+(.+?)[ \t]*$", line)
        if heading:
            level = len(heading.group(1))
            value = plain_text(re.sub(r"[ \t]+#+$", "", heading.group(2)))
            if level == 1 and not title_found:
                title = value or name
                title_found = True
            if level <= 2:
                if level == 2:
                    preamble = False
                inside = level == 2 and value == "결정할 것"
                current_key = 0
                if inside:
                    sections += 1
            elif inside and level == 3:
                if is_answer_section(value):
                    inside = False
                    current_key = 0
                    continue
                numbered = re.fullmatch(r"(?:(\d+)[.)]|([A-Z]\d+\s+결정\s+(\d+)))\s+(.+)", value)
                number = ""
                identifier = ""
                item_title = value
                if numbered:
                    item_title = numbered.group(4)
                    if numbered.group(1):
                        number = numbered.group(1)
                        identifier = number
                    else:
                        number = numbered.group(2)
                        identifier = numbered.group(3)
                items.append(QuestionItem(number, item_title, "unknown"))
                item_ids.append(identifier)
                current_key = len(items)
            continue
        if preamble:
            for part in plain_text(line).split("·"):
                part = part.strip()
                numbered = re.fullmatch(r"질문\s+(\d+)\s*[:：]?\s+(.+)", part)
                if numbered:
                    metadata.setdefault(numbered.group(1), []).append(explicit_status(numbered.group(2)))
                elif part in ("답변 대기", "답변 완료", "답변완료"):
                    global_states.append(explicit_status(part))
                elif part in ("승인 대기", "전체 승인 확인 대기"):
                    approval_waiting = True
                elif re.match(r"^(?:답변|부분\s*답변|일부\s*답변)(?:\s|[(:]|$)", part):
                    global_states.append("unknown")
        if inside and current_key:
            state = re.fullmatch(r"\s*(?:[-*]\s*)?답(?:변)?\s*[:：]\s*(.*?)\s*", plain_text(line))
            if state:
                item_states.setdefault(current_key, []).append(explicit_status(state.group(1)))
    numbers = [identifier for identifier in item_ids if identifier]
    structure_known = bool(items) and sections == 1 and len(set(numbers)) == len(numbers) and set(metadata).issubset(numbers)
    recorded = answer_recorded(lines)
    global_state = consistent_status(global_states)
    for key, item in enumerate(items, 1):
        states = metadata.get(item_ids[key - 1], []) + item_states.get(key, [])
        if states:
            item.status = consistent_status(states)
        elif not metadata and not item_states and global_state == "answered":
            item.status = "answered"
        elif not metadata and not item_states and global_state == "pending" and not recorded:
            item.status = "pending"
    count_known = structure_known and all(item.status != "unknown" for item in items)
    is_confirm = name.startswith("confirm-")
    if is_confirm and not pending:
        for item in items:
            item.status = "answered"
        return QuestionSummary(name, title, items, False, structure_known, structure_known)
    if approval_waiting:
        pending = True
    elif any(item.status == "pending" for item in items) or any(set(states) == {"pending"} for states in metadata.values()):
        pending = True
    elif any(consistent_status(states) == "unknown" for states in (global_states, *metadata.values(), *item_states.values()) if states):
        pending = True
    elif (metadata or item_states) and any(item.status == "unknown" for item in items):
        pending = True
    elif count_known:
        pending = False
    elif global_state == "answered" and not items and not metadata:
        pending = False
    elif global_state == "pending" and not recorded:
        pending = True
    return QuestionSummary(name, title, items, pending, count_known, structure_known)
