import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "scripts" / "han_state.py"
sys.path.insert(0, str(SCRIPT.parent))
sys.path.insert(0, str(SCRIPT.parents[1] / "hooks"))

import han_state  # noqa: E402
import mission_guard  # noqa: E402


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def adapter_body(project="base"):
    return {
        "schema": 1,
        "project": project,
        "paths": {"stateDir": "_My/missions"},
        "runtimes": {"codex": {"project": "codex-only"}, "grok": {"project": "grok-only"}},
    }


def plant(folder, kind, project="base"):
    path = folder / kind / "orchestrator" / "adapter.json"
    write_json(path, adapter_body(project))
    return path


def isolate_registry(monkeypatch, tmp_path):
    monkeypatch.delenv("HAN_ADAPTERS_DIR", raising=False)
    monkeypatch.delenv("HAN_REPO", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_ADAPTERS_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    home = tmp_path / "codex-home"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setattr(mission_guard, "in_place_adapters_dir", lambda: str(tmp_path / "missing-adapters"))


def test_grok_walk_prefers_nearest_then_grok_claude_codex(tmp_path, monkeypatch):
    isolate_registry(monkeypatch, tmp_path)
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    plant(parent, ".grok", "parent-grok")
    plant(parent, ".codex", "parent-codex")
    claude = plant(child, ".claude", "child-claude")
    plant(child, ".codex", "child-codex")
    found = mission_guard.find_adapter({"cwd": str(child), "han_runtime": "grok"})
    assert Path(found.adapter_path) == claude
    grok = plant(child, ".grok", "child-grok")
    found = mission_guard.find_adapter({"cwd": str(child), "han_runtime": "grok"})
    assert Path(found.adapter_path) == grok


def test_claude_and_codex_ignore_grok_adapter(tmp_path, monkeypatch):
    isolate_registry(monkeypatch, tmp_path)
    project = tmp_path / "game"
    project.mkdir()
    plant(project, ".grok")
    empty = tmp_path / "empty-registry"
    empty.mkdir()
    monkeypatch.setenv("HAN_ADAPTERS_DIR", str(empty))
    for runtime in ("claude", "codex"):
        with pytest.raises(mission_guard.AdapterNotFound):
            mission_guard.find_adapter({"cwd": str(project), "han_runtime": runtime})
    codex = plant(project, ".codex", "codex")
    found = mission_guard.find_adapter({"cwd": str(project), "han_runtime": "codex"})
    assert Path(found.adapter_path) == codex
    assert ".grok" not in found.adapter_path


def test_grok_falls_back_to_codex_adapter_and_ignores_claude_project_dir(tmp_path, monkeypatch):
    isolate_registry(monkeypatch, tmp_path)
    project = tmp_path / "game"
    other = tmp_path / "other"
    project.mkdir()
    other.mkdir()
    codex = plant(project, ".codex", "from-codex")
    plant(other, ".grok", "from-env")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(other))
    found = mission_guard.find_adapter({"cwd": str(project), "han_runtime": "grok"})
    assert Path(found.adapter_path) == codex


def test_grok_registry_order_skips_claude_and_codex_config(tmp_path, monkeypatch):
    isolate_registry(monkeypatch, tmp_path)
    repo = tmp_path / "han-repo"
    repo_adapters = repo / "adapters"
    (repo_adapters / "game").mkdir(parents=True)
    env_adapters = tmp_path / "env-adapters"
    env_adapters.mkdir()
    plugin_adapters = tmp_path / "plugin-adapters"
    plugin_adapters.mkdir()
    config_adapters = tmp_path / "config-adapters"
    config_adapters.mkdir()
    codex_home = Path(os.environ["CODEX_HOME"])
    (codex_home / "config.toml").write_text(
        f'[shell_environment_policy.set]\nHAN_ADAPTERS_DIR = "{config_adapters.as_posix()}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("HAN_REPO", str(repo))
    assert mission_guard.resolve_registry("grok") == mission_guard.Registry(str(repo_adapters), "hanRepo")
    assert mission_guard.resolve_registry("claude") == mission_guard.Registry("", "none")
    assert mission_guard.resolve_registry("codex") == mission_guard.Registry(str(config_adapters), "codexConfig")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_ADAPTERS_DIR", str(plugin_adapters))
    assert mission_guard.resolve_registry("grok") == mission_guard.Registry(str(repo_adapters), "hanRepo")
    assert mission_guard.resolve_registry("claude") == mission_guard.Registry(str(plugin_adapters), "userConfig")
    monkeypatch.setenv("HAN_ADAPTERS_DIR", str(env_adapters))
    assert mission_guard.resolve_registry("grok") == mission_guard.Registry(str(env_adapters), "env")
    monkeypatch.delenv("HAN_ADAPTERS_DIR")
    monkeypatch.setenv("HAN_REPO", str(tmp_path / "missing-repo"))
    with pytest.raises(mission_guard.GuardOff, match="HAN_REPO/adapters 없음"):
        mission_guard.resolve_registry("grok")


def test_grok_registry_matches_projects_json(tmp_path, monkeypatch):
    isolate_registry(monkeypatch, tmp_path)
    project = tmp_path / "work"
    project.mkdir()
    registry = tmp_path / "adapters"
    write_json(registry / "game" / "adapter.json", adapter_body("registered"))
    write_json(registry / "projects.json", [{"adapter": "game", "roots": [str(project)]}])
    monkeypatch.setenv("HAN_ADAPTERS_DIR", str(registry))
    found = mission_guard.find_adapter({"cwd": str(project / "nested"), "han_runtime": "grok"})
    assert found.project == str(project)
    assert found.adapter_path == str(registry / "game" / "adapter.json")


def test_snapshot_reads_state_without_runtime_overlay_or_writes(tmp_path):
    project = tmp_path / "game"
    plant(project, ".grok")
    write_json(
        project / ".grok" / "orchestrator" / "local.json",
        {"project": "from-local"},
    )
    state = project / "_My" / "missions"
    mission = state / "20261026-demo"
    mission.mkdir(parents=True)
    (state / "ACTIVE").write_text(
        json.dumps({"id": "20261026-demo", "session": "sess", "openedAt": "2026-10-26T00:00:00+09:00"}),
        encoding="utf-8",
    )
    (mission / "mission.md").write_text(
        "# 미션\n\n상태: 진행\n현재 단계: impl\n확인 A: 승인\n확인 B: 대기\n확인 C: 대기\n\n## 목표\n\n버튼 입력을 서버로 보낸다\n",
        encoding="utf-8",
    )
    closed = state / "20261001-old"
    closed.mkdir()
    (closed / "mission.md").write_text("상태: 닫힘\n현재 단계: review\n\n## 목표\n\n지난 일\n", encoding="utf-8")
    stale = state / "20261026-stale"
    stale.mkdir()
    (stale / "mission.md").write_text("상태: 진행\n현재 단계: content\n\n## 목표\n\n끊긴 조사\n", encoding="utf-8")
    slot = tmp_path / "slots" / "slot1"
    slot_mission = slot / "_My" / "missions" / "20261026-slot"
    slot_mission.mkdir(parents=True)
    (slot / "_My" / "missions" / "ACTIVE").write_text(
        json.dumps({"id": "20261026-slot", "session": "slot-sess", "openedAt": "2026-10-26T01:00:00+09:00"}),
        encoding="utf-8",
    )
    (slot_mission / "mission.md").write_text("상태: 진행\n현재 단계: poc\n\n## 목표\n\n슬롯 일\n", encoding="utf-8")
    idle = tmp_path / "slots" / "slot2"
    idle.mkdir(parents=True)
    (state / "slots.tsv").write_text(
        "name\tpath\tclient\tcreated\tsynced\tstate\n"
        f"slot1\t{slot}\tclient_slot1\t2026-10-26T00:00:00+09:00\t2026-10-26T00:00:00+09:00\tready\n"
        f"slot2\t{idle}\tclient_slot2\t2026-10-26T00:00:00+09:00\t-\tready\n",
        encoding="utf-8",
    )
    before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    lines = han_state.build_snapshot(str(project / "Unity")).lines
    text = "\n".join(lines)
    after = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    assert before == after
    assert "프로젝트: from-local" in text
    assert "codex-only" not in text and "grok-only" not in text
    assert "20261026-demo [ACTIVE] 상태=진행 단계=impl 확인 A=승인 확인 B=대기 확인 C=대기 목표=버튼 입력을 서버로 보낸다" in text
    assert "20261026-stale [진행, ACTIVE 아님]" in text
    assert "20261001-old" not in text
    assert f"slot1 state=ready" in text and "active=20261026-slot 상태=진행 단계=poc" in text
    assert "비어 있는 ready 슬롯: slot2" in text
    assert "지난 일" not in text


def test_brief_mentions_existing_work_and_does_not_write(tmp_path):
    project = tmp_path / "game"
    plant(project, ".claude", "shared")
    state = project / "_My" / "missions"
    state.mkdir(parents=True)
    (state / "ACTIVE").write_text(json.dumps({"id": "20261026-demo", "session": "sess", "openedAt": ""}), encoding="utf-8")
    before = sorted(path.relative_to(project).as_posix() for path in project.rglob("*"))
    text = "\n".join(han_state.build_brief(str(project), "  버튼\n입력  ", "codex"))
    after = sorted(path.relative_to(project).as_posix() for path in project.rglob("*"))
    assert before == after
    assert "사람이 그 세션에 붙일 문장: $han-codex:han-mission 버튼 입력" in text
    assert "메인 ACTIVE가 이미 있다 (20261026-demo)" in text
    assert "Grok은 이 문장을 실행하지 않는다." in text
    claude = "\n".join(han_state.build_brief(str(project), "조사", "claude"))
    assert "사람이 그 세션에 붙일 문장: /han:mission 조사" in claude


def test_grok_brief_targets_main_or_registered_slot_and_does_not_write(tmp_path):
    project = tmp_path / "game"
    plant(project, ".grok", "shared")
    state = project / "_My" / "missions"
    state.mkdir(parents=True)
    slot = tmp_path / "slots" / "slot2"
    slot.mkdir(parents=True)
    busy = tmp_path / "slots" / "slot1"
    busy_mission = busy / "_My" / "missions" / "20261026-slot"
    busy_mission.mkdir(parents=True)
    (busy / "_My" / "missions" / "ACTIVE").write_text(
        json.dumps({"id": "20261026-slot", "session": "slot-sess", "openedAt": ""}),
        encoding="utf-8",
    )
    (busy_mission / "mission.md").write_text("상태: 진행\n현재 단계: poc\n\n## 목표\n\n슬롯 일\n", encoding="utf-8")
    (state / "slots.tsv").write_text(
        "name\tpath\tclient\tcreated\tsynced\tstate\n"
        f"slot1\t{busy}\tclient_slot1\t2026-10-26T00:00:00+09:00\t2026-10-26T00:00:00+09:00\tready\n"
        f"slot2\t{slot}\tclient_slot2\t2026-10-26T00:00:00+09:00\t-\tready\n",
        encoding="utf-8",
    )
    before = sorted(path.relative_to(project).as_posix() for path in project.rglob("*"))
    main_text = "\n".join(han_state.build_brief(str(project), "  버튼\n입력  ", "grok"))
    slot_text = "\n".join(han_state.build_brief(str(project), "슬롯 작업", "grok", "slot2"))
    busy_text = "\n".join(han_state.build_brief(str(project), "겹침", "grok", "slot1"))
    after = sorted(path.relative_to(project).as_posix() for path in project.rglob("*"))
    assert before == after
    assert "실행 대상: main (메인)" in main_text
    assert "SendToAgent:" in main_text and "대상: main" in main_text
    assert f"프로젝트 경로: {project}" in main_text
    assert "목표: 버튼 입력" in main_text
    assert "쓰기 확인:" in main_text and "실행 규칙:" in main_text
    assert "/han:mission" not in main_text and "$han-codex:han-mission" not in main_text
    assert "실행 대상: slot2 (slot2)" in slot_text
    assert f"프로젝트 경로: {slot}" in slot_text
    assert "slot1 은 이미 미션이 붙어 있다" in busy_text
    with pytest.raises(han_state.Usage, match="등록되지 않은 슬롯: slot9"):
        han_state.build_brief(str(project), "x", "grok", "slot9")


def test_grok_brief_uses_local_main_and_configured_slot_names(tmp_path):
    main = tmp_path / "main"
    slot_root = tmp_path / "workspaces"
    slot = slot_root / "worker-Delta"
    slot.mkdir(parents=True)
    adapter_path = plant(slot, ".grok", "shared")
    write_json(
        adapter_path.parent / "local.json",
        {"vars": {"main": str(main)}, "slots": {"root": str(slot_root), "names": ["worker-Delta"]}},
    )
    state = main / "_My" / "missions"
    state.mkdir(parents=True)
    (state / "ACTIVE").write_text(
        json.dumps({"id": "20261026-main", "session": "main-session", "openedAt": ""}),
        encoding="utf-8",
    )
    main_text = "\n".join(han_state.build_brief(str(slot / "Editor"), "메인 작업", "grok", "main"))
    slot_text = "\n".join(han_state.build_brief(str(slot / "Editor"), "슬롯 작업", "grok"))
    explicit = "\n".join(han_state.build_brief(str(slot), "슬롯 작업", "grok", "worker-Delta"))
    assert "메인 ACTIVE: 20261026-main session=main-session" in main_text
    assert "메인 ACTIVE가 이미 있다 (20261026-main)" in main_text
    assert "대상: main" in main_text and f"프로젝트 경로: {main}" in main_text
    assert "대상: worker-Delta" in slot_text and f"프로젝트 경로: {slot}" in slot_text
    assert "대상: worker-Delta" in explicit and f"프로젝트 경로: {slot}" in explicit
    with pytest.raises(han_state.Usage, match="등록되지 않은 슬롯"):
        han_state.build_brief(str(slot), "슬롯 작업", "grok", "worker-delta")


def test_grok_brief_prefers_recorded_path_and_longest_containing_slot(tmp_path):
    project = tmp_path / "main"
    adapter_path = plant(project, ".grok", "shared")
    write_json(
        adapter_path.parent / "local.json",
        {"slots": {"root": str(tmp_path / "default-slots"), "names": ["review", "worker"]}},
    )
    state = project / "_My" / "missions"
    state.mkdir(parents=True)
    parent_slot = project / "workspaces" / "review"
    nested_slot = parent_slot / "worker"
    nested_slot.mkdir(parents=True)
    (state / "slots.tsv").write_text(
        "name\tpath\tstate\n"
        f"review\t{parent_slot}\tready\n"
        f"worker\t{nested_slot}\tready\n",
        encoding="utf-8",
    )
    inferred = "\n".join(han_state.build_brief(str(nested_slot / "Editor"), "작업", "grok"))
    explicit = "\n".join(han_state.build_brief(str(project), "작업", "grok", "worker"))
    outside = "\n".join(han_state.build_brief(str(parent_slot) + "-other", "작업", "grok"))
    assert "대상: worker" in inferred and f"프로젝트 경로: {nested_slot}" in inferred
    assert "대상: worker" in explicit and f"프로젝트 경로: {nested_slot}" in explicit
    assert "대상: main" in outside and f"프로젝트 경로: {project}" in outside


def test_grok_brief_rejects_slot_without_a_registered_path(tmp_path):
    project = tmp_path / "game"
    adapter_path = plant(project, ".grok")
    write_json(adapter_path.parent / "local.json", {"slots": {"names": ["worker"]}})
    with pytest.raises(han_state.Usage, match="등록되지 않은 슬롯: worker"):
        han_state.build_brief(str(project), "작업", "grok", "worker")
    main_text = "\n".join(han_state.build_brief(str(project), "작업", "grok"))
    assert "대상: main" in main_text and f"프로젝트 경로: {project}" in main_text


@pytest.mark.parametrize("table_exists", [False, True])
@pytest.mark.parametrize("corrupt", [False, True])
def test_snapshot_checks_configured_slot_active_without_table_or_row(tmp_path, table_exists, corrupt):
    main = tmp_path / "main"
    slot_root = tmp_path / "workspaces"
    slot = slot_root / "worker"
    slot_state = slot / "_My" / "missions"
    slot_state.mkdir(parents=True)
    (slot_root / "waiting").mkdir()
    adapter_path = plant(slot, ".grok", "shared")
    write_json(
        adapter_path.parent / "local.json",
        {"vars": {"main": str(main)}, "slots": {"root": str(slot_root), "names": ["worker", "waiting"]}},
    )
    if table_exists:
        state = main / "_My" / "missions"
        state.mkdir(parents=True)
        (state / "slots.tsv").write_text(
            f"name\tpath\tstate\nlisted\t{tmp_path / 'missing'}\tpending\n",
            encoding="utf-8",
        )
    active_text = "{invalid" if corrupt else json.dumps({"id": "20261026-busy", "session": "slot-session", "openedAt": ""})
    (slot_state / "ACTIVE").write_text(active_text, encoding="utf-8")
    snapshot = han_state.build_snapshot(str(slot / "Editor"))
    brief = "\n".join(han_state.build_brief(str(slot / "Editor"), "새 작업", "grok"))
    assert f"worker state=미확인 synced=- path={slot}" in brief
    assert "waiting state=미확인" in brief
    assert snapshot.idle_slots == ()
    assert "비어 있는 ready 슬롯: 없음" in brief
    assert "대상: worker" in brief
    if corrupt:
        assert "active=손상" in brief
        assert "경고: worker ACTIVE가 손상되어 있다" in brief
        assert snapshot.busy_slots == ()
    else:
        assert "active=20261026-busy" in brief
        assert "슬롯 ACTIVE가 있다 (worker:20261026-busy)" in brief
        assert "worker 은 이미 미션이 붙어 있다" in brief
        assert snapshot.busy_slots == ("worker:20261026-busy",)


def test_grok_skill_registration_names():
    skills = SCRIPT.parents[1] / "skills"
    registered = (
        "han-status", "han-doctor", "han-mission-brief", "han-run",
        "han-mission", "han-slots", "han-adapter-init", "han-distill",
    )
    plugin = SCRIPT.parents[2]
    manifest = json.loads((plugin / ".grok-plugin/plugin.json").read_text(encoding="utf-8"))
    paths = manifest["skills"]
    assert len(paths) == 8
    assert set(paths) == {f"./grok/skills/{name}" for name in registered}
    names = {Path(path).name for path in paths}
    assert "han-watch" not in names and "han-handoff" not in names
    for path in paths:
        name = Path(path).name
        text = (plugin / path / "SKILL.md").read_text(encoding="utf-8")
        assert f"name: {name}\n" in text
    run = (skills / "han-run" / "SKILL.md").read_text(encoding="utf-8")
    assert "CLAUDE.md" in run and ".claude/rules/" in run
    assert "mission_state.py" in run and "han_state.py" in run and "--runtime grok" in run
    assert "~/.grok/rules/" in run and "submit" in run
    mission = (skills / "han-mission" / "SKILL.md").read_text(encoding="utf-8")
    slot_skill = (skills / "han-slots" / "SKILL.md").read_text(encoding="utf-8")
    adapter = (skills / "han-adapter-init" / "SKILL.md").read_text(encoding="utf-8")
    distill = (skills / "han-distill" / "SKILL.md").read_text(encoding="utf-8")
    assert "--runtime grok" in mission and "mission_state.py" in mission
    assert "plugins/han/skills/mission/references/" in mission and "--force-tier" in mission
    assert "사람에게 묻는 법" not in mission and "ACTIVE" in mission
    assert "--runtime grok" in slot_skill and "slots.py" in slot_skill and "han-run" in slot_skill
    assert "ready" in slot_skill and "han-watch" not in slot_skill
    assert "check_guard.py" in adapter and "adapter-schema.md" in adapter and "pluginConfigs" in adapter
    assert "distill.py" in distill and "--transcript" in distill and "HAN_TRANSCRIPT" in distill
    watch = (skills / "han-watch" / "SKILL.md").read_text(encoding="utf-8")
    handoff = (skills / "han-handoff" / "SKILL.md").read_text(encoding="utf-8")
    assert "등록하는 스킬이 아니다" in watch and "han-status" in watch and "han-doctor" in watch
    assert "등록하는 스킬이 아니다" in handoff and "han-mission-brief" in handoff
    assert "실행 봇" in handoff and "SendToAgent" in handoff
    brief = (skills / "han-mission-brief" / "SKILL.md").read_text(encoding="utf-8")
    assert "SendToAgent" in brief and "실행 대상" in brief and "<slot-path>" in brief
    assert "--runtime claude" in brief and "--runtime codex" in brief
    status = (skills / "han-status" / "SKILL.md").read_text(encoding="utf-8")
    doctor = (skills / "han-doctor" / "SKILL.md").read_text(encoding="utf-8")
    assert "실행 봇" in status and "읽기" in status
    assert "Claude·Codex의 han이 맡는다" not in status
    assert "읽기 전용" in doctor and "han-mission-brief" in doctor
    readme = (SCRIPT.parents[1] / "README.md").read_text(encoding="utf-8")
    for name in registered:
        assert name in readme
    assert "han-watch" in readme and "등록하지 않는다" in readme
    assert "<project>" in readme and "<slot-path>" in readme and "실행 규칙" in readme


def test_cli_snapshot_and_missing_adapter(tmp_path):
    project = tmp_path / "game"
    plant(project, ".grok", "cli-game")
    env = os.environ.copy()
    env.pop("HAN_REPO", None)
    env.pop("CLAUDE_PROJECT_DIR", None)
    seen = subprocess.run(
        [sys.executable, str(SCRIPT), "snapshot", "--project", str(project)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
    )
    assert seen.returncode == 0, seen.stderr
    assert "프로젝트: cli-game" in seen.stdout
    assert "메인 ACTIVE: 없음" in seen.stdout
    empty = tmp_path / "empty"
    empty.mkdir()
    missing_project = tmp_path / "nowhere"
    missing_project.mkdir()
    env["HAN_ADAPTERS_DIR"] = str(empty)
    missing = subprocess.run(
        [sys.executable, str(SCRIPT), "snapshot", "--project", str(missing_project)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
    )
    assert missing.returncode == 2
    assert "projects.json 없음" in missing.stderr
    assert missing.stdout == ""
    brief = subprocess.run(
        [sys.executable, str(SCRIPT), "brief", "--project", str(project), "--goal", "버튼"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
    )
    assert brief.returncode == 0, brief.stderr
    assert "SendToAgent:" in brief.stdout
    assert "대상: main" in brief.stdout
    assert "/han:mission" not in brief.stdout
