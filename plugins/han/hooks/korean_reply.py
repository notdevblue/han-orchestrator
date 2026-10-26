import json
import os
import re
import sys

DISABLE_ENV = "HAN_KOREAN_REPLY_DISABLE"
CODEX_ENABLE_ENV = "HAN_CODEX_KOREAN_REPLY"
CONFIG_DIR_ENV = "CLAUDE_CONFIG_DIR"
PROJECT_DIR_ENV = "CLAUDE_PROJECT_DIR"
DISABLE_VALUES = ("1", "true", "yes")
ENABLE_VALUES = ("on", "true", "1", "yes")
KOREAN_MARKERS = ("한국어", "korean")
MIN_LETTERS = 80
MIN_HANGUL_RATIO = 0.3
TRANSCRIPT_TAIL_BYTES = 1024 * 1024
REASON = "답을 한국어로 다시 써라. 코드·식별자·인용은 원문 유지"
CODE_BLOCK = re.compile(r"```.*?(?:```|\Z)", re.DOTALL)
INLINE_CODE = re.compile(r"`[^`\n]*`")
QUOTE_LINE = re.compile(r"^[ \t]*>.*$", re.MULTILINE)
URL = re.compile(r"\w+://\S+")
PATH_TOKEN = re.compile(r"\S*[/\\]\S*")
DOTTED_TOKEN = re.compile(r"[\w-]+(?:\.[\w-]+)+")
HANGUL = re.compile(r"[ᄀ-ᇿ㄰-㆏가-힣]")
LATIN = re.compile(r"[A-Za-zÀ-ɏ]")


def read_payload():
    if sys.stdin is None or sys.stdin.isatty():
        return {}
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def disabled():
    return os.environ.get(DISABLE_ENV, "").strip().lower() in DISABLE_VALUES


def settings_paths(payload):
    config_dir = os.environ.get(CONFIG_DIR_ENV, "").strip() or os.path.join(os.path.expanduser("~"), ".claude")
    projects = []
    for candidate in (os.environ.get(PROJECT_DIR_ENV, ""), str(payload.get("cwd") or "")):
        if candidate.strip() and candidate not in projects:
            projects.append(candidate)
    paths = []
    for project in projects:
        paths.append(os.path.join(project, ".claude", "settings.local.json"))
        paths.append(os.path.join(project, ".claude", "settings.json"))
    paths.append(os.path.join(config_dir, "settings.json"))
    return paths


def configured_language(payload):
    # 우선순위 순서로 훑어 language 를 처음 정의한 파일의 값을 씀
    for path in settings_paths(payload):
        try:
            with open(path, encoding="utf-8-sig") as f:
                settings = json.load(f)
        except (OSError, ValueError):
            continue
        language = settings.get("language") if isinstance(settings, dict) else None
        if isinstance(language, str) and language.strip():
            return language
    return ""


def wants_korean(payload, runtime):
    if runtime == "codex":
        return os.environ.get(CODEX_ENABLE_ENV, "").strip().lower() in ENABLE_VALUES
    language = configured_language(payload).lower()
    return any(marker in language for marker in KOREAN_MARKERS)


def prose_of(text):
    for pattern in (CODE_BLOCK, INLINE_CODE, QUOTE_LINE, URL, PATH_TOKEN, DOTTED_TOKEN):
        text = pattern.sub(" ", text)
    return text


def needs_korean(text):
    prose = prose_of(text)
    hangul = len(HANGUL.findall(prose))
    latin = len(LATIN.findall(prose))
    letters = hangul + latin
    if letters < MIN_LETTERS:
        return False
    return hangul / letters < MIN_HANGUL_RATIO


def entry_text(entry):
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text")
    return ""


def last_reply_from_transcript(path):
    # last_assistant_message 가 없는 입력용. 마지막 사용자 프롬프트 뒤의 마지막 assistant 글만 봄
    if not path or not os.path.isfile(path):
        return ""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - TRANSCRIPT_TAIL_BYTES))
        tail = f.read().decode("utf-8", errors="replace")
    for line in reversed(tail.splitlines()):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("isSidechain"):
            continue
        kind = entry.get("type")
        if kind == "assistant":
            text = entry_text(entry)
            if text.strip():
                return text
        if kind == "user" and entry_text(entry).strip():
            return ""
    return ""


def last_reply(payload, runtime):
    message = payload.get("last_assistant_message")
    if isinstance(message, str):
        return message
    if runtime == "codex":
        return ""
    return last_reply_from_transcript(str(payload.get("transcript_path") or ""))


def decide(payload, runtime="claude"):
    if disabled() or payload.get("stop_hook_active"):
        return {}
    if not wants_korean(payload, runtime):
        return {}
    if needs_korean(last_reply(payload, runtime)):
        return {"decision": "block", "reason": REASON}
    return {}


def main(runtime="claude"):
    try:
        output = decide(read_payload(), runtime)
    except Exception:  # 검사 실패로 세션을 막지 않음
        output = {}
    if output:
        sys.stdout.write(json.dumps(output, ensure_ascii=True))
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
