#!/usr/bin/env python3
"""p4 를 이 폴더의 작업 공간으로 고정해 실행함: p4 -c <작업 공간> <인자...>.

사용: python p4c.py [--project <폴더>] [--client <작업 공간>] [--which] [--dry-run] <p4 명령> [인자...]
  작업 공간은 슬롯 폴더면 slots.client 틀, 메인 폴더면 slots.mainClient, 아니면 .p4config 의 P4CLIENT
  --client 는 위에서 정한 작업 공간과 같을 때만 받음(다른 슬롯·메인에 쓰는 사고 방지)
  --which    정한 작업 공간 이름만 출력
  --dry-run  실행할 명령줄만 출력
  인자에 전역 -c 를 또 주면 거부. 하위 명령 뒤 -c <CL> 은 그대로 전달
  종료 코드: p4 의 종료 코드, 2 사용법·작업 공간 오류
미션 가드는 이 래퍼 호출을 `p4 -c <작업 공간> ...` 로 보고 같은 deny 규칙(submit 금지 등)을 적용함.
"""
import os
import subprocess
import sys

SKILLS_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.normpath(os.path.join(SKILLS_DIR, "..", "hooks")))
sys.path.insert(0, os.path.join(SKILLS_DIR, "slots", "scripts"))
import mission_guard  # noqa: E402
import slots  # noqa: E402

USAGE = 2


def fail(message):
    print(f"오류: {message}", file=sys.stderr)
    return USAGE


def parse(argv):
    options = {"project": "", "client": "", "which": False, "dry_run": False}
    while argv and argv[0].startswith("--"):
        flag = argv.pop(0)
        if flag == "--":
            break
        if flag in ("--project", "--client"):
            if not argv:
                raise slots.UsageError(f"{flag} 값 없음")
            options[flag[2:]] = argv.pop(0)
        elif flag in ("--which", "--dry-run"):
            options[flag[2:].replace("-", "_")] = True
        elif flag == "--help":
            options["help"] = True
        else:
            raise slots.UsageError(f"모르는 옵션: {flag}")
    return options, argv


def global_client_given(args):
    for arg in args:
        if not arg.startswith("-") or len(arg) < 2:
            return False
        if arg == "-c" or (arg.startswith("-c") and len(arg) > 2):
            return True
    return False


def p4_argv(adapter):
    configured = (adapter.get("slots") or {}).get("p4") or "p4"
    return list(configured) if isinstance(configured, list) else [str(configured)]


def main(argv=None, runtime="claude"):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        options, args = parse(argv)
        if options.get("help"):
            print(__doc__)
            return 0
        located = slots.locate(options["project"], runtime)
        adapter = slots.read_adapter(located.adapter_path, runtime)
    except slots.UsageError as e:
        return fail(e)
    expected = mission_guard.expected_p4_client(located.project, adapter)
    client = options["client"] or expected
    if not client:
        return fail(f"작업 공간을 정하지 못함: {located.project} (--client 를 주거나 .p4config 에 P4CLIENT)")
    if options["client"] and expected and options["client"].casefold() != expected.casefold():
        return fail(f"--client {options['client']} 가 이 폴더의 작업 공간({expected})과 다름")
    if options["which"]:
        print(client)
        return 0
    if not args:
        print(__doc__)
        return USAGE
    if global_client_given(args):
        return fail("전역 -c 는 래퍼가 붙임 — 빼고 다시 실행")
    command = [*p4_argv(adapter), "-c", client, *args]
    if options["dry_run"]:
        print(subprocess.list2cmdline(command))
        return 0
    try:
        return subprocess.run(command, cwd=located.project, env=slots.child_env(located.project)).returncode
    except OSError as e:
        return fail(f"p4 실행 실패: {e}")


if __name__ == "__main__":
    sys.exit(main())
