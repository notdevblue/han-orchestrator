import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import mission_guard
import question_write_guard


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "sample-project"
    adapter_folder = root / ".claude" / "orchestrator"
    adapter_folder.mkdir(parents=True)
    adapter = {"schema": 1, "paths": {"stateDir": "state"}, "guard": {"coordinatorWrite": ["state/**"]}}
    (adapter_folder / "adapter.json").write_text(json.dumps(adapter), encoding="utf-8")
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.setenv("HAN_ADAPTERS_DIR", str(tmp_path / "no-registry"))
    return root


def outcome(project, tool="Write", **tool_input):
    return mission_guard.decide({"cwd": str(project), "tool_name": tool, "tool_input": tool_input})


@pytest.mark.parametrize("tool,key", [("Write", "file_path"), ("Edit", "path"), ("MultiEdit", "file_path"), ("NotebookEdit", "notebook_path")])
@pytest.mark.parametrize("target", ["state/mission/ask-1.md", "state/mission/confirm-A.md", "state/mission/questions/ask-1.json", "state/mission/questions/history/ask-1/r1.json"])
def test_structured_new_and_existing_question_files(project, tool, key, target):
    assert outcome(project, tool, **{key: target}).deny
    path = project / target
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("old", encoding="utf-8")
    assert outcome(project, tool, **{key: target}).deny


@pytest.mark.parametrize("extra", [{}, {"agent_id": "child"}, {"session_id": "different"}])
def test_protection_precedes_all_active_and_role_exceptions(project, extra):
    for active in ("", '{"id":"mission","session":"owner"}', "{broken"):
        state = project / "state"
        state.mkdir(exist_ok=True)
        if active:
            (state / "ACTIVE").write_text(active, encoding="utf-8")
        elif (state / "ACTIVE").exists():
            (state / "ACTIVE").unlink()
        payload = {"cwd": str(project), "tool_name": "Write", "tool_input": {"path": "state/mission/ask-1.md"}, **extra}
        assert mission_guard.decide(payload).deny


@pytest.mark.parametrize("target", ["state/mission/details/ask-1.md", "state/mission/details/history/notes.md", "state/mission/details/history/ask-1-original.md.txt", "state/mission/draft.json", "state/mission/reply.json", "state/mission/report.md", "src/app.py"])
def test_other_project_writes_allowed_without_active(project, target):
    assert not outcome(project, path=target).deny


@pytest.mark.parametrize("header", ["Add File", "Update File", "Delete File", "Move to"])
@pytest.mark.parametrize("key", ["command", "patch", "input", "patch_text"])
def test_all_patch_targets_and_aliases(project, header, key):
    patch = f"*** Begin Patch\n*** Update File: draft.md\n@@\n-old\n+new\n*** {header}: state/mission/ask-1.md\n*** End Patch"
    assert outcome(project, "apply_patch", **{key: patch}).deny


def test_patch_allows_draft_and_detail(project):
    assert not outcome(project, "apply_patch", command="*** Begin Patch\n*** Add File: state/mission/details/ask-1.md\n+text\n*** End Patch").deny


@pytest.mark.parametrize("target", ["state/mission/details", "state/mission/details/history"])
@pytest.mark.parametrize("source", ["confirm-A2.md", "questions/confirm-A2.json"])
def test_removing_parent_of_generated_history_is_protected(project, target, source):
    mission = project / "state/mission"
    history = mission / "details/history/confirm-A2.md"
    history.parent.mkdir(parents=True)
    history.write_text("# Generated example history", encoding="utf-8")
    corresponding = mission / source
    corresponding.parent.mkdir(parents=True, exist_ok=True)
    corresponding.write_text("Example", encoding="utf-8")
    assert outcome(project, "Bash", command=f'rm -rf "{target}"').deny


def test_handwritten_history_remains_editable(project):
    history = project / "state/mission/details/history/notes.md"
    history.parent.mkdir(parents=True)
    history.write_text("# Handwritten garden notes", encoding="utf-8")
    assert not outcome(project, "Bash", command='rm -rf "state/mission/details/history"').deny


@pytest.mark.parametrize("sources", [(), ("confirm-A2.md",), ("questions/confirm-A2.json",), ("confirm-A2.md", "questions/confirm-A2.json")])
@pytest.mark.parametrize("tool,key", [("Write", "file_path"), ("Edit", "path"), ("MultiEdit", "file_path"), ("NotebookEdit", "notebook_path")])
def test_generated_history_requires_matching_root_or_record(project, sources, tool, key):
    mission = project / "state/mission"
    mission.mkdir(parents=True)
    for name in sources:
        source = mission / name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("Example", encoding="utf-8")
    target = "state/mission/details/history/confirm-A2.md"
    assert bool(outcome(project, tool, **{key: target}).deny) == bool(sources)
    history = project / target
    history.parent.mkdir(parents=True, exist_ok=True)
    history.write_text("# Example history", encoding="utf-8")
    assert bool(outcome(project, tool, **{key: target}).deny) == bool(sources)


@pytest.mark.parametrize("source", ["confirm-A2.md", "questions/confirm-A2.json"])
@pytest.mark.parametrize("target", ["details/history/confirm-A2.md", "details", "details/history"])
def test_mixed_case_mission_preserves_filesystem_case_for_history_protection(project, monkeypatch, source, target):
    assert mission_guard.valid_mission_id("MyMission")
    mission = project / "state/MyMission"
    history = mission / "details/history/confirm-A2.md"
    history.parent.mkdir(parents=True)
    history.write_text("# Generated example history", encoding="utf-8")
    corresponding = mission / source
    corresponding.parent.mkdir(parents=True, exist_ok=True)
    corresponding.write_text("Example", encoding="utf-8")
    generated_history = question_write_guard.generated_history
    queried = []

    def check_filesystem_case(folder, name):
        assert Path(folder).name == "MyMission"
        queried.append((folder, name))
        return generated_history(folder, name)

    monkeypatch.setattr(question_write_guard, "generated_history", check_filesystem_case)
    if target == "details/history/confirm-A2.md":
        result = outcome(project, path=str(mission / target))
    else:
        result = outcome(project, "Bash", command=f'rm -rf "{mission / target}"')
    assert result.deny and queried


@pytest.mark.parametrize("name", ["confirm-A2.before-current-history.r11.md", "confirm-A2.details.before-current-history.r11.md", "notes.md"])
@pytest.mark.parametrize("target", ["state/mission/details", "state/mission/details/history"])
def test_original_history_names_are_editable_and_do_not_protect_parents(project, name, target):
    mission = project / "state/mission"
    history = mission / "details/history" / name
    history.parent.mkdir(parents=True)
    history.write_text("# Original example bytes", encoding="utf-8")
    (mission / "confirm-A2.md").write_text("# Current example", encoding="utf-8")
    assert not outcome(project, path=str(history)).deny
    assert not outcome(project, "Bash", command=f'rm -rf "{target}"').deny


def test_orphan_history_cannot_grant_automatic_document_sharing(project, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "skills/mission/scripts"))
    import mission_state

    mission = project / "state/mission"
    history = mission / "details/history/confirm-A2.md"
    history.parent.mkdir(parents=True)
    history.write_text("# Orphan example history", encoding="utf-8")
    assert not outcome(project, path=str(history)).deny
    for parent in (history.parent, history.parent.parent):
        assert not outcome(project, "Bash", command=f'rm -rf "{parent}"').deny
    assert not mission_state.auto_question_document(str(mission), "details/history/confirm-A2.md")
    assert mission_state.auto_question_documents(str(mission)) == set()
    assert outcome(project, path=str(mission / "confirm-A2.md")).deny
    assert outcome(project, path=str(mission / "questions/confirm-A2.json")).deny


def test_raw_patch_string_and_malformed_input(project):
    payload = {"cwd": str(project), "tool_name": "apply_patch", "tool_input": "*** Begin Patch\n*** Delete File: state/mission/ask-1.md\n*** End Patch"}
    assert mission_guard.decide(payload).deny
    payload["tool_input"] = []
    assert not mission_guard.decide(payload).deny


@pytest.mark.parametrize("target", ["state/mission/../mission/ASK-1.MD", "STATE/mission/ask-1.md"])
def test_dot_and_case_aliases(project, target):
    assert outcome(project, path=target).deny


def test_absolute_target_finds_adapter_from_elsewhere(project, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "elsewhere"))
    assert outcome(tmp_path, path=str(project / "state" / "mission" / "ask-1.md")).deny


def test_registry_adapter_and_runtime_local_state_override(project, tmp_path, monkeypatch):
    adapter_folder = project / ".claude" / "orchestrator"
    original = json.loads((adapter_folder / "adapter.json").read_text(encoding="utf-8"))
    (adapter_folder / "adapter.json").unlink()
    registry = tmp_path / "registry"
    (registry / "sample").mkdir(parents=True)
    (registry / "projects.json").write_text(json.dumps([{"adapter": "sample", "roots": [str(project)]}]), encoding="utf-8")
    (registry / "sample" / "adapter.json").write_text(json.dumps(original), encoding="utf-8")
    (registry / "sample" / "local.json").write_text(json.dumps({"runtimes": {"codex": {"paths": {"stateDir": "records"}}}}), encoding="utf-8")
    monkeypatch.setenv("HAN_ADAPTERS_DIR", str(registry))
    payload = {"han_runtime": "codex", "cwd": str(tmp_path), "tool_name": "Write", "tool_input": {"path": str(project / "records" / "mission" / "ask-1.md")}}
    assert mission_guard.decide(payload).deny


def test_tool_cwd_resolved_once(project, tmp_path):
    payload = {"cwd": str(tmp_path), "tool_name": "Write", "tool_input": {"cwd": "sample-project", "path": "state/mission/ask-1.md"}}
    assert mission_guard.decide(payload).deny


def test_codex_main_resolves_relative_tool_cwd_once(project):
    payload = {"cwd": str(project), "tool_name": "Bash", "tool_input": {"cwd": "state/mission", "command": "echo answer > ask-1.md"}}
    source = "import sys; sys.path.insert(0,sys.argv[1]); import mission_guard; mission_guard.main('codex')"
    result = subprocess.run([sys.executable, "-B", "-c", source, str(Path(__file__).parent)], input=json.dumps(payload).encode("utf-8"), capture_output=True, cwd=project)
    assert result.returncode == 0
    assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_symlink_target_finds_real_adapter(project, tmp_path):
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(project, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks unavailable")
    assert outcome(tmp_path, path=str(alias / "state" / "mission" / "ask-1.md")).deny


@pytest.mark.skipif(os.name != "nt", reason="Windows junction")
def test_junction_target_finds_real_adapter(project, tmp_path):
    alias = tmp_path / "junction"
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(project)], capture_output=True)
    assert result.returncode == 0, result.stderr
    assert outcome(tmp_path, path=str(alias / "state" / "mission" / "ask-1.md")).deny


def test_file_alias_outside_project_is_protected(project, tmp_path):
    source = project / "state" / "mission" / "ask-1.md"
    source.parent.mkdir(parents=True)
    source.write_text("question", encoding="utf-8")
    alias = tmp_path / "alias.md"
    try:
        alias.symlink_to(source)
    except OSError:
        pytest.skip("file symlinks unavailable")
    assert outcome(tmp_path, path=str(alias)).deny


@pytest.mark.parametrize("source", [
    "from pathlib import Path\np=Path('state/mission/ask-1.md'); s=p.read_text(); p.write_text(s.replace('pending','answered'))",
    "from pathlib import Path\np=Path('state')/'mission'/'questions'/'ask-1.json'; p.write_bytes(b'{}')",
    "from pathlib import Path\np=Path('state/mission/ask-1.md'); p.open('a').write('answer')",
    "open('state/mission/confirm-A.md', mode='w').write('answer')",
    "from pathlib import Path\np=Path('state/mission/ask-1.md'); q=p; q.unlink()",
    "import shutil\nshutil.copy('draft.json','state/mission/questions/ask-1.json')",
    "from pathlib import Path\np=Path('state/mission/ask-1.md'); p.rename('draft.md')",
    "import shutil\nshutil.rmtree('state/mission')",
])
def test_python_heredoc_regression_and_ordinary_mutations(project, source):
    question = project / "state" / "mission" / "ask-1.md"
    question.parent.mkdir(parents=True)
    question.write_text("question", encoding="utf-8")
    assert outcome(project, "Bash", command=f"python - <<'PY'\n{source}\nPY").deny


def test_python_c_and_powershell_herestring(project):
    assert outcome(project, "Bash", command='python -c "from pathlib import Path; p=Path(\'state/mission/ask-1.md\'); p.write_text(\'x\')"').deny
    assert outcome(project, "PowerShell", command="@'\nfrom pathlib import Path\nPath('state/mission/ask-1.md').write_text('x')\n'@ | python -").deny


@pytest.mark.parametrize("command", [
    "echo answer > state/mission/ask-1.md", "echo answer >> state/mission/ask-1.md",
    "Set-Content -LiteralPath state/mission/ask-1.md -Value answer",
    "Add-Content state/mission/ask-1.md answer", "echo answer | Out-File -FilePath state/mission/ask-1.md",
    "Set-Content -Encoding utf8 state/mission/ask-1.md answer",
    "cp draft.md state/mission/ask-1.md", "mv state/mission/ask-1.md draft.md",
    "Remove-Item -LiteralPath state/mission/questions -Recurse", "rm state/mission/questions/history/r1.json",
    "rm -rf state/mission", "Move-Item -LiteralPath state/mission -Destination archive",
    "$p='state/mission/ask-1.md'; Set-Content $p answer",
    "echo okay; echo answer > state/mission/ask-1.md",
])
def test_shell_write_targets(project, command):
    question = project / "state" / "mission" / "ask-1.md"
    question.parent.mkdir(parents=True)
    question.write_text("question", encoding="utf-8")
    assert outcome(project, "PowerShell", command=command).deny


def test_draft_directory_removal_allowed_without_question_descendants(project):
    drafts = project / "state" / "drafts"
    drafts.mkdir(parents=True)
    (drafts / "report.md").write_text("draft", encoding="utf-8")
    assert not outcome(project, "Bash", command="rm -rf state/drafts").deny
    assert not outcome(project, "PowerShell", command="Move-Item state/drafts archive").deny


@pytest.mark.parametrize("command", [
    "cd state/mission; Set-Content ask-1.md answer",
    "Set-Location -LiteralPath state/mission; Add-Content ask-1.md answer",
    "cd state/mission; echo answer > ask-1.md",
    "cd state/mission; cd ../..; cd state/mission; Set-Content ask-1.md answer",
    "cd state/mission; python - <<'PY'\nfrom pathlib import Path\nPath('ask-1.md').write_text('x')\nPY",
])
def test_literal_directory_changes_resolve_following_targets(project, command):
    assert outcome(project, "PowerShell", command=command).deny


@pytest.mark.parametrize("command", [
    "cat state/mission/ask-1.md", "Get-Content state/mission/questions/ask-1.json",
    "cat state/mission/questions/ask-1.json > state/mission/draft.json",
    "cp state/mission/ask-1.md state/mission/draft.md",
    "Copy-Item -LiteralPath state/mission/ask-1.md -Destination state/mission/draft.md",
    "echo '>' state/mission/ask-1.md",
    "cd state/mission/details; Set-Content ask-1.md draft",
    "cat > draft.py <<'PY'\nfrom pathlib import Path\nPath('state/mission/ask-1.md').write_text('example')\nPY",
    "@'\nfrom pathlib import Path\nPath('state/mission/ask-1.md').write_text('example')\n'@ | Set-Content draft.py",
    "python - <<'PY'\nfrom pathlib import Path\np=Path('state/mission/questions/ask-1.json'); s=p.read_text(); Path('draft.json').write_text(s)\nPY",
    "python - <<'PY'\nfrom pathlib import Path\np=Path('state/mission/ask-1.md'); s=p.read_text(); p=Path('draft.md'); p.write_text(s)\nPY",
])
def test_reads_and_read_to_external_draft_allowed(project, command):
    assert not outcome(project, "Bash", command=command).deny


def test_move_and_copy_to_mission_directory_uses_actual_destination_filename(project):
    mission = project / "state" / "mission"
    mission.mkdir(parents=True)
    (mission / "ask-1.md").write_text("question", encoding="utf-8")
    (project / "draft.json").write_text("{}", encoding="utf-8")
    for executable in ("Move-Item", "Copy-Item", "mv", "cp"):
        assert not outcome(project, "PowerShell", command=f"{executable} draft.json state/mission").deny
        assert outcome(project, "PowerShell", command=f"{executable} ask-1.md state/mission").deny


@pytest.mark.parametrize("action", ["publish --input draft.json", "answer ask-1.md --input reply.json", "restore --input draft.json --source history.json --expect-revision 1 --expect-sha256 abc", "render ask-1.md"])
def test_bundled_cli_and_appended_direct_write(project, action):
    script = Path(__file__).parent.parent / "skills" / "mission" / "scripts" / "mission_state.py"
    command = f'python "{script}" --project "{project}" --mission mission question {action}'
    assert not outcome(project, "Bash", command=command).deny
    assert outcome(project, "Bash", command=command + " > state/mission/ask-1.md").deny
    assert outcome(project, "Bash", command=command + "; echo answer > state/mission/ask-1.md").deny


def test_nonbundled_question_cli_writes_denied_but_reads_allowed(project):
    assert outcome(project, "Bash", command="python other/mission_state.py question answer ask-1.md --input reply.json").deny
    assert not outcome(project, "Bash", command="python other/mission_state.py question show ask-1.md").deny


def test_allow_once_does_not_override_question_protection(project):
    state = project / "state"
    folder = state / "mission"
    folder.mkdir(parents=True)
    (state / "ACTIVE").write_text('{"id":"mission","session":"owner"}', encoding="utf-8")
    command = "echo answer > state/mission/ask-1.md"
    (folder / "allow-once").write_text(command + "\n", encoding="utf-8")
    assert outcome(project, "Bash", command=command).deny
    assert (folder / "allow-once").read_text(encoding="utf-8") == command + "\n"


def test_coordinator_guard_still_runs(project):
    (project / "state").mkdir()
    (project / "state" / "ACTIVE").write_text('{"id":"mission","session":"owner"}', encoding="utf-8")
    payload = {"cwd": str(project), "session_id": "owner", "tool_name": "Write", "tool_input": {"file_path": "src/app.py"}}
    assert "roles.implement" in mission_guard.decide(payload).deny
    payload["tool_input"]["file_path"] = "state/mission/report.md"
    assert not mission_guard.decide(payload).deny


def test_unavailable_target_registry_does_not_skip_existing_shell_deny(project, tmp_path, monkeypatch):
    adapter_path = project / ".claude" / "orchestrator" / "adapter.json"
    adapter = json.loads(adapter_path.read_text(encoding="utf-8"))
    adapter["guard"]["deny"] = [{"pattern": "^p4 submit", "reason": "blocked submit"}]
    adapter_path.write_text(json.dumps(adapter), encoding="utf-8")
    (project / "state").mkdir()
    (project / "state" / "ACTIVE").write_text('{"id":"mission","session":"owner"}', encoding="utf-8")
    registry = tmp_path / "broken-registry"
    registry.mkdir()
    (registry / "projects.json").write_text("{broken", encoding="utf-8")
    monkeypatch.setenv("HAN_ADAPTERS_DIR", str(registry))
    result = outcome(project, "Bash", command=f'cp draft.md "{tmp_path / "external.md"}"; p4 submit')
    assert "blocked submit" in result.deny


def test_bad_registry_cannot_discard_valid_local_question_context(project, tmp_path, monkeypatch):
    registry = tmp_path / "broken-registry"
    registry.mkdir()
    (registry / "projects.json").write_text("{broken", encoding="utf-8")
    monkeypatch.setenv("HAN_ADAPTERS_DIR", str(registry))
    command = f'echo draft > "{tmp_path / "external.md"}"; echo answer > state/mission/ask-1.md'
    assert outcome(project, "Bash", command=command).deny
    patch = f"*** Begin Patch\n*** Add File: {tmp_path / 'external.md'}\n+draft\n*** Delete File: state/mission/ask-1.md\n*** End Patch"
    assert outcome(project, "apply_patch", command=patch).deny
