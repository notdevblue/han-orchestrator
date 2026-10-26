import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import doctor  # noqa: E402


def make_registry(tmp_path, cases=True, unity=None):
    adapters = tmp_path / "adapters"
    project_adapter = adapters / "t"
    project_adapter.mkdir(parents=True)
    main = tmp_path / "main"
    (main / "state").mkdir(parents=True)
    adapter = {"schema": 1, "project": "T", "paths": {"vcsRoot": ".", "stateDir": "state"},
               "guard": {"deny": [{"pattern": "^p4 submit", "reason": "no"}]}}
    if unity:
        adapter["vars"] = {"unityExe": unity}
    (project_adapter / "adapter.json").write_text(json.dumps(adapter), encoding="utf-8")
    if cases:
        (project_adapter / "guard-cases.json").write_text(json.dumps([{"expect": "deny", "command": "p4 submit"}]), encoding="utf-8")
    (adapters / "projects.json").write_text(json.dumps([{"adapter": "t", "roots": [str(main)]}]), encoding="utf-8")
    return adapters, main


def levels(report, area):
    return [row["level"] for row in report.rows if row["area"] == area]


def test_doctor_reports_registry_adapter_cases_and_stale_active(tmp_path, monkeypatch):
    adapters, main = make_registry(tmp_path, unity=str(tmp_path / "missing.exe"))
    active = main / "state" / "ACTIVE"
    active.write_text(json.dumps({"id": "m1", "session": "s"}), encoding="utf-8")
    old = 1_000_000_000
    os.utime(active, (old, old))
    monkeypatch.setenv("HAN_ADAPTERS_DIR", str(adapters))
    report = doctor.run("claude")
    assert levels(report, "registry") == ["OK"]
    assert levels(report, "guard-cases") == ["OK"]
    assert levels(report, "unity") == ["FAIL"]
    stale = [row["text"] for row in report.rows if row["area"] == "active"]
    assert stale and "close-stale" in stale[0] and "--force-close-stale" in stale[0]
    assert active.exists()  # 읽기 전용


def test_doctor_without_registry_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("HAN_ADAPTERS_DIR", str(tmp_path / "none"))
    report = doctor.run("codex")
    assert levels(report, "registry") == ["FAIL"]


def test_doctor_cli_json(tmp_path, monkeypatch, capsys):
    adapters, _ = make_registry(tmp_path, cases=False)
    monkeypatch.setenv("HAN_ADAPTERS_DIR", str(adapters))
    doctor.main(["--json"])
    out = json.loads(capsys.readouterr().out)
    assert any(row["area"] == "guard-cases" and row["level"] == "WARN" for row in out["rows"])
