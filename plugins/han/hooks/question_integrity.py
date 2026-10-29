import json
import os
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
SCRIPTS = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "skills", "mission", "scripts"))
sys.path.insert(0, SCRIPTS)

import mission_guard
import mission_state
import question_records
import verification
from hook_runtime import normalize_payload
from question_write_guard import adapter_contexts


SUPPORTED_TOOLS = ("Bash", "PowerShell", "exec_command", "Write", "Edit", "MultiEdit", "NotebookEdit", "apply_patch", "ApplyPatch")
CACHE_DIR = Path(tempfile.gettempdir()) / "han-question-integrity"


def question_fingerprint(folder):
    try:
        names = set(question_records.document_names(folder))
        names.update(name for name in os.listdir(folder) if mission_state.QUESTION_FILE.fullmatch(name))
        paths = [question_records.contained_path(folder, name) for name in sorted(names)]
        paths.extend(question_records.history_path(folder, name) for name in sorted(names))
        pending = [question_records.contained_path(folder, "questions")]
        while pending:
            directory = pending.pop()
            if not directory.exists():
                continue
            with os.scandir(directory) as entries:
                for entry in entries:
                    path = question_records.contained_path(folder, *Path(entry.path).relative_to(Path(folder).resolve()).parts)
                    if entry.is_dir():
                        if path.resolve() != path.absolute():
                            return ""
                        pending.append(path)
                    else:
                        paths.append(path)
        return verification.digest([(str(path), str(path.resolve()), verification.file_hash(path)) for path in sorted(paths)])
    except (OSError, ValueError, RuntimeError):
        return ""


def inspection_cache_path(folder, session, runtime):
    sources = (__file__, mission_state.__file__, question_records.__file__, verification.__file__,
               os.path.join(SCRIPTS, "question_summary.py"))
    key = verification.digest([str(Path(folder).resolve()), session, runtime,
                               [(str(Path(path).resolve()), verification.file_hash(path)) for path in sources]])
    return CACHE_DIR / f"{key}.json"


def cached_inspection(path, fingerprint):
    if not fingerprint:
        return False
    try:
        return json.loads(path.read_text(encoding="utf-8")) == {"fingerprint": fingerprint}
    except (OSError, ValueError):
        return False


def inspection_issues(folder, session, runtime, event):
    if event == "Stop":
        return [f"{name}: {issue}" for name, _, issue in mission_state.question_inspections(folder) if issue]
    fingerprint = question_fingerprint(folder)
    try:
        path = inspection_cache_path(folder, session, runtime)
    except (OSError, ValueError):
        fingerprint = ""
        path = CACHE_DIR / "unavailable.json"
    if cached_inspection(path, fingerprint):
        return []
    issues = [f"{name}: {issue}" for name, _, issue in mission_state.question_inspections(folder) if issue]
    if not issues and fingerprint and question_fingerprint(folder) == fingerprint:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            question_records.write_json(path, {"fingerprint": fingerprint})
        except OSError:
            pass
    return issues


def notice(event, message):
    output = {"systemMessage": message}
    if event == "PostToolUse":
        output["hookSpecificOutput"] = {"hookEventName": event, "additionalContext": message}
    return output


def block(event, reason):
    output = {"decision": "block", "reason": reason}
    if event == "PostToolUse":
        output["hookSpecificOutput"] = {"hookEventName": event, "additionalContext": reason}
    return output


def repair_direction(located, active, folder, runtime):
    command = f'python "{os.path.join(SCRIPTS, "mission_state.py")}" --runtime {runtime} question check --project "{located.project}" --mission "{active.id}"'
    return (
        f"질문 기록 확인 필요: {folder}. {command} 로 확인할 것. "
        "Markdown에만 남은 답·승인은 먼저 원문을 별도 보존하고 실제 답·대상 개정을 확인할 것. "
        "질문 정의·개정이 그대로이고 답 기록만 빠졌으면 question answer <문서.md> --input <reply.json> 으로 사용자 원문과 updates를 기록할 것. "
        "정본에 누락된 문항·설계까지 복구해야 하면 독립 검토한 JSON으로 question restore "
        "--input <reviewed.json> --source <saved-original.md> --expect-revision <revision> --expect-sha256 <sha256> 를 사용할 것. "
        "현재 JSON이 올바르고 생성본만 잘못됐으면 question render <문서.md>, 질문 개정은 question publish --input <draft.json> 를 사용할 것. "
        "JSON 누락·손상은 신뢰할 수 있는 원본·백업과 대조해 명시적으로 복구할 것. 답·승인을 추론하거나 ACTIVE를 인계하지 말 것."
    )


def decide(payload, runtime=""):
    if not isinstance(payload, dict):
        return notice("", "질문 무결성 검사 미실행: 훅 입력이 객체가 아님; 현재 미션에서 question check를 실행할 것")
    payload = normalize_payload(payload, runtime)
    runtime = payload["han_runtime"]
    event = payload.get("hook_event_name")
    if not isinstance(event, str) or not event:
        return notice("", "질문 무결성 검사 미실행: hook_event_name 없음; 현재 미션에서 question check를 실행할 것")
    if event not in ("PostToolUse", "Stop"):
        return {}
    if event == "PostToolUse" and payload.get("tool_name") not in SUPPORTED_TOOLS:
        return {}
    try:
        contexts = adapter_contexts(payload, [], mission_guard)
    except Exception as error:
        return notice(event, f"질문 무결성 검사 미실행: 어댑터 문맥 확인 실패 ({type(error).__name__}: {error}); 현재 미션에서 question check를 실행할 것")
    for located, adapter in contexts:
        try:
            state_dir = mission_guard.state_dir_of(located.project, adapter)
            active = mission_guard.read_active(state_dir)
        except Exception as error:
            return notice(event, f"질문 무결성 검사 미실행: ACTIVE 문맥 확인 실패 ({type(error).__name__}: {error}); 현재 미션에서 question check를 실행할 것")
        if not active.id:
            continue
        if active.corrupt or not mission_guard.valid_mission_id(active.id) or not active.session:
            return notice(event, f"질문 무결성 검사 미실행: 유효한 소유 세션을 확인할 수 없음 ({mission_guard.active_path_of(state_dir)}); ACTIVE 원본을 확인하고 question check를 실행할 것")
        if not isinstance(payload.get("session_id"), str) or not payload["session_id"]:
            return notice(event, "질문 무결성 검사 미실행: 훅 session_id 없음; 현재 미션에서 question check를 실행할 것")
        if mission_guard.runs_in_other_session(active, payload):
            continue
        folder = os.path.join(state_dir, active.id)
        try:
            root = os.path.realpath(state_dir)
            resolved = os.path.realpath(folder)
            if os.path.commonpath((root, resolved)) != root or resolved == root:
                raise ValueError(f"미션 폴더가 stateDir 밖을 가리킴: {folder}")
            issues = inspection_issues(folder, active.session, runtime, event)
        except Exception as error:
            issues = [f"검사 실패 ({type(error).__name__}: {error})"]
        if issues:
            return block(event, "\n".join([*issues, repair_direction(located, active, folder, runtime)]))
    return {}


def main(runtime=""):
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
    except (OSError, ValueError, UnicodeError) as error:
        output = notice("", f"질문 무결성 검사 미실행: 훅 입력 읽기 실패 ({type(error).__name__}); 현재 미션에서 question check를 실행할 것")
    else:
        output = decide(payload, runtime)
    if output:
        sys.stdout.write(json.dumps(output, ensure_ascii=True))
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
