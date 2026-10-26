import json
import os
import re
import sys
import tempfile

DISABLE_ENV = "HAN_SESSION_TITLE_DISABLE"
DISABLE_VALUES = ("1", "true", "yes")
TITLE_LIMIT = 40
SCAN_LIMIT_BYTES = 5 * 1024 * 1024
MISSION_COMMAND = re.compile(r"^[/$]han(?:-codex)?:(?:han-)?mission\s+", re.IGNORECASE)
SPACES = re.compile(r"\s+")
TITLED_LINE = re.compile(r'"type"\s*:\s*"custom-title"')
USER_LINE = re.compile(r'"type"\s*:\s*"user"')
DONE_DIR = os.path.join(tempfile.gettempdir(), "han-session-title")


def read_payload():
    if sys.stdin is None or sys.stdin.isatty():
        return {}
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def title_of(prompt):
    lines = [line.strip() for line in prompt.splitlines() if line.strip()]
    if not lines:
        return ""
    first = SPACES.sub(" ", lines[0])
    mission = MISSION_COMMAND.match(first)
    if mission:
        first = "미션: " + first[mission.end():]
    return first if len(first) <= TITLE_LIMIT else first[: TITLE_LIMIT - 1] + "…"


def prompt_text(entry):
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(block.get("text", "") for block in content if isinstance(block, dict))
    return ""


def already_started(transcript, prompt):
    # 이미 이름이 있거나(-n·/rename·이 훅) 앞 프롬프트가 있는 세션은 이어서 연 것이니 이름을 안 바꿈
    if not transcript or not os.path.isfile(transcript):
        return False
    if os.path.getsize(transcript) > SCAN_LIMIT_BYTES:
        return True
    with open(transcript, encoding="utf-8", errors="replace") as f:
        for line in f:
            if TITLED_LINE.search(line):
                return True
            if not USER_LINE.search(line) or '"promptId"' not in line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if prompt_text(entry).strip() != prompt.strip():
                return True
    return False


def decide(payload):
    if os.environ.get(DISABLE_ENV, "").strip().lower() in DISABLE_VALUES:
        return {}
    session = str(payload.get("session_id") or "")
    prompt = str(payload.get("prompt") or "")
    if not session or not re.fullmatch(r"[A-Za-z0-9_-]+", session):
        return {}
    done = os.path.join(DONE_DIR, session)
    if os.path.exists(done):
        return {}
    named = str(payload.get("session_title") or "").strip()
    title = "" if named or already_started(str(payload.get("transcript_path") or ""), prompt) else title_of(prompt)
    os.makedirs(DONE_DIR, exist_ok=True)
    with open(done, "w", encoding="utf-8") as f:
        f.write(title)
    if not title:
        return {}
    return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "sessionTitle": title}}


def main():
    try:
        output = decide(read_payload())
    except Exception:  # 이름 붙이기 실패로 프롬프트를 막지 않음
        output = {}
    if output:
        sys.stdout.write(json.dumps(output, ensure_ascii=True))
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
