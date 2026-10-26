import fnmatch
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import NamedTuple

GUARDED_TOOLS = ("Bash", "PowerShell")
WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
SUPPORTED_SCHEMA = 1
ADAPTER_RELATIVE = (".claude", "orchestrator", "adapter.json")
REGISTRY_FILE = "projects.json"
REGISTRY_ENV = "HAN_ADAPTERS_DIR"
REPO_ENV = "HAN_REPO"
USER_CONFIG_ENV = "CLAUDE_PLUGIN_OPTION_ADAPTERS_DIR"
LOCAL_FILE = "local.json"
LOG_ENV = "HAN_GUARD_LOG"
LOG_FIELDS = ("session_id", "agent_id", "agent_type", "tool_name", "cwd", "hook_event_name")
LOG_COMMAND_LENGTH = 120
SESSIONS_FILE = "sessions.tsv"
SESSIONS_HEADER = ("at", "action", "session", "transcript")
SESSION_ACTIONS = ("open", "resume")
CELL_BREAKS = re.compile(r"[\t\r\n]+")
MAX_DEPTH = 6
STALE_HOURS_DEFAULT = 24
STALE_ENV = "HAN_STALE_HOURS"
HEARTBEAT_SECONDS = 5 * 60
STALE_SCAN_LIMIT = 5000
FORCE_CLOSE_HINT = "닫으려면 사용자 확인 후 mission_state.py close-stale --force-close-stale"
P4_EXECUTABLES = ("p4", "p4.exe")
# p4 전역 옵션 중 값을 받는 것(-c 작업 공간, -x 파일 목록 등). -C 는 charset 이라 대소문자를 가림
P4_VALUE_OPTIONS = ("-b", "-c", "-C", "-d", "-F", "-H", "-L", "-p", "-P", "-Q", "-r", "-u", "-v", "-x", "-z")
P4_WRITE_COMMANDS = frozenset((
    "add", "edit", "delete", "copy", "merge", "integrate", "integ", "move", "rename", "reconcile", "rec",
    "resolve", "revert", "submit", "shelve", "unshelve", "sync", "flush", "clean", "undo", "lock", "unlock",
    "change", "changelist", "open", "populate", "obliterate",
))
P4_READ_FLAG_COMMANDS = ("change", "changelist")  # -o 는 spec 출력만
P4_TOKEN = re.compile(r"""(?:"[^"]*"|'[^']*'|\S+)""")
P4C_NAME = "p4c.py"
P4C_SCRIPT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "skills", "mission", "scripts", P4C_NAME))
P4C_CALL = re.compile(
    r"""^(?:(?:"[^"]*[\\/]|'[^']*[\\/]|\S*[\\/])?(?:python3?|py)(?:\.exe)?["']?(?:\s+-\S+)*\s+)?"""
    r"""(?:"[^"]*p4c\.py"|'[^']*p4c\.py'|\S*p4c\.py)(?=\s|$)\s*(.*)$""",
    re.IGNORECASE | re.DOTALL,
)
P4C_OWN_VALUE_OPTIONS = ("--project", "--client")
P4C_OWN_FLAGS = ("--which", "--dry-run")

MARKER = re.compile(
    r"""^(?:echo|Write-Output|Write-Host)\s+["']?han-mission\s+(open|resume|close)\s+(\S+?)["']?\s*$""",
    re.IGNORECASE,
)
MISSION_ID = re.compile(r"\w[\w.\-]*")
MARKER_ALONE_REASON = "마커(han-mission open|resume|close)는 단독 호출로. 다른 명령과 섞으면 처리하지 않고 막음"

CONTINUATIONS = {
    "Bash": re.compile(r"\\\r?\n"),
    "PowerShell": re.compile(r"`\r?\n"),
}
ESCAPE_CHARS = {"Bash": "\\", "PowerShell": "`"}
CHAIN_PREFIX = re.compile(
    r"^(?:&\s*|\$?\(\s*|\{\s*|!\s*|(?:do|then|else|if|while|until|time|exec)\s+)+",
    re.IGNORECASE,
)
BASH_ASSIGN = re.compile(r"^(?:export\s+)?([A-Za-z_]\w*)=(.*)$", re.DOTALL)
POWERSHELL_ASSIGN = re.compile(r"^\$(?:env:)?([A-Za-z_]\w*)\s*=\s*(.*)$", re.DOTALL)
SIMPLE_VALUE = re.compile(r"""^(?:"[^"]*"|'[^']*'|\S*)$""")
FIRST_TOKEN_REST = re.compile(r"""^(?:"[^"]*"|'[^']*'|\S+)\s*(.*)$""", re.DOTALL)
VARIABLE_REFERENCE = re.compile(r"""(["']?)(?:\$\{(\w+)\}|\$env:(\w+)|\$(\w+))\1""")
COMMAND_SUBSTITUTION = ("$(", "`")
QUOTED_TEXT = re.compile(r"""\"[^\"]*\"|'[^']*'""")
BASH_GROUP_OPENER = re.compile(r"[$<>]\(")
POWERSHELL_GROUP_OPENER = re.compile(r"\(")
POWERSHELL_SUBEXPRESSION_OPENER = re.compile(r"\$\(")
BACKTICK_BODY = re.compile(r"`([^`]*)`")

WRAPPER_BODIES = (
    r"xargs(?:\s+-\S+)*",
    r"cmd(?:\.exe)?\s+/[ck]",
    r"env(?:\s+-\S+)*(?:\s+\w+=\S+)*",
    r"nohup",
    r"sudo(?:\s+-\S+)*",
    r"start(?:\s+/\w+)*",
    r"(?:powershell|pwsh)(?:\.exe)?(?:\s+-\w+(?:\s+[^-\s]\S*)?)*?\s+-c(?:ommand)?",
    r"(?:ba|da|z)?sh(?:\.exe)?(?:\s+-\w+)*?\s+-\w*c",
    r"eval",
    r"command",
    r"timeout\s+\S+",
)
WRAPPERS = tuple(
    re.compile(r"^(?:\S*[\\/])?" + body + r"(?=\s|$)\s*", re.IGNORECASE) for body in WRAPPER_BODIES
)


class GuardOff(Exception):
    pass


class AdapterNotFound(Exception):
    pass


class Active(NamedTuple):
    id: str
    session: str
    opened_at: str
    corrupt: bool = False


NO_ACTIVE = Active("", "", "")
CORRUPT_ACTIVE = Active("-", "", "", True)


class Staleness(NamedTuple):
    stale: bool
    last: float  # 마지막 활동 epoch 초, 0 이면 모름
    hours: float  # 마지막 활동 뒤 지난 시간
    limit: float  # 끊김 기준 시간, 0 이면 끔


NOT_STALE = Staleness(False, 0.0, 0.0, 0.0)


class Located(NamedTuple):
    project: str
    adapter_path: str


class Registry(NamedTuple):
    path: str
    source: str


class Verdict(NamedTuple):
    kind: str
    reason: str = ""
    marker: object = None

    @property
    def denied(self):
        return self.kind in ("rule", "mixed")


@dataclass
class Outcome:
    deny: str = ""
    messages: list = field(default_factory=list)


class Variables:
    def __init__(self, fold_case):
        self.fold_case = fold_case
        self.values = {}

    def _key(self, name):
        return name.lower() if self.fold_case else name

    def put(self, name, value):
        self.values[self._key(name)] = value

    def lookup(self, name):
        return self.values.get(self._key(name))


def read_json_file(path):
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def valid_mission_id(mission_id):
    return bool(MISSION_ID.fullmatch(mission_id)) and ".." not in mission_id


def in_place_adapters_dir():
    # hooks -> plugins/han -> plugins -> 저장소 루트. 설치된 캐시 폴더에선 이 경로가 없음
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "..", "..", "adapters"))


def han_repo_adapters_dir():
    # Grok에는 SessionStart 주입이 없다. HAN_REPO는 이 저장소를 받은 폴더다.
    repo = os.environ.get(REPO_ENV, "").strip()
    if not repo:
        return ""
    path = os.path.abspath(os.path.join(repo, "adapters"))
    if not os.path.isdir(path):
        raise GuardOff(f"HAN_REPO/adapters 없음: {path}")
    return path


def adapter_relatives(runtime):
    if runtime == "codex":
        return ((".codex", "orchestrator", "adapter.json"), ADAPTER_RELATIVE)
    if runtime == "grok":
        # 가까운 폴더가 우선이고, 한 폴더 안에서는 .grok → .claude → .codex
        return (
            (".grok", "orchestrator", "adapter.json"),
            ADAPTER_RELATIVE,
            (".codex", "orchestrator", "adapter.json"),
        )
    return (ADAPTER_RELATIVE,)


def registry_missing_message(runtime):
    if runtime == "codex":
        return f"레지스트리 경로 미설정 ({REGISTRY_ENV}·Codex config.toml·<저장소>/adapters 가 비어 있음)"
    if runtime == "grok":
        return f"레지스트리 경로 미설정 ({REGISTRY_ENV}·HAN_REPO·<저장소>/adapters 가 비어 있음)"
    return f"레지스트리 경로 미설정 ({REGISTRY_ENV}·플러그인 설정 adapters_dir 이 비어 있고 <저장소>/adapters 도 없음)"


def codex_config_registry_dir():
    codex_home = os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")
    path = os.path.join(codex_home, "config.toml")
    try:
        os.stat(path)
    except FileNotFoundError:
        return ""
    except OSError as e:
        raise GuardOff(f"Codex config.toml 접근 실패 ({path}: {e})")
    try:
        import tomllib
    except ImportError:
        raise GuardOff(f"Codex config.toml 을 읽으려면 Python 3.11 이상이 필요함 ({path})")
    try:
        with open(path, "rb") as config_file:
            config = tomllib.load(config_file)
    except FileNotFoundError:
        return ""
    except (OSError, ValueError) as e:
        raise GuardOff(f"Codex config.toml 읽기 실패 ({path}: {e})")
    policy = config.get("shell_environment_policy", {})
    if not isinstance(policy, dict):
        raise GuardOff(f"Codex config.toml 형식 오류 ({path}): shell_environment_policy 는 객체여야 함")
    values = policy.get("set", {})
    if not isinstance(values, dict):
        raise GuardOff(f"Codex config.toml 형식 오류 ({path}): shell_environment_policy.set 는 객체여야 함")
    configured = values.get(REGISTRY_ENV, "")
    if configured == "":
        return ""
    if not isinstance(configured, str):
        raise GuardOff(f"Codex config.toml 형식 오류 ({path}): {REGISTRY_ENV} 는 문자열이어야 함")
    return configured


def resolve_registry(runtime="claude"):
    sources = [("env", REGISTRY_ENV)]
    if runtime == "claude":
        sources.append(("userConfig", USER_CONFIG_ENV))
    for source, variable in sources:
        configured = os.environ.get(variable)
        if configured:
            return Registry(os.path.abspath(configured), source)
    if runtime == "codex":
        configured = codex_config_registry_dir()
        if configured:
            return Registry(os.path.abspath(configured), "codexConfig")
    if runtime == "grok":
        configured = han_repo_adapters_dir()
        if configured:
            return Registry(configured, "hanRepo")
    fallback = in_place_adapters_dir()
    if os.path.isdir(fallback):
        return Registry(fallback, "in-place")
    return Registry("", "none")


def registry_dir(runtime="claude"):
    return resolve_registry(runtime).path


def registry_adapter_path(name, runtime="claude"):
    return os.path.join(registry_dir(runtime), name, "adapter.json")


def normalize_path(path):
    return os.path.normpath(path).replace("\\", "/").rstrip("/").lower()


def root_matches(folder, root):
    folder_parts = normalize_path(folder).split("/")
    root_parts = normalize_path(root).split("/")
    if len(folder_parts) != len(root_parts):
        return False
    return all(fnmatch.fnmatchcase(part, pattern) for part, pattern in zip(folder_parts, root_parts))


def load_registry(directory):
    path = os.path.join(directory, REGISTRY_FILE)
    if not os.path.isfile(path):
        return []
    try:
        entries = read_json_file(path)
    except (OSError, ValueError) as e:
        raise GuardOff(f"어댑터 registry 읽기 실패 ({path}: {e})")
    valid = isinstance(entries, list)
    for entry in entries if valid else []:
        valid = (
            valid
            and isinstance(entry, dict)
            and isinstance(entry.get("adapter"), str)
            and valid_mission_id(entry["adapter"])
            and isinstance(entry.get("roots"), list)
            and all(isinstance(root, str) for root in entry["roots"])
        )
    if not valid:
        raise GuardOff(f"어댑터 registry 형식 오류 ({path}): [{{adapter, roots[]}}] 배열이어야 함")
    return entries


def ancestors(start):
    folders = []
    folder = os.path.abspath(start)
    while True:
        folders.append(folder)
        parent = os.path.dirname(folder)
        if parent == folder:
            return folders
        folder = parent


def find_adapter(payload):
    runtime = payload.get("han_runtime", "claude")
    start = (os.environ.get("CLAUDE_PROJECT_DIR") if runtime == "claude" else "") or payload.get("cwd") or ""
    if not start:
        raise AdapterNotFound("시작 폴더 없음 (CLAUDE_PROJECT_DIR·cwd 가 비어 있음)")
    chain = ancestors(start)
    for folder in chain:
        for relative in adapter_relatives(runtime):
            path = os.path.join(folder, *relative)
            if os.path.isfile(path):
                return Located(folder, path)
    registry = resolve_registry(runtime)
    append_log({"registry_source": registry.source, "registry_dir": registry.path})
    directory = registry.path
    if not directory:
        raise AdapterNotFound(registry_missing_message(runtime))
    registry_file = os.path.join(directory, REGISTRY_FILE)
    if not os.path.isdir(directory):
        raise AdapterNotFound(f"레지스트리 폴더 없음: {directory} (출처 {registry.source})")
    if not os.path.isfile(registry_file):
        raise AdapterNotFound(f"{REGISTRY_FILE} 없음: {registry_file}")
    entries = load_registry(directory)
    for folder in chain:
        for entry in entries:
            if any(root_matches(folder, root) for root in entry["roots"]):
                return Located(folder, os.path.join(directory, entry["adapter"], "adapter.json"))
    raise AdapterNotFound(f"레지스트리 roots 에 {start} 가 맞는 항목 없음 ({registry_file})")


def locate_adapter(payload):
    try:
        return find_adapter(payload)
    except AdapterNotFound:
        return None


def merge_local(base, local):
    merged = dict(base)
    for key, value in local.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_local(merged[key], value)
        else:
            merged[key] = value
    return merged


def read_local(adapter_path, problems):
    path = os.path.join(os.path.dirname(adapter_path), LOCAL_FILE)
    if not os.path.isfile(path):
        return {}
    try:
        local = read_json_file(path)
    except (OSError, ValueError) as e:
        problems.append(f"local.json 무시 (adapter.json 만 씀): {path}: {e}")
        return {}
    if not isinstance(local, dict):
        problems.append(f"local.json 무시 (adapter.json 만 씀): {path}: 객체가 아님")
        return {}
    return local


def load_adapter(adapter_path, problems=None, runtime="claude"):
    problems = [] if problems is None else problems
    try:
        adapter = read_json_file(adapter_path)
    except (OSError, ValueError) as e:
        raise GuardOff(f"adapter.json 읽기 실패 ({adapter_path}: {e})")
    if not isinstance(adapter, dict):
        raise GuardOff(f"adapter.json schema 가 1이 아님 ({adapter_path})")
    local = read_local(adapter_path, problems)
    if runtime == "codex":
        overlay = (adapter.get("runtimes") or {}).get("codex") or {}
        if not isinstance(overlay, dict):
            raise GuardOff("runtimes.codex 는 객체여야 함")
        adapter = merge_local(adapter, overlay)
    adapter = merge_local(adapter, local)
    if runtime == "codex":
        overlay = (local.get("runtimes") or {}).get("codex") or {}
        if not isinstance(overlay, dict):
            raise GuardOff("local.json 의 runtimes.codex 는 객체여야 함")
        adapter = merge_local(adapter, overlay)
    if adapter.get("schema") != SUPPORTED_SCHEMA:
        raise GuardOff(f"adapter.json schema 가 1이 아님 ({adapter_path})")
    return adapter


def state_dir_of(project, adapter):
    relative = (adapter.get("paths") or {}).get("stateDir")
    if not isinstance(relative, str) or not relative:
        raise GuardOff("adapter.json 에 paths.stateDir 없음")
    return os.path.normpath(os.path.join(project, relative))


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def active_path_of(state_dir):
    return os.path.join(state_dir, "ACTIVE")


def read_active(state_dir):
    try:
        with open(active_path_of(state_dir), encoding="utf-8-sig", errors="replace") as f:
            text = f.read()
    except FileNotFoundError:
        return NO_ACTIVE
    except OSError:
        return CORRUPT_ACTIVE
    if text.lstrip().startswith("{"):
        try:
            data = json.loads(text)
        except ValueError:
            return CORRUPT_ACTIVE
        if not isinstance(data, dict):
            return CORRUPT_ACTIVE
        return Active(
            str(data.get("id") or "-"),
            str(data.get("session") or ""),
            str(data.get("openedAt") or ""),
        )
    return Active(text.splitlines()[0].strip() if text.strip() else "-", "", "")


def write_active(state_dir, active):
    os.makedirs(state_dir, exist_ok=True)
    final = active_path_of(state_dir)
    temp = f"{final}.{os.getpid()}.tmp"
    data = {"id": active.id, "session": active.session, "openedAt": active.opened_at}
    try:
        with open(temp, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")
        os.replace(temp, final)
    except BaseException:
        remove_quietly(temp)
        raise


def remove_quietly(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def stale_hours_of(adapter):
    # 우선순위: HAN_STALE_HOURS 환경변수 > adapter(local.json 병합) mission.staleHours > 24. 0 이면 끊김 판정 끔
    raw = os.environ.get(STALE_ENV, "")
    if raw.strip() == "":
        raw = ((adapter or {}).get("mission") or {}).get("staleHours", STALE_HOURS_DEFAULT)
    try:
        hours = float(raw)
    except (TypeError, ValueError):
        return float(STALE_HOURS_DEFAULT)
    return hours if hours >= 0 else float(STALE_HOURS_DEFAULT)


def iso_epoch(text):
    try:
        moment = datetime.fromisoformat(str(text))
    except ValueError:
        return 0.0
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return moment.timestamp()


def newest_mtime(folder, limit=STALE_SCAN_LIMIT):
    newest, seen = 0.0, 0
    for root, dirs, files in os.walk(folder):
        dirs[:] = [name for name in dirs if not name.startswith(".")]
        for name in files:
            seen += 1
            if seen > limit:
                return newest
            try:
                newest = max(newest, os.path.getmtime(os.path.join(root, name)))
            except OSError:
                pass
    return newest


def last_activity(state_dir, active):
    # 활동 = openedAt, ACTIVE 파일 mtime(같은 세션 명령마다 heartbeat 가 갱신), 미션 폴더 안 파일 중 가장 최근 mtime
    candidates = [iso_epoch(active.opened_at)]
    try:
        candidates.append(os.path.getmtime(active_path_of(state_dir)))
    except OSError:
        pass
    if active.id and valid_mission_id(active.id):
        candidates.append(newest_mtime(os.path.join(state_dir, active.id)))
    return max(candidates)


def staleness(state_dir, active, limit, now=None):
    if not active.id or active.corrupt:
        return NOT_STALE
    last = last_activity(state_dir, active)
    now = time.time() if now is None else now
    hours = max(0.0, (now - last) / 3600) if last else float("inf")
    return Staleness(bool(limit) and hours >= limit, last, hours, float(limit))


def age_text(hours):
    if hours == float("inf"):
        return "알 수 없음"
    if hours < 1:
        return f"{int(hours * 60)}분"
    if hours < 48:
        return f"{int(hours)}시간"
    return f"{hours / 24:.1f}일"


def stale_text(state):
    last = datetime.fromtimestamp(state.last).strftime("%Y-%m-%d %H:%M") if state.last else "기록 없음"
    return f"끊김 의심: 마지막 활동 {age_text(state.hours)} 전({last}), 기준 {state.limit:g}시간"


def heartbeat(state_dir, payload, now=None):
    # ACTIVE 를 다시 쓰지 않고 mtime 만 갱신. 그 사이 close 로 지워졌으면 utime 이 실패해 되살리지 않음
    session_id = str(payload.get("session_id") or "")
    if not session_id:
        return
    active = read_active(state_dir)
    if not active.id or active.corrupt or active.session != session_id:
        return
    path = active_path_of(state_dir)
    now = time.time() if now is None else now
    try:
        if now - os.path.getmtime(path) >= HEARTBEAT_SECONDS:
            os.utime(path, (now, now))
    except OSError:
        pass


def join_continuations(command, tool_name="Bash"):
    return CONTINUATIONS.get(tool_name, CONTINUATIONS["Bash"]).sub(" ", command)


def is_redirect_ampersand(command, index):
    before = command[index - 1] if index > 0 else ""
    after = command[index + 1] if index + 1 < len(command) else ""
    return before == ">" or after == ">"


def split_fragments(command, tool_name="Bash", quote_aware=True):
    escape = ESCAPE_CHARS.get(tool_name, "\\")
    fragments = []
    current = []
    quote = ""

    def flush():
        text = "".join(current).strip()
        current.clear()
        if text:
            fragments.append(text)

    index = 0
    while index < len(command):
        char = command[index]
        following = command[index + 1] if index + 1 < len(command) else ""
        if quote:
            current.append(char)
            if char == escape and quote == '"' and following:
                current.append(following)
                index += 2
                continue
            if char == quote:
                quote = ""
            index += 1
        elif quote_aware and char == escape and following in ("'", '"'):
            current.append(char + following)
            index += 2
        elif quote_aware and char in "'\"":
            quote = char
            current.append(char)
            index += 1
        elif command.startswith("${", index):
            end = command.find("}", index)
            end = len(command) - 1 if end < 0 else end
            current.append(command[index : end + 1])
            index = end + 1
        elif command[index : index + 2] in ("&&", "||"):
            flush()
            index += 2
        elif char == "&" and is_redirect_ampersand(command, index):
            current.append(char)
            index += 1
        elif char in "\r\n;|&{}":
            flush()
            index += 1
        else:
            current.append(char)
            index += 1
    flush()
    if quote:
        # 닫히지 않은 따옴표(echo it's ...)가 뒤 명령을 삼키지 않게 따옴표 무시로 다시 나눔
        return split_fragments(command, tool_name, quote_aware=False)
    return fragments


def unquote(value):
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def substitute(fragment, variables):
    def replace(match):
        name = match.group(2) or match.group(3) or match.group(4)
        value = variables.lookup(name)
        return match.group(0) if value is None else value

    return VARIABLE_REFERENCE.sub(replace, fragment)


def strip_chain_prefix(fragment):
    return CHAIN_PREFIX.sub("", fragment).strip()


def peel_assignment(fragment, variables):
    while True:
        powershell = POWERSHELL_ASSIGN.match(fragment)
        match = powershell or BASH_ASSIGN.match(fragment)
        if not match:
            return fragment
        name, value = match.group(1), match.group(2).strip()
        if SIMPLE_VALUE.match(value):
            variables.put(name, unquote(value))
            return fragment
        if powershell or any(mark in value for mark in COMMAND_SUBSTITUTION):
            return strip_chain_prefix(value)
        fragment = FIRST_TOKEN_REST.match(value).group(1)


def peel_wrapper(fragment):
    for wrapper in WRAPPERS:
        match = wrapper.match(fragment)
        if match:
            return unquote(fragment[match.end() :].strip())
    return ""


def group_bodies(source, scan, opener):
    bodies = []
    skip_until = 0
    for match in opener.finditer(scan):
        if match.start() < skip_until:
            continue
        depth = 0
        end = -1
        for position in range(match.end() - 1, len(scan)):
            if scan[position] == "(":
                depth += 1
            elif scan[position] == ")":
                depth -= 1
                if depth == 0:
                    end = position
                    break
        stop = len(scan) if end < 0 else end
        bodies.append(source[match.end() : stop])
        skip_until = stop
    return bodies


def substitution_bodies(text, tool_name):
    if tool_name == "PowerShell":
        masked = QUOTED_TEXT.sub(lambda m: "_" * len(m.group(0)), text)
        # 큰따옴표 안 $(...) 는 PowerShell 이 실행하므로 원문에서, 맨 괄호만 따옴표 가린 본문에서 찾음
        return group_bodies(text, text, POWERSHELL_SUBEXPRESSION_OPENER) + group_bodies(
            text, masked, POWERSHELL_GROUP_OPENER
        )
    return group_bodies(text, text, BASH_GROUP_OPENER) + BACKTICK_BODY.findall(text)


def expand(text, tool_name, variables, depth):
    fragments = []
    if depth > MAX_DEPTH:
        return fragments
    for raw in split_fragments(text, tool_name):
        fragment = strip_chain_prefix(substitute(raw, variables))
        fragment = strip_chain_prefix(peel_assignment(fragment, variables))
        if not fragment:
            continue
        fragments.append(fragment)
        inner = peel_wrapper(fragment)
        if inner:
            fragments.extend(expand(inner, tool_name, variables, depth + 1))
    for body in substitution_bodies(text, tool_name):
        fragments.extend(expand(body, tool_name, variables, depth + 1))
    return fragments


def command_fragments(command, tool_name="Bash"):
    variables = Variables(fold_case=tool_name == "PowerShell")
    fragments = expand(join_continuations(command, tool_name), tool_name, variables, 0)
    return list(dict.fromkeys(fragments))


def rule_exceptions(rule, problems):
    patterns = rule.get("except", [])
    if not isinstance(patterns, list) or any(not isinstance(pattern, str) for pattern in patterns):
        problems.append("guard.deny.except 형식 오류: 문자열 배열이어야 함. 이 규칙의 예외를 적용하지 않음")
        return []
    compiled = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern, re.IGNORECASE))
        except re.error as e:
            problems.append(f"guard.deny.except 정규식 오류: {pattern} ({e}). 이 규칙의 예외를 적용하지 않음")
            return []
    return compiled


def compile_patterns(values, label, problems):
    if values is None:
        return []
    if not isinstance(values, list):
        problems.append(f"{label} 형식 오류: 배열이어야 함. 적용하지 않음")
        return []
    compiled = []
    for value in values:
        pattern = value.get("pattern") if isinstance(value, dict) else value
        if not isinstance(pattern, str):
            problems.append(f"{label} 형식 오류: 문자열 또는 {{pattern}} 이어야 함. 이 항목 건너뜀")
            continue
        try:
            compiled.append(re.compile(pattern, re.IGNORECASE))
        except re.error as e:
            problems.append(f"{label} 정규식 오류: {pattern} ({e}). 이 항목 건너뜀")
    return compiled


def allowed_fragments(adapter, fragments, problems):
    # guard.allow: 조각 전체가 맞으면 그 조각은 deny·p4Deny 를 보지 않음(전역 허용 목록). p4Client 검사는 그대로
    allows = compile_patterns(((adapter or {}).get("guard") or {}).get("allow"), "guard.allow", problems)
    if not allows:
        return fragments
    return [fragment for fragment in fragments if not any(allow.fullmatch(fragment) for allow in allows)]


def p4_deny_reason(adapter, fragments, problems):
    """guard.p4Deny: p4 하위 명령 이름으로 막는 규칙. 정규식으로 p4 실행 파일·전역 옵션을 다시 쓰지 않는다.

    [{"commands": ["submit"], "when": "<나머지 인자 정규식>", "unless": "<정규식>", "reason": "..."}]
    when 이 있으면 맞을 때만, unless 가 맞으면 막지 않음. 나머지 인자는 따옴표를 벗겨 공백으로 이은 문자열.
    """
    rules = ((adapter or {}).get("guard") or {}).get("p4Deny")
    if not rules:
        return ""
    if not isinstance(rules, list):
        problems.append("guard.p4Deny 형식 오류: 배열이어야 함. 적용하지 않음")
        return ""
    parsed = [invocation for invocation in map(p4_invocation, fragments) if invocation]
    if not parsed:
        return ""
    for rule in rules:
        commands = rule.get("commands") if isinstance(rule, dict) else None
        if isinstance(commands, str):
            commands = [commands]
        if not isinstance(commands, list) or not commands or not all(isinstance(item, str) for item in commands):
            problems.append("guard.p4Deny.commands 형식 오류: 문자열 배열이어야 함. 이 규칙 건너뜀")
            continue
        names = {item.lower() for item in commands}
        conditions = {}
        try:
            for key in ("when", "unless"):
                value = rule.get(key)
                conditions[key] = re.compile(value, re.IGNORECASE) if isinstance(value, str) and value else None
        except re.error as e:
            problems.append(f"guard.p4Deny 정규식 오류: {e}. 이 규칙 건너뜀")
            continue
        for _, command, rest in parsed:
            if command not in names:
                continue
            text = " ".join(rest)
            if conditions["when"] and not conditions["when"].search(text):
                continue
            if conditions["unless"] and conditions["unless"].search(text):
                continue
            return str(rule.get("reason") or f"p4 {command} 금지")
    return ""


def find_denied_reason(adapter, fragments, problems=None):
    problems = [] if problems is None else problems
    fragments = allowed_fragments(adapter, fragments, problems)
    if not fragments:
        return ""
    for rule in (adapter.get("guard") or {}).get("deny") or []:
        pattern = rule.get("pattern") if isinstance(rule, dict) else None
        if not isinstance(pattern, str):
            continue
        try:
            compiled = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            problems.append(f"잘못된 정규식 건너뜀: {pattern} ({e})")
            continue
        exceptions = rule_exceptions(rule, problems)
        if any(
            compiled.search(fragment) and not any(exception.fullmatch(fragment) for exception in exceptions)
            for fragment in fragments
        ):
            return str(rule.get("reason") or "차단된 명령")
    return p4_deny_reason(adapter, fragments, problems)


def expected_p4_client(project, adapter):
    # 이 폴더가 쓸 작업 공간: 슬롯 폴더면 slots.client 틀, 메인 폴더면 slots.mainClient, 아니면 .p4config 의 P4CLIENT
    if not project:
        return ""
    slots = (adapter or {}).get("slots") or {}
    root, template = slots.get("root"), slots.get("client")
    parent, name = os.path.split(os.path.normpath(project))
    if isinstance(root, str) and root and isinstance(template, str) and "{name}" in template and name:
        if normalize_path(parent) == normalize_path(root):
            return template.replace("{name}", name)
    main = ((adapter or {}).get("vars") or {}).get("main")
    main_client = slots.get("mainClient")
    if isinstance(main, str) and main and isinstance(main_client, str) and main_client:
        if normalize_path(project) == normalize_path(main):
            return main_client
    try:
        with open(os.path.join(project, ".p4config"), encoding="utf-8-sig") as f:
            match = re.search(r"^P4CLIENT=(.*)$", f.read(), re.M)
    except OSError:
        return ""
    return match.group(1).strip() if match else ""


def p4_invocation(fragment):
    """p4 호출이면 (전역 -c 값 또는 None, 하위 명령, 나머지 인자), 아니면 None."""
    tokens = [unquote(token) for token in P4_TOKEN.findall(fragment)]
    if tokens and tokens[0] == "&":  # PowerShell 호출 연산자
        tokens = tokens[1:]
    if not tokens or re.split(r"[\\/]", tokens[0])[-1].lower() not in P4_EXECUTABLES:
        return None
    client, index = None, 1
    while index < len(tokens) and tokens[index].startswith("-") and len(tokens[index]) > 1:
        flag = tokens[index][:2]
        if flag in P4_VALUE_OPTIONS:
            value = tokens[index][2:]
            if not value:
                index += 1
                value = tokens[index] if index < len(tokens) else ""
            if flag == "-c":
                client = value
        index += 1
    command = tokens[index].lower() if index < len(tokens) else ""
    return client, command, tokens[index + 1 :]


def p4c_as_p4(fragment, project="", adapter=None):
    """p4c.py 래퍼 호출을 같은 효과의 `p4 -c <작업 공간> ...` 조각으로 바꿈(deny 규칙이 래퍼로 우회되지 않게)."""
    match = P4C_CALL.match(fragment)
    if not match:
        return ""
    tokens = P4_TOKEN.findall(match.group(1))
    client = ""
    while tokens and tokens[0].startswith("--"):
        flag = tokens.pop(0)
        if flag == "--":
            break
        if flag in P4C_OWN_VALUE_OPTIONS and tokens:
            value = unquote(tokens.pop(0))
            if flag == "--client":
                client = value
    client = client or expected_p4_client(project, adapter) or "p4c-client"
    return " ".join(["p4", "-c", client, *tokens])


def p4_policy(adapter):
    policy = ((adapter or {}).get("guard") or {}).get("p4Client")
    if not isinstance(policy, dict) or not policy.get("require"):
        return None
    commands = policy.get("commands")
    if isinstance(commands, list) and all(isinstance(item, str) for item in commands):
        return frozenset(item.lower() for item in commands)
    return P4_WRITE_COMMANDS


def p4_client_reason(adapter, fragments, project=""):
    commands = p4_policy(adapter)
    if commands is None:
        return ""
    expected = expected_p4_client(project, adapter)
    for fragment in fragments:
        parsed = p4_invocation(fragment)
        if not parsed:
            continue
        client, command, rest = parsed
        if command not in commands or (command in P4_READ_FLAG_COMMANDS and "-o" in rest):
            continue
        if not client:
            return (
                f"p4 {command} 는 전역 -c <작업 공간> 필수(P4CLIENT·PWD 함정). "
                f"래퍼로: python \"{P4C_SCRIPT}\" {command} ... (작업 공간 {expected or '자동'} 고정)"
            )
        unresolved = "$" in client or "{" in client
        if expected and not unresolved and client.casefold() != expected.casefold():
            return f"p4 -c {client} 가 이 폴더의 작업 공간({expected})과 다름 — 다른 슬롯·메인 작업 공간에 쓰지 않음"
    return ""


def contains_marker(command, tool_name="Bash"):
    return any(MARKER.match(fragment) for fragment in command_fragments(command, tool_name))


def judge_command(adapter, command, tool_name="Bash", problems=None, project=""):
    fragments = command_fragments(command, tool_name)
    markers = [match for match in map(MARKER.match, fragments) if match]
    if markers:
        if len(fragments) == 1:
            return Verdict("marker", marker=markers[0])
        return Verdict("mixed", MARKER_ALONE_REASON)
    wrapped = [p4 for p4 in (p4c_as_p4(fragment, project, adapter) for fragment in fragments) if p4]
    fragments = list(dict.fromkeys(fragments + wrapped))
    reason = find_denied_reason(adapter, fragments, problems) or p4_client_reason(adapter, fragments, project)
    return Verdict("rule", reason) if reason else Verdict("allow")


def corrupt_active_notice(state_dir):
    return f"ACTIVE 손상: {active_path_of(state_dir)}, 확인 후 지우거나 close"


def allow_once_path(state_dir, mission_id):
    return os.path.join(state_dir, mission_id, "allow-once")


def clear_allow_once(state_dir, mission_id):
    path = allow_once_path(state_dir, mission_id)
    remove_quietly(path)
    folder = os.path.dirname(path)
    if os.path.isdir(folder):
        for name in os.listdir(folder):
            if name.startswith("allow-once.") and name.endswith(".claim"):
                remove_quietly(os.path.join(folder, name))


def open_mission(state_dir, mission_id, session_id, active, outcome):
    if active.corrupt:
        outcome.deny = corrupt_active_notice(state_dir)
        return
    if active.id and active.id != mission_id:
        outcome.deny = f"다른 미션 {active.id} 진행 중"
        return
    write_active(state_dir, Active(mission_id, session_id, now_iso()))
    outcome.messages.append(f"미션 {mission_id} 가드 켜짐")


def resume_mission(state_dir, mission_id, session_id, active, outcome):
    if active.corrupt:
        outcome.deny = corrupt_active_notice(state_dir)
        return
    if active.id != mission_id:
        outcome.deny = f"다른 미션 {active.id} 진행 중" if active.id else f"미션 {mission_id} 가 열려 있지 않음"
        return
    write_active(state_dir, Active(mission_id, session_id, active.opened_at or now_iso()))
    outcome.messages.append(f"미션 {mission_id} 가드 세션 갱신 (이전 session: {active.session or '없음'})")


def close_mission(state_dir, mission_id, session_id, active, outcome):
    if active.corrupt:
        remove_quietly(active_path_of(state_dir))
        clear_allow_once(state_dir, mission_id)
        outcome.messages.append(f"손상된 ACTIVE 를 지움 (close {mission_id})")
        return
    if not active.id:
        clear_allow_once(state_dir, mission_id)
        outcome.messages.append(f"미션 {mission_id} 는 이미 닫혀 있음")
        return
    if active.id != mission_id:
        outcome.deny = f"다른 미션 {active.id} 진행 중"
        return
    if active.session and active.session != session_id:
        outcome.deny = f"다른 세션의 미션 {active.id} — resume 후 close"
        return
    remove_quietly(active_path_of(state_dir))
    clear_allow_once(state_dir, mission_id)
    outcome.messages.append(f"미션 {mission_id} 가드 꺼짐")


MARKER_ACTIONS = {"open": open_mission, "resume": resume_mission, "close": close_mission}


def handle_marker(marker, state_dir, session_id, outcome, transcript="", stale_hours=STALE_HOURS_DEFAULT):
    action, mission_id = marker.group(1).lower(), marker.group(2)
    if not valid_mission_id(mission_id):
        outcome.deny = f"미션 id 형식 오류: {mission_id}"
        return
    active = read_active(state_dir)
    MARKER_ACTIONS[action](state_dir, mission_id, session_id, active, outcome)
    if outcome.deny and active.id and not active.corrupt:
        try:
            state = staleness(state_dir, active, stale_hours)
        except Exception:  # 안내만 포기
            state = NOT_STALE
        if state.stale:
            outcome.deny += f" — {active.id} {stale_text(state)}. {FORCE_CLOSE_HINT}"
    if action in SESSION_ACTIONS and not outcome.deny:
        try:
            folder = os.path.join(state_dir, mission_id)
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, SESSIONS_FILE)
            lines = ["\t".join(SESSIONS_HEADER)] if not os.path.isfile(path) or os.path.getsize(path) == 0 else []
            cells = [now_iso(), action, session_id, transcript]
            lines.append("\t".join(CELL_BREAKS.sub(" ", cell).strip() for cell in cells))
            with open(path, "a", encoding="utf-8", newline="\n") as f:
                f.write("\n".join(lines) + "\n")
        except OSError as e:  # 가드는 이미 켜졌으니 기록 실패로 마커를 거부하지 않음
            outcome.messages.append(f"{SESSIONS_FILE} 기록 실패: {e}")


def consume_allow_once(state_dir, mission_id, command):
    path = allow_once_path(state_dir, mission_id)
    if not valid_mission_id(mission_id) or not os.path.isfile(path):
        return False
    claim = f"{path}.{os.getpid()}.claim"
    try:
        os.replace(path, claim)
    except FileNotFoundError:
        return False
    try:
        with open(claim, encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
        wanted = command.strip()
        for index, line in enumerate(lines):
            if line.strip() != wanted:
                continue
            remaining = [rest for i, rest in enumerate(lines) if i != index and rest.strip()]
            if remaining:
                with open(claim, "w", encoding="utf-8", newline="\n") as f:
                    f.write("\n".join(remaining) + "\n")
                os.replace(claim, path)
            else:
                os.remove(claim)
            return True
        os.replace(claim, path)
        return False
    except BaseException:
        if os.path.exists(claim) and not os.path.exists(path):
            try:
                os.replace(claim, path)
            except OSError:
                pass
        raise


def runs_in_other_session(active, payload):
    session_id = payload.get("session_id")
    if not active.session or not session_id:
        return False
    return active.session != session_id


def judge_denied_command(payload, command, active, state_dir, reason, outcome, stale_hours=STALE_HOURS_DEFAULT):
    try:
        if runs_in_other_session(active, payload):
            note = ""
            try:
                state = staleness(state_dir, active, stale_hours)
                note = f" ({stale_text(state)})" if state.stale else ""
            except Exception:  # 안내만 포기
                pass
            outcome.messages.append(f"미션 {active.id}가 다른 세션에서 진행 중{note} — 이 세션은 막지 않음: {reason}")
        elif consume_allow_once(state_dir, active.id, command):
            outcome.messages.append(f"1회 허용 사용: {command.strip()}")
        else:
            outcome.deny = f"{reason} (미션 진행 중: {active.id})"
    except Exception as e:  # 규칙에 걸린 뒤의 오류는 허용이 아니라 거부
        path = allow_once_path(state_dir, active.id)
        outcome.deny = f"{reason} (미션 진행 중: {active.id}; 1회 허용 확인 실패라 허용 안 함: {path} — {type(e).__name__}: {e})"


def apply_rule(payload, command, verdict, state_dir, problems, outcome, stale_hours=STALE_HOURS_DEFAULT):
    active = read_active(state_dir)
    if not active.id:
        return
    outcome.messages.extend(problems)
    if active.corrupt:
        outcome.deny = f"{verdict.reason} ({corrupt_active_notice(state_dir)})"
        return
    judge_denied_command(payload, command, active, state_dir, verdict.reason, outcome, stale_hours)


def report_problems_while_active(state_dir, problems, outcome):
    try:
        active = read_active(state_dir)
    except Exception:  # 규칙에 안 걸린 명령이라 알림만 포기
        return
    if active.id:
        outcome.messages.extend(problems)


def glob_regex(pattern):
    """`**` 는 여러 단계, `*`·`?` 는 한 단계 안. 슬래시로 비교하고 대소문자는 무시."""
    out = []
    i = 0
    text = pattern.replace("\\", "/").strip("/")
    while i < len(text):
        if text.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif text.startswith("**", i):
            out.append(".*")
            i += 2
        elif text[i] == "*":
            out.append("[^/]*")
            i += 1
        elif text[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(text[i]))
            i += 1
    return re.compile("".join(out), re.IGNORECASE)


def coordinator_write_globs(adapter, problems):
    value = ((adapter or {}).get("guard") or {}).get("coordinatorWrite")
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        problems.append("guard.coordinatorWrite 형식 오류: 문자열 배열이어야 함. 적용하지 않음")
        return None
    return value


def written_path(payload):
    tool_input = payload.get("tool_input") or {}
    for key in ("file_path", "notebook_path", "path"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def coordinator_write_reason(project, cwd, target, globs):
    """프로젝트 안 경로가 globs 어디에도 안 맞으면 거부 이유. 프로젝트 밖(메모리·임시 파일)은 보지 않음."""
    base = os.path.abspath(project)
    path = os.path.abspath(os.path.join(cwd or base, target))
    try:
        relative = os.path.relpath(path, base)
    except ValueError:  # 다른 드라이브
        return ""
    relative = relative.replace(os.sep, "/")
    if relative == ".." or relative.startswith("../"):
        return ""
    if any(glob_regex(pattern).fullmatch(relative) for pattern in globs):
        return ""
    return (
        f"코디네이터는 미션 중 프로젝트 파일을 직접 쓰지 않음: {relative} "
        f"(허용: {', '.join(globs)}). roles.implement 로 파견할 것"
    )


def decide_write(payload):
    outcome = Outcome()
    if payload.get("agent_id"):  # 서브에이전트 쓰기는 코디네이터 제한 대상이 아님
        return outcome
    target = written_path(payload)
    if not target:
        return outcome
    located = locate_adapter(payload)
    if not located:
        return outcome
    problems = []
    adapter = load_adapter(located.adapter_path, problems, runtime=payload.get("han_runtime", "claude"))
    globs = coordinator_write_globs(adapter, problems)
    if globs is None:
        return outcome
    active = read_active(state_dir_of(located.project, adapter))
    if not active.id or active.corrupt or not active.session:
        return outcome
    if active.session != str(payload.get("session_id") or ""):
        return outcome
    outcome.messages.extend(problems)
    outcome.deny = coordinator_write_reason(located.project, payload.get("cwd"), target, globs)
    return outcome


def decide(payload):
    outcome = Outcome()
    tool_name = payload.get("tool_name")
    if tool_name in WRITE_TOOLS:
        try:
            return decide_write(payload)
        except GuardOff:
            return outcome
    if tool_name not in GUARDED_TOOLS:
        return outcome
    command = (payload.get("tool_input") or {}).get("command")
    if not isinstance(command, str):
        return outcome
    try:
        located = find_adapter(payload)
    except AdapterNotFound as e:
        if contains_marker(command, tool_name):
            outcome.messages.append(f"미션 가드 꺼짐: 어댑터 못 찾음 — {e}")
        return outcome
    problems = []
    adapter = load_adapter(located.adapter_path, problems, runtime=payload.get("han_runtime", "claude"))
    state_dir = state_dir_of(located.project, adapter)
    stale_hours = stale_hours_of(adapter)

    verdict = judge_command(adapter, command, tool_name, problems, located.project)
    if verdict.kind != "marker":
        try:
            heartbeat(state_dir, payload)
        except Exception:  # heartbeat 실패로 도구를 막지 않음
            pass
    if verdict.kind == "mixed":
        outcome.deny = verdict.reason
    elif verdict.kind == "marker":
        handle_marker(
            verdict.marker,
            state_dir,
            str(payload.get("session_id") or ""),
            outcome,
            str(payload.get("transcript_path") or ""),
            stale_hours,
        )
    elif verdict.kind == "rule":
        try:
            apply_rule(payload, command, verdict, state_dir, problems, outcome, stale_hours)
        except Exception as e:  # 규칙에 걸린 뒤의 오류는 허용이 아니라 거부
            outcome.deny = f"{verdict.reason} (가드 상태 확인 실패라 허용 안 함: {type(e).__name__}: {e})"
    elif problems:
        report_problems_while_active(state_dir, problems, outcome)
    return outcome


def render_output(outcome):
    output = {}
    specific = {}
    if outcome.messages:
        text = " / ".join(outcome.messages)
        output["systemMessage"] = text
        specific["additionalContext"] = f"미션 가드: {text}"
    if outcome.deny:
        specific["permissionDecision"] = "deny"
        specific["permissionDecisionReason"] = outcome.deny
    if specific:
        output["hookSpecificOutput"] = {"hookEventName": "PreToolUse", **specific}
    return output


def log_payload(payload):
    command = (payload.get("tool_input") or {}).get("command")
    entry = {key: payload.get(key) for key in LOG_FIELDS}
    entry["command"] = command[:LOG_COMMAND_LENGTH] if isinstance(command, str) else None
    append_log(entry)


def append_log(entry):
    path = os.environ.get(LOG_ENV)
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def main(runtime="claude"):
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
        payload = payload if isinstance(payload, dict) else {}
        payload["han_runtime"] = runtime
        if runtime == "codex" and payload.get("tool_name") == "Bash":
            tool_input = payload.get("tool_input") or {}
            shell = str(tool_input.get("shell") or "").lower()
            if "powershell" in shell or "pwsh" in shell or (os.name == "nt" and not shell):
                payload["tool_name"] = "PowerShell"
            if tool_input.get("cwd"):
                payload["cwd"] = os.path.abspath(os.path.join(payload.get("cwd") or os.getcwd(), tool_input["cwd"]))
        log_payload(payload)
        outcome = decide(payload)
    except GuardOff as e:
        outcome = Outcome(messages=[f"미션 가드 꺼짐: {e}"])
    except Exception as e:  # 가드 오류로 도구를 막지 않음
        outcome = Outcome(messages=[f"미션 가드 꺼짐: 내부 오류 ({e})"])
    output = render_output(outcome)
    if output:
        sys.stdout.write(json.dumps(output, ensure_ascii=True))
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
