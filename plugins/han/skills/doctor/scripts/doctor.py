"""han 설치·어댑터 상태 점검(읽기 전용). 아무것도 고치거나 닫지 않는다.

사용: python doctor.py [--runtime claude|codex|grok] [--json]
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
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(PLUGIN, "hooks"))
sys.path.insert(0, os.path.join(PLUGIN, "skills", "adapter-init", "scripts"))

import check_guard  # noqa: E402
import mission_guard  # noqa: E402

SHARED_MISSION_STATE = os.path.join(PLUGIN, "skills", "mission", "scripts", "mission_state.py")
MISSION_STATE = {
    "claude": SHARED_MISSION_STATE,
    "codex": os.path.join(PLUGIN, "codex", "skills", "han-mission", "scripts", "mission_state.py"),
    "grok": SHARED_MISSION_STATE,
}
GROK_NATIVE_ROLES = ("researcher", "dev-small", "dev-medium", "verifier", "reviewer")


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
        runtime_flag = " --runtime grok" if runtime == "grok" else ""
        command = f'python "{MISSION_STATE[runtime]}"{runtime_flag} --project "{root}" --mission {active.id} close-stale'
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


def grok_han_hook(entry):
    source = entry.get("source")
    source_path = ""
    if isinstance(source, dict):
        source_path = source.get("path", "")
    paths = [str(entry.get("target") or ""), str(source_path)]
    for path in paths:
        normalized = "/" + "/".join(part for part in path.replace("\\", "/").lower().split("/") if part)
        if "/han/hooks/" in normalized or "/han/grok/" in normalized or "/hooks/han" in normalized:
            return True
    return False


def grok_path(path):
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def check_grok_agents(report, inspection):
    expected = {f"han:{role}": os.path.join(PLUGIN, "grok", "agents", f"{role}.md") for role in GROK_NATIVE_ROLES}
    agents = inspection.get("agents")
    discovered = {}
    if isinstance(agents, list):
        for entry in agents:
            if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                discovered.setdefault(entry["name"], []).append(entry)
    missing = sorted(set(expected) - set(discovered))
    wrong_source = []
    for name, path in expected.items():
        entries = discovered.get(name, [])
        if not entries:
            continue
        source = entries[0].get("source")
        source_path = ""
        if isinstance(source, dict):
            source_path = source.get("path", "")
        if len(entries) != 1 or not isinstance(source_path, str) or not source_path or grok_path(source_path) != grok_path(path):
            wrong_source.append(name)
    unexpected = sorted(name for name in discovered if name.startswith("han:") and name not in expected)
    level = "OK"
    detail = f"native 역할 검색 {len(expected) - len(missing) - len(wrong_source)}/{len(expected)}개"
    for label, names in (("없음", missing), ("원본 경로 불일치·중복", wrong_source), ("예상 밖 역할", unexpected)):
        if names:
            level = "WARN"
            detail += f" — {label}: {', '.join(names)}"
    detail += " — 검색 결과는 파견·모델·effort 실행 검증이 아님"
    report.add(level, "grok-agents", detail)
    plugins = inspection.get("plugins")
    selected = []
    if isinstance(plugins, list):
        selected = [entry for entry in plugins if isinstance(entry, dict) and entry.get("name") == "han"]
    native = []
    for entry in selected:
        path = entry.get("path", "")
        if isinstance(path, str) and path and grok_path(path) == grok_path(PLUGIN) and entry.get("enabled") is True:
            native.append(entry)
    level = "WARN"
    if len(selected) == 1 and len(native) == 1:
        level = "OK"
    report.add(level, "grok-native", f"선택된 han 경로·활성 상태 {level} — 기대 경로 {PLUGIN}")


def check_grok(report):
    executable = shutil.which("grok")
    if not executable:
        report.add("FAIL", "grok-cli", "PATH 에 Grok 실행 파일 없음")
        return
    try:
        version = subprocess.run([executable, "--version"], capture_output=True, text=True, encoding="utf-8", timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        report.add("FAIL", "grok-cli", f"{executable}: 버전 조회 실패 ({type(exc).__name__})")
        return
    if version.returncode:
        report.add("FAIL", "grok-cli", f"{executable}: 버전 조회 종료 코드 {version.returncode}")
        return
    report.add("OK", "grok-cli", f"{executable}: {version.stdout.strip()}")
    try:
        result = subprocess.run([executable, "inspect", "--json"], capture_output=True, text=True, encoding="utf-8", timeout=10)
        if result.returncode:
            report.add("WARN", "grok-registration", f"inspect 종료 코드 {result.returncode} — 등록 상태 확인 못 함")
            return
        inspection = json.loads(result.stdout)
        if not isinstance(inspection, dict):
            raise ValueError("inspect JSON object 필요")
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        report.add("WARN", "grok-registration", f"inspect 확인 실패 ({type(exc).__name__})")
        return
    skills = inspection.get("skills")
    discovered = set()
    if isinstance(skills, list):
        discovered = {entry.get("name") for entry in skills if isinstance(entry, dict) and isinstance(entry.get("name"), str)}
    skill_root = os.path.join(PLUGIN, "grok", "skills")
    expected = {name for name in os.listdir(skill_root) if os.path.isfile(os.path.join(skill_root, name, "SKILL.md"))}
    expected -= {"han-watch", "han-handoff"}
    missing = sorted(expected - discovered)
    level = "OK"
    detail = f"스킬 검색 {len(expected & discovered)}/{len(expected)}개"
    if missing:
        level = "WARN"
        detail += f" — 없음: {', '.join(missing)}"
    report.add(level, "grok-skills", detail)
    check_grok_agents(report, inspection)
    hooks = inspection.get("hooks")
    registered = []
    if isinstance(hooks, list):
        registered = [entry for entry in hooks if isinstance(entry, dict) and grok_han_hook(entry)]
    level = "WARN"
    if registered:
        level = "OK"
    report.add(level, "grok-hooks", f"han 훅 검색 {len(registered)}개 — 검색 결과는 로드·실행 검증이 아님")
    trusted = inspection.get("projectTrusted")
    level, trust_text = "WARN", "확인 못 함"
    if trusted is True:
        level, trust_text = "OK", "신뢰됨"
    elif trusted is False:
        trust_text = "신뢰 안 됨"
    report.add(level, "grok-trust", f"현재 폴더 {trust_text} — 훅별 신뢰·활성·실행 여부는 별도 확인 필요")
    report.add("WARN", "grok-capabilities", "슬롯 실행은 Grok 기본 압축 설정 사용 — Claude·Codex slots.autocompact 옵션은 전달하지 않음. 하위 에이전트 상태는 저장된 metadata·lifecycle 기준")


def run(runtime):
    report = Report()
    check_python(report)
    try:
        check_registry(report, runtime)
    except Exception as e:  # 점검 도구는 끝까지 출력
        report.add("FAIL", "registry", f"내부 오류 {type(e).__name__}: {e}")
    check_versions(report)
    if runtime == "grok":
        check_grok(report)
    return report


def main(argv=None, runtime="claude"):
    with contextlib.suppress(AttributeError):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="han doctor (읽기 전용)")
    parser.add_argument("--runtime", choices=("claude", "codex", "grok"), default=runtime)
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
