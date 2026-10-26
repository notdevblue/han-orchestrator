import glob
import json
import os
import sys

DISABLE_ENV = "HAN_RULES_DISABLE"
PLUGIN_ROOT_ENV = "CLAUDE_PLUGIN_ROOT"
ENV_FILE_ENV = "CLAUDE_ENV_FILE"
USER_CONFIG_ENV = "CLAUDE_PLUGIN_OPTION_ADAPTERS_DIR"
INJECT_ENV = "CLAUDE_PLUGIN_OPTION_INJECT_RULES"
REGISTRY_ENV = "HAN_ADAPTERS_DIR"
TRANSCRIPT_ENV = "HAN_TRANSCRIPT"
DISABLE_VALUES = ("1", "true", "yes")
INJECT_VALUES = ("on", "true", "1", "yes")
SESSION_START = "SessionStart"
EVENTS = (SESSION_START, "SubagentStart")
HEADER = "다음은 사용자 개인 규칙(han 플러그인)이다. 프로젝트 규칙과 함께 따른다."
CONTEXT_LIMIT = 10000
MAIN_SESSION_RULES = "main-session.md"
SHELL_ESCAPED = ('"', "$", "`")


def plugin_root():
    configured = os.environ.get(PLUGIN_ROOT_ENV)
    if configured:
        return configured
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def disabled():
    return os.environ.get(DISABLE_ENV, "").strip().lower() in DISABLE_VALUES


def inject_enabled():
    return os.environ.get(INJECT_ENV, "").strip().lower() in INJECT_VALUES


def read_payload():
    if sys.stdin is None or sys.stdin.isatty():
        return {}
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def event_of(payload):
    name = payload.get("hook_event_name")
    return name if name in EVENTS else SESSION_START


def runtime_root(runtime):
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "codex") if runtime == "codex" else plugin_root()
    return os.path.abspath(root)


def read_rules(runtime="claude", event=SESSION_START):
    root = runtime_root(runtime)
    pattern = os.path.join(glob.escape(os.path.join(root, "rules")), "*.md")
    paths = sorted(glob.glob(pattern), key=os.path.basename)
    shared = [path for path in paths if os.path.basename(path) != MAIN_SESSION_RULES]
    main = [path for path in paths if os.path.basename(path) == MAIN_SESSION_RULES]
    texts = []
    for path in shared + main if event == SESSION_START else shared:
        with open(path, encoding="utf-8-sig") as f:
            text = f.read().strip()
        if text:
            texts.append(text)
    return texts


def human_document_context(runtime):
    root = runtime_root(runtime)
    skill = "han-mission" if runtime == "codex" else "mission"
    paths = (
        ("공통 규칙", os.path.join(root, "rules", "human-documents.md")),
        ("작성 안내", os.path.join(root, "skills", skill, "references", "human-writing.md")),
    )
    if not all(os.path.isfile(path) for _, path in paths):
        return ""
    return "\n".join(["## 사람용 문서 자료", *[f"- {name}: `{path}`" for name, path in paths]])


def build_context(texts, document_context=""):
    sections = [section for section in [document_context, *texts] if section]
    return "\n\n".join([HEADER, *sections]) if sections else ""


def fit_limit(context):
    if len(context) <= CONTEXT_LIMIT:
        return context, ""
    warning = f"개인 규칙 {len(context)}자가 상한 {CONTEXT_LIMIT}자를 넘어 뒤 {len(context) - CONTEXT_LIMIT}자가 잘림"
    return context[:CONTEXT_LIMIT], warning


def export_line(name, value):
    escaped = value.replace("\\", "/")
    for char in SHELL_ESCAPED:
        escaped = escaped.replace(char, "\\" + char)
    return f'export {name}="{escaped}"'


def has_line(path, line):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return line in f.read().splitlines()
    except FileNotFoundError:
        return False


def export_to_env_file(env_file, name, value, consequence):
    line = export_line(name, value)
    try:
        if has_line(env_file, line):
            return ""
        with open(env_file, "a", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")
    except OSError as e:
        return f"{name} 를 {ENV_FILE_ENV} 에 쓰지 못함 ({consequence}): {e}"
    return ""


def export_registry_dir():
    env_file = os.environ.get(ENV_FILE_ENV)
    configured = os.environ.get(USER_CONFIG_ENV, "").strip()
    if not env_file or not configured or os.environ.get(REGISTRY_ENV):
        return ""
    return export_to_env_file(env_file, REGISTRY_ENV, configured, "Bash 도구에서 어댑터 레지스트리를 못 찾을 수 있음")


def export_transcript_path(payload):
    env_file = os.environ.get(ENV_FILE_ENV)
    transcript = payload.get("transcript_path")
    if not env_file or not isinstance(transcript, str) or not transcript.strip():
        return ""
    return export_to_env_file(env_file, TRANSCRIPT_ENV, transcript.strip(), "Bash 도구에서 대화 기록 경로를 못 찾을 수 있음")


def render_output(event, context, warnings):
    output = {}
    if warnings:
        output["systemMessage"] = " / ".join(warnings)
    if context:
        output["hookSpecificOutput"] = {"hookEventName": event, "additionalContext": context}
    return output


def decide(payload, runtime="claude"):
    event = event_of(payload)
    warnings = []
    contexts = []
    if event == SESSION_START and runtime == "claude":
        for export_warning in (export_registry_dir(), export_transcript_path(payload)):
            if export_warning:
                warnings.append(export_warning)
    if event == SESSION_START and runtime == "codex":
        transcript = payload.get("transcript_path")
        if isinstance(transcript, str) and transcript.strip():
            contexts.append(f"han-distill 대화 기록 경로 (--transcript): {json.dumps(transcript.strip(), ensure_ascii=False)}")
    enabled = os.environ.get("HAN_CODEX_INJECT_RULES", "").strip().lower() in INJECT_VALUES if runtime == "codex" else inject_enabled()
    if not disabled() and enabled:
        context, limit_warning = fit_limit(build_context(read_rules(runtime, event), human_document_context(runtime)))
        contexts.append(context)
        if limit_warning:
            warnings.append(limit_warning)
    return render_output(event, "\n\n".join(filter(None, contexts)), warnings)


def main(runtime="claude"):
    try:
        output = decide(read_payload(), runtime)
    except Exception as e:  # 훅 실패로 세션을 막지 않고 알림만
        output = {"systemMessage": f"개인 규칙 주입 실패: {type(e).__name__}: {e}"}
    if output:
        sys.stdout.write(json.dumps(output, ensure_ascii=True))
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
