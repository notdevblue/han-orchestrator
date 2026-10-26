import json
import os
import posixpath
import re
import sys
import tempfile

DISABLE_ENV = "HAN_MEMORY_NOTICE_DISABLE"
CONFIG_DIR_ENV = "CLAUDE_CONFIG_DIR"
DISABLE_VALUES = ("1", "true", "yes")
NOTICE = "코드·네이밍·설계 규칙처럼 팀이 따를 내용이면 메모리 말고 프로젝트 가이드 문서에 쓴다. 메모리는 이 사용자만의 사실·선호만."
DONE_DIR = os.path.join(tempfile.gettempdir(), "han-memory-notice")


def read_payload():
    if sys.stdin is None or sys.stdin.isatty():
        return {}
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def slashed(path):
    return posixpath.normpath(path.replace("\\", "/"))


def config_dirs():
    dirs = [slashed(os.path.join(os.path.expanduser("~"), ".claude"))]
    configured = os.environ.get(CONFIG_DIR_ENV, "").strip()
    if configured:
        dirs.append(slashed(configured))
    return dirs


def is_memory_note(path):
    if not path.lower().endswith(".md"):
        return False
    target = slashed(path).lower()
    for config_dir in config_dirs():
        prefix = config_dir.lower().rstrip("/") + "/projects/"
        if not target.startswith(prefix):
            continue
        parts = target[len(prefix):].split("/")
        if len(parts) >= 3 and parts[0] and parts[1] == "memory":
            return True
    return False


def decide(payload):
    if os.environ.get(DISABLE_ENV, "").strip().lower() in DISABLE_VALUES:
        return {}
    path = (payload.get("tool_input") or {}).get("file_path")
    if not isinstance(path, str) or not is_memory_note(path):
        return {}
    session = str(payload.get("session_id") or "")
    if not session or not re.fullmatch(r"[A-Za-z0-9_-]+", session):
        return {}
    done = os.path.join(DONE_DIR, session)
    if os.path.exists(done):
        return {}
    os.makedirs(DONE_DIR, exist_ok=True)
    with open(done, "w", encoding="utf-8") as f:
        f.write(path)
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": NOTICE}}


def main():
    try:
        output = decide(read_payload())
    except Exception:  # 알림 실패로 쓰기를 막지 않음
        output = {}
    if output:
        sys.stdout.write(json.dumps(output, ensure_ascii=True))
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
