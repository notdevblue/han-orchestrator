"""어댑터 guard.deny 규칙을 표본 명령에 돌려 기대(deny|allow)와 맞는지 확인함.

사용: python check_guard.py [--runtime claude|codex|grok] <adapter.json|registry 어댑터 이름> [cases.json|cases.tsv]
  .json  [{"expect": "deny|allow", "command": "...", "tool": "Bash|PowerShell"}]
         tool 은 생략하면 Bash. 명령에 줄바꿈 가능
  .tsv   기대<TAB>명령 한 줄씩(항상 Bash, 줄바꿈 해석 없음), # 로 시작하는 줄은 주석
  cases 생략 시 어댑터 옆의 guard-cases.json
  종료 코드: 0 통과, 1 어긋남·오류, 2 사용법, 3 어댑터·표본 파일을 못 읽음
  python check_guard.py --doctor   이 PC 의 python 해석 경로와 WindowsApps 스텁 여부 출력
훅과 같은 읽기(mission_guard.load_adapter, 옆의 local.json 깊은 병합 포함)와 판정 함수
(mission_guard.judge_command)를 쓰고, 세션·ACTIVE 는 무시하고 규칙 매칭만 봄.
마커(han-mission ...) 단독 호출은 allow, 마커가 다른 명령과 섞이면 deny.
"""
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "hooks"))

import mission_guard  # noqa: E402

EXPECTATIONS = ("deny", "allow")
TOOLS = mission_guard.GUARDED_TOOLS
DEFAULT_TOOL = "Bash"


def load_json_cases(path):
    cases, errors = [], []
    with open(path, encoding="utf-8-sig") as f:
        data = json.load(f)
    if not isinstance(data, list):
        return cases, ["최상위가 배열이 아님"]
    for index, item in enumerate(data, 1):
        item = item if isinstance(item, dict) else {}
        expect = str(item.get("expect", "")).strip().lower()
        command = item.get("command")
        tool = item.get("tool", DEFAULT_TOOL)
        if expect not in EXPECTATIONS or not isinstance(command, str) or tool not in TOOLS:
            errors.append(f"항목 {index}: expect(deny|allow)/command/tool(Bash|PowerShell) 형식 오류")
            continue
        cases.append((expect, command, tool))
    return cases, errors


def load_tsv_cases(path):
    cases, errors = [], []
    with open(path, encoding="utf-8-sig") as f:
        for number, line in enumerate(f, 1):
            line = line.rstrip("\r\n")
            if not line.strip() or line.startswith("#"):
                continue
            expect, tab, command = line.partition("\t")
            expect = expect.strip().lower()
            if not tab or expect not in EXPECTATIONS:
                errors.append(f"{number}번째 줄: '기대<TAB>명령' 형식 아님")
                continue
            cases.append((expect, command, DEFAULT_TOOL))
    return cases, errors


def load_cases(path):
    if path.lower().endswith(".json"):
        return load_json_cases(path)
    return load_tsv_cases(path)


def resolve_adapter(argument, runtime="claude"):
    if os.path.isfile(argument):
        return argument
    directory = mission_guard.registry_dir(runtime)
    if not directory:
        raise mission_guard.AdapterNotFound(
            f"어댑터 파일이 아니고 레지스트리 경로도 없음: {argument} "
            f"({mission_guard.REGISTRY_ENV} 를 정하거나 어댑터 파일 경로를 줘야 함)"
        )
    registered = mission_guard.registry_adapter_path(argument, runtime)
    if not os.path.isfile(registered):
        raise mission_guard.AdapterNotFound(f"어댑터를 찾지 못함: {argument} (레지스트리에도 없음: {registered})")
    return registered


def judge(adapter, command, tool, problems):
    verdict = mission_guard.judge_command(adapter, command, tool, problems)
    return ("deny" if verdict.denied else "allow"), verdict.reason


def evaluate(adapter_path, cases_path, runtime="claude"):
    problems, mismatches = [], []
    adapter = mission_guard.load_adapter(resolve_adapter(adapter_path, runtime), problems, runtime=runtime)
    cases, errors = load_cases(cases_path)
    for expect, command, tool in cases:
        actual, reason = judge(adapter, command, tool, problems)
        if actual != expect:
            mismatches.append((expect, actual, command, reason, tool))
    return mismatches, errors, sorted(set(problems))


def doctor():
    path = shutil.which("python")
    if not path:
        print("python=NOT FOUND (PATH 에 python 없음 — 훅의 command 'python' 이 실행 안 됨)")
        return 1
    try:
        result = subprocess.run([path, "--version"], capture_output=True, timeout=15)
        output = (result.stdout + result.stderr).decode("utf-8", errors="replace").strip()
        returncode = result.returncode
    except (OSError, subprocess.SubprocessError) as e:
        output, returncode = f"실행 실패: {e}", -1
    in_windowsapps = "windowsapps" in path.lower()
    runs = returncode == 0 and output.lower().startswith("python ")
    print(f"python={path}")
    print(f"windowsapps_path={in_windowsapps}")
    print(f"version_output={output!r} exit={returncode}")
    print(f"stub={in_windowsapps and not runs}")
    return 0 if runs else 1


RUNTIMES = ("claude", "codex", "grok")


def consume_runtime(argv, runtime):
    if not argv or argv[0] != "--runtime":
        return argv, runtime, None
    if len(argv) < 2 or argv[1] not in RUNTIMES:
        return None, runtime, "ERROR --runtime 은 claude, codex, grok 중 하나"
    return argv[2:], argv[1], None


def main(runtime="claude"):
    sys.stdout.reconfigure(encoding="utf-8")
    args, runtime, error = consume_runtime(sys.argv[1:], runtime)
    if error:
        print(error)
        return 2
    if "--doctor" in args:
        return doctor()
    if len(args) not in (1, 2):
        print(__doc__)
        return 2
    try:
        adapter_path = resolve_adapter(args[0], runtime)
        cases_path = args[1] if len(args) == 2 else os.path.join(os.path.dirname(adapter_path), "guard-cases.json")
        mismatches, errors, problems = evaluate(adapter_path, cases_path, runtime)
    except (mission_guard.AdapterNotFound, mission_guard.GuardOff, OSError, ValueError) as e:
        print(f"ERROR 읽기 실패: {e}")
        return 3
    for expect, actual, command, reason, tool in mismatches:
        print(f"MISMATCH expected={expect} actual={actual} tool={tool} :: {command!r} :: {reason}")
    for message in errors:
        print(f"ERROR {message}")
    for message in problems:
        print(f"PROBLEM {message}")
    print(f"mismatches={len(mismatches)} errors={len(errors)}")
    return 1 if mismatches or errors or problems else 0


if __name__ == "__main__":
    sys.exit(main())
