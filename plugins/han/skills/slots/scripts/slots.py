#!/usr/bin/env python3
import argparse
import contextlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime

HOOKS_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "hooks"))
sys.path.insert(0, HOOKS_DIR)
sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "mission", "scripts")))
import mission_guard  # noqa: E402

OK = 0
FAILED = 1
USAGE = 2
RUNTIMES = ("claude", "codex", "grok")

ROW_FIELDS = ("name", "path", "client", "created", "synced", "state")
VARIABLE = re.compile(r"\{(\w+)\}")
BASELINE_LINE = re.compile(r"Change (\d+) on ")
CLIENT_NAME_LINE = re.compile(r"^Client name:\s*(.+)$", re.M)
NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
CHURN_TOKEN = re.compile(r"(\.\.\.|\*)")
CHURN_WILDCARDS = {"...": ".*", "*": "[^/]*"}
DEPOT_LINE_PATH = re.compile(r"^(//.+?)(?:#\d+)? - ")
WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
MISSION_FIELD = re.compile(r"^(상태|현재 단계):[ \t]*(.*?)\s*$")
SLOT_DESCRIPTION = "AI mission slot {name} (han)"
SLOT_MARKER = "AI mission slot"
KEEP_DESCRIPTION = "han slot keep-local {name}"
MAIN_KEEP_DESCRIPTION = "han main keep-local"
NO_RECONCILE = "no file(s) to reconcile"
SYNC_STEP = "sync"

STATE_CREATING = "creating"
STATE_READY = "ready"
STATE_FAILED = "failed:"
STATE_REFRESH_FAILED = "failed:refresh:"

PREVIEW_LIMIT = 20
ROWS_LOCK_TIMEOUT = 30
EXCLUSIVE_LOCK_TIMEOUT = 30 * 60
LOCK_STALE_SECONDS = 2 * 60 * 60
LOCK_UNREADABLE_SECONDS = 10
LOCK_POLL_SECONDS = 0.2


class UsageError(Exception):
    pass


class LockBusy(RuntimeError):
    pass


def normalize(path):
    return os.path.normpath(str(path))


def same_path(first, second):
    return mission_guard.normalize_path(first) == mission_guard.normalize_path(second)


def inside_root(root, path):
    root_key = os.path.normcase(os.path.abspath(root))
    path_key = os.path.normcase(os.path.abspath(path))
    try:
        return path_key != root_key and os.path.commonpath([root_key, path_key]) == root_key
    except ValueError:
        return False


def is_link(path):
    return os.path.islink(path) or getattr(os.path, "isjunction", lambda _: False)(path)


def project_dir_name(path):
    return "".join(
        char if char.isascii() and char.isalnum() else "-" * (2 if ord(char) > 0xFFFF else 1) for char in str(path)
    )


def claude_memory_dir(project_path):
    home = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return normalize(os.path.join(home, "projects", project_dir_name(project_path), "memory"))


def read_adapter(adapter_path, runtime="claude"):
    problems = []
    try:
        adapter = mission_guard.load_adapter(adapter_path, problems, runtime=runtime)
    except mission_guard.GuardOff as e:
        raise UsageError(str(e))
    if problems:
        raise UsageError("; ".join(problems))
    return adapter


def locate(project_arg, runtime="claude"):
    saved = os.environ.get("CLAUDE_PROJECT_DIR")
    if project_arg:
        os.environ.pop("CLAUDE_PROJECT_DIR", None)
    try:
        located = mission_guard.locate_adapter({"cwd": os.path.abspath(project_arg or os.getcwd()), "han_runtime": runtime})
    except mission_guard.GuardOff as e:
        raise UsageError(str(e))
    finally:
        if saved is not None:
            os.environ["CLAUDE_PROJECT_DIR"] = saved
    if not located:
        raise UsageError("어댑터를 찾지 못함 (--project <경로>, HAN_ADAPTERS_DIR, 레지스트리 roots 확인)")
    return located


def expand(template, values):
    missing = sorted({key for key in VARIABLE.findall(template) if key not in values})
    if missing:
        raise UsageError(f"정의 안 된 변수 {', '.join('{' + key + '}' for key in missing)}: {template}")
    return VARIABLE.sub(lambda m: values[m.group(1)], template)


def process_alive(pid):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5
        code = wintypes.DWORD()
        try:
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


THREAD_LOCKS = {}
THREAD_LOCKS_GUARD = threading.Lock()


def thread_lock_of(path):
    key = mission_guard.normalize_path(path)
    with THREAD_LOCKS_GUARD:
        return THREAD_LOCKS.setdefault(key, threading.Lock())


class FileLock:
    def __init__(self, path, label, timeout=0):
        self.path = path
        self.label = label
        self.timeout = timeout
        self.holder = ""
        self.thread_lock = thread_lock_of(path)

    def __enter__(self):
        if not self.thread_lock.acquire(timeout=self.timeout):
            raise LockBusy(f"{self.label} 잠금 사용 중 (같은 프로세스의 다른 작업)")
        try:
            self.acquire()
        except BaseException:
            self.thread_lock.release()
            raise
        return self

    def __exit__(self, *_):
        try:
            if self.read_pid() == os.getpid():
                os.remove(self.path)
        except OSError:
            pass
        self.thread_lock.release()

    def acquire(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if self.reclaim_if_stale():
                    continue
                if time.monotonic() >= deadline:
                    raise LockBusy(f"{self.label} 잠금 사용 중 ({self.holder})")
                time.sleep(LOCK_POLL_SECONDS)
                continue
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                f.write(f"{os.getpid()}\t{datetime.now().isoformat(timespec='seconds')}\n")
            return

    def read_holder(self):
        with open(self.path, encoding="utf-8", errors="replace") as f:
            parts = f.read().strip().split("\t")
        pid = int(parts[0]) if parts[0].isdigit() else 0
        return pid, (parts[1] if len(parts) > 1 else "")

    def read_pid(self):
        try:
            return self.read_holder()[0]
        except OSError:
            return 0

    def reclaim_if_stale(self):
        try:
            pid, started = self.read_holder()
            age = time.time() - os.path.getmtime(self.path)
        except FileNotFoundError:
            return True
        except OSError:
            return False
        self.holder = f"pid {pid}, {started or '시각 없음'}"
        stale = (
            age > LOCK_STALE_SECONDS
            or pid == os.getpid()
            or (pid and not process_alive(pid))
            or (not pid and age > LOCK_UNREADABLE_SECONDS)
        )
        if not stale:
            return False
        try:
            os.remove(self.path)
        except FileNotFoundError:
            pass
        except OSError:
            return False
        return True


@dataclass
class Step:
    id: str
    command: str
    cwd: str
    group: int
    exclusive: bool
    ok_exit: list
    log: str
    out: str


@dataclass
class StepResult:
    id: str
    code: int
    seconds: float
    ok: bool
    out: str
    log: str

    def failure_text(self):
        paths = [path for path in (self.out, self.log) if os.path.isfile(path)]
        return f"{self.id} 종료 코드 {self.code} — 로그 {', '.join(paths) or self.out}"


@dataclass
class Outcome:
    name: str
    state: str
    detail: str = ""
    notes: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def failed(self):
        return self.state == "failed"


class Completed:
    def __init__(self, code, out, err):
        self.code = code
        self.out = out
        self.err = err

    @property
    def lines(self):
        return [line for line in self.out.splitlines() if line.strip()]


def spec_pattern(key):
    return re.compile(rf"^{key}:(?P<v>[^\n]*(?:\n[ \t]+[^\n]*)*)", re.M)


def get_field(spec, key):
    match = spec_pattern(key).search(spec)
    return match.group("v").strip() if match else ""


def set_field(spec, key, value, block=False):
    text = f"{key}:\n\t{value}" if block else f"{key}:\t{value}"
    pattern = spec_pattern(key)
    if pattern.search(spec):
        return pattern.sub(lambda _: text, spec, count=1)
    return spec.rstrip("\n") + "\n\n" + text + "\n"


def p4config_client(slot):
    try:
        with open(os.path.join(slot, ".p4config"), encoding="utf-8-sig") as f:
            match = re.search(r"^P4CLIENT=(.*)$", f.read(), re.M)
    except OSError:
        return ""
    return match.group(1).strip() if match else ""


PARENT_SESSION_ENV = (
    "CLAUDECODE",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_SESSION_ATTENDED",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_CODE_BRIDGE_SESSION_ID",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_EXECPATH",
    "CLAUDE_EFFORT",
    "CLAUDE_PID",
    "AI_AGENT",
    "TRACEPARENT",
    "HAN_TRANSCRIPT",
)

AGENT_SHELL_ENV = ("TERM", "MSYSTEM", "SHELL")

AGENT_TOOL_ENV = ("NO_COLOR", "GIT_TERMINAL_PROMPT")


def launch_env():
    # 부모 세션 표식이 남으면 새 claude 가 자식 세션으로 보고 대화 기록을 안 남김
    # 에이전트 셸(git bash) 단말 표식이 남으면 새 탭 claude 가 mintty 로 보고 화면을 다르게 그림
    # 에이전트 PowerShell 도구가 넣은 값이 남으면 새 탭 claude 가 흑백으로 뜨고 git 인증을 못 물음
    dropped = PARENT_SESSION_ENV + AGENT_SHELL_ENV + AGENT_TOOL_ENV
    return {key: value for key, value in os.environ.items() if key.upper() not in dropped}


def child_env(cwd):
    # p4 는 실제 cwd 보다 PWD 로 .p4config 를 찾음, 부모 PWD 가 남으면 레지스트리 P4CLIENT 로 떨어짐
    return {**os.environ, "PWD": cwd or os.getcwd()}


def decode(data):
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n")


class NoVcs:
    name = "none"
    has_workspaces = False

    def client_exists(self, client):
        return False

    def identity_problem(self, name, client, slot):
        return ""

    def create_workspace(self, name, client, slot):
        pass

    def write_config(self, client, slot):
        pass

    def opened(self, client):
        return []

    def shelved(self, client):
        return []

    def pending(self, client):
        return []

    def local_changes(self, client, edits_only=False):
        return [], 0

    def is_churn(self, line):
        return False

    def restore_churn(self, client):
        pass

    def describe_churn(self, client):
        return []

    def resolve_conflicts(self, client):
        return []

    def describe_keep_local(self, client, description):
        return []

    def describe_resolve(self, client):
        return []

    def workspace_name_in(self, slot):
        return ""

    def delete_workspace(self, client):
        pass

    def describe_create(self, name, client, slot):
        return []

    def baseline(self):
        raise UsageError("{baseline} 은 slots.vcs 가 있어야 씀")


class Perforce:
    name = "perforce"
    has_workspaces = True

    def __init__(self, argv, main_client, stream, main, churn=()):
        self.argv = argv
        self.main_client = main_client
        self.stream = stream
        self.main = main
        self.churn = list(churn)
        self.churn_patterns = [churn_pattern(stream, entry) for entry in self.churn]

    def call(self, client, *args, stdin=""):
        command = [*self.argv, "-c", client, *args]
        try:
            done = subprocess.run(
                command, cwd=self.main, env=child_env(self.main), input=stdin.encode("utf-8"), capture_output=True
            )
        except OSError as e:
            return Completed(127, "", f"{command[0]} 실행 실패: {e}")
        return Completed(done.returncode, decode(done.stdout), decode(done.stderr))

    def checked(self, client, *args, stdin=""):
        done = self.call(client, *args, stdin=stdin)
        if done.code != 0:
            raise RuntimeError(f"p4 -c {client} {' '.join(args)} 실패: {done.err.strip() or done.out.strip()}")
        return done

    def client_exists(self, client):
        return bool(self.checked(self.main_client, "clients", "-e", client).lines)

    def identity_problem(self, name, client, slot):
        if client.casefold() == self.main_client.casefold():
            return f"슬롯 작업 공간이 메인 작업 공간({self.main_client})과 같음"
        if self.client_exists(client):
            spec = self.checked(self.main_client, "client", "-o", client).out
            root = get_field(spec, "Root")
            if not same_path(root, slot):
                return f"작업 공간 {client} 의 Root({root or '없음'}) 가 슬롯 경로({slot})와 다름"
            if SLOT_MARKER not in get_field(spec, "Description"):
                return f"작업 공간 {client} 의 Description 에 슬롯 표식({SLOT_MARKER}) 없음 — 슬롯용으로 만든 작업 공간이 아님"
        if os.path.isdir(slot):
            configured = p4config_client(slot)
            if configured.casefold() != client.casefold():
                return f"{os.path.join(slot, '.p4config')} 의 P4CLIENT({configured or '없음'}) 가 {client} 와 다름"
        return ""

    def create_workspace(self, name, client, slot):
        spec = self.checked(self.main_client, "client", "-o", "-S", self.stream, client).out
        main_spec = self.checked(self.main_client, "client", "-o", self.main_client).out
        for key in ("Options", "LineEnd", "SubmitOptions"):
            value = get_field(main_spec, key)
            if value:
                spec = set_field(spec, key, value)
        spec = set_field(spec, "Root", slot)
        spec = set_field(spec, "Description", SLOT_DESCRIPTION.format(name=name), block=True)
        self.checked(client, "client", "-i", stdin=spec)

    def write_config(self, client, slot):
        source = os.path.join(self.main, ".p4config")
        with open(source, encoding="utf-8", newline="") as f:
            text = f.read()
        line = f"P4CLIENT={client}"
        if re.search(r"^P4CLIENT=[^\r\n]*", text, re.M):
            text = re.sub(r"^P4CLIENT=[^\r\n]*", lambda _: line, text, count=1, flags=re.M)
        else:
            text = text.rstrip("\r\n") + "\n" + line + "\n"
        with open(os.path.join(slot, ".p4config"), "w", encoding="utf-8", newline="") as f:
            f.write(text)

    def listing(self, client, *args):
        return self.checked(client, *args).lines

    def opened(self, client):
        return [line for line in self.checked(client, "opened").out.splitlines() if line.startswith("//")]

    def shelved(self, client):
        return self.listing(client, "changes", "-s", "shelved", "-c", client)

    def pending(self, client):
        return self.listing(client, "changes", "-s", "pending", "-c", client)

    def local_changes(self, client, edits_only=False):
        flags = ["-n", "-e"] if edits_only else ["-n"]
        out = self.checked(client, "reconcile", *flags, f"//{client}/...").out
        lines = [line for line in out.splitlines() if line.startswith("//")]
        kept = [line for line in lines if not self.is_churn(line)]
        return kept, len(lines) - len(kept)

    def is_churn(self, line):
        match = DEPOT_LINE_PATH.match(line)
        return bool(match) and any(pattern.fullmatch(match.group(1)) for pattern in self.churn_patterns)

    def restore_churn(self, client):
        for entry in self.churn:
            self.checked(client, "sync", "-f", f"//{client}/{entry}#have")

    def describe_churn(self, client):
        return [f"p4 -c {client} sync -f //{client}/{entry}#have" for entry in self.churn]

    def keep_changelist(self, client, description):
        out = self.checked(client, "changes", "-s", "pending", "-c", client, "-l").out
        found = re.search(rf"^Change (\d+) on [^\n]*\n\n\t{re.escape(description)}[ \t]*$", out, re.M)
        if found:
            return found.group(1)
        spec = f"Change:\tnew\n\nClient:\t{client}\n\nDescription:\n\t{description}\n"
        created = re.search(r"Change (\d+) created", self.checked(client, "change", "-i", stdin=spec).out)
        if not created:
            raise RuntimeError(f"보관 CL({description}) 을 만들지 못함")
        return created.group(1)

    def keep_local(self, client, description, changes):
        matches = [DEPOT_LINE_PATH.match(line) for line in changes]
        if not all(matches):
            raise RuntimeError(f"로컬 변경의 파일 경로를 읽지 못해 보관 못 함: {changes[0]}")
        changelist = self.keep_changelist(client, description)
        files = "\n".join(match.group(1) for match in matches)
        self.checked(client, "-x", "-", "reconcile", "-e", "-c", changelist, stdin=files)
        return changelist, len(matches)

    def resolve_conflicts(self, client):
        self.checked(client, "resolve", "-am")
        return [line for line in self.checked(client, "resolve", "-n").out.splitlines() if line.startswith("//")]

    def describe_keep_local(self, client, description):
        return [
            f"p4 -c {client} change -i  ({description}, 이미 있으면 재사용)",
            f"p4 -c {client} -x - reconcile -e -c <보관 CL>  (reconcile -n -e 로 찾은 열리지 않은 변경 파일)",
        ]

    def describe_resolve(self, client):
        return [f"p4 -c {client} resolve -am", f"p4 -c {client} resolve -n  (남은 충돌이 있으면 멈춤)"]

    def workspace_name_in(self, slot):
        try:
            done = subprocess.run([*self.argv, "info"], cwd=slot, env=child_env(slot), capture_output=True)
        except OSError:
            return ""
        match = CLIENT_NAME_LINE.search(decode(done.stdout))
        return match.group(1).strip() if done.returncode == 0 and match else ""

    def delete_workspace(self, client):
        self.checked(client, "client", "-d", client)

    def latest_change(self, spec, label, flags=()):
        out = self.checked(self.main_client, "changes", "-m1", *flags, spec).out
        match = BASELINE_LINE.match(out.strip())
        if not match:
            raise RuntimeError(f"{label} 에서 기준 CL 을 못 읽음: {out.strip() or '출력 없음'}")
        return match.group(1)

    def baseline(self):
        return self.latest_change(f"//{self.main_client}/...#have", f"{self.main_client} 의 have")

    def head(self):
        # 기본값은 pending·shelved CL 도 훑어 submit 안 된 번호가 나옴
        return self.latest_change(f"{self.stream.rstrip('/')}/...", f"{self.stream} 의 head", ("-s", "submitted"))

    def sync_preview(self, head):
        out = self.checked(self.main_client, "sync", "-n", f"//{self.main_client}/...@{head}").out
        return [line for line in out.splitlines() if line.startswith("//")]

    def unopened_edits(self, pending):
        opened = {match.group(1).casefold() for match in map(DEPOT_LINE_PATH.match, self.opened(self.main_client)) if match}
        paths = []
        for line in pending:
            match = DEPOT_LINE_PATH.match(line)
            if match and match.group(1).casefold() not in opened and not self.is_churn(line):
                paths.append(match.group(1))
        if not paths:
            return []
        done = self.call(self.main_client, "-x", "-", "reconcile", "-n", "-e", stdin="\n".join(paths))
        # 바뀐 파일이 없을 때 p4 가 경고로 0 아닌 종료를 내는 경우가 있음
        if done.code != 0 and NO_RECONCILE not in (done.out + done.err).lower():
            raise RuntimeError(f"p4 -c {self.main_client} -x - reconcile -n -e 실패: {done.err.strip() or done.out.strip()}")
        return [line for line in done.out.splitlines() if line.startswith("//")]

    def sync_main(self, head):
        self.checked(self.main_client, "sync", "-q", "--parallel=threads=8", f"//{self.main_client}/...@{head}")

    def describe_main_sync(self, head, keep_local):
        keep = self.describe_keep_local(self.main_client, MAIN_KEEP_DESCRIPTION) if keep_local else []
        sync = f"p4 -c {self.main_client} sync -q --parallel=threads=8 //{self.main_client}/...@{head}"
        return [*keep, sync, *self.describe_resolve(self.main_client)]

    def describe_create(self, name, client, slot):
        return [
            f"p4 -c {self.main_client} clients -e {client}",
            f"p4 -c {self.main_client} client -o -S {self.stream} {client}"
            f"  (Root={slot}, Options·LineEnd·SubmitOptions는 {self.main_client}에서 복사)",
            f"p4 -c {client} client -i",
            f"{os.path.join(slot, '.p4config')} 작성 (P4CLIENT={client})",
        ]


def churn_pattern(stream, entry):
    text = f"{stream.rstrip('/')}/{entry}"
    body = "".join(CHURN_WILDCARDS.get(part) or re.escape(part) for part in CHURN_TOKEN.split(text))
    return re.compile(body, re.IGNORECASE)


def churn_entries(slots):
    entries = slots.get("churn")
    if entries is None:
        return []
    if not isinstance(entries, list):
        raise UsageError(f"slots.churn 은 문자열 배열이어야 함: {entries!r}")
    for entry in entries:
        if not isinstance(entry, str) or not entry.strip():
            raise UsageError(f"slots.churn 항목은 비어 있지 않은 문자열이어야 함: {entry!r}")
        if entry.startswith(("/", "\\")) or WINDOWS_DRIVE.match(entry):
            raise UsageError(f"slots.churn 항목은 스트림 루트 기준 상대 경로여야 함: {entry}")
        if "\\" in entry:
            raise UsageError(f"slots.churn 항목은 / 로 구분해야 함: {entry}")
        if ".." in entry.split("/"):
            raise UsageError(f"slots.churn 항목에 .. 를 쓸 수 없음: {entry}")
    return list(dict.fromkeys(entries))


def build_vcs(slots, main):
    kind = slots.get("vcs") or "none"
    if kind == "none":
        return NoVcs()
    if kind != "perforce":
        raise UsageError(f"slots.vcs 는 perforce 또는 none: {kind}")
    for key in ("stream", "mainClient"):
        if not slots.get(key):
            raise UsageError(f"slots.vcs 가 perforce 면 slots.{key} 필요 (local.json 확인)")
    argv = slots.get("p4") or "p4"
    return Perforce(
        argv if isinstance(argv, list) else [argv], slots["mainClient"], slots["stream"], main, churn_entries(slots)
    )


class Config:
    def __init__(self, located, adapter, runtime="claude"):
        self.runtime = runtime
        slots = adapter.get("slots")
        if not isinstance(slots, dict) or not slots.get("enabled"):
            raise UsageError("어댑터에 slots 설정이 없거나 enabled 가 아님")
        for key in ("root", "client"):
            if not isinstance(slots.get(key), str) or not slots[key]:
                raise UsageError(f"slots.{key} 가 없음 (local.json 확인)")
        self.vars = {key: str(value) for key, value in (adapter.get("vars") or {}).items()}
        self.project_name = str(adapter.get("project") or os.path.basename(located.project))
        self.main = normalize(self.vars.get("main") or located.project)
        self.state_rel = (adapter.get("paths") or {}).get("stateDir")
        if not isinstance(self.state_rel, str) or not self.state_rel:
            raise UsageError("adapter.json 에 paths.stateDir 없음")
        self.state_dir = normalize(os.path.join(self.main, self.state_rel))
        self.root = slots["root"]
        self.client_template = slots["client"]
        self.stale_hours = mission_guard.stale_hours_of(adapter)
        self.names = list(dict.fromkeys(slots.get("names") or []))
        for name in self.names:
            self.check_format(name)
        self.prepare = list(slots.get("prepare") or [])
        self.refresh_ids = list(slots.get("refresh") or [])
        self.editor = slots.get("editor") or ""
        self.autocompact = slots.get("autocompact", 500000 if runtime == "claude" else 200000)
        if type(self.autocompact) is not int or not 100000 <= self.autocompact <= 1000000:
            raise UsageError("slots.autocompact 는 100000~1000000 정수여야 함")
        self.stages = [stage for stage in adapter.get("stages") or [] if isinstance(stage, dict) and stage.get("id")]
        self.vcs = build_vcs(slots, self.main)
        self.baseline = ""
        self.baseline_origin = ""
        self.rows_path = os.path.join(self.state_dir, "slots.tsv")
        self.exclusive_lock_path = os.path.join(self.state_dir, "slots", "exclusive.lock")
        self.main_lock_path = os.path.join(self.state_dir, "slots", "main.lock")

    def slot_path(self, name):
        return normalize(os.path.join(self.root, name))

    def client_of(self, name):
        return self.client_template.replace("{name}", name)

    def slot_state_dir(self, name):
        return normalize(os.path.join(self.slot_path(name), self.state_rel))

    def slot_lock_path(self, name):
        return normalize(os.path.join(self.state_dir, "slots", f"{name}.lock"))

    def log_path(self, name, step_id, suffix=".log"):
        return normalize(os.path.join(self.state_dir, "slots", f"{name}-{step_id}{suffix}"))

    def timing_path(self, name):
        return normalize(os.path.join(self.state_dir, "slots", f"{name}-timing.tsv"))

    def values(self, name, step_id=""):
        values = dict(self.vars)
        values.update(
            name=name,
            slot=self.slot_path(name),
            client=self.client_of(name),
            main=self.main,
        )
        if self.runtime == "claude":
            values.update(memoryDir=claude_memory_dir(self.slot_path(name)), mainMemoryDir=claude_memory_dir(self.main))
        if step_id:
            values["log"] = self.log_path(name, step_id)
        return values

    def prepare_entries(self):
        entries = []
        for index, raw in enumerate(self.prepare):
            step_id = str(raw.get("id") or "") if isinstance(raw, dict) else ""
            if not step_id or not isinstance(raw.get("run"), str) or not raw["run"]:
                raise UsageError(f"slots.prepare[{index}] 에 id 와 run 문자열이 필요함")
            if not isinstance(raw.get("cwd") or "", str):
                raise UsageError(f"slots.prepare[{index}] ({step_id}) 의 cwd 가 문자열이 아님")
            try:
                group = int(raw.get("group", 0))
            except (TypeError, ValueError):
                raise UsageError(f"slots.prepare[{index}] ({step_id}) 의 group 이 숫자가 아님: {raw.get('group')!r}")
            entries.append((step_id, group, raw))
        return entries

    def build_steps(self, name, only=None):
        steps = []
        for step_id, group, raw in self.prepare_entries():
            if only is not None and step_id not in only:
                continue
            values = self.values(name, step_id)
            if "{baseline}" in raw["run"] + (raw.get("cwd") or ""):
                if not self.baseline:
                    self.baseline = self.vcs.baseline()
                values["baseline"] = self.baseline
            cwd = expand(raw["cwd"], values) if raw.get("cwd") else ""
            steps.append(
                Step(
                    id=step_id,
                    command=expand(raw["run"], values),
                    cwd=normalize(cwd) if cwd else "",
                    group=group,
                    exclusive=bool(raw.get("exclusive")),
                    ok_exit=list(raw.get("okExit") or [0]),
                    log=values["log"],
                    out=self.log_path(name, step_id, ".out.log"),
                )
            )
        steps.sort(key=lambda step: step.group)
        return steps

    def refresh_steps(self, name, keep_local=False):
        known = {step_id for step_id, _, _ in self.prepare_entries()}
        unknown = [step_id for step_id in self.refresh_ids if step_id not in known]
        if unknown:
            raise UsageError(f"slots.refresh 의 단계가 prepare 에 없음: {', '.join(unknown)}")
        if keep_local and self.vcs.has_workspaces and SYNC_STEP not in self.refresh_ids:
            raise UsageError(f"--keep-local 은 slots.refresh 에 {SYNC_STEP} 단계가 있어야 함 (resolve 를 그 뒤에 함)")
        return self.build_steps(name, only=set(self.refresh_ids))

    def check_refresh(self, names, keep_local):
        # 진짜 {baseline} 은 메인 sync 뒤에 채워야 해서 자리표시자로 설정 오류만 먼저 잡음
        saved, self.baseline = self.baseline, "0" if self.vcs.has_workspaces else ""
        try:
            for name in names:
                self.refresh_steps(name, keep_local)
        finally:
            self.baseline = saved

    def check_format(self, name):
        if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
            raise UsageError(f"슬롯 이름 형식 오류 (영숫자로 시작, 영숫자·_·- 만): {name}")
        return name

    def check_name(self, name):
        self.check_format(name)
        if name not in self.names:
            raise UsageError(f"slots.names 에 없는 슬롯 이름: {name} (허용: {', '.join(self.names) or '없음'})")
        return name

    def path_problem(self, name):
        slot = self.slot_path(name)
        if not inside_root(self.root, slot):
            return f"슬롯 경로가 root({self.root}) 안이 아니거나 root 자체임: {slot}"
        if is_link(slot):
            return f"슬롯 폴더가 링크·junction 임: {slot}"
        return ""


class Rows:
    def __init__(self, path):
        self.path = path

    def read(self):
        try:
            with open(self.path, encoding="utf-8-sig") as f:
                lines = f.read().splitlines()
        except FileNotFoundError:
            return []
        header = lines[0].split("\t") if lines else []
        legacy = "state" not in header
        rows = []
        for line in lines[1:]:
            cells = line.split("\t")
            row = {key: "-" for key in ROW_FIELDS}
            row.update({column: cell for column, cell in zip(header, cells) if column in ROW_FIELDS})
            if legacy:
                row["state"] = STATE_READY
            if len(cells) >= 2 and row["name"] not in ("", "-"):
                rows.append(row)
        return rows

    def find(self, name):
        return next((row for row in self.read() if row["name"] == name), {})

    def upsert(self, name, **values):
        with FileLock(self.path + ".lock", "slots.tsv", ROWS_LOCK_TIMEOUT):
            rows = self.read()
            row = next((r for r in rows if r["name"] == name), None)
            if row is None:
                row = {key: "-" for key in ROW_FIELDS}
                row["name"] = name
                rows.append(row)
            row.update(values)
            self.write(rows)

    def remove(self, name):
        with FileLock(self.path + ".lock", "slots.tsv", ROWS_LOCK_TIMEOUT):
            self.write([row for row in self.read() if row["name"] != name])

    def write(self, rows):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        temp = f"{self.path}.{os.getpid()}.tmp"
        with open(temp, "w", encoding="utf-8", newline="\n") as f:
            f.write("\t".join(ROW_FIELDS) + "\n")
            for row in rows:
                f.write("\t".join(row.get(key, "-") or "-" for key in ROW_FIELDS) + "\n")
        os.replace(temp, self.path)


def row_state(row):
    return row.get("state", "")


class Runner:
    def __init__(self, config):
        self.config = config
        self.rows = Rows(config.rows_path)
        self.say_lock = threading.Lock()
        self.timing_lock = threading.Lock()

    def say(self, text):
        with self.say_lock:
            print(text, file=sys.stderr, flush=True)

    def execute(self, step):
        os.makedirs(os.path.dirname(step.out), exist_ok=True)
        argv = step.command if os.name == "nt" else shlex.split(step.command)
        with open(step.out, "wb") as out:
            try:
                return subprocess.run(
                    argv,
                    cwd=step.cwd or None,
                    env=child_env(step.cwd),
                    stdout=out,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                ).returncode
            except OSError as e:
                out.write(f"실행 실패: {e}\n".encode("utf-8"))
                return 127

    def record_timing(self, name, step_id, started, seconds, code):
        path = self.config.timing_path(name)
        with self.timing_lock:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8", newline="\n") as f:
                f.write(f"{started}\t{step_id}\t{seconds:.1f}\t{code}\n")

    def run_step(self, name, step):
        def timed():
            started = datetime.now().isoformat(timespec="seconds")
            begin = time.monotonic()
            code = self.execute(step)
            return started, time.monotonic() - begin, code

        try:
            if step.exclusive:
                with FileLock(self.config.exclusive_lock_path, "exclusive 단계", EXCLUSIVE_LOCK_TIMEOUT):
                    started, seconds, code = timed()
            else:
                started, seconds, code = timed()
        except LockBusy as e:
            os.makedirs(os.path.dirname(step.out), exist_ok=True)
            with open(step.out, "w", encoding="utf-8") as f:
                f.write(f"{e}\n")
            started, seconds, code = datetime.now().isoformat(timespec="seconds"), 0.0, 126
        self.record_timing(name, step.id, started, seconds, code)
        result = StepResult(step.id, code, seconds, code in step.ok_exit, step.out, step.log)
        self.say(f"[{name}] {step.id} {'ok' if result.ok else 'FAIL'} {seconds:.1f}s")
        return result

    def run_steps(self, name, steps):
        done = []
        for group in sorted({step.group for step in steps}):
            batch = [step for step in steps if step.group == group]
            with ThreadPoolExecutor(max_workers=len(batch)) as pool:
                results = list(pool.map(lambda step: self.run_step(name, step), batch))
            done.extend(results)
            if not all(result.ok for result in results):
                break
        return done


def format_seconds(results):
    return ", ".join(f"{result.id} {result.seconds:.0f}s" for result in results)


def first_failure(results):
    return next((result for result in results if not result.ok), None)


def print_baseline(config):
    if config.baseline:
        origin = config.baseline_origin or f"메인 워크스페이스 {config.vcs.main_client} 의 have"
        print(f"기준 CL: {config.baseline} ({origin})")


def describe_steps(steps):
    lines = []
    for step in steps:
        flags = " exclusive" if step.exclusive else ""
        where = f" (cwd {step.cwd})" if step.cwd else ""
        lines.append(f"  group {step.group}{flags} {step.id}{where}: {step.command}")
    return lines


def split_at_sync(steps):
    sync_group = next((step.group for step in steps if step.id == SYNC_STEP), None)
    if sync_group is None:
        return steps, []
    return [step for step in steps if step.group <= sync_group], [step for step in steps if step.group > sync_group]


def describe_refresh(config, name, steps, keep_local):
    vcs, client = config.vcs, config.client_of(name)
    head, tail = split_at_sync(steps) if keep_local else (steps, [])
    keep_description = KEEP_DESCRIPTION.format(name=name)
    keep = [f"  keep-local: {command}" for command in vcs.describe_keep_local(client, keep_description)] if keep_local else []
    churn = [f"  churn 되돌림: {command}" for command in vcs.describe_churn(client)]
    resolve = [f"  resolve (sync 뒤): {command}" for command in vcs.describe_resolve(client)] if keep_local else []
    return [*keep, *churn, *describe_steps(head), *resolve, *describe_steps(tail)]


def registry_warning(config):
    directory = mission_guard.registry_dir()
    if not directory:
        return "레지스트리를 찾지 못해 roots 확인 못 함 (HAN_ADAPTERS_DIR 가 세션 환경에 없음)"
    try:
        entries = mission_guard.load_registry(directory)
    except mission_guard.GuardOff as e:
        return f"레지스트리를 읽지 못해 roots 확인 못 함: {e}"
    sample = config.slot_path("x")
    if any(mission_guard.root_matches(sample, root) for entry in entries for root in entry["roots"]):
        return ""
    return f"레지스트리 roots 에 {config.root}/* 가 없음 — 슬롯에서 띄운 세션이 어댑터를 못 찾음"


def read_mission_fields(slot_state, mission_id):
    fields = {"상태": "-", "현재 단계": "-"}
    path = os.path.join(slot_state, mission_id, "mission.md")
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            for line in f:
                match = MISSION_FIELD.match(line)
                if match:
                    fields[match.group(1)] = match.group(2) or "-"
    except OSError:
        pass
    return fields


def active_of(config, name):
    return mission_guard.read_active(config.slot_state_dir(name))


def stale_note(config, name, active):
    # 끊김 의심이면 안내 문구, 아니면 "". 판정 실패는 조용히 무시(상태 출력이 깨지지 않게)
    if not active or not active.id or active.corrupt:
        return ""
    try:
        state = mission_guard.staleness(config.slot_state_dir(name), active, getattr(config, "stale_hours", 0))
    except Exception:
        return ""
    return mission_guard.stale_text(state) if state.stale else ""


def active_text(active):
    if active.corrupt:
        return "손상"
    return f"{active.id}@{active.session[:8]}" if active.session else active.id


def remove_tree(path):
    def make_writable(function, target, *_):
        os.chmod(target, 0o700)
        function(target)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=make_writable)
    else:
        shutil.rmtree(path, onerror=make_writable)


def display_width(text):
    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)


def render_table(header, rows):
    widths = [max(display_width(str(row[i])) for row in [header, *rows]) for i in range(len(header))]
    lines = []
    for row in [header, *rows]:
        cells = [str(cell) + " " * (width - display_width(str(cell))) for cell, width in zip(row, widths)]
        lines.append("  ".join(cells).rstrip())
    return lines


def state_refusal(name, state, allow_refresh_failed):
    if state == STATE_READY:
        return ""
    if allow_refresh_failed and state.startswith(STATE_REFRESH_FAILED):
        return ""
    if not state:
        return f"슬롯 {name} 이 slots.tsv 에 없음 — create --resume {name} 으로 준비를 마쳐야 함"
    if state == STATE_CREATING:
        return f"슬롯 {name} 의 state 가 creating — create --resume {name} 으로 이어서 마쳐야 함"
    if state.startswith(STATE_REFRESH_FAILED):
        return f"슬롯 {name} 의 state 가 {state} — refresh 로 복구해야 함 (run 은 --refresh)"
    if state.startswith(STATE_FAILED):
        return f"슬롯 {name} 의 state 가 {state} — create --resume {name} 으로 준비를 다시 돌려야 함"
    return f"슬롯 {name} 의 state 가 {state} — ready 가 아님"


def slot_refusal(config, name):
    slot = config.slot_path(name)
    client = config.client_of(name)
    problem = config.path_problem(name)
    if problem:
        return problem
    if not os.path.isdir(slot):
        return f"슬롯 폴더 없음: {slot}"
    if config.vcs.has_workspaces and not config.vcs.client_exists(client):
        return f"작업 공간 없음: {client}"
    return config.vcs.identity_problem(name, client, slot)


def idle_refusal(config, name, allow_refresh_failed):
    state = row_state(Rows(config.rows_path).find(name))
    refusal = state_refusal(name, state, allow_refresh_failed) or slot_refusal(config, name)
    if refusal:
        return refusal
    active = active_of(config, name)
    if active.id:
        stale = stale_note(config, name, active)
        hint = f" ({stale}; {mission_guard.FORCE_CLOSE_HINT})" if stale else ""
        return f"슬롯 {name} 에서 미션 {active_text(active)} 진행 중{hint}"
    return ""


def preview(items):
    shown = list(items[:PREVIEW_LIMIT])
    if len(items) > PREVIEW_LIMIT:
        shown.append(f"… 외 {len(items) - PREVIEW_LIMIT}개")
    return shown


def churn_notes(skipped):
    return [f"엔진이 다시 쓰는 파일 {skipped}개는 검사에서 뺌"] if skipped else []


def local_change_reasons(config, client, edits_only, consequence):
    changes, skipped = config.vcs.local_changes(client, edits_only)
    notes = churn_notes(skipped)
    if not changes:
        return [], notes
    reasons = [f"로컬 변경 {len(changes)}개 — {consequence}. 버리려면 --discard-local", *(f"  {line}" for line in preview(changes))]
    return reasons, notes


def workspace_warning(config, name):
    current = config.vcs.workspace_name_in(config.slot_path(name))
    client = config.client_of(name)
    if not current or current.casefold() == client.casefold():
        return ""
    return f"p4 info 의 작업 공간이 {current} (기대 {client}) — P4CLIENT 환경변수·레지스트리가 .p4config 를 이김, 쓰기 명령은 p4 -c"


def create_one(runner, name, steps, resume):
    config, vcs = runner.config, runner.config.vcs
    slot = config.slot_path(name)
    client = config.client_of(name)
    with FileLock(config.slot_lock_path(name), f"슬롯 {name}"):
        row = runner.rows.find(name)
        if resume:
            if row_state(row) == STATE_READY:
                return Outcome(name, "skipped", "이미 ready — 최신화는 refresh")
            refusal = slot_refusal(config, name)
            if refusal:
                return Outcome(name, "failed", f"이어받을 수 없음: {refusal}")
        else:
            problem = config.path_problem(name)
            if problem:
                return Outcome(name, "failed", problem)
            if os.path.exists(slot):
                return Outcome(name, "skipped", f"폴더가 이미 있음: {slot} (준비가 덜 끝났으면 create --resume {name})")
            if vcs.client_exists(client):
                return Outcome(name, "skipped", f"작업 공간이 이미 있음: {client} (이어받으려면 create --resume {name})")
        begin = time.monotonic()
        today = date.today().isoformat()
        marked = {"path": slot.replace("\\", "/"), "client": client, "state": STATE_CREATING}
        if not row:
            marked.update(created=today, synced="-")
        runner.rows.upsert(name, **marked)
        step = "workspace"
        try:
            if not resume:
                vcs.create_workspace(name, client, slot)
                step = "folder"
                os.makedirs(slot)
                step = "config"
                vcs.write_config(client, slot)
            step = "prepare"
            results = runner.run_steps(name, steps)
        except (RuntimeError, OSError):
            runner.rows.upsert(name, state=STATE_FAILED + step)
            raise
        failure = first_failure(results)
        if failure:
            runner.rows.upsert(name, state=STATE_FAILED + failure.id)
            return Outcome(name, "failed", failure.failure_text())
        runner.rows.upsert(name, synced=today, state=STATE_READY)
        outcome = Outcome(name, "resumed" if resume else "created", f"{time.monotonic() - begin:.0f}s ({format_seconds(results)})")
        outcome.warnings.append(workspace_warning(config, name))
        return outcome


def guarded(function, runner, name, *args):
    try:
        return function(runner, name, *args)
    except (RuntimeError, OSError) as e:
        return Outcome(name, "failed", str(e))


def report(outcomes, warnings=()):
    counts = {}
    for outcome in outcomes:
        counts[outcome.state] = counts.get(outcome.state, 0) + 1
    print("요약: " + (", ".join(f"{state} {count}" for state, count in counts.items()) or "대상 없음"))
    for outcome in outcomes:
        print(f"  {outcome.name}: {outcome.state}" + (f" — {outcome.detail}" if outcome.detail else ""))
        for note in outcome.notes:
            print(f"    {note}")
    for warning in filter(None, [*warnings, *(w for outcome in outcomes for w in outcome.warnings)]):
        print(f"경고: {warning}")
    return FAILED if any(outcome.failed for outcome in outcomes) else OK


def unique_names(config, names):
    return list(dict.fromkeys(config.check_name(name) for name in names))


def require_names(config, names):
    if not names:
        raise UsageError("슬롯 이름이 필요함")
    return unique_names(config, names)


def cmd_create(config, args):
    names = unique_names(config, args.names)
    if args.resume and not names:
        raise UsageError("create --resume 은 슬롯 이름이 필요함")
    if not names:
        names = [name for name in config.names if not os.path.exists(config.slot_path(name))]
    if not names:
        print("만들 슬롯 없음 (slots.names 가 모두 있음)")
        return OK
    plans = {name: config.build_steps(name) for name in names}
    print_baseline(config)
    if args.dry_run:
        return dry_create(config, plans, args.resume)
    runner = Runner(config)
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        outcomes = list(pool.map(lambda name: guarded(create_one, runner, name, plans[name], args.resume), names))
    created = any(outcome.state in ("created", "resumed") for outcome in outcomes)
    return report(outcomes, [registry_warning(config)] if created else [])


def dry_create(config, plans, resume):
    rows = Rows(config.rows_path)
    for name, steps in plans.items():
        slot = config.slot_path(name)
        client = config.client_of(name)
        if resume:
            print(f"[{name}] create --resume (dry-run) 폴더 {slot}")
            if row_state(rows.find(name)) == STATE_READY:
                print("  이미 ready — 건너뜀")
                continue
            refusal = slot_refusal(config, name)
            if refusal:
                print(f"  거부: {refusal}")
                continue
            print("  작업 공간·폴더 만들기는 건너뛰고 prepare 전체를 다시 실행")
        else:
            exists = "있음 — 건너뜀" if os.path.exists(slot) else "없음"
            print(f"[{name}] create (dry-run) 폴더 {slot} {exists}")
            for line in config.vcs.describe_create(name, client, slot):
                print(f"  {line}")
        for line in describe_steps(steps):
            print(line)
    return OK


def refresh_one(runner, name, steps, keep_local, discard_local):
    config, vcs = runner.config, runner.config.vcs
    client = config.client_of(name)
    with FileLock(config.slot_lock_path(name), f"슬롯 {name}"):
        refusal = idle_refusal(config, name, allow_refresh_failed=True)
        if refusal:
            return Outcome(name, "failed", f"갱신 거부: {refusal}")
        opened = vcs.opened(client)
        churn_opened = [line for line in opened if vcs.is_churn(line)]
        if churn_opened:
            return Outcome(name, "failed", "엔진이 다시 쓰는 파일(slots.churn)이 열려 있음 — 갱신 거부", churn_opened[:PREVIEW_LIMIT])
        if opened and not keep_local:
            return Outcome(name, "failed", f"열린 파일 {len(opened)}개 — 갱신 거부 (--keep-local 로 보관하고 진행)", opened[:PREVIEW_LIMIT])
        notes = []
        if keep_local:
            changes, skipped = vcs.local_changes(client, True)
            notes = churn_notes(skipped)
            if changes:
                changelist, count = vcs.keep_local(client, KEEP_DESCRIPTION.format(name=name), changes)
                notes.append(f"로컬 변경 보관: 보관 CL {changelist} 에 열리지 않은 변경 파일 {count}개를 엶")
        elif not discard_local:
            reasons, notes = local_change_reasons(config, client, True, "열리지 않은 채 바뀐 파일이 sync 로 덮임")
            if reasons:
                return Outcome(name, "failed", "갱신 거부", [*reasons, *notes])
        try:
            vcs.restore_churn(client)
        except RuntimeError:
            runner.rows.upsert(name, state=STATE_REFRESH_FAILED + "churn")
            raise
        head, tail = split_at_sync(steps) if keep_local else (steps, [])
        stage = "prepare"
        conflicts = []
        try:
            results = runner.run_steps(name, head)
            failure = first_failure(results)
            if keep_local and not failure:
                stage = "resolve"
                conflicts = vcs.resolve_conflicts(client)
                stage = "prepare"
                if not conflicts:
                    results += runner.run_steps(name, tail)
                    failure = first_failure(results)
        except (RuntimeError, OSError):
            runner.rows.upsert(name, state=STATE_REFRESH_FAILED + stage)
            raise
        if conflicts:
            runner.rows.upsert(name, state=STATE_REFRESH_FAILED + "resolve")
            detail = f"충돌 {len(conflicts)}개 남음 — 직접 resolve 한 뒤 refresh --keep-local 다시"
            return Outcome(name, "failed", detail, [*preview(conflicts), *notes])
        if failure:
            runner.rows.upsert(name, state=STATE_REFRESH_FAILED + failure.id)
            return Outcome(name, "failed", failure.failure_text(), notes)
        runner.rows.upsert(name, synced=date.today().isoformat(), state=STATE_READY)
        outcome = Outcome(name, "refreshed", format_seconds(results), notes)
        outcome.warnings.append(workspace_warning(config, name))
        return outcome


def reject_conflicting_local_flags(args):
    if args.keep_local and args.discard_local:
        raise UsageError("--keep-local 과 --discard-local 은 같이 못 씀")


def sync_main_workspace(config, names, main_keep_local, dry_run):
    vcs = config.vcs
    if not vcs.has_workspaces:
        return None
    if all(idle_refusal(config, name, allow_refresh_failed=True) for name in names):
        return None
    main = vcs.main_client
    with contextlib.nullcontext() if dry_run else FileLock(config.main_lock_path, "메인 sync", EXCLUSIVE_LOCK_TIMEOUT):
        head = vcs.head()
        pending = vcs.sync_preview(head)
        if not pending:
            print(f"메인 sync: 메인 워크스페이스 {main} 은 이미 head {head} — 생략")
            return None
        have = vcs.baseline()
        overwritten = vcs.unopened_edits(pending)
        if dry_run:
            print(f"[메인 {main}] sync (dry-run) have {have} → head {head}, 바뀔 파일 {len(pending)}개")
        if overwritten and not main_keep_local:
            detail = (
                f"메인 워크스페이스 {main} 에 열지 않고 고친 파일 {len(overwritten)}개가 sync 로 덮임 — "
                "메인 sync·슬롯 갱신 안 함 (--main-keep-local 로 메인 보관 CL 에 열고 진행)"
            )
            return report([Outcome("main", "failed", f"메인 sync 거부: {detail}", preview(overwritten))])
        if dry_run:
            if overwritten:
                print(f"  열지 않고 고쳐 sync 로 덮일 파일 {len(overwritten)}개 — 메인 보관 CL 에 열고 진행")
                print("\n".join(f"    {line}" for line in preview(overwritten)))
            print("\n".join(f"  {line}" for line in vcs.describe_main_sync(head, bool(overwritten))))
            config.baseline = head
            config.baseline_origin = f"메인 sync 뒤 head {head} 기준, 지금 메인 have 는 {have}"
            return None
        notes = []
        if overwritten:
            changelist, count = vcs.keep_local(main, MAIN_KEEP_DESCRIPTION, overwritten)
            notes.append(f"메인 보관 CL {changelist} 에 열리지 않은 변경 파일 {count}개를 엶")
        vcs.sync_main(head)
        conflicts = vcs.resolve_conflicts(main)
        if conflicts:
            detail = f"메인 충돌 {len(conflicts)}개 남음 — 직접 resolve 한 뒤 다시 (슬롯 갱신 안 함)"
            return report([Outcome("main", "failed", detail, [*preview(conflicts), *notes])])
        print(f"메인 sync: {have} → {head} (파일 {len(pending)}개)")
        for note in notes:
            print(note)
    return None


def cmd_refresh(config, args):
    names = require_names(config, args.names)
    reject_conflicting_local_flags(args)
    config.check_refresh(names, args.keep_local)
    code = sync_main_workspace(config, names, args.main_keep_local, args.dry_run)
    if code is not None:
        return code
    plans = {name: config.refresh_steps(name, args.keep_local) for name in names}
    print_baseline(config)
    if args.dry_run:
        for name, steps in plans.items():
            print(f"[{name}] refresh (dry-run)")
            refusal = idle_refusal(config, name, allow_refresh_failed=True)
            if refusal:
                print(f"  거부: {refusal}")
                continue
            print("\n".join(describe_refresh(config, name, steps, args.keep_local)))
        return OK
    runner = Runner(config)
    outcomes = [guarded(refresh_one, runner, name, plans[name], args.keep_local, args.discard_local) for name in names]
    return report(outcomes)


SESSION_NAME_LIMIT = 40


def wt_command(config, name, task, session_task="", resume_session=""):
    prompt = f"$han-codex:han-mission {task}" if config.runtime == "codex" else f"/han:mission {task}"
    escaped = prompt.replace(";", "\\;")
    title = f"{config.project_name}:{name}"
    # wt.exe 는 .exe 만 찾아서 npm shim(codex.cmd) 은 전체 경로로 넘겨야 뜸
    executable = shutil.which(config.runtime) or config.runtime
    command = ["wt.exe", "-w", "0", "nt", "--title", title, "-d", config.slot_path(name), executable]
    if config.runtime == "codex":
        options = ["-c", f"model_auto_compact_token_limit={config.autocompact}"]
        return command + options + (["resume", resume_session] if resume_session else []) + [escaped]
    session_name = f"{name}: {session_task or task}"
    if len(session_name) > SESSION_NAME_LIMIT:
        session_name = session_name[: SESSION_NAME_LIMIT - 1] + "…"
    session = resume_session or str(uuid.uuid4())
    return command + ["--autocompact", str(config.autocompact), "-n", session_name.replace(";", "\\;"),
                      "--resume" if resume_session else "--session-id", session,
                      "--", escaped]


def launch_mission(config, name, command):
    subprocess.Popen(command, env=launch_env())


def launch_editor(config, name):
    command = expand(config.editor, config.values(name))
    argv = command if os.name == "nt" else shlex.split(command)
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(
        argv,
        env=launch_env(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )


RESUME_PROMPT = "미션 이어서"


def resume_refusal(config, name):
    state = row_state(Rows(config.rows_path).find(name))
    refusal = state_refusal(name, state, allow_refresh_failed=False) or slot_refusal(config, name)
    if refusal:
        return refusal, ""
    mission_id = active_of(config, name).id
    if not mission_id:
        return f"슬롯 {name} 에 진행 중 미션 없음 — run {name} <작업> 으로 새로 시작", ""
    return "", mission_id


def session_runtime(folder, session):
    import mission_state

    if not mission_state.SESSION_ID.fullmatch(session):
        return ""
    sessions_path = os.path.join(folder, mission_guard.SESSIONS_FILE)
    rows = mission_state.read_table(sessions_path, mission_guard.SESSIONS_HEADER) if os.path.isfile(sessions_path) else []
    transcript = next((row["transcript"] for row in reversed(rows) if row["session"] == session and os.path.isfile(row["transcript"])), "")
    if not transcript:
        home = os.path.expanduser("~")
        transcript = mission_state.search_codex(session, home) or mission_state.search_claude(session, home)
    if not transcript:
        return ""
    with open(transcript, encoding="utf-8-sig") as stream:
        first = json.loads(stream.readline())
    return "codex" if first.get("type") == "session_meta" else "claude"


def resume_command(config, name, mission_id, new_session=False):
    session = active_of(config, name).session
    resume_session = session if re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", session) else ""
    if new_session:
        resume_session = ""
    if resume_session:
        import mission_state
        try:
            runtime = session_runtime(os.path.join(config.slot_state_dir(name), mission_id), session)
        except (OSError, ValueError, mission_state.StateError) as exc:
            raise UsageError(f"슬롯 {name} 세션 기록 확인 실패: {exc}") from exc
        if not runtime:
            raise UsageError(f"슬롯 {name} 의 기존 세션 {session} 기록을 못 찾음 — --new-session 으로 새 {config.runtime} 세션에서 이어받을 것")
        if runtime != config.runtime:
            raise UsageError(
                f"슬롯 {name} 의 기존 세션은 {runtime} — 해당 런타임의 슬롯 스크립트로 이어받거나 --new-session 으로 새 {config.runtime} 세션에서 이어받을 것"
            )
    return wt_command(config, name, RESUME_PROMPT, f"{mission_id} 이어서", resume_session)


def cmd_run_resume(config, args, name):
    if args.task:
        raise UsageError("--resume 에는 작업을 줄 수 없음")
    if args.refresh or args.keep_local or args.discard_local or args.main_keep_local:
        raise UsageError(
            "--resume 은 --refresh·--keep-local·--discard-local·--main-keep-local 과 같이 못 씀 — 진행 중 미션의 열린 파일을 건드리면 안 됨"
        )
    if args.editor and not config.editor:
        raise UsageError("slots.editor 가 없음")
    if args.editor:
        expand(config.editor, config.values(name))
    refusal, mission_id = resume_refusal(config, name)
    if refusal:
        print(f"거부: {refusal}")
        return FAILED
    command = resume_command(config, name, mission_id, args.new_session)
    if args.dry_run:
        print_baseline(config)
        if args.editor:
            print(f"editor: {expand(config.editor, config.values(name))}")
        print("wt: " + subprocess.list2cmdline(command))
        return OK
    print_baseline(config)
    with FileLock(config.slot_lock_path(name), f"슬롯 {name}"):
        refusal, mission_id = resume_refusal(config, name)
        if refusal:
            print(f"거부: {refusal}")
            return FAILED
        command = resume_command(config, name, mission_id, args.new_session)
        if args.editor:
            launch_editor(config, name)
        try:
            launch_mission(config, name, command)
        except OSError as e:
            print(f"wt.exe 실행 실패: {e}")
            return FAILED
    print(f"요약: {name} 에서 미션 {mission_id} 이어서 열기 요청 — {subprocess.list2cmdline(command)}")
    return OK


def cmd_run(config, args):
    name = config.check_name(args.name)
    if args.resume:
        return cmd_run_resume(config, args, name)
    if args.new_session:
        raise UsageError("--new-session 은 --resume 과 같이 써야 함")
    task = " ".join(args.task).strip()
    if not task:
        raise UsageError("run <name> <작업...> 에 작업이 필요함")
    if '"' in task or "\n" in task or "\r" in task:
        raise UsageError('작업 문자열에 큰따옴표(")·개행을 쓸 수 없음 — 바꿔 쓰기')
    if args.editor and not config.editor:
        raise UsageError("slots.editor 가 없음")
    reject_conflicting_local_flags(args)
    if args.main_keep_local and not args.refresh:
        raise UsageError("--main-keep-local 은 --refresh 와 같이 써야 함")
    if args.refresh:
        config.check_refresh([name], args.keep_local)
    if args.editor:
        expand(config.editor, config.values(name))
    command = wt_command(config, name, task)
    if args.dry_run:
        refusal = idle_refusal(config, name, allow_refresh_failed=args.refresh)
        if refusal:
            print(f"거부: {refusal}")
            return FAILED
        if args.refresh:
            code = sync_main_workspace(config, [name], args.main_keep_local, dry_run=True)
            if code is not None:
                return code
        refresh_plan = config.refresh_steps(name, args.keep_local) if args.refresh else []
        print_baseline(config)
        if args.refresh:
            print(f"[{name}] refresh (dry-run)")
            print("\n".join(describe_refresh(config, name, refresh_plan, args.keep_local)))
        if args.editor:
            print(f"editor: {expand(config.editor, config.values(name))}")
        print("wt: " + subprocess.list2cmdline(command))
        return OK
    if args.refresh:
        code = sync_main_workspace(config, [name], args.main_keep_local, dry_run=False)
        if code is not None:
            return code
    refresh_plan = config.refresh_steps(name, args.keep_local) if args.refresh else []
    print_baseline(config)
    if args.refresh:
        outcome = guarded(refresh_one, Runner(config), name, refresh_plan, args.keep_local, args.discard_local)
        if outcome.failed:
            return report([outcome])
        for warning in filter(None, outcome.warnings):
            print(f"경고: {warning}")
    with FileLock(config.slot_lock_path(name), f"슬롯 {name}"):
        refusal = idle_refusal(config, name, allow_refresh_failed=False)
        if refusal:
            print(f"거부: {refusal}")
            return FAILED
        if args.editor:
            launch_editor(config, name)
        try:
            launch_mission(config, name, command)
        except OSError as e:
            print(f"wt.exe 실행 실패: {e}")
            return FAILED
    print(f"요약: {name} 에서 미션 시작 요청 — {subprocess.list2cmdline(command)}")
    return OK


def blockers_of(config, name, has_client):
    client = config.client_of(name)
    reasons = []
    active = active_of(config, name)
    if active.id:
        reasons.append(f"ACTIVE 미션 {active_text(active)}")
    if not has_client:
        return reasons
    for label, items in (
        ("열린 파일", config.vcs.opened(client)),
        ("shelved CL", config.vcs.shelved(client)),
        ("pending CL", config.vcs.pending(client)),
    ):
        if items:
            reasons.append(f"{label} {len(items)}개")
            reasons.extend(f"  {item}" for item in items[:PREVIEW_LIMIT])
    return reasons


def remove_one(runner, name, discard_local):
    config, vcs = runner.config, runner.config.vcs
    client = config.client_of(name)
    slot = config.slot_path(name)
    with FileLock(config.slot_lock_path(name), f"슬롯 {name}"):
        row = runner.rows.find(name)
        state = row_state(row)
        if state == STATE_CREATING:
            return Outcome(name, "failed", f"state 가 creating — create 가 돌고 있거나 중단됨. create --resume {name} 으로 마친 뒤 지움")
        if state and state != STATE_READY and not state.startswith(STATE_FAILED):
            return Outcome(name, "failed", f"알 수 없는 state {state}")
        problem = config.path_problem(name) or vcs.identity_problem(name, client, slot)
        if problem:
            return Outcome(name, "failed", f"슬롯이 확실하지 않아 거부: {problem}")
        has_client = vcs.client_exists(client)
        folder = os.path.isdir(slot)
        if not has_client and not folder:
            if not row:
                return Outcome(name, "skipped", "폴더·작업 공간·slots.tsv 행이 모두 없음")
            runner.rows.remove(name)
            return Outcome(name, "removed", "폴더·작업 공간은 이미 없어 slots.tsv 행만 지움")
        reasons = blockers_of(config, name, has_client)
        notes = []
        if not reasons and has_client and not discard_local:
            reasons, notes = local_change_reasons(config, client, False, "기록 안 된 새 파일·편집·삭제가 사라짐")
        if reasons:
            return Outcome(name, "failed", "지우면 안 되는 상태", [*reasons, *notes])
        if has_client:
            vcs.delete_workspace(client)
        if folder:
            try:
                remove_tree(slot)
            except OSError as e:
                runner.rows.upsert(name, path=slot.replace("\\", "/"), client=client, state=STATE_FAILED + "remove")
                return Outcome(name, "failed", f"폴더 삭제 실패 ({e}) — Unity 에디터·터미널 등이 잡고 있을 수 있음. 닫고 다시")
        runner.rows.remove(name)
        return Outcome(name, "removed", slot, notes)


def cmd_remove(config, args):
    names = require_names(config, args.names)
    if not args.yes:
        print("거부: 되돌릴 수 없음 — 사용자 확인 후 --yes")
        return FAILED
    runner = Runner(config)
    return report([guarded(remove_one, runner, name, args.discard_local) for name in names])


def status_row(config, name, tsv):
    if not NAME_PATTERN.fullmatch(name):
        return [name, "-", "-", "-", "-", "-", "-", "-", "-", "이름 형식 오류 (slots.tsv 행)"]
    slot = config.slot_path(name)
    client = config.client_of(name)
    folder = os.path.isdir(slot)
    notes = []
    has_client, opened = "-", "-"
    if not isinstance(config.vcs, NoVcs):
        try:
            has_client = config.vcs.client_exists(client)
            opened = len(config.vcs.opened(client)) if has_client else "-"
            if has_client and folder:
                notes.append(workspace_warning(config, name))
        except RuntimeError as e:
            has_client, opened = "?", "?"
            notes.append(str(e).splitlines()[0])
        has_client = {True: "yes", False: "no"}.get(has_client, has_client)
    if name not in config.names:
        notes.append("slots.names 에 없음")
    if tsv is None and folder:
        notes.append("slots.tsv 에 없음")
    if tsv is not None and not folder:
        notes.append("폴더 없음")
    if folder and has_client == "no":
        notes.append("작업 공간 없음")
    active = active_of(config, name) if folder else None
    state, step = "-", "-"
    if active and active.id and not active.corrupt:
        fields = read_mission_fields(config.slot_state_dir(name), active.id)
        state, step = fields["상태"], fields["현재 단계"]
        notes.append(stale_note(config, name, active))
    return [
        name,
        "yes" if folder else "no",
        has_client,
        opened,
        active_text(active) if active and active.id else "-",
        state,
        step,
        (tsv or {}).get("synced", "-"),
        (tsv or {}).get("state", "-"),
        "; ".join(filter(None, notes)),
    ]


def cmd_status(config, args):
    tsv = {row["name"]: row for row in Rows(config.rows_path).read()}
    names = list(dict.fromkeys(config.check_format(name) for name in args.names))
    names = names or [*tsv, *[name for name in config.names if name not in tsv]]
    with ThreadPoolExecutor(max_workers=max(1, min(len(names), 8))) as pool:
        rows = list(pool.map(lambda name: status_row(config, name, tsv.get(name)), names))
    present = sum(1 for row in rows if row[1] == "yes")
    active = sum(1 for row in rows if row[4] != "-")
    stale = sum(1 for row in rows if "끊김 의심" in row[9])
    stale_text = f" (끊김 의심 {stale}개)" if stale else ""
    print(f"요약: 슬롯 {len(rows)}개 중 폴더 {present}개, ACTIVE {active}개{stale_text}")
    header = ["name", "folder", "client", "opened", "active", "상태", "현재 단계", "synced", "state", "비고"]
    print("\n".join(render_table(header, rows)))
    return OK


def build_parser():
    parser = argparse.ArgumentParser(prog="slots.py", description="슬롯 워크스페이스 관리")
    parser.add_argument("--project", help="어댑터를 찾기 시작할 폴더 (기본: 현재 폴더)")
    parser.add_argument("--runtime", choices=RUNTIMES, default=None, help="어댑터 검색. 기본은 호출 런타임, Grok은 grok")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--project", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    common.add_argument("--runtime", choices=RUNTIMES, default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", parents=[common]).add_argument("names", nargs="*")

    create = sub.add_parser("create", parents=[common])
    create.add_argument("names", nargs="*")
    create.add_argument("--resume", action="store_true")
    create.add_argument("--jobs", type=int, default=2)
    create.add_argument("--dry-run", action="store_true")

    refresh = sub.add_parser("refresh", parents=[common])
    refresh.add_argument("names", nargs="*")
    refresh.add_argument("--keep-local", action="store_true")
    refresh.add_argument("--discard-local", action="store_true")
    refresh.add_argument("--main-keep-local", action="store_true")
    refresh.add_argument("--dry-run", action="store_true")

    run = sub.add_parser("run", parents=[common])
    run.add_argument("name")
    run.add_argument("task", nargs="*")
    run.add_argument("--editor", action="store_true")
    run.add_argument("--resume", action="store_true")
    run.add_argument("--new-session", action="store_true")
    run.add_argument("--refresh", action="store_true")
    run.add_argument("--keep-local", action="store_true")
    run.add_argument("--discard-local", action="store_true")
    run.add_argument("--main-keep-local", action="store_true")
    run.add_argument("--dry-run", action="store_true")

    remove = sub.add_parser("remove", parents=[common])
    remove.add_argument("names", nargs="*")
    remove.add_argument("--yes", action="store_true")
    remove.add_argument("--discard-local", action="store_true")
    return parser


COMMANDS = {
    "status": cmd_status,
    "create": cmd_create,
    "refresh": cmd_refresh,
    "run": cmd_run,
    "remove": cmd_remove,
}


def main(argv=None, runtime="claude"):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as e:
        return USAGE if e.code else OK
    runtime = getattr(args, "runtime", None) or runtime
    try:
        if args.command == "run" and runtime == "grok":
            raise UsageError("run 은 Claude·Codex 세션만 띄운다. Grok은 슬롯 폴더에서 grok 를 열고 han-run 을 쓴다")
        located = locate(args.project, runtime)
        config = Config(located, read_adapter(located.adapter_path, runtime), runtime)
        return COMMANDS[args.command](config, args)
    except UsageError as e:
        print(f"사용 오류: {e}", file=sys.stderr)
        return USAGE
    except RuntimeError as e:
        print(f"실패: {e}", file=sys.stderr)
        return FAILED


if __name__ == "__main__":
    sys.exit(main())
