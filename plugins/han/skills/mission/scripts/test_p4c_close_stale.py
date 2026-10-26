import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mission_state  # noqa: E402
import p4c  # noqa: E402

DAY = 24 * 3600
ADAPTER = {"schema": 1, "paths": {"stateDir": "state"}, "stages": [{"id": "impl"}]}
MISSION_MD = "# m1\n\n상태: 진행\n현재 단계: impl\n\n## 목표\nx\n"


@pytest.fixture
def project(tmp_path, monkeypatch):
    for name in ("CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_OPTION_ADAPTERS_DIR", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "HAN_STALE_HOURS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HAN_ADAPTERS_DIR", str(tmp_path / "no-registry"))
    root = tmp_path / "project"
    (root / ".claude" / "orchestrator").mkdir(parents=True)
    (root / ".claude" / "orchestrator" / "adapter.json").write_text(json.dumps(ADAPTER), encoding="utf-8")
    folder = root / "state" / "m1"
    folder.mkdir(parents=True)
    (folder / "mission.md").write_text(MISSION_MD, encoding="utf-8")
    (root / "state" / "ACTIVE").write_text(
        json.dumps({"id": "m1", "session": "s1", "openedAt": "2026-10-19T09:00:00+09:00"}), encoding="utf-8"
    )
    return root


def age(root, seconds):
    moment = time.time() - seconds
    for path in [root / "state" / "ACTIVE", *(root / "state" / "m1").rglob("*")]:
        os.utime(path, (moment, moment))


def run(capsys, root, *args):
    capsys.readouterr()
    code = mission_state.main(["--project", str(root), *args])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_active_reports_live_and_stale(project, capsys):
    code, out, _ = run(capsys, project, "active")
    assert code == 0 and "ACTIVE m1" in out and "살아 있음" in out
    age(project, 2 * DAY)
    code, out, _ = run(capsys, project, "active")
    assert code == 0 and "끊김 의심" in out


def test_active_without_active_file(project, capsys):
    (project / "state" / "ACTIVE").unlink()
    code, out, _ = run(capsys, project, "active")
    assert code == 0 and "ACTIVE 없음" in out


def test_close_stale_refuses_live_mission(project, capsys):
    code, _, err = run(capsys, project, "close-stale", "--force-close-stale")
    assert code == 1 and "끊김 아님" in err
    assert (project / "state" / "ACTIVE").exists()


def test_close_stale_preview_without_flag(project, capsys):
    age(project, 2 * DAY)
    code, out, _ = run(capsys, project, "close-stale")
    assert code == 1 and "미리보기" in out
    assert (project / "state" / "ACTIVE").exists()
    assert "상태: 진행" in (project / "state" / "m1" / "mission.md").read_text(encoding="utf-8")


def test_close_stale_with_flag(project, capsys):
    age(project, 2 * DAY)
    code, out, _ = run(capsys, project, "close-stale", "--force-close-stale", "--reason", "좀비")
    assert code == 0, out
    assert not (project / "state" / "ACTIVE").exists()
    folder = project / "state" / "m1"
    assert "상태: 중단" in (folder / "mission.md").read_text(encoding="utf-8")
    log = (folder / "force-close.log").read_text(encoding="utf-8")
    assert "session=s1" in log and "reason=좀비" in log


def test_close_stale_keep_state_and_hours(project, capsys):
    age(project, 3 * 3600)
    code, _, _ = run(capsys, project, "close-stale", "--force-close-stale")
    assert code == 1
    code, _, _ = run(capsys, project, "close-stale", "--force-close-stale", "--hours", "2", "--keep-state")
    assert code == 0
    assert "상태: 진행" in (project / "state" / "m1" / "mission.md").read_text(encoding="utf-8")


def test_p4c_dry_run_pins_client(project, capsys):
    (project / ".p4config").write_text("P4CLIENT=ws_cfg\n", encoding="utf-8")
    assert p4c.main(["--project", str(project), "--which"]) == 0
    assert capsys.readouterr().out.strip() == "ws_cfg"
    assert p4c.main(["--project", str(project), "--dry-run", "edit", "-c", "12", "a.cs"]) == 0
    assert capsys.readouterr().out.strip() == "p4 -c ws_cfg edit -c 12 a.cs"


def test_p4c_refuses_mismatch_and_double_client(project, capsys):
    (project / ".p4config").write_text("P4CLIENT=ws_cfg\n", encoding="utf-8")
    assert p4c.main(["--project", str(project), "--client", "ws_other", "sync"]) == 2
    assert p4c.main(["--project", str(project), "-c", "ws_cfg", "sync"]) == 2
    assert p4c.main(["--project", str(project), "-cws_cfg", "sync"]) == 2


def test_p4c_without_client_fails(project, capsys):
    assert p4c.main(["--project", str(project), "sync"]) == 2
    assert "작업 공간" in capsys.readouterr().err
