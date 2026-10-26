"""han 설치·어댑터 상태 점검(읽기 전용). 아무것도 고치거나 닫지 않는다.

사용: python doctor.py [--runtime claude|codex] [--json]
  점검: python 스텁, 레지스트리(HAN_ADAPTERS_DIR), 어댑터+local.json 읽기 문제, guard-cases,
        ACTIVE 끊김 의심(닫기 명령은 제안만), local.json vars.unityExe, 저장소 버전과 설치본 버전.
  줄 형식: OK|WARN|FAIL <영역>: <내용>. 종료 코드: FAIL 이 있으면 1, 아니면 0.
"""
import argparse
import contextlib
import glob
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(PLUGIN, "hooks"))
sys.path.insert(0, os.path.join(PLUGIN, "skills", "adapter-init", "scripts"))

import check_guard  # noqa: E402
import mission_guard  # noqa: E402

MISSION_STATE = {
    "claude": os.path.join(PLUGIN, "skills", "mission", "scripts", "mission_state.py"),
    "codex": os.path.join(PLUGIN, "codex", "skills", "han-mission", "scripts", "mission_state.py"),
}


class Report:
    def __init__(self):
        self.rows = []

    def add(self, level, area, text):
        self.rows.append({"level": level, "area": area, "text": text})

    @property
    def failed(self):
        return any(row["level"] == "FAIL" for row in self.rows)


def read_json(path):
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def check_python(report):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = check_guard.doctor()
    detail = " ".join(line.strip() for line in buffer.getvalue().splitlines() if line.strip())
    report.add("OK" if code == 0 else "FAIL", "python", detail)


def expand_roots(roots):
    for root in roots:
        if any(ch in root for ch in "*?["):
            yield from sorted(path for path in glob.glob(root) if os.path.isdir(path))
        else:
            yield root


def check_active(report, adapter, root, runtime):
    try:
        state_dir = mission_guard.state_dir_of(root, adapter)
    except mission_guard.GuardOff as e:
        report.add("FAIL", "active", f"{root}: {e}")
        return
    active = mission_guard.read_active(state_dir)
    if active.corrupt:
        report.add("WARN", "active", f"{root}: ACTIVE 손상 ({mission_guard.active_path_of(state_dir)})")
        return
    if not active.id:
        return
    limit = mission_guard.stale_hours_of(adapter)
    state = mission_guard.staleness(state_dir, active, limit) if limit else None
    if state and state.stale:
        command = f'python "{MISSION_STATE[runtime]}" --project "{root}" --mission {active.id} close-stale'
        report.add(
            "WARN", "active",
            f"{root}: {active.id} {mission_guard.stale_text(state)}. 닫을지는 사용자가 정함 — 미리보기: {command} "
            "(닫기는 사용자 승인 뒤 --force-close-stale)",
        )
    else:
        report.add("OK", "active", f"{root}: {active.id} 진행 중")


def check_adapter(report, name, adapter_path, roots, runtime):
    if not os.path.isfile(adapter_path):
        report.add("FAIL", "adapter", f"{name}: adapter.json 없음 ({adapter_path})")
        return
    problems = []
    try:
        adapter = mission_guard.load_adapter(adapter_path, problems, runtime=runtime)
    except (mission_guard.GuardOff, OSError, ValueError) as e:
        report.add("FAIL", "adapter", f"{name}: 읽기 실패 ({e})")
        return
    for problem in sorted(set(problems)):
        report.add("WARN", "adapter", f"{name}: {problem}")
    if not problems:
        report.add("OK", "adapter", f"{name}: {adapter_path}")
    cases = os.path.join(os.path.dirname(adapter_path), "guard-cases.json")
    if os.path.isfile(cases):
        try:
            mismatches, errors, guard_problems = check_guard.evaluate(adapter_path, cases, runtime)
        except (mission_guard.GuardOff, OSError, ValueError) as e:
            report.add("FAIL", "guard-cases", f"{name}: 읽기 실패 ({e})")
        else:
            level = "FAIL" if mismatches or errors else ("WARN" if guard_problems else "OK")
            report.add(level, "guard-cases", f"{name}: mismatches={len(mismatches)} errors={len(errors)} problems={len(guard_problems)}")
    else:
        report.add("WARN", "guard-cases", f"{name}: guard-cases.json 없음 — 가드 규칙 회귀를 못 봄")
    unity = ((adapter.get("vars") or {}).get("unityExe"))
    if isinstance(unity, str) and unity:
        report.add("OK" if os.path.isfile(unity) else "FAIL", "unity", f"{name}: vars.unityExe {'있음' if os.path.isfile(unity) else '없음'} ({unity})")
    names = (adapter.get("slots") or {}).get("names") or []
    if len(names) > 3:
        report.add("OK", "slots", f"{name}: slots.names {len(names)}개(새 어댑터 권장 2~3개, 있는 슬롯은 그대로 둠)")
    for root in expand_roots(roots):
        if os.path.isdir(root):
            check_active(report, adapter, root, runtime)


def check_registry(report, runtime):
    registry = mission_guard.resolve_registry(runtime)
    if not registry.path:
        report.add("FAIL", "registry", f"{mission_guard.REGISTRY_ENV} 미설정(출처 없음)")
        return
    if not os.path.isdir(registry.path):
        report.add("FAIL", "registry", f"폴더 없음: {registry.path} (출처 {registry.source})")
        return
    try:
        entries = mission_guard.load_registry(registry.path)
    except mission_guard.GuardOff as e:
        report.add("FAIL", "registry", str(e))
        return
    report.add("OK" if entries else "WARN", "registry", f"{registry.path} (출처 {registry.source}) 항목 {len(entries)}개")
    for entry in entries:
        check_adapter(report, entry["adapter"], os.path.join(registry.path, entry["adapter"], "adapter.json"), entry["roots"], runtime)


def repo_versions():
    versions = {}
    for key, path, pick in (
        ("claude", os.path.join(PLUGIN, ".claude-plugin", "plugin.json"), lambda j: j.get("version")),
        ("codex", os.path.join(PLUGIN, ".codex-plugin", "plugin.json"), lambda j: j.get("version")),
        ("marketplace", os.path.join(PLUGIN, "..", "..", ".claude-plugin", "marketplace.json"),
         lambda j: next((p.get("version") for p in j.get("plugins", []) if p.get("name") == "han"), None)),
    ):
        try:
            versions[key] = pick(read_json(path))
        except (OSError, ValueError):
            versions[key] = None
    return versions


def installed_versions():
    home = os.path.expanduser("~")
    claude = None
    try:
        info = read_json(os.path.join(home, ".claude", "plugins", "installed_plugins.json"))
        entries = (info.get("plugins") or {}).get("han@han-orchestrator")
        entry = entries[0] if isinstance(entries, list) and entries else entries
        claude = (entry or {}).get("version")
    except (OSError, ValueError, AttributeError):
        pass
    codex_home = os.environ.get("CODEX_HOME") or os.path.join(home, ".codex")
    cache = os.path.join(codex_home, "plugins", "cache", "han-orchestrator-codex", "han-codex")
    codex = sorted(os.listdir(cache), key=version_key) if os.path.isdir(cache) else []
    return claude, codex


def version_key(text):
    return tuple(int(part) if part.isdigit() else 0 for part in text.split("."))


def check_versions(report):
    repo = repo_versions()
    claude, codex = installed_versions()
    if repo["marketplace"] and repo["claude"] and repo["marketplace"] != repo["claude"]:
        report.add("FAIL", "version", f"plugin.json {repo['claude']} ≠ marketplace.json {repo['marketplace']}")
    if repo["claude"]:
        level = "OK" if claude == repo["claude"] else "WARN"
        report.add(level, "version", f"Claude han 저장소 {repo['claude']} / 설치 {claude or '없음'}")
    if repo["codex"]:
        latest = codex[-1] if codex else None
        level = "OK" if latest == repo["codex"] else "WARN"
        report.add(level, "version", f"Codex han-codex 저장소 {repo['codex']} / 설치 캐시 {', '.join(codex) or '없음'}")


def run(runtime):
    report = Report()
    check_python(report)
    try:
        check_registry(report, runtime)
    except Exception as e:  # 점검 도구는 끝까지 출력
        report.add("FAIL", "registry", f"내부 오류 {type(e).__name__}: {e}")
    check_versions(report)
    return report


def main(argv=None, runtime="claude"):
    with contextlib.suppress(AttributeError):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="han doctor (읽기 전용)")
    parser.add_argument("--runtime", choices=("claude", "codex"), default=runtime)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = run(args.runtime)
    if args.json:
        print(json.dumps({"ok": not report.failed, "rows": report.rows}, ensure_ascii=False, indent=1))
    else:
        for row in report.rows:
            print(f"{row['level']} {row['area']}: {row['text']}")
        print("결과: " + ("FAIL 있음" if report.failed else "문제 없음(WARN 은 확인만)"))
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
