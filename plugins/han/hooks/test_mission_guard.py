import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent
sys.path.insert(0, str(HOOKS_DIR))

import mission_guard  # noqa: E402

SCRIPT = HOOKS_DIR / "mission_guard.py"
CHECK_GUARD = HOOKS_DIR.parent / "skills" / "adapter-init" / "scripts" / "check_guard.py"
SAMPLE_ADAPTER = HOOKS_DIR / "testdata" / "sample-adapter.json"
REPO_ROOT = HOOKS_DIR.parent.parent.parent
EXAMPLE_ADAPTER = REPO_ROOT / "adapters" / "example" / "adapter.json"
EXAMPLE_CASES = REPO_ROOT / "adapters" / "example" / "guard-cases.json"
NO_REGISTRY = Path(tempfile.gettempdir()) / "han-guard-test-no-registry"
SUBMIT_RULE = {"pattern": r"^p4\s+submit\b", "reason": "submit 금지"}
SYNC_RULE = {"pattern": r"^p4\s+sync\b", "reason": "sync 금지"}
STATE = Path("_My") / "missions"
POWERSHELL = "PowerShell"
CLEARED_ENV = ("CLAUDE_PROJECT_DIR", "HAN_ADAPTERS_DIR", "CLAUDE_PLUGIN_OPTION_ADAPTERS_DIR", "HAN_GUARD_LOG")

MUST_DENY = [
    "p4 submit -c 123",
    "P4.EXE SUBMIT -c 1",
    "CLI=Tool/VcsWrapper/vcs-wrapper.exe\n$CLI pending submit 123",
    "p4 \\\n  submit -c 123",
    "p4 \\\r\n  submit -c 123",
    "p4 -c otherclient submit",
    '& "p4.exe" submit -c 1',
    "$P = 'p4'\n& $P submit",
    '$CLI = "Tool/VcsWrapper/vcs-wrapper.exe"; $CLI pending submit 1',
    "x=$(p4 submit -c 1)",
    "$out = p4 submit -c 1",
    "FOO=1 p4 edit a.cs",
    "echo it's here; p4 submit -c 1",
    "Tool/VcsWrapper/vcs-wrapper.exe pending submit 123",
    "Tool/QualityGate/qualitygate.bat submit 123 --check-only",
    "python Tool/cli.py submit 1",
    "p4 -x list.txt edit",
    "p4 -x list.txt add",
    "p4 edit Foo.cs && p4 edit -c 123 Bar.cs",
    "p4 revert -c 123 Foo.cs; p4 opened -a Foo.cs",
    "p4 revert -a //...",
    "Tool/VcsWrapper/vcs-wrapper.exe pending move-files 123 Foo.cs",
    "Tool/VcsWrapper/vcs-wrapper.exe workspace delete x",
    "vcs-wrapper git pull",
    "p4 unshelve -s 9 -f",
    "p4 unshelve -s 999 -f -c 123",
    "p4 edit -c default Foo.cs",
    "p4 obliterate -y //x/...",
    "p4 shelve -d -c 1",
    "p4 change -d 5",
    "p4 changelist -f 5",
    "p4 reopen -c 1 a.cs",
    "p4 sync",
    "p4 flush",
    "p4 clean",
    "p4 integrate a b",
    "p4 protect",
    "p4 lock a.cs",
    "p4 client -d ws",
    "p4 workspace -d ws",
    "p4 reconcile Unity/Game/Assets/...",
    "p4 rec",
    "p4 open Foo.cs",
    "p4 rename a b",
    "p4 integ a b",
    "p4 undo //x/...@1",
    "p4 resolve -at",
    "p4 resolve -ay",
    "p4 populate //a/... //b/...",
    "p4 client -i < spec.txt",
    "p4 edit -c a.cs",
    "p4 opened & p4 sync",
    "echo $(p4 sync)",
    "x=`p4 sync`",
    "echo `p4 sync`",
    'echo "$(p4 sync)"',
    "cat <(p4 sync)",
    "p4 opened -c 1 | xargs p4 revert",
    "p4 opened -c 1 | xargs -I {} p4 revert {}",
    "cmd /c p4 sync",
    'cmd.exe /c "p4 sync"',
    "env P4CLIENT=x p4 sync",
    "nohup p4 sync",
    "sudo p4 sync",
    "start /b p4 sync",
    'powershell -NoProfile -Command "p4 sync"',
    'powershell.exe -NoProfile -ExecutionPolicy Bypass -c "p4 sync; echo done"',
    'pwsh -c "p4 sync"',
    'bash -c "p4 sync"',
    'echo "a\\"b"; p4 sync; echo "x\\"y"',
    'p4 -x "C:/my list.txt" edit',
    "p4 -zprog=x sync",
    "C:\\Perforce\\p4.exe sync",
    '"C:\\Program Files\\Perforce\\p4.exe" sync',
    "vcs-wrapper.exe pending submit 123",
    "vcs-wrapper-tui pending submit 1",
    "vcs-wrapper.exe p4 submit -c 1",
    "vcs-wrapper.exe p4 -c 12 submit",
    'vcs-wrapper.exe pending new "fix submit gate"',
]

MUST_ALLOW = [
    "p4 edit -c 123 a.cs",
    "p4 edit -c $CL a.cs",
    'p4 edit -c "$CL" A.cs',
    "CL=123\np4 edit -c $CL a.cs",
    'CL=123; p4 edit -c "$CL" a.cs',
    "CL=5; p4 edit -c ${CL} a.cs",
    "p4 -x list.txt edit -c 12",
    "p4 -x C:/tmp/list.txt edit -c 123",
    'p4 -x "C:/my list.txt" edit -c 123',
    "p4 add -c 99 b.cs b.cs.meta",
    "p4 shelve -f -c 123",
    "p4 opened -a x",
    "p4 opened -c 123",
    "p4 opened",
    "p4 describe -s 1",
    "p4 changes -s submitted -m5",
    "p4 changes -s pending -c ws-yourname_Main",
    "p4 revert -a -c 123",
    "p4 diff -du Wiki/common/perforce-workflow.md",
    'p4 -F "%depotFile%" opened -c 123 | p4 -x - diff -du',
    r"Build\project-build.bat code test GameServer",
    r"Tool\QualityGate\qualitygate.bat change-description 1 --description-file x",
    "p4 edit -c 123 Unity/Game/Packages/packages-lock.json",
    "p4 opened -a Unity/Game/Packages/packages-lock.json",
    "p4 fstat //Depot/Main/Code/Admin/x.cs",
    "p4 edit -c 123 Code/GameServer/admin/Ops.cs",
    "p4 add -c 123 Tool/foo-sync.md",
    "p4 edit -c 1 Unity/Game/Assets/HeroArrangeSystem.Sync.cs",
    "p4 add -c 123 Wiki/reports/20261015_sync_audit.md",
    'Tool/VcsWrapper/vcs-wrapper.exe pending new "Game mission m1 M1"',
    'Tool/VcsWrapper/vcs-wrapper.exe pending new "Game mission 20261015-safe-submit-fix M1"',
    "Tool/VcsWrapper/vcs-wrapper.exe desc show 123",
    "dotnet run --project Tool/DataTool -- D:/Work/Game_Main",
    "p4 reconcile -c 123 Unity/Game/Assets/...",
    "p4 rec -c 123 a.cs",
    "p4 open -c 12 a.cs",
    "p4 resolve -c 123 -am a.cs",
    "p4 edit -c123 a.cs",
    "p4 add -c99 b.cs",
    "p4 revert -a -c123",
    "p4 opened 2>&1",
    "p4 opened &> out.txt",
    "p4 opened >& out.txt",
    "p4 changes -m5 &",
    "p4 edit -c 1 a.{cs,meta}",
    "echo ${CL}",
    "cmd /c p4 opened",
    "xargs echo",
    "echo $(date)",
]

POWERSHELL_DENY = [
    "p4 `\n submit -c 123",
    "p4 `\r\n  sync",
    "dir *.cs | ForEach-Object { p4 edit $_ }",
    "Get-ChildItem | % { p4 edit $_.FullName }",
    "try { p4 sync } catch {}",
    "try{p4 sync}catch{}",
    "if ($true) { p4 submit -c 1 }",
    "Write-Host (p4 sync)",
    'Write-Host "$(p4 sync)"',
    "echo $(p4 sync)",
    "$x = $(p4 sync)",
    "$p = 'p4'; & $P sync",
    "$p = 'p4'\n& $P sync",
    "cd D:\\Work\\x\\\np4 sync",
    '& "C:\\Program Files\\Perforce\\p4.exe" sync',
    'powershell -Command "& { p4 sync }"',
    'echo "a`"b"; p4 sync; echo "x`"y"',
]

POWERSHELL_ALLOW = [
    "Get-ChildItem | % { p4 edit -c 123 $_.FullName }",
    'Write-Host "a (p4 sync) b"',
]


def make_adapter(project, deny=None, schema=1, state_dir="_My/missions"):
    adapter_dir = project / ".claude" / "orchestrator"
    adapter_dir.mkdir(parents=True, exist_ok=True)
    adapter = {
        "schema": schema,
        "paths": {"stateDir": state_dir},
        "guard": {"deny": [SUBMIT_RULE] if deny is None else deny},
    }
    (adapter_dir / "adapter.json").write_text(json.dumps(adapter), encoding="utf-8")


def make_active(project, mission_id="m-001", session="s1"):
    state_dir = project / STATE
    state_dir.mkdir(parents=True, exist_ok=True)
    if session is None:
        (state_dir / "ACTIVE").write_text(mission_id + "\n", encoding="utf-8")
        return
    data = {"id": mission_id, "session": session, "openedAt": "2026-10-15T09:00:00+09:00"}
    (state_dir / "ACTIVE").write_text(json.dumps(data), encoding="utf-8")


def make_corrupt_active(project):
    state_dir = project / STATE
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "ACTIVE").write_text('{"id": "m-1", "sess', encoding="utf-8")
    return state_dir / "ACTIVE"


def read_active(project):
    return json.loads((project / STATE / "ACTIVE").read_text(encoding="utf-8"))


def make_registry(folder, entries, adapters=None):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "projects.json").write_text(json.dumps(entries), encoding="utf-8")
    for name, deny in (adapters if adapters is not None else {"example": [SUBMIT_RULE]}).items():
        (folder / name).mkdir(exist_ok=True)
        adapter = {"schema": 1, "paths": {"stateDir": "_My/missions"}, "guard": {"deny": deny}}
        (folder / name / "adapter.json").write_text(json.dumps(adapter), encoding="utf-8")
    return folder


def run_raw(stdin, env_project=None, cwd=None, env=None):
    base = {k: v for k, v in os.environ.items() if k not in CLEARED_ENV}
    base["HAN_ADAPTERS_DIR"] = str(NO_REGISTRY)
    base.update(env or {})
    if env_project is not None:
        base["CLAUDE_PROJECT_DIR"] = str(env_project)
    return subprocess.run([sys.executable, "-B", str(SCRIPT)], input=stdin, capture_output=True, env=base, cwd=cwd, timeout=30)


def run_guard(project, command, tool="Bash", session="s1", start=None, env=None, **extra):
    payload = {"tool_name": tool, "tool_input": {"command": command}, "cwd": str(project), **extra}
    if session is not None:
        payload["session_id"] = session
    result = run_raw(json.dumps(payload).encode("utf-8"), env_project=start or project, env=env)
    assert result.returncode == 0
    text = result.stdout.decode("utf-8").strip()
    return json.loads(text) if text else {}


def denial(output):
    decision = output.get("hookSpecificOutput") or {}
    return decision.get("permissionDecisionReason", "") if decision.get("permissionDecision") == "deny" else ""


def is_denied(output):
    return denial(output) != ""


def context_of(output):
    return (output.get("hookSpecificOutput") or {}).get("additionalContext", "")


def rule_reason(adapter, command, tool="Bash"):
    verdict = mission_guard.judge_command(adapter, command, tool)
    return verdict.reason if verdict.denied else ""


@pytest.fixture
def project(tmp_path):
    return tmp_path


@pytest.fixture(scope="module")
def sample_adapter():
    return json.loads(SAMPLE_ADAPTER.read_text(encoding="utf-8"))


@pytest.mark.parametrize("command", MUST_DENY)
def test_sample_rules_deny(sample_adapter, command):
    assert rule_reason(sample_adapter, command) != "", command


@pytest.mark.parametrize("command", MUST_ALLOW)
def test_sample_rules_allow(sample_adapter, command):
    assert rule_reason(sample_adapter, command) == "", command


@pytest.mark.parametrize("command", POWERSHELL_DENY)
def test_sample_rules_deny_powershell(sample_adapter, command):
    assert rule_reason(sample_adapter, command, POWERSHELL) != "", command


@pytest.mark.parametrize("command", POWERSHELL_ALLOW)
def test_sample_rules_allow_powershell(sample_adapter, command):
    assert rule_reason(sample_adapter, command, POWERSHELL) == "", command


def test_sample_wrapper_quoted_submit_word_is_known_false_positive(sample_adapter):
    assert rule_reason(sample_adapter, 'vcs-wrapper.exe pending new "fix submit-gate"') == ""
    assert rule_reason(sample_adapter, 'vcs-wrapper.exe pending new "fix submit gate"') != ""


def test_sample_matches_example_adapter_rules(sample_adapter):
    if not EXAMPLE_ADAPTER.is_file():
        pytest.skip("adapters/example/adapter.json 없음")
    example = json.loads(EXAMPLE_ADAPTER.read_text(encoding="utf-8"))
    assert sample_adapter["guard"] == example["guard"]


def test_example_regression_cases_via_check_guard():
    if not EXAMPLE_ADAPTER.is_file() or not EXAMPLE_CASES.is_file():
        pytest.skip("adapters/example 없음")
    result = subprocess.run(
        [sys.executable, "-B", str(CHECK_GUARD), str(EXAMPLE_ADAPTER), str(EXAMPLE_CASES)], capture_output=True, timeout=60
    )
    assert result.returncode == 0, result.stdout.decode("utf-8")
    assert b"mismatches=0" in result.stdout


def test_fragments_join_continuations_and_split():
    assert mission_guard.command_fragments("p4\\\nsubmit -c 1") == ["p4 submit -c 1"]
    assert mission_guard.command_fragments("a && b || c | d; e\nf") == list("abcdef")


def test_continuation_is_per_tool():
    assert mission_guard.command_fragments("p4\\\nsync", "PowerShell") == ["p4\\", "sync"]
    assert mission_guard.command_fragments("p4`\nsync", "PowerShell") == ["p4 sync"]
    assert mission_guard.command_fragments("p4`\nsync", "Bash") == ["p4`", "sync"]


def test_fragments_split_on_braces_and_single_ampersand():
    assert mission_guard.command_fragments("a & b") == ["a", "b"]
    assert mission_guard.command_fragments("x { y } z") == ["x", "y", "z"]
    assert mission_guard.command_fragments("a 2>&1 | b &> f >& g") == ["a 2>&1", "b &> f >& g"]


def test_fragments_keep_quoted_separators():
    assert mission_guard.command_fragments('echo "a; b" && c') == ['echo "a; b"', "c"]


def test_fragments_unclosed_quote_does_not_swallow_rest():
    assert mission_guard.command_fragments("echo it's; p4 submit") == ["echo it's", "p4 submit"]


def test_fragments_escaped_quote_does_not_hide_next_command():
    assert "p4 sync" in mission_guard.command_fragments('echo "a\\"b"; p4 sync; echo "x\\"y"')
    assert "p4 sync" in mission_guard.command_fragments('echo "a`"b"; p4 sync; echo "x`"y"', "PowerShell")


def test_fragments_substitute_variables():
    assert mission_guard.command_fragments("CL=123\np4 edit -c $CL a") == ["CL=123", "p4 edit -c 123 a"]
    assert mission_guard.command_fragments("$CL = 5; p4 edit -c ${CL} a")[-1] == "p4 edit -c 5 a"
    assert mission_guard.command_fragments('$env:CL="7"; p4 edit -c "$env:CL" a')[-1] == "p4 edit -c 7 a"


def test_powershell_variables_ignore_case():
    assert "p4 sync" in mission_guard.command_fragments("$p = 'p4'; & $P sync", "PowerShell")
    assert "$P sync" in mission_guard.command_fragments("$p = 'p4'; & $P sync", "Bash")


def test_fragments_add_substitution_bodies():
    assert "p4 sync" in mission_guard.command_fragments("echo $(p4 sync) tail")
    assert "p4 sync" in mission_guard.command_fragments("echo `p4 sync` tail")
    assert "p4 sync" in mission_guard.command_fragments("echo $(echo $(p4 sync))")
    assert "p4 sync" in mission_guard.command_fragments("echo $(p4 sync)", "PowerShell")
    assert "p4 sync" in mission_guard.command_fragments("Write-Host (p4 sync)", "PowerShell")


def test_fragments_peel_wrappers_recursively():
    assert "p4 sync" in mission_guard.command_fragments("sudo env A=1 nohup p4 sync")
    assert "p4 sync" in mission_guard.command_fragments('cmd /c "echo a && p4 sync"')
    assert "p4 sync" in mission_guard.command_fragments('powershell -NoProfile -c "p4 sync"')


def test_registry_root_matching():
    assert mission_guard.root_matches("D:\\Work\\Game_Main", "d:/work/game_main")
    assert mission_guard.root_matches("D:/Work/slots/s1", "D:/Work/slots/*")
    assert not mission_guard.root_matches("D:/Work/slots/s1/a", "D:/Work/slots/*")
    assert not mission_guard.root_matches("D:/Work/slots", "D:/Work/slots/*")
    assert mission_guard.root_matches("D:/Work/slots/s1/", "D:/Work/slots/s?")


def test_no_adapter_no_output(project):
    assert run_guard(project, "p4 submit -c 123") == {}


def test_no_active_passes(project):
    make_adapter(project)
    assert run_guard(project, "p4 submit -c 123") == {}


def test_same_session_denies_with_mission_id(project):
    make_adapter(project)
    make_active(project, "m-042", session="s1")
    output = run_guard(project, "p4 submit -c 123", session="s1")
    assert "m-042" in denial(output)


def test_other_session_is_warned_not_denied(project):
    make_adapter(project)
    make_active(project, "m-042", session="s1")
    output = run_guard(project, "p4 submit -c 123", session="s2")
    assert not is_denied(output)
    assert "m-042" in output["systemMessage"]
    assert "submit 금지" in output["systemMessage"]


def test_legacy_active_denies_every_session(project):
    make_adapter(project)
    make_active(project, "m-042", session=None)
    assert "m-042" in denial(run_guard(project, "p4 submit -c 1", session="s1"))
    assert is_denied(run_guard(project, "p4 submit -c 1", session="other"))


def test_subagent_follows_its_parent_session_id(project):
    make_adapter(project)
    make_active(project, "m-042", session="s1")
    assert is_denied(run_guard(project, "p4 submit -c 1", session="s1", agent_id="agent-1"))
    assert not is_denied(run_guard(project, "p4 submit -c 1", session="s2", agent_id="agent-1"))


def test_missing_session_id_denies(project):
    make_adapter(project)
    make_active(project, session="s1")
    assert is_denied(run_guard(project, "p4 submit -c 1", session=None))


def test_deny_output_shape(project):
    make_adapter(project)
    make_active(project, "m-1", session="s1")
    output = run_guard(project, "p4 submit -c 1")
    assert output["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert set(output) == {"hookSpecificOutput"}


def test_allows_other_command(project):
    make_adapter(project)
    make_active(project)
    assert run_guard(project, "p4 opened") == {}


def test_powershell_tool_is_denied(project):
    make_adapter(project)
    make_active(project)
    assert is_denied(run_guard(project, "p4 submit -c 123", tool="PowerShell"))


def test_powershell_continuation_is_joined_only_for_powershell(project):
    make_adapter(project, deny=[SYNC_RULE])
    make_active(project)
    assert is_denied(run_guard(project, "p4 `\n sync", tool="PowerShell"))
    assert is_denied(run_guard(project, "p4 \\\n sync", tool="Bash"))
    assert not is_denied(run_guard(project, "p4 \\\n sync", tool="PowerShell"))


def test_other_tool_passes(project):
    make_adapter(project)
    make_active(project)
    assert run_guard(project, "p4 submit -c 123", tool="Read") == {}


def test_multiline_command_is_checked_per_fragment(project):
    make_adapter(project)
    make_active(project)
    assert is_denied(run_guard(project, "echo hi\np4 submit -c 1"))


def test_adapter_found_from_parent_folder_via_env(project):
    make_adapter(project)
    make_active(project)
    sub = project / "a" / "b"
    sub.mkdir(parents=True)
    assert is_denied(run_guard(project, "p4 submit -c 1", start=sub))


def test_adapter_found_from_parent_folder_via_cwd(project):
    make_adapter(project)
    make_active(project)
    sub = project / "a" / "b"
    sub.mkdir(parents=True)
    payload = {"tool_name": "Bash", "tool_input": {"command": "p4 submit -c 1"}, "cwd": str(sub), "session_id": "s1"}
    result = run_raw(json.dumps(payload).encode("utf-8"))
    assert is_denied(json.loads(result.stdout.decode("utf-8")))


def test_broken_adapter_json_turns_guard_off_with_message(project):
    adapter_dir = project / ".claude" / "orchestrator"
    adapter_dir.mkdir(parents=True)
    (adapter_dir / "adapter.json").write_text("{not json", encoding="utf-8")
    make_active(project)
    output = run_guard(project, "p4 submit -c 123")
    assert not is_denied(output)
    assert "미션 가드 꺼짐" in output["systemMessage"]
    assert "미션 가드 꺼짐" in context_of(output)


def test_unknown_schema_turns_guard_off_with_message(project):
    make_adapter(project, schema=2)
    make_active(project)
    output = run_guard(project, "p4 submit -c 123")
    assert not is_denied(output)
    assert "미션 가드 꺼짐" in output["systemMessage"]


def test_missing_state_dir_turns_guard_off_with_message(project):
    make_adapter(project, state_dir="")
    output = run_guard(project, "p4 submit -c 123")
    assert "미션 가드 꺼짐" in output["systemMessage"]


def test_invalid_regex_is_skipped_reported_and_later_rule_applies(project):
    make_adapter(project, deny=[{"pattern": "([unclosed", "reason": "bad"}, SUBMIT_RULE])
    make_active(project)
    output = run_guard(project, "p4 submit -c 123")
    assert is_denied(output)
    assert "잘못된 정규식" in output["systemMessage"]
    assert "잘못된 정규식" in context_of(output)


def test_invalid_regex_is_reported_on_allowed_command_only_while_mission_active(project):
    make_adapter(project, deny=[{"pattern": "([unclosed", "reason": "bad"}, SUBMIT_RULE])
    assert run_guard(project, "p4 opened") == {}
    make_active(project)
    output = run_guard(project, "p4 opened")
    assert not is_denied(output)
    assert "잘못된 정규식" in output["systemMessage"]
    assert "잘못된 정규식" in context_of(output)


def test_rule_exception_matches_entire_fragment_and_only_its_own_rule():
    rule = {"pattern": "submit", "except": ["rg submit"], "reason": "submit"}
    adapter = {"guard": {"deny": [rule]}}
    assert mission_guard.judge_command(adapter, "rg submit").kind == "allow"
    assert mission_guard.judge_command(adapter, "rg submit extra").denied
    assert mission_guard.judge_command(adapter, "prefix rg submit").denied
    adapter["guard"]["deny"].append({"pattern": "^rg", "reason": "separate rule"})
    assert mission_guard.judge_command(adapter, "rg submit").reason == "separate rule"


@pytest.mark.parametrize(
    "command,tool",
    [
        ("rg submit; p4 submit -c 1", "Bash"),
        ("rg submit && p4 submit -c 1", "Bash"),
        ('rg submit "$(p4 submit -c 1)"', "Bash"),
        ('Write-Output "$(p4 submit -c 1)"', "PowerShell"),
        ("Write-Output (p4 submit -c 1)", "PowerShell"),
        ("cmd /c p4 submit -c 1", "Bash"),
        ("$p='p4'; & $p submit -c 1", "PowerShell"),
    ],
)
def test_rule_exception_leaves_other_fragments_checked(command, tool):
    adapter = {"guard": {"deny": [{"pattern": "submit", "except": ["rg.*", "Write-Output.*"]}]}}
    assert mission_guard.judge_command(adapter, command, tool).denied


@pytest.mark.parametrize("exceptions", [None, "rg submit", {}, [None], [1], ["rg submit", 1], ["rg submit", "["]])
def test_invalid_rule_exceptions_do_not_grant_allowance(project, exceptions):
    make_adapter(project, deny=[{"pattern": "submit", "except": exceptions}])
    make_active(project)
    output = run_guard(project, "rg submit")
    assert is_denied(output)
    assert "guard.deny.except" in output["systemMessage"]
    assert "예외를 적용하지 않음" in context_of(output)


def test_invalid_rule_exceptions_report_on_allowed_commands(project):
    make_adapter(project, deny=[{"pattern": "submit", "except": False}])
    make_active(project)
    output = run_guard(project, "p4 opened")
    assert not is_denied(output)
    assert "guard.deny.except" in output["systemMessage"]


def test_check_guard_reports_invalid_exception_without_allowing_it(tmp_path):
    make_adapter(tmp_path, deny=[{"pattern": "submit", "except": ["rg submit", "["]}])
    adapter = tmp_path / ".claude" / "orchestrator" / "adapter.json"
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps([{"expect": "deny", "command": "rg submit"}]), encoding="utf-8")
    mismatches, errors, problems = load_check_guard().evaluate(str(adapter), str(cases))
    assert (mismatches, errors) == ([], [])
    assert len(problems) == 1
    result = run_check_guard_cli(str(adapter), str(cases))
    assert result.returncode == 1
    assert "guard.deny.except" in result.stdout.decode("utf-8")


def test_garbage_stdin_does_not_block(project):
    result = run_raw(b"not json")
    assert result.returncode == 0
    output = json.loads(result.stdout.decode("utf-8"))
    assert "미션 가드 꺼짐" in output["systemMessage"]
    assert not is_denied(output)


def test_output_is_ascii_json_that_round_trips_korean(project):
    make_adapter(project)
    make_active(project)
    result = run_raw(
        json.dumps(
            {"tool_name": "Bash", "tool_input": {"command": "p4 submit"}, "cwd": str(project), "session_id": "s1"}
        ).encode(),
        env_project=project,
    )
    result.stdout.decode("ascii")
    assert "submit 금지" in denial(json.loads(result.stdout))


def test_messages_are_also_additional_context(project):
    make_adapter(project)
    make_active(project, "m-042", session="s1")
    output = run_guard(project, "p4 submit -c 123", session="s2")
    specific = output["hookSpecificOutput"]
    assert specific["hookEventName"] == "PreToolUse"
    assert "m-042" in specific["additionalContext"]
    assert "permissionDecision" not in specific
    opened = run_guard(project, "echo han-mission resume m-042", session="s2")
    assert "가드 세션 갱신" in context_of(opened)


def test_marker_open_creates_active_with_session(project):
    make_adapter(project)
    output = run_guard(project, "echo han-mission open m-1", session="s9")
    assert not is_denied(output)
    assert "m-1" in output["systemMessage"]
    assert "가드 켜짐" in context_of(output)
    active = read_active(project)
    assert active["id"] == "m-1"
    assert active["session"] == "s9"
    assert active["openedAt"]
    assert sorted(p.name for p in (project / STATE).iterdir()) == ["ACTIVE", "m-1"]
    assert sorted(p.name for p in (project / STATE / "m-1").iterdir()) == ["sessions.tsv"]


def test_marker_works_with_quotes_and_powershell(project):
    make_adapter(project)
    assert not is_denied(run_guard(project, 'echo "han-mission open m-1"'))
    assert read_active(project)["id"] == "m-1"
    assert not is_denied(run_guard(project, "Write-Host han-mission close m-1", tool="PowerShell"))
    assert not (project / STATE / "ACTIVE").exists()


def test_marker_open_same_id_rewrites(project):
    make_adapter(project)
    make_active(project, "m-1", session="old")
    assert not is_denied(run_guard(project, "echo han-mission open m-1", session="new"))
    assert read_active(project)["session"] == "new"


def test_marker_open_other_id_is_denied_and_keeps_active(project):
    make_adapter(project)
    make_active(project, "m-1", session="s1")
    output = run_guard(project, "echo han-mission open m-2", session="s2")
    assert "m-1" in denial(output)
    assert read_active(project)["id"] == "m-1"


def test_marker_open_replaces_legacy_active_of_same_id(project):
    make_adapter(project)
    make_active(project, "m-1", session=None)
    run_guard(project, "echo han-mission open m-1", session="s5")
    assert read_active(project)["session"] == "s5"


def test_marker_resume_updates_session_and_names_previous(project):
    make_adapter(project)
    make_active(project, "m-1", session="old")
    output = run_guard(project, "echo han-mission resume m-1", session="new")
    assert not is_denied(output)
    assert "이전 session: old" in output["systemMessage"]
    active = read_active(project)
    assert active["session"] == "new"
    assert active["openedAt"] == "2026-10-15T09:00:00+09:00"


def read_sessions(project, mission_id="m-1"):
    text = (project / STATE / mission_id / "sessions.tsv").read_text(encoding="utf-8")
    return [line.split("\t") for line in text.splitlines()]


def test_marker_open_and_resume_append_session_rows(project):
    make_adapter(project)
    run_guard(project, "echo han-mission open m-1", session="s1", transcript_path="C:/t/s1.jsonl")
    run_guard(project, "echo han-mission resume m-1", session="s2", transcript_path="C:/t/s2.jsonl")
    rows = read_sessions(project)
    assert rows[0] == ["at", "action", "session", "transcript"]
    assert [row[1:] for row in rows[1:]] == [["open", "s1", "C:/t/s1.jsonl"], ["resume", "s2", "C:/t/s2.jsonl"]]
    assert rows[1][0] and rows[2][0]


def test_marker_session_row_without_transcript_has_empty_value(project):
    make_adapter(project)
    run_guard(project, "echo han-mission open m-1", session="s1")
    assert read_sessions(project)[1][1:] == ["open", "s1", ""]


def test_denied_or_close_marker_leaves_no_session_row(project):
    make_adapter(project)
    make_active(project, "m-1", session="s1")
    run_guard(project, "echo han-mission open m-2", session="s2")
    run_guard(project, "echo han-mission close m-1", session="s1")
    assert not (project / STATE / "m-2").exists()
    assert not (project / STATE / "m-1" / "sessions.tsv").exists()


def test_marker_resume_other_or_missing_is_denied(project):
    make_adapter(project)
    assert is_denied(run_guard(project, "echo han-mission resume m-1"))
    make_active(project, "m-1", session="s1")
    assert is_denied(run_guard(project, "echo han-mission resume m-2"))
    assert read_active(project)["session"] == "s1"


def test_marker_close_removes_active(project):
    make_adapter(project)
    make_active(project, "m-1")
    output = run_guard(project, "echo han-mission close m-1")
    assert not is_denied(output)
    assert not (project / STATE / "ACTIVE").exists()


def test_marker_close_other_is_denied(project):
    make_adapter(project)
    make_active(project, "m-1")
    assert is_denied(run_guard(project, "echo han-mission close m-2"))
    assert (project / STATE / "ACTIVE").exists()


def test_marker_close_from_other_session_is_denied_until_resume(project):
    make_adapter(project)
    make_active(project, "m-1", session="s1")
    output = run_guard(project, "echo han-mission close m-1", session="s2")
    assert "resume 후 close" in denial(output)
    assert (project / STATE / "ACTIVE").exists()
    assert not is_denied(run_guard(project, "echo han-mission resume m-1", session="s2"))
    assert not is_denied(run_guard(project, "echo han-mission close m-1", session="s2"))
    assert not (project / STATE / "ACTIVE").exists()


def test_marker_close_legacy_active_from_any_session(project):
    make_adapter(project)
    make_active(project, "m-1", session=None)
    assert not is_denied(run_guard(project, "echo han-mission close m-1", session="whatever"))
    assert not (project / STATE / "ACTIVE").exists()


def test_marker_close_removes_allow_once_of_that_mission(project):
    make_adapter(project)
    make_active(project, "m-1", session="s1")
    path = write_allow_once(project, "m-1", ["p4 submit -c 1"])
    other = write_allow_once(project, "m-2", ["p4 submit -c 1"])
    run_guard(project, "echo han-mission close m-1", session="s1")
    assert not path.exists()
    assert other.exists()


@pytest.mark.parametrize("bad_id", [".", "..", "a..b", "../evil", "-x"])
def test_marker_with_bad_id_is_denied(project, bad_id):
    make_adapter(project)
    output = run_guard(project, f"echo han-mission open {bad_id}")
    assert "형식 오류" in denial(output)
    assert not (project / STATE / "ACTIVE").exists()


@pytest.mark.parametrize("good_id", ["m-1", "a.b", "20261015-safe-submit-fix", "m_1"])
def test_marker_with_good_id_is_accepted(project, good_id):
    make_adapter(project)
    assert not is_denied(run_guard(project, f"echo han-mission open {good_id}"))
    assert read_active(project)["id"] == good_id


def test_marker_mixed_with_other_fragment_is_denied_and_not_processed(project):
    make_adapter(project)
    make_active(project, "m-1", session="s1")
    for command in (
        "echo han-mission close m-1 && p4 submit -c 1",
        "echo han-mission close m-1; ls",
        "ls\necho han-mission close m-1",
        "cmd /c echo han-mission close m-1",
    ):
        output = run_guard(project, command, session="s1")
        assert "단독 호출" in denial(output), command
        assert read_active(project)["id"] == "m-1"


def test_marker_mixed_is_denied_even_without_active_mission(project):
    make_adapter(project)
    assert "단독 호출" in denial(run_guard(project, "echo han-mission open m-1 && ls"))
    assert not (project / STATE / "ACTIVE").exists()


def test_guard_applies_right_after_open_in_same_session(project):
    make_adapter(project)
    run_guard(project, "echo han-mission open m-1", session="s1")
    assert is_denied(run_guard(project, "p4 submit -c 1", session="s1"))
    assert not is_denied(run_guard(project, "p4 submit -c 1", session="s2"))


def test_corrupt_active_denies_rule_hits_in_every_session_with_path(project):
    make_adapter(project)
    path = make_corrupt_active(project)
    for session in ("s1", "s2"):
        reason = denial(run_guard(project, "p4 submit -c 1", session=session))
        assert "ACTIVE 손상" in reason
        assert str(path) in reason
        assert "지우거나 close" in reason
    assert run_guard(project, "p4 opened") == {}


def test_corrupt_active_ignores_allow_once(project):
    make_adapter(project)
    make_corrupt_active(project)
    write_allow_once(project, "m-1", ["p4 submit -c 1"])
    assert is_denied(run_guard(project, "p4 submit -c 1"))


def test_corrupt_active_is_removed_by_close_of_any_id(project):
    make_adapter(project)
    make_corrupt_active(project)
    output = run_guard(project, "echo han-mission close whatever", session="s7")
    assert not is_denied(output)
    assert not (project / STATE / "ACTIVE").exists()
    assert run_guard(project, "p4 submit -c 1") == {}


def test_corrupt_active_blocks_open_and_resume(project):
    make_adapter(project)
    path = make_corrupt_active(project)
    assert "ACTIVE 손상" in denial(run_guard(project, "echo han-mission open m-1"))
    assert "ACTIVE 손상" in denial(run_guard(project, "echo han-mission resume m-1"))
    assert path.exists()


def test_write_active_is_atomic_when_replace_fails(project, monkeypatch):
    state_dir = project / STATE
    make_active(project, "m-1", session="s1")

    def fail(*args):
        raise PermissionError("locked")

    monkeypatch.setattr(mission_guard.os, "replace", fail)
    with pytest.raises(PermissionError):
        mission_guard.write_active(str(state_dir), mission_guard.Active("m-2", "s2", "t"))
    assert read_active(project)["id"] == "m-1"
    assert sorted(p.name for p in state_dir.iterdir()) == ["ACTIVE"]


def write_allow_once(project, mission_id, lines):
    folder = project / STATE / mission_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "allow-once"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_allow_once_passes_exact_command_then_is_consumed(project):
    make_adapter(project)
    make_active(project, "m-1")
    path = write_allow_once(project, "m-1", ["p4 submit -c 123"])
    output = run_guard(project, "  p4 submit -c 123  ")
    assert not is_denied(output)
    assert "1회 허용 사용: p4 submit -c 123" in output["systemMessage"]
    assert "1회 허용 사용" in context_of(output)
    assert not path.exists()
    assert sorted(p.name for p in path.parent.iterdir()) == []
    assert is_denied(run_guard(project, "p4 submit -c 123"))


def test_allow_once_ignores_different_command(project):
    make_adapter(project)
    make_active(project, "m-1")
    path = write_allow_once(project, "m-1", ["p4 submit -c 123"])
    assert is_denied(run_guard(project, "p4 submit -c 124"))
    assert is_denied(run_guard(project, "p4 submit -c 123 extra"))
    assert path.exists()


def test_allow_once_consumes_only_matching_line(project):
    make_adapter(project)
    make_active(project, "m-1")
    path = write_allow_once(project, "m-1", ["p4 submit -c 1", "p4 submit -c 2"])
    assert not is_denied(run_guard(project, "p4 submit -c 1"))
    assert path.read_text(encoding="utf-8").splitlines() == ["p4 submit -c 2"]


def test_allow_once_of_other_mission_does_not_apply(project):
    make_adapter(project)
    make_active(project, "m-1")
    write_allow_once(project, "m-2", ["p4 submit -c 1"])
    assert is_denied(run_guard(project, "p4 submit -c 1"))


def test_allow_once_unreadable_file_denies_with_path_and_keeps_file(project):
    make_adapter(project)
    make_active(project, "m-1")
    path = project / STATE / "m-1" / "allow-once"
    path.parent.mkdir(parents=True)
    path.write_bytes("p4 submit -c 1\n".encode("utf-16"))
    reason = denial(run_guard(project, "p4 submit -c 1"))
    assert "허용 안 함" in reason
    assert str(path) in reason
    assert path.exists()


def test_allow_once_lost_race_means_not_allowed(project, monkeypatch):
    path = write_allow_once(project, "m-1", ["p4 submit -c 1"])

    def vanished(*args):
        raise FileNotFoundError("claimed by another")

    monkeypatch.setattr(mission_guard.os, "replace", vanished)
    assert mission_guard.consume_allow_once(str(project / STATE), "m-1", "p4 submit -c 1") is False
    assert path.exists()


def test_allow_once_second_consumer_after_claim_is_denied(project):
    state_dir = str(project / STATE)
    write_allow_once(project, "m-1", ["p4 submit -c 1"])
    assert mission_guard.consume_allow_once(state_dir, "m-1", "p4 submit -c 1") is True
    assert mission_guard.consume_allow_once(state_dir, "m-1", "p4 submit -c 1") is False


def test_exception_inside_judge_denies_instead_of_passing(project, monkeypatch):
    def boom(*args):
        raise FileNotFoundError("gone")

    monkeypatch.setattr(mission_guard, "consume_allow_once", boom)
    outcome = mission_guard.Outcome()
    active = mission_guard.Active("m-1", "s1", "")
    mission_guard.judge_denied_command({"session_id": "s1"}, "p4 submit", active, str(project / STATE), "no", outcome)
    assert outcome.deny
    assert "allow-once" in outcome.deny
    assert not outcome.messages


def test_exception_reading_state_after_rule_hit_denies(project, monkeypatch):
    make_adapter(project)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    monkeypatch.setenv("HAN_ADAPTERS_DIR", str(NO_REGISTRY))

    def boom(*args):
        raise RuntimeError("disk error")

    monkeypatch.setattr(mission_guard, "read_active", boom)
    payload = {"tool_name": "Bash", "tool_input": {"command": "p4 submit -c 1"}, "cwd": str(project)}
    assert "허용 안 함" in mission_guard.decide(payload).deny
    payload["tool_input"]["command"] = "p4 opened"
    assert not mission_guard.decide(payload).deny


def test_registry_exact_root_match_enables_guard(project):
    registry = make_registry(project / "registry", [{"adapter": "example", "roots": [str(project / "repo")]}])
    repo = project / "repo"
    repo.mkdir()
    env = {"HAN_ADAPTERS_DIR": str(registry)}
    assert not is_denied(run_guard(repo, "echo han-mission open m-1", env=env))
    assert (repo / STATE / "ACTIVE").exists()
    assert is_denied(run_guard(repo, "p4 submit -c 1", env=env))


def test_registry_glob_root_and_subfolder_walk_up(project):
    registry = make_registry(project / "registry", [{"adapter": "example", "roots": [str(project / "slots" / "*")]}])
    slot = project / "slots" / "s1"
    nested = slot / "Unity" / "Assets"
    nested.mkdir(parents=True)
    env = {"HAN_ADAPTERS_DIR": str(registry)}
    assert not is_denied(run_guard(nested, "echo han-mission open m-1", env=env))
    assert (slot / STATE / "ACTIVE").exists()
    assert not (nested / STATE / "ACTIVE").exists()
    assert is_denied(run_guard(nested, "p4 submit -c 1", env=env))


def test_registry_match_ignores_case_and_separators(project):
    repo = project / "Repo"
    repo.mkdir()
    shouted = str(repo).upper().replace("\\", "/")
    registry = make_registry(project / "registry", [{"adapter": "example", "roots": [shouted]}])
    env = {"HAN_ADAPTERS_DIR": str(registry)}
    run_guard(repo, "echo han-mission open m-1", env=env)
    assert is_denied(run_guard(repo, "p4 submit -c 1", env=env))


def test_registry_no_match_is_silent(project):
    registry = make_registry(project / "registry", [{"adapter": "example", "roots": [str(project / "other")]}])
    repo = project / "repo"
    repo.mkdir()
    assert run_guard(repo, "p4 submit -c 1", env={"HAN_ADAPTERS_DIR": str(registry)}) == {}


def test_registry_missing_is_silent(project):
    repo = project / "repo"
    repo.mkdir()
    assert run_guard(repo, "p4 submit -c 1", env={"HAN_ADAPTERS_DIR": str(project / "nope")}) == {}


def test_registry_broken_json_turns_guard_off_with_message(project):
    registry = project / "registry"
    registry.mkdir()
    (registry / "projects.json").write_text("[{broken", encoding="utf-8")
    repo = project / "repo"
    repo.mkdir()
    output = run_guard(repo, "p4 submit -c 1", env={"HAN_ADAPTERS_DIR": str(registry)})
    assert not is_denied(output)
    assert "미션 가드 꺼짐" in output["systemMessage"]
    assert "registry" in context_of(output)


@pytest.mark.parametrize("entries", [{"adapter": "example"}, [{"adapter": "example"}], [{"adapter": "../x", "roots": []}]])
def test_registry_wrong_shape_turns_guard_off_with_message(project, entries):
    registry = make_registry(project / "registry", entries)
    repo = project / "repo"
    repo.mkdir()
    output = run_guard(repo, "p4 submit -c 1", env={"HAN_ADAPTERS_DIR": str(registry)})
    assert "미션 가드 꺼짐" in output["systemMessage"]


def test_registry_matched_adapter_file_missing_turns_guard_off(project):
    repo = project / "repo"
    repo.mkdir()
    registry = make_registry(
        project / "registry", [{"adapter": "ghost", "roots": [str(repo)]}], adapters={}
    )
    output = run_guard(repo, "p4 submit -c 1", env={"HAN_ADAPTERS_DIR": str(registry)})
    assert "미션 가드 꺼짐" in output["systemMessage"]


def test_in_repo_adapter_takes_precedence_over_registry(project):
    repo = project / "repo"
    repo.mkdir()
    make_adapter(repo, deny=[SUBMIT_RULE])
    make_active(repo)
    registry = make_registry(
        project / "registry", [{"adapter": "example", "roots": [str(repo)]}], adapters={"example": [SYNC_RULE]}
    )
    env = {"HAN_ADAPTERS_DIR": str(registry)}
    assert is_denied(run_guard(repo, "p4 submit -c 1", env=env))
    assert not is_denied(run_guard(repo, "p4 sync", env=env))


def test_in_repo_adapter_makes_broken_registry_irrelevant(project):
    repo = project / "repo"
    repo.mkdir()
    make_adapter(repo)
    make_active(repo)
    registry = project / "registry"
    registry.mkdir()
    (registry / "projects.json").write_text("nope", encoding="utf-8")
    assert is_denied(run_guard(repo, "p4 submit -c 1", env={"HAN_ADAPTERS_DIR": str(registry)}))


@pytest.fixture
def clean_env(monkeypatch):
    for name in CLEARED_ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_default_registry_dir_is_marketplace_adapters_folder(clean_env):
    if not (REPO_ROOT / "adapters").is_dir():
        pytest.skip("제자리 실행이 아님")
    choice = mission_guard.resolve_registry()
    assert Path(choice.path) == REPO_ROOT / "adapters"
    assert choice.source == "in-place"


def test_registry_order_han_env_beats_user_config(clean_env, tmp_path):
    clean_env.setenv("HAN_ADAPTERS_DIR", str(tmp_path / "a"))
    clean_env.setenv("CLAUDE_PLUGIN_OPTION_ADAPTERS_DIR", str(tmp_path / "b"))
    choice = mission_guard.resolve_registry()
    assert Path(choice.path) == tmp_path / "a"
    assert choice.source == "env"


def test_registry_order_user_config_beats_in_place(clean_env, tmp_path):
    clean_env.setenv("CLAUDE_PLUGIN_OPTION_ADAPTERS_DIR", str(tmp_path / "b"))
    choice = mission_guard.resolve_registry()
    assert Path(choice.path) == tmp_path / "b"
    assert choice.source == "userConfig"


def test_registry_order_env_wins_even_when_folder_missing(clean_env, tmp_path):
    clean_env.setenv("HAN_ADAPTERS_DIR", str(tmp_path / "missing"))
    assert mission_guard.resolve_registry().source == "env"


def test_registry_order_empty_variables_fall_through(clean_env):
    clean_env.setenv("HAN_ADAPTERS_DIR", "")
    clean_env.setenv("CLAUDE_PLUGIN_OPTION_ADAPTERS_DIR", "")
    assert mission_guard.resolve_registry().source in ("in-place", "none")


def test_registry_order_none_when_in_place_folder_absent(clean_env, tmp_path):
    clean_env.setattr(mission_guard, "in_place_adapters_dir", lambda: str(tmp_path / "nope"))
    assert mission_guard.resolve_registry() == mission_guard.Registry("", "none")
    assert mission_guard.registry_dir() == ""


def test_registry_none_is_silent_for_hook(project):
    repo = project / "repo"
    repo.mkdir()
    shim = "import sys,mission_guard as m;m.in_place_adapters_dir=lambda:'" + str(project / "nope").replace("\\", "/")
    shim += "';sys.exit(m.main())"
    payload = {"tool_name": "Bash", "tool_input": {"command": "p4 submit"}, "cwd": str(repo)}
    env = {k: v for k, v in os.environ.items() if k not in CLEARED_ENV}
    env["PYTHONPATH"] = str(HOOKS_DIR)
    result = subprocess.run(
        [sys.executable, "-B", "-c", shim], input=json.dumps(payload).encode(), capture_output=True, env=env, timeout=30
    )
    assert result.returncode == 0
    assert result.stdout == b""


def test_hook_uses_user_config_variable_when_han_env_absent(project):
    registry = make_registry(project / "registry", [{"adapter": "example", "roots": [str(project / "repo")]}])
    repo = project / "repo"
    repo.mkdir()
    env = {"CLAUDE_PLUGIN_OPTION_ADAPTERS_DIR": str(registry), "HAN_ADAPTERS_DIR": ""}
    run_guard(repo, "echo han-mission open m-1", env=env)
    assert is_denied(run_guard(repo, "p4 submit -c 1", env=env))


def test_hook_han_env_beats_user_config_variable(project):
    wanted = make_registry(project / "wanted", [{"adapter": "example", "roots": [str(project / "repo")]}])
    other = make_registry(project / "other", [])
    repo = project / "repo"
    repo.mkdir()
    env = {"HAN_ADAPTERS_DIR": str(wanted), "CLAUDE_PLUGIN_OPTION_ADAPTERS_DIR": str(other)}
    run_guard(repo, "echo han-mission open m-1", env=env)
    assert is_denied(run_guard(repo, "p4 submit -c 1", env=env))


def test_log_records_registry_source_only_when_registry_is_consulted(project):
    registry = make_registry(project / "registry", [])
    repo = project / "repo"
    repo.mkdir()
    log = project / "guard.log"
    run_guard(repo, "p4 submit", env={"HAN_ADAPTERS_DIR": str(registry), "HAN_GUARD_LOG": str(log)})
    entries = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(entries) == 2
    assert entries[1] == {"registry_source": "env", "registry_dir": str(registry)}


def test_log_has_no_registry_line_when_in_repo_adapter_found(project):
    make_adapter(project)
    log = project / "guard.log"
    run_guard(project, "echo hi", env={"HAN_GUARD_LOG": str(log)})
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1


def write_json(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


def test_merge_local_recurses_objects_and_replaces_arrays_and_scalars():
    base = {"schema": 1, "paths": {"stateDir": "a", "keep": "k"}, "guard": {"deny": [1, 2]}, "x": 1}
    local = {"paths": {"stateDir": "b"}, "guard": {"deny": [3]}, "x": {"y": 2}, "new": True}
    assert mission_guard.merge_local(base, local) == {
        "schema": 1,
        "paths": {"stateDir": "b", "keep": "k"},
        "guard": {"deny": [3]},
        "x": {"y": 2},
        "new": True,
    }
    assert base["paths"]["stateDir"] == "a"


def test_load_adapter_without_local_returns_adapter_json(project):
    path = write_adapter_file(project)
    assert mission_guard.load_adapter(str(path)) == json.loads(path.read_text(encoding="utf-8"))


def test_load_adapter_merges_local_next_to_adapter_json(project):
    path = write_adapter_file(project)
    write_json(path.parent / "local.json", {"paths": {"stateDir": "elsewhere"}, "guard": {"deny": [SYNC_RULE]}})
    adapter = mission_guard.load_adapter(str(path))
    assert adapter["paths"]["stateDir"] == "elsewhere"
    assert adapter["guard"]["deny"] == [SYNC_RULE]
    assert adapter["schema"] == 1


def test_load_adapter_broken_local_uses_adapter_only_and_reports(project):
    path = write_adapter_file(project)
    (path.parent / "local.json").write_text("{broken", encoding="utf-8")
    problems = []
    adapter = mission_guard.load_adapter(str(path), problems)
    assert adapter["guard"]["deny"] == [SUBMIT_RULE]
    assert len(problems) == 1
    assert "local.json 무시" in problems[0]


def test_load_adapter_non_object_local_is_reported(project):
    path = write_adapter_file(project)
    (path.parent / "local.json").write_text("[1]", encoding="utf-8")
    problems = []
    assert mission_guard.load_adapter(str(path), problems)["schema"] == 1
    assert "객체가 아님" in problems[0]


def test_load_adapter_local_cannot_hide_a_bad_schema(project):
    path = write_adapter_file(project)
    write_json(path.parent / "local.json", {"schema": 2})
    with pytest.raises(mission_guard.GuardOff):
        mission_guard.load_adapter(str(path))


def test_hook_applies_local_deny_rules(project):
    path = write_adapter_file(project)
    write_json(path.parent / "local.json", {"guard": {"deny": [SYNC_RULE]}})
    make_active(project)
    assert is_denied(run_guard(project, "p4 sync"))
    assert not is_denied(run_guard(project, "p4 submit -c 1"))


def test_hook_registry_adapter_gets_local_json_too(project):
    repo = project / "repo"
    repo.mkdir()
    registry = make_registry(project / "registry", [{"adapter": "example", "roots": [str(repo)]}])
    write_json(registry / "example" / "local.json", {"guard": {"deny": [SYNC_RULE]}})
    make_active(repo)
    env = {"HAN_ADAPTERS_DIR": str(registry)}
    assert is_denied(run_guard(repo, "p4 sync", env=env))
    assert not is_denied(run_guard(repo, "p4 submit -c 1", env=env))


def test_hook_warns_about_broken_local_while_mission_active(project):
    path = write_adapter_file(project)
    (path.parent / "local.json").write_text("{broken", encoding="utf-8")
    assert run_guard(project, "p4 opened") == {}
    make_active(project)
    output = run_guard(project, "p4 opened")
    assert "local.json 무시" in output["systemMessage"]
    assert "local.json 무시" in context_of(output)
    assert is_denied(run_guard(project, "p4 submit -c 1"))


def test_diagnostic_log_records_selected_fields_only(project):
    make_adapter(project)
    log = project / "guard.log"
    long_command = "echo " + "x" * 300
    run_guard(
        project,
        long_command,
        session="s1",
        env={"HAN_GUARD_LOG": str(log)},
        agent_id="a1",
        agent_type="dev",
        hook_event_name="PreToolUse",
        secret="nope",
    )
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert set(entry) == {"session_id", "agent_id", "agent_type", "tool_name", "cwd", "hook_event_name", "command"}
    assert entry["session_id"] == "s1"
    assert entry["agent_id"] == "a1"
    assert entry["agent_type"] == "dev"
    assert entry["tool_name"] == "Bash"
    assert entry["hook_event_name"] == "PreToolUse"
    assert entry["cwd"] == str(project)
    assert entry["command"] == long_command[:120]


def test_no_log_without_env(project):
    make_adapter(project)
    run_guard(project, "echo hi")
    assert not list(project.glob("*.log"))


def test_unwritable_log_does_not_break_guard(project):
    make_adapter(project)
    make_active(project)
    output = run_guard(project, "p4 submit -c 1", env={"HAN_GUARD_LOG": str(project)})
    assert is_denied(output)


def load_check_guard():
    spec = importlib.util.spec_from_file_location("check_guard", CHECK_GUARD)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_adapter_file(folder):
    make_adapter(folder)
    return folder / ".claude" / "orchestrator" / "adapter.json"


def test_check_guard_json_cases_with_multiline_and_tool(tmp_path):
    adapter = write_adapter_file(tmp_path)
    cases = [
        {"expect": "deny", "command": "echo a\np4 submit -c 1"},
        {"expect": "deny", "command": "p4 \\\n submit"},
        {"expect": "deny", "command": "p4 `\n submit", "tool": "PowerShell"},
        {"expect": "allow", "command": "p4 opened"},
    ]
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(cases), encoding="utf-8")
    mismatches, errors, problems = load_check_guard().evaluate(str(adapter), str(path))
    assert (mismatches, errors, problems) == ([], [], [])


def test_check_guard_uses_hook_marker_judgement(tmp_path):
    adapter = write_adapter_file(tmp_path)
    cases = [
        {"expect": "allow", "command": "echo han-mission open m-1"},
        {"expect": "deny", "command": "echo han-mission open m-1 && ls"},
    ]
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(cases), encoding="utf-8")
    assert load_check_guard().evaluate(str(adapter), str(path)) == ([], [], [])


def test_check_guard_reports_bad_tool_and_mismatch(tmp_path):
    adapter = write_adapter_file(tmp_path)
    path = tmp_path / "cases.json"
    cases = [{"expect": "allow", "command": "p4 submit"}, {"expect": "deny", "command": "x", "tool": "Zsh"}]
    path.write_text(json.dumps(cases), encoding="utf-8")
    mismatches, errors, _ = load_check_guard().evaluate(str(adapter), str(path))
    assert [(m[0], m[1]) for m in mismatches] == [("allow", "deny")]
    assert len(errors) == 1


def test_check_guard_tsv_does_not_interpret_backslash_n_and_reports_bad_lines(tmp_path):
    adapter = write_adapter_file(tmp_path)
    path = tmp_path / "cases.tsv"
    path.write_text(
        "# 주석\nallow\tp4 opened\\nsubmit\nallow\tD:\\Work\\new\\p4 opened\nno tab here\n\nbad\tp4 opened\n",
        encoding="utf-8",
    )
    mismatches, errors, _ = load_check_guard().evaluate(str(adapter), str(path))
    assert mismatches == []
    assert len(errors) == 2
    assert "4번째 줄" in errors[0]
    assert "6번째 줄" in errors[1]


def test_check_guard_resolves_registry_adapter_name(tmp_path, monkeypatch):
    registry = make_registry(tmp_path / "registry", [], adapters={"demo": [SUBMIT_RULE]})
    monkeypatch.setenv("HAN_ADAPTERS_DIR", str(registry))
    path = tmp_path / "cases.json"
    path.write_text(json.dumps([{"expect": "deny", "command": "p4 submit"}]), encoding="utf-8")
    assert load_check_guard().evaluate("demo", str(path)) == ([], [], [])


def test_check_guard_cli_exit_code(tmp_path):
    adapter = write_adapter_file(tmp_path)
    path = tmp_path / "cases.json"
    path.write_text(json.dumps([{"expect": "deny", "command": "p4 submit"}]), encoding="utf-8")
    ok = subprocess.run([sys.executable, "-B", str(CHECK_GUARD), str(adapter), str(path)], capture_output=True)
    assert ok.returncode == 0
    assert b"mismatches=0" in ok.stdout
    path.write_text(json.dumps([{"expect": "allow", "command": "p4 submit"}]), encoding="utf-8")
    bad = subprocess.run([sys.executable, "-B", str(CHECK_GUARD), str(adapter), str(path)], capture_output=True)
    assert bad.returncode == 1


def test_check_guard_runtime_flag(tmp_path):
    adapter = write_adapter_file(tmp_path)
    path = tmp_path / "cases.json"
    path.write_text(json.dumps([{"expect": "deny", "command": "p4 submit"}]), encoding="utf-8")
    ok = subprocess.run(
        [sys.executable, "-B", str(CHECK_GUARD), "--runtime", "grok", str(adapter), str(path)],
        capture_output=True,
    )
    assert ok.returncode == 0
    assert b"mismatches=0" in ok.stdout
    bad = subprocess.run([sys.executable, "-B", str(CHECK_GUARD), "--runtime", "nope"], capture_output=True)
    assert bad.returncode == 2
    assert b"ERROR --runtime" in bad.stdout


def test_check_guard_default_cases_file_next_to_adapter(tmp_path):
    adapter = write_adapter_file(tmp_path)
    (adapter.parent / "guard-cases.json").write_text(
        json.dumps([{"expect": "deny", "command": "p4 submit"}]), encoding="utf-8"
    )
    ok = subprocess.run([sys.executable, "-B", str(CHECK_GUARD), str(adapter)], capture_output=True)
    assert ok.returncode == 0


def test_check_guard_doctor_reports_python_resolution():
    result = subprocess.run([sys.executable, "-B", str(CHECK_GUARD), "--doctor"], capture_output=True, timeout=60)
    text = result.stdout.decode("utf-8")
    assert result.returncode in (0, 1)
    assert "python=" in text
    if "NOT FOUND" not in text:
        assert "windowsapps_path=" in text
        assert "stub=" in text


GUARD_OFF_MARKERS = [
    "echo han-mission open m-1",
    "echo han-mission resume m-1",
    "echo han-mission close m-1",
    'echo "han-mission open m-1"',
    "echo han-mission open m-1 && ls",
]


def assert_guard_off_notice(output, reason_part):
    assert not is_denied(output)
    for text in (output["systemMessage"], context_of(output)):
        assert "미션 가드 꺼짐: 어댑터 못 찾음" in text
        assert reason_part in text


@pytest.mark.parametrize("command", GUARD_OFF_MARKERS)
def test_marker_without_adapter_warns_guard_off_when_registry_folder_missing(project, command):
    repo = project / "repo"
    repo.mkdir()
    output = run_guard(repo, command, env={"HAN_ADAPTERS_DIR": str(project / "nope")})
    assert_guard_off_notice(output, "레지스트리 폴더 없음")
    assert not (repo / STATE / "ACTIVE").exists()


def test_marker_without_adapter_warns_when_powershell(project):
    repo = project / "repo"
    repo.mkdir()
    output = run_guard(
        repo, "Write-Host han-mission open m-1", tool=POWERSHELL, env={"HAN_ADAPTERS_DIR": str(project / "nope")}
    )
    assert_guard_off_notice(output, "레지스트리 폴더 없음")


def test_marker_without_adapter_warns_when_projects_json_missing(project):
    registry = project / "registry"
    registry.mkdir()
    repo = project / "repo"
    repo.mkdir()
    output = run_guard(repo, "echo han-mission open m-1", env={"HAN_ADAPTERS_DIR": str(registry)})
    assert_guard_off_notice(output, "projects.json 없음")


def test_marker_without_adapter_warns_when_no_root_matches(project):
    registry = make_registry(project / "registry", [{"adapter": "example", "roots": [str(project / "other")]}])
    repo = project / "repo"
    repo.mkdir()
    output = run_guard(repo, "echo han-mission open m-1", env={"HAN_ADAPTERS_DIR": str(registry)})
    assert_guard_off_notice(output, "맞는 항목 없음")
    assert str(repo) in output["systemMessage"]


def test_marker_without_adapter_warns_when_registry_path_unset(project):
    repo = project / "repo"
    repo.mkdir()
    shim = "import sys,mission_guard as m;m.in_place_adapters_dir=lambda:'" + str(project / "nope").replace("\\", "/")
    shim += "';sys.exit(m.main())"
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "echo han-mission open m-1"},
        "cwd": str(repo),
        "session_id": "s1",
    }
    env = {k: v for k, v in os.environ.items() if k not in CLEARED_ENV}
    env["PYTHONPATH"] = str(HOOKS_DIR)
    result = subprocess.run(
        [sys.executable, "-B", "-c", shim], input=json.dumps(payload).encode(), capture_output=True, env=env, timeout=30
    )
    assert_guard_off_notice(json.loads(result.stdout), "레지스트리 경로 미설정")


def test_marker_without_start_folder_warns_guard_off():
    result = run_raw(json.dumps({"tool_name": "Bash", "tool_input": {"command": "echo han-mission open m-1"}}).encode())
    assert_guard_off_notice(json.loads(result.stdout), "시작 폴더 없음")


@pytest.mark.parametrize("command", ["p4 submit -c 1", "echo hello", "echo han-mission open"])
def test_non_marker_without_adapter_stays_silent(project, command):
    repo = project / "repo"
    repo.mkdir()
    assert run_guard(repo, command, env={"HAN_ADAPTERS_DIR": str(project / "nope")}) == {}


def test_marker_open_success_message_is_what_skill_checks_for(project):
    make_adapter(project)
    output = run_guard(project, "echo han-mission open m-7")
    assert "미션 m-7 가드 켜짐" in output["systemMessage"]
    assert "어댑터 못 찾음" not in output["systemMessage"]


def test_find_adapter_raises_with_reason_and_locate_adapter_stays_none(project, monkeypatch):
    repo = project / "repo"
    repo.mkdir()
    monkeypatch.setenv("HAN_ADAPTERS_DIR", str(project / "nope"))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    with pytest.raises(mission_guard.AdapterNotFound, match="레지스트리 폴더 없음"):
        mission_guard.find_adapter({"cwd": str(repo)})
    assert mission_guard.locate_adapter({"cwd": str(repo)}) is None


def run_check_guard_cli(*args, env=None):
    base = {k: v for k, v in os.environ.items() if k not in CLEARED_ENV}
    base.update(env or {})
    return subprocess.run([sys.executable, "-B", str(CHECK_GUARD), *args], capture_output=True, env=base, timeout=60)


def test_check_guard_registry_folder_missing_is_one_line_error_not_traceback(tmp_path):
    result = run_check_guard_cli("demo", env={"HAN_ADAPTERS_DIR": str(tmp_path / "nope")})
    text = result.stdout.decode("utf-8")
    assert result.returncode == 3
    assert text.startswith("ERROR 읽기 실패: ")
    assert "Traceback" not in result.stderr.decode("utf-8")
    assert len(text.strip().splitlines()) == 1


def test_check_guard_unknown_adapter_name_in_existing_registry(tmp_path):
    registry = make_registry(tmp_path / "registry", [], adapters={"demo": [SUBMIT_RULE]})
    result = run_check_guard_cli("ghost", env={"HAN_ADAPTERS_DIR": str(registry)})
    assert result.returncode == 3
    assert "ghost" in result.stdout.decode("utf-8")


def test_check_guard_registry_unset_is_one_line_error(tmp_path):
    shim = "import sys,mission_guard as m;m.in_place_adapters_dir=lambda:'" + str(tmp_path / "nope").replace("\\", "/")
    shim += "';sys.argv=['check_guard.py','demo'];import runpy;runpy.run_path(r'" + str(CHECK_GUARD)
    shim += "', run_name='__main__')"
    env = {k: v for k, v in os.environ.items() if k not in CLEARED_ENV}
    env["PYTHONPATH"] = str(HOOKS_DIR)
    result = subprocess.run([sys.executable, "-B", "-c", shim], capture_output=True, env=env, timeout=60)
    assert result.returncode == 3
    assert "레지스트리 경로도 없음" in result.stdout.decode("utf-8")
    assert "Traceback" not in result.stderr.decode("utf-8")


def test_check_guard_missing_cases_file_is_one_line_error(tmp_path):
    adapter = write_adapter_file(tmp_path)
    result = run_check_guard_cli(str(adapter), str(tmp_path / "nope.json"))
    assert result.returncode == 3
    assert result.stdout.decode("utf-8").startswith("ERROR 읽기 실패: ")


def test_check_guard_applies_local_json_deep_merge_like_the_hook(tmp_path):
    adapter = write_adapter_file(tmp_path)
    write_json(adapter.parent / "local.json", {"guard": {"deny": [SYNC_RULE]}})
    cases = tmp_path / "cases.json"
    cases.write_text(
        json.dumps([{"expect": "deny", "command": "p4 sync"}, {"expect": "allow", "command": "p4 submit -c 1"}]),
        encoding="utf-8",
    )
    assert load_check_guard().evaluate(str(adapter), str(cases)) == ([], [], [])


def test_check_guard_reports_broken_local_json_as_problem(tmp_path):
    adapter = write_adapter_file(tmp_path)
    (adapter.parent / "local.json").write_text("{broken", encoding="utf-8")
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps([{"expect": "deny", "command": "p4 submit"}]), encoding="utf-8")
    _, _, problems = load_check_guard().evaluate(str(adapter), str(cases))
    assert any("local.json 무시" in problem for problem in problems)
