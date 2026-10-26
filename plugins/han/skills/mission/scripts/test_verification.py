import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import mission_state
import verification


@pytest.fixture
def mission(tmp_path, monkeypatch):
    root = tmp_path / "project"
    adapter = root / ".claude/orchestrator"
    adapter.mkdir(parents=True)
    (adapter / "adapter.json").write_text(json.dumps({
        "schema": 1, "paths": {"stateDir": "state"}, "verify": {"grades": ["pytest-pass", "editmode-pass"]},
        "guard": {"deny": [{"pattern": "\\bsubmit\\b", "reason": "submit 금지"}]},
    }), encoding="utf-8")
    state = root / "state"
    state.mkdir()
    (state / "ACTIVE").write_text('{"id":"m1","session":"s1"}', encoding="utf-8")
    monkeypatch.chdir(root)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.setenv("HAN_ADAPTERS_DIR", str(tmp_path / "none"))
    assert mission_state.main(["init"]) == 0
    assert mission_state.main([
        "unit", "add", "M1", "--goal", "계약", "--files", "src.py", "--done", "동작 보존",
        "--verify", "verify", "--timebox", "60", "--forbid", "submit",
    ]) == 0
    assert mission_state.main(["unit", "set", "M1", "--status", "doing"]) == 0
    (root / "src.py").write_text("value = 1\n", encoding="utf-8")
    script = root / "check.py"
    script.write_text("from pathlib import Path\np=Path('runs')\np.write_text(p.read_text()+'x' if p.exists() else 'x')\n", encoding="utf-8")
    plan = {
        "grade": "pytest-pass", "inputs": ["src.py", "check.py"], "environment": {"target": "test"},
        "commands": [{"argv": [sys.executable, str(script)], "timeoutSeconds": 10}],
    }
    (root / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    return root, state / "m1", plan


def verify(mission):
    root, folder, _ = mission
    code = mission_state.main(["verify", "M1", "--plan", str(root / "plan.json")])
    path = Path(json.loads((folder / "verification/M1.latest.json").read_text())["receipt"])
    return code, path


def review(mission, receipt, status="clean"):
    root, folder, _ = mission
    evidence = root / "review.md"
    evidence.write_text("독립 리뷰 결과\n", encoding="utf-8")
    assert mission_state.main([
        "review", "M1", "--receipt", str(receipt), "--evidence", str(evidence),
        "--reviewer", "fresh-reviewer", "--kind", "unit", "--status", status,
    ]) == 0
    return Path(json.loads((folder / "verification/M1.review.latest.json").read_text())["review"])


def test_review_counts_match_status_and_preserve_unknown_legacy_values(mission):
    root, folder, _ = mission
    _, receipt = verify(mission)
    previous = review(mission, receipt)
    assert json.loads(previous.read_text())['findingsCount'] == -1
    args = ['review', 'M1', '--receipt', str(receipt), '--evidence', str(root / 'review.md'),
            '--reviewer', 'fresh-reviewer', '--kind', 'unit', '--status', 'clean',
            '--findings-count', '0', '--fixed-count', '2']
    assert mission_state.main(args) == 0
    path = Path(json.loads((folder / 'verification/M1.review.latest.json').read_text())['review'])
    record = json.loads(path.read_text())
    assert record['findingsCount'] == 0 and record['fixedCount'] == 2 and record['recordedAt']
    args[args.index('--findings-count') + 1] = '1'
    assert mission_state.main(args) == 1
    assert json.loads((folder / 'verification/M1.review.latest.json').read_text())['review'] == str(path)


def test_unchanged_verification_is_reused_and_source_or_environment_change_runs_again(mission):
    root, _, plan = mission
    first_code, first = verify(mission)
    assert first_code == 0 and (root / "runs").read_text() == "x"
    assert verify(mission) == (0, first)
    assert (root / "runs").read_text() == "x"
    (root / "src.py").write_text("value = 2\n")
    assert verify(mission)[1] != first
    assert (root / "runs").read_text() == "xx"
    plan["environment"]["target"] = "other"
    (root / "plan.json").write_text(json.dumps(plan))
    verify(mission)
    assert (root / "runs").read_text() == "xxx"


def test_complete_requires_clean_review_and_current_verification_and_is_idempotent(mission):
    root, folder, _ = mission
    _, receipt = verify(mission)
    findings = review(mission, receipt, "findings")
    command = ["complete", "M1", "--receipt", str(receipt), "--review", str(findings), "--done-evidence", "動作確認"]
    before = (folder / "ledger.tsv").read_bytes()
    assert mission_state.main(command) == 1
    assert (folder / "ledger.tsv").read_bytes() == before
    clean = review(mission, receipt)
    command[command.index(str(findings))] = str(clean)
    assert mission_state.main(command) == 0
    lines = (folder / "ledger.tsv").read_text().splitlines()
    assert len(lines) == 2 and "\tpass\tpytest-pass\t" in lines[-1]
    assert (folder / "units.tsv").read_text().rstrip().endswith("\tdone")
    assert mission_state.main(command) == 0
    assert (folder / "ledger.tsv").read_text().splitlines() == lines
    (root / "src.py").write_text("value = 9\n")
    assert mission_state.main(command) == 1


def test_failure_is_not_cached_and_guard_rejection_never_runs_command(mission):
    root, _, plan = mission
    plan["commands"][0]["argv"] = [sys.executable, "-c", "raise SystemExit(7)"]
    (root / "plan.json").write_text(json.dumps(plan))
    first_code, first = verify(mission)
    assert first_code == 1 and json.loads(first.read_text())["exitCodes"] == [7]
    assert verify(mission)[1] != first
    plan["commands"][0]["argv"] = [sys.executable, "-c", "from pathlib import Path; Path('runs').write_text('BAD')", "submit"]
    (root / "plan.json").write_text(json.dumps(plan))
    assert mission_state.main(["verify", "M1", "--plan", str(root / "plan.json")]) == 1
    assert not (root / "runs").exists()


@pytest.mark.parametrize("runtime", ["claude", "codex"])
@pytest.mark.parametrize("latest_status", ["findings", "clean"])
def test_complete_rejects_superseded_clean_review(mission, runtime, latest_status):
    _, folder, _ = mission
    _, receipt = verify(mission)
    old_clean = review(mission, receipt)
    latest = review(mission, receipt, latest_status)
    command = [
        "complete", "M1", "--receipt", str(receipt), "--review", str(old_clean),
        "--done-evidence", "checked",
    ]
    before = {name: (folder / name).read_bytes() for name in ("units.tsv", "ledger.tsv")}
    assert mission_state.main(command, runtime=runtime) == 1
    assert all((folder / name).read_bytes() == data for name, data in before.items())

    command[command.index("--review") + 1] = str(latest)
    if latest_status == "findings":
        assert mission_state.main(command, runtime=runtime) == 1
        assert all((folder / name).read_bytes() == data for name, data in before.items())
        command[command.index("--review") + 1] = str(review(mission, receipt))
    assert mission_state.main(command, runtime=runtime) == 0
    assert (folder / "units.tsv").read_text().rstrip().endswith("\tdone")


def test_complete_requires_latest_review_pointer(mission):
    _, folder, _ = mission
    _, receipt = verify(mission)
    clean = review(mission, receipt)
    (folder / "verification/M1.review.latest.json").unlink()
    before = {name: (folder / name).read_bytes() for name in ("units.tsv", "ledger.tsv")}
    assert mission_state.main([
        "complete", "M1", "--receipt", str(receipt), "--review", str(clean),
        "--done-evidence", "checked",
    ]) == 1
    assert all((folder / name).read_bytes() == data for name, data in before.items())


def test_editmode_rejects_zero_tests_wrong_project_and_stale_result(mission):
    root, _, plan = mission
    plan["grade"] = "editmode-pass"
    plan["results"] = [{
        "path": "result.json", "equals": {"status": "passed"}, "positive": ["tests.0.startedCount"],
        "projectField": "projectPath",
    }]
    for project, count, expected in [(str(root), 0, 1), (str(root.parent), 2, 1), (str(root), 2, 0)]:
        payload = {"status": "passed", "projectPath": project, "tests": [{"startedCount": count}]}
        plan["commands"][0]["argv"] = [sys.executable, "-c", f"from pathlib import Path; Path('result.json').write_text({json.dumps(payload)!r})"]
        (root / "plan.json").write_text(json.dumps(plan))
        assert verify(mission)[0] == expected
    plan["commands"][0]["argv"] = [sys.executable, "-c", "pass"]
    (root / "plan.json").write_text(json.dumps(plan))
    assert verify(mission)[0] == 1


def test_changed_during_run_and_tampered_results_are_rejected(mission):
    root, folder, plan = mission
    _, receipt = verify(mission)
    data = json.loads(receipt.read_text())
    Path(data["log"]).write_text("changed")
    with pytest.raises(verification.VerificationError, match="로그"):
        verification.validate_receipt(str(root), str(folder), "M1", data)
    plan["commands"][0]["argv"] = [sys.executable, "-c", "from pathlib import Path; Path('src.py').write_text('changed')"]
    (root / "plan.json").write_text(json.dumps(plan))
    assert verify(mission)[0] == 1


def test_review_scope_reports_additions_and_deletions(mission):
    root, folder, plan = mission
    source = root / "src"
    source.mkdir()
    (source / "a.py").write_text("a")
    plan["inputs"] = ["src"]
    (root / "plan.json").write_text(json.dumps(plan))
    _, receipt = verify(mission)
    review(mission, receipt)
    plan["unitBrief"] = json.loads(receipt.read_text())["plan"]["unitBrief"]
    assert verification.review_scope(str(root), str(folder), "M1", plan) == []
    (source / "b.py").write_text("b")
    (source / "a.py").unlink()
    assert verification.review_scope(str(root), str(folder), "M1", plan) == ["src/a.py", "src/b.py"]


def test_context_is_bounded_and_keeps_history_outside_prompt(mission, capsys):
    _, folder, _ = mission
    for index in range(20):
        assert mission_state.main([
            "decision", "add", "--what", f"decision {index}", "--chosen", "x" * 500,
            "--why", "reason", "--affects", "M1",
        ]) == 0
    capsys.readouterr()
    assert mission_state.main(["context"]) == 0
    output = capsys.readouterr().out
    assert len(output) < 2200 and "D20" in output and "D1:" not in output
    assert str(folder / "mission.md") in output
    assert (folder / "decisions.tsv").stat().st_size > 10000


def test_codex_entrypoint_uses_same_receipts(mission):
    root, _, _ = mission
    assert mission_state.main(["verify", "M1", "--plan", str(root / "plan.json")], runtime="codex") == 0
    assert (root / "runs").read_text() == "x"
    assert verify(mission)[0] == 0 and (root / "runs").read_text() == "x"


def test_inputs_outside_project_are_rejected(mission):
    root, _, plan = mission
    plan["inputs"] = ["../secret"]
    (root / "plan.json").write_text(json.dumps(plan))
    assert mission_state.main(["verify", "M1", "--plan", str(root / "plan.json")]) == 1
    assert not (root / "runs").exists()


def test_complete_rejects_changed_contract_and_review_evidence(mission):
    root, _, _ = mission
    _, receipt = verify(mission)
    checked = review(mission, receipt)
    command = ["complete", "M1", "--receipt", str(receipt), "--review", str(checked), "--done-evidence", "checked"]
    (root / "review.md").write_text("tampered")
    assert mission_state.main(command) == 1
    checked = review(mission, receipt)
    command[command.index("--review") + 1] = str(checked)
    assert mission_state.main(["unit", "set", "M1", "--done", "새 완료 조건"]) == 0
    assert mission_state.main(command) == 1


def test_later_failed_run_cannot_be_hidden_by_an_older_pass(mission):
    root, _, plan = mission
    _, receipt = verify(mission)
    checked = review(mission, receipt)
    plan["commands"][0]["argv"] = [sys.executable, "-c", "raise SystemExit(7)"]
    (root / "plan.json").write_text(json.dumps(plan))
    assert verify(mission)[0] == 1
    assert mission_state.main([
        "complete", "M1", "--receipt", str(receipt), "--review", str(checked), "--done-evidence", "old pass",
    ]) == 1
