#!/usr/bin/env python3
"""Grok 상태 조회와 실행 대상별 인계 브리프."""

import argparse
import os
import re
import sys
from typing import NamedTuple

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "hooks")))

import mission_guard  # noqa: E402

USAGE = 2
HEADER_FIELD = re.compile(r"^(상태|현재 단계|확인 [A-Za-z]\d*):[ \t]*(.*?)\s*$")
SLOT_FIELDS = ("name", "path", "client", "created", "synced", "state")
PROMPTS = {
    "claude": "/han:mission {goal}",
    "codex": "$han-codex:han-mission {goal}",
}
RUNTIME_LABEL = {"grok": "Grok", "claude": "Claude", "codex": "Codex"}
SLOT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


class Usage(Exception):
    pass


class Snapshot(NamedTuple):
    lines: list
    project: str
    main: str
    cwd: str
    active: mission_guard.Active
    busy_slots: tuple
    idle_slots: tuple
    slot_paths: dict


class GrokTarget(NamedTuple):
    slot: str
    path: str


def one_line(text, limit=160):
    collapsed = " ".join(str(text).split()).strip()
    return collapsed[:limit]


def header_fields(text):
    fields = {}
    for line in text.splitlines():
        if line.startswith("## "):
            break
        match = HEADER_FIELD.match(line)
        if match:
            fields[match.group(1)] = match.group(2).strip()
    return fields


def goal_line(text):
    lines = text.splitlines()
    try:
        start = next(index for index, line in enumerate(lines) if line.strip() == "## 목표")
    except StopIteration:
        return ""
    for line in lines[start + 1:]:
        if line.startswith("## "):
            return ""
        if line.strip():
            return one_line(line)
    return ""


def read_mission(state_dir, mission_id):
    path = os.path.join(state_dir, mission_id, "mission.md")
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as handle:
            text = handle.read()
    except FileNotFoundError:
        return {}, ""
    except OSError as error:
        return {}, f"읽기 실패 ({error})"
    return header_fields(text), goal_line(text)


def mission_lines(state_dir, active):
    if not os.path.isdir(state_dir):
        return ["- 없음"]
    names = []
    try:
        entries = sorted(os.listdir(state_dir))
    except OSError as error:
        return [f"- 상태 폴더를 읽지 못함 ({error})"]
    for name in entries:
        folder = os.path.join(state_dir, name)
        if os.path.isdir(folder) and os.path.isfile(os.path.join(folder, "mission.md")) and mission_guard.valid_mission_id(name):
            names.append(name)
    lines = []
    active_id = "" if active.corrupt else active.id
    for name in names:
        fields, goal = read_mission(state_dir, name)
        interesting = name == active_id or fields.get("상태") == "진행"
        if not interesting:
            continue
        marker = " [ACTIVE]" if name == active_id else " [진행, ACTIVE 아님]"
        confirms = " ".join(f"{key}={value}" for key, value in fields.items() if key.startswith("확인 "))
        pieces = [
            f"- {name}{marker}",
            f"상태={fields.get('상태', '-')}",
            f"단계={fields.get('현재 단계', '-')}",
        ]
        if confirms:
            pieces.append(confirms)
        pieces.append(f"목표={goal or '-'}")
        lines.append(" ".join(pieces))
    if active_id and active_id not in names:
        lines.insert(0, f"- {active_id} [ACTIVE] mission.md 없음")
    return lines or ["- 없음"]


def read_slot_rows(path):
    if not os.path.isfile(path):
        return [], "표 없음"
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as handle:
            lines = [line for line in handle.read().splitlines() if line.strip()]
    except OSError as error:
        return [], f"표를 읽지 못함 ({path}: {error})"
    if not lines:
        return [], ""
    header = lines[0].split("\t")
    if any(column not in header for column in ("name", "path", "state")):
        return [], f"표를 읽지 못함 ({path})"
    rows = []
    for line in lines[1:]:
        cells = line.split("\t")
        row = {key: "-" for key in SLOT_FIELDS}
        for column, cell in zip(header, cells):
            if column in row and cell != "":
                row[column] = cell
        rows.append(row)
    return rows, ""


def slot_lines(state_dir, state_rel, configured_paths):
    rows, problem = read_slot_rows(os.path.join(state_dir, "slots.tsv"))
    rows_by_name = {row["name"]: row for row in rows}
    for name, path in configured_paths.items():
        if name not in rows_by_name:
            rows_by_name[name] = {key: "-" for key in SLOT_FIELDS}
            rows_by_name[name].update(name=name, path=path, state="미확인")
        elif rows_by_name[name]["path"] in ("", "-"):
            rows_by_name[name]["path"] = path
    lines = [f"- {problem}"] if problem else []
    busy = []
    idle = []
    paths = {}
    for row in rows_by_name.values():
        name = row["name"]
        slot_path = row["path"]
        if slot_path and slot_path != "-":
            paths[name] = slot_path
        folder_ok = bool(slot_path and slot_path != "-" and os.path.isdir(slot_path))
        status, step, active_text = "-", "-", "폴더없음"
        if folder_ok:
            active = mission_guard.read_active(os.path.normpath(os.path.join(slot_path, state_rel)))
            if active.corrupt:
                active_text = "손상"
                lines.append(f"경고: {name} ACTIVE가 손상되어 있다. 새 미션을 열지 말고 사용자에게 알린다.")
            elif not active.id:
                active_text = "없음"
                if row["state"] == "ready":
                    idle.append(name)
            else:
                active_text = active.id
                fields, _goal = read_mission(os.path.normpath(os.path.join(slot_path, state_rel)), active.id)
                status = fields.get("상태", "-")
                step = fields.get("현재 단계", "-")
                busy.append(f"{name}:{active.id}")
        lines.append(
            f"- {name} state={row['state']} synced={row['synced']} path={slot_path} "
            f"active={active_text} 상태={status} 단계={step}"
        )
    return (lines or ["- 행 없음"]), tuple(busy), idle, paths


def configured_slot_paths(adapter):
    slots = adapter.get("slots") or {}
    if not isinstance(slots, dict):
        raise Usage("slots 는 객체여야 함")
    names = slots.get("names") or []
    if not isinstance(names, list):
        raise Usage("slots.names 는 배열이어야 함")
    for name in names:
        if not isinstance(name, str) or not SLOT_NAME.fullmatch(name):
            raise Usage(f"슬롯 이름 형식 오류 (영숫자로 시작, 영숫자·_·- 만): {name}")
    root = slots.get("root") or ""
    if not isinstance(root, str):
        raise Usage("slots.root 는 문자열이어야 함")
    return {name: os.path.normpath(os.path.join(root, name)) for name in names} if root else {}


def match_slot(path, slot_paths):
    norm = mission_guard.normalize_path(path)
    ranked = sorted(slot_paths.items(), key=lambda item: len(mission_guard.normalize_path(item[1])), reverse=True)
    for slot, root in ranked:
        root_norm = mission_guard.normalize_path(root)
        if norm == root_norm or norm.startswith(root_norm + "/"):
            return slot
    return ""


def resolve_grok_target(snapshot, slot_name):
    requested = slot_name.strip()
    if requested == "main":
        return GrokTarget("main", snapshot.main)
    if requested:
        if requested not in snapshot.slot_paths:
            allowed = ", ".join(("main", *snapshot.slot_paths))
            raise Usage(f"등록되지 않은 슬롯: {requested} (허용: {allowed})")
        return GrokTarget(requested, snapshot.slot_paths[requested])
    matched = match_slot(snapshot.cwd, snapshot.slot_paths)
    if matched:
        return GrokTarget(matched, snapshot.slot_paths[matched])
    return GrokTarget("main", snapshot.main)


def grok_handoff_lines(goal, target):
    place = "메인" if target.slot == "main" else target.slot
    return [
        "인계:",
        "런타임: Grok",
        f"실행 대상: {target.slot} ({place})",
        f"프로젝트 경로: {target.path}",
        "SendToAgent:",
        f"대상: {target.slot}",
        f"자리: {place}",
        f"프로젝트 경로: {target.path}",
        f"목표: {goal}",
        "제약: submit 금지. 범위 밖 수정 금지. 미션 상태 파일은 han 스크립트로만 쓴다.",
        "쓰기 확인: 파일을 쓰기 전에 이번 변경의 범위와 경로를 확인하고, 확인 없이 쓰지 않는다.",
        "실행 규칙: 저장소 코드는 프로젝트의 실행 규칙을 따르고, 로컬 상태·슬롯·작업 폴더는 han 스크립트로 본다.",
        "코디네이터는 이 대상을 개인 환경의 실행 봇에 연결한다. 조회 스크립트는 프로젝트 코드를 쓰지 않는다.",
    ]


def format_active(active):
    if active.corrupt:
        return "손상"
    if not active.id:
        return "없음"
    session = active.session or "-"
    opened = active.opened_at or "-"
    return f"{active.id} session={session} openedAt={opened}"


def build_snapshot(project):
    start = os.path.abspath(project or os.getcwd())
    located = mission_guard.find_adapter({"cwd": start, "han_runtime": "grok"})
    problems = []
    adapter = mission_guard.load_adapter(located.adapter_path, problems, runtime="claude")
    main = os.path.normpath(str((adapter.get("vars") or {}).get("main") or located.project))
    state_dir = mission_guard.state_dir_of(main, adapter)
    state_rel = (adapter.get("paths") or {}).get("stateDir")
    active = mission_guard.read_active(state_dir)
    slots, busy, idle, slot_paths = slot_lines(state_dir, state_rel, configured_slot_paths(adapter))
    project_name = str(adapter.get("project") or os.path.basename(located.project))
    lines = ["역할: 조회 전용. 실행은 코디네이터가 메인 또는 슬롯의 실행 봇에 인계하는 다음 단계다."]
    lines.extend(f"경고: {problem}" for problem in problems)
    lines.extend(
        [
            f"어댑터: {located.adapter_path}",
            f"프로젝트: {project_name}",
            f"작업 폴더: {located.project}",
            f"상태 폴더: {state_dir}",
            f"메인 ACTIVE: {format_active(active)}",
            "메인 미션:",
            *mission_lines(state_dir, active),
            "슬롯:",
            *slots,
            f"비어 있는 ready 슬롯: {', '.join(idle) if idle else '없음'}",
        ]
    )
    return Snapshot(lines, located.project, main, start, active, busy, tuple(idle), slot_paths)


def build_brief(project, goal, runtime, slot=""):
    collapsed = one_line(goal, limit=500)
    if not collapsed:
        raise Usage("goal 이 비어 있음")
    if runtime not in RUNTIME_LABEL:
        raise Usage("runtime 은 grok, claude, codex 중 하나")
    snapshot = build_snapshot(project)
    lines = list(snapshot.lines)
    if snapshot.active.corrupt:
        lines.append("주의: 메인 ACTIVE가 손상되어 있다. 새 미션을 열지 말고 사용자에게 알린다.")
    elif snapshot.active.id:
        lines.append(f"주의: 메인 ACTIVE가 이미 있다 ({snapshot.active.id}). 새 미션으로 열기 전에 사용자 확인이 필요하다.")
    if snapshot.busy_slots:
        lines.append(f"주의: 슬롯 ACTIVE가 있다 ({', '.join(snapshot.busy_slots)}). 그 슬롯에 다른 미션을 겹치지 않는다.")
    if runtime == "grok":
        target = resolve_grok_target(snapshot, slot)
        busy_names = {item.split(":", 1)[0] for item in snapshot.busy_slots}
        if target.slot != "main" and target.slot in busy_names:
            lines.append(f"주의: {target.slot} 은 이미 미션이 붙어 있다. 그 봇에 다른 미션을 겹치지 않는다.")
        lines.extend(grok_handoff_lines(collapsed, target))
        return lines
    lines.extend(
        [
            "인계:",
            f"런타임: {RUNTIME_LABEL[runtime]}",
            f"작업 폴더: {snapshot.project}",
            f"사람이 그 세션에 붙일 문장: {PROMPTS[runtime].format(goal=collapsed)}",
            "Grok은 이 문장을 실행하지 않는다. 미션 파일과 프로젝트 코드는 그 런타임 세션이 쓴다.",
        ]
    )
    if slot.strip():
        lines.append(f"슬롯: {slot.strip()}")
    return lines


def configure_stdio():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def main(argv=None):
    configure_stdio()
    parser = argparse.ArgumentParser(prog="han_state.py", description="han 상태 조회와 Grok·Claude·Codex 인계 문장")
    sub = parser.add_subparsers(dest="command", required=True)
    snapshot = sub.add_parser("snapshot")
    snapshot.add_argument("--project", default="")
    brief = sub.add_parser("brief")
    brief.add_argument("--project", default="")
    brief.add_argument("--goal", required=True)
    brief.add_argument("--runtime", default="grok", choices=tuple(RUNTIME_LABEL))
    brief.add_argument("--slot", default="", help="grok 인계 대상. main 또는 등록된 슬롯 이름. 비우면 작업 폴더로 고른다")
    try:
        args = parser.parse_args(argv)
        if args.command == "snapshot":
            lines = build_snapshot(args.project).lines
        else:
            lines = build_brief(args.project, args.goal, args.runtime, args.slot)
    except (Usage, mission_guard.AdapterNotFound, mission_guard.GuardOff) as error:
        print(f"오류: {error}", file=sys.stderr)
        return USAGE
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
