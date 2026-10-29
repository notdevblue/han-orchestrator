import json
import os
import sys
from types import SimpleNamespace

import pytest

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


def test_doctor_grok_runtime_uses_shared_mission_script(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(doctor, "check_grok", lambda report: report.add("OK", "grok-cli", "synthetic"))
    adapters, main = make_registry(tmp_path)
    active = main / "state" / "ACTIVE"
    active.write_text(json.dumps({"id": "m1", "session": "s"}), encoding="utf-8")
    old = 1_000_000_000
    os.utime(active, (old, old))
    monkeypatch.setenv("HAN_ADAPTERS_DIR", str(adapters))
    report = doctor.run("grok")
    assert levels(report, "registry") == ["OK"]
    assert doctor.MISSION_STATE["grok"] == doctor.MISSION_STATE["claude"]
    doctor.main(["--runtime", "grok"])
    out = capsys.readouterr().out.replace("\\", "/")
    assert 'skills/mission/scripts/mission_state.py" --runtime grok --project' in out
    assert "codex/skills" not in out
    assert active.exists()


def test_doctor_cli_json(tmp_path, monkeypatch, capsys):
    adapters, _ = make_registry(tmp_path, cases=False)
    monkeypatch.setenv("HAN_ADAPTERS_DIR", str(adapters))
    doctor.main(["--json"])
    out = json.loads(capsys.readouterr().out)
    assert any(row["area"] == "guard-cases" and row["level"] == "WARN" for row in out["rows"])


def grok_inspection(trusted=False):
    skill_root = os.path.join(doctor.PLUGIN, "grok", "skills")
    skills = [{"name": name} for name in os.listdir(skill_root)
              if name not in ("han-watch", "han-handoff") and os.path.isfile(os.path.join(skill_root, name, "SKILL.md"))]
    agents = [{"name": f"han:{role}", "source": {"type": "plugin", "path": os.path.join(doctor.PLUGIN, "grok", "agents", f"{role}.md")}}
              for role in doctor.GROK_NATIVE_ROLES]
    return {"projectTrusted": trusted, "skills": skills, "agents": agents,
            "plugins": [{"name": "han", "path": doctor.PLUGIN, "enabled": True, "provides": {"agents": 1}}],
            "hooks": [{"event": "(plugin)", "hookType": "file", "target": "/example/plugins/han/hooks/hooks.json"}]}


def test_grok_doctor_reports_discovery_separately_from_trust(monkeypatch):
    calls = []
    monkeypatch.setattr(doctor.shutil, "which", lambda name: "/example/grok")

    def run(argv, **kwargs):
        calls.append(argv)
        assert kwargs["timeout"] == 10
        if argv[-1] == "--version":
            return SimpleNamespace(returncode=0, stdout="grok 1.0.70")
        return SimpleNamespace(returncode=0, stdout=json.dumps(grok_inspection()))

    monkeypatch.setattr(doctor.subprocess, "run", run)
    report = doctor.Report()
    doctor.check_grok(report)
    assert calls == [["/example/grok", "--version"], ["/example/grok", "inspect", "--json"]]
    assert levels(report, "grok-cli") == ["OK"]
    assert levels(report, "grok-skills") == ["OK"]
    assert levels(report, "grok-agents") == ["OK"]
    assert levels(report, "grok-native") == ["OK"]
    assert levels(report, "grok-hooks") == ["OK"]
    assert levels(report, "grok-trust") == ["WARN"]
    assert any("로드·실행 검증이 아님" in row["text"] for row in report.rows)
    assert any("하위 에이전트 상태는 저장된 metadata·lifecycle 기준" in row["text"] for row in report.rows)


def test_grok_doctor_missing_binary_fails_without_running_it(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: "")
    monkeypatch.setattr(doctor.subprocess, "run", lambda *args, **kwargs: pytest.fail("must not launch missing CLI"))
    report = doctor.Report()
    doctor.check_grok(report)
    assert levels(report, "grok-cli") == ["FAIL"]


@pytest.mark.parametrize("target", ["/example/plugins/han/hooks/hooks.json", r"C:\\example\\plugins\\han\\hooks/hooks.json"])
def test_grok_hook_registration_accepts_native_and_compat_paths(target):
    assert doctor.grok_han_hook({"target": target})
    assert not doctor.grok_han_hook({"target": "/example/plugins/other/hooks/hooks.json"})


@pytest.mark.parametrize("output,code", [("not JSON", 0), ("[]", 0), ("{}", 1)])
def test_grok_doctor_inspection_failures_are_explicit(monkeypatch, output, code):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: "/example/grok")

    def run(argv, **kwargs):
        if argv[-1] == "--version":
            return SimpleNamespace(returncode=0, stdout="grok 1.0.70")
        return SimpleNamespace(returncode=code, stdout=output)

    monkeypatch.setattr(doctor.subprocess, "run", run)
    report = doctor.Report()
    doctor.check_grok(report)
    assert levels(report, "grok-registration") == ["WARN"]
    assert not levels(report, "grok-trust")


def test_grok_doctor_missing_registration_is_a_warning(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: "/example/grok")

    def run(argv, **kwargs):
        if argv[-1] == "--version":
            return SimpleNamespace(returncode=0, stdout="grok 1.0.70")
        return SimpleNamespace(returncode=0, stdout=json.dumps({"projectTrusted": True, "skills": [], "hooks": []}))

    monkeypatch.setattr(doctor.subprocess, "run", run)
    report = doctor.Report()
    doctor.check_grok(report)
    assert levels(report, "grok-skills") == ["WARN"]
    assert levels(report, "grok-agents") == ["WARN"]
    assert levels(report, "grok-native") == ["WARN"]
    assert levels(report, "grok-hooks") == ["WARN"]
    assert levels(report, "grok-trust") == ["OK"]


@pytest.mark.parametrize("change,detail", [
    ("missing", "없음: han:reviewer"),
    ("wrong-name", "예상 밖 역할: han:other"),
    ("compat-source", "원본 경로 불일치·중복: han:reviewer"),
    ("missing-source", "원본 경로 불일치·중복: han:reviewer"),
    ("duplicate", "원본 경로 불일치·중복: han:reviewer"),
])
def test_grok_doctor_reports_missing_or_wrong_native_roles(change, detail):
    inspection = grok_inspection()
    reviewer = inspection["agents"][-1]
    if change == "missing":
        inspection["agents"].pop()
    elif change == "wrong-name":
        reviewer["name"] = "han:other"
    elif change == "compat-source":
        reviewer["source"]["path"] = os.path.join(doctor.PLUGIN, "agents", "reviewer.md")
    elif change == "missing-source":
        reviewer.pop("source")
    else:
        inspection["agents"].append(dict(reviewer))
    report = doctor.Report()
    doctor.check_grok_agents(report, inspection)
    assert levels(report, "grok-agents") == ["WARN"]
    row = next(row for row in report.rows if row["area"] == "grok-agents")
    assert detail in row["text"]
    assert "4/5개" in row["text"]


@pytest.mark.parametrize("change", ["wrong-path", "disabled", "missing", "duplicate"])
def test_grok_doctor_rejects_inactive_or_wrong_selected_plugin(change):
    inspection = grok_inspection()
    plugin = inspection["plugins"][0]
    if change == "wrong-path":
        plugin["path"] = os.path.join(doctor.PLUGIN, "other")
    elif change == "disabled":
        plugin["enabled"] = False
    elif change == "missing":
        inspection.pop("plugins")
    else:
        inspection["plugins"].append(dict(plugin))
    report = doctor.Report()
    doctor.check_grok_agents(report, inspection)
    assert levels(report, "grok-native") == ["WARN"]


def test_grok_doctor_does_not_use_registration_directory_count_as_role_count():
    inspection = grok_inspection()
    inspection["agents"].append({"name": "other:researcher", "source": {"type": "plugin", "path": "/other/researcher.md"}})
    report = doctor.Report()
    doctor.check_grok_agents(report, inspection)
    assert levels(report, "grok-agents") == ["OK"]
    assert levels(report, "grok-native") == ["OK"]
    assert any("파견·모델·effort 실행 검증이 아님" in row["text"] for row in report.rows)
