"""Claude 스킬 자료를 Codex 스킬로 복사하는 단일 원본 도구.

원본은 plugins/han/skills/<스킬>/ 이다. Codex 쪽 같은 경로 파일은 사본이라 직접 고치지 않는다.
스크립트는 이미 Codex 래퍼가 공유 코드를 import 하므로 대상이 아니다. SKILL.md 와 adapter-schema.md 는
런타임별 차이가 있어 대상이 아니다.

  python codex/sync_shared.py          # 다르면 목록을 출력하고 1
  python codex/sync_shared.py --write  # Claude 원본으로 덮어씀
"""
import argparse
import filecmp
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAIRS = (("skills/mission/references", "codex/skills/han-mission/references"),)
SKIP = {"adapter-schema.md", "verification-workflow.md", "usage-policy.md"}


def shared_files():
    for source_dir, target_dir in PAIRS:
        source_root = os.path.join(ROOT, *source_dir.split("/"))
        target_root = os.path.join(ROOT, *target_dir.split("/"))
        for folder, _, names in os.walk(source_root):
            for name in sorted(names):
                if name in SKIP:
                    continue
                source = os.path.join(folder, name)
                relative = os.path.relpath(source, source_root)
                target = os.path.join(target_root, relative)
                if os.path.exists(target):
                    yield source, target


def differing():
    return [(s, t) for s, t in shared_files() if not filecmp.cmp(s, t, shallow=False)]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    diffs = differing()
    for source, target in diffs:
        rel = os.path.relpath(target, ROOT).replace(os.sep, "/")
        if args.write:
            shutil.copyfile(source, target)
            print(f"복사: {rel}")
        else:
            print(f"다름: {rel}")
    if diffs and not args.write:
        print("Claude 원본과 다름. `python codex/sync_shared.py --write` 로 맞출 것")
        return 1
    print(f"공유 자료 {len(list(shared_files()))}개 일치")
    return 0


if __name__ == "__main__":
    sys.exit(main())
