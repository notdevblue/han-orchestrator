import json
import os
import sys
import time
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent
sys.path.insert(0, str(HOOKS_DIR))

import mission_guard  # noqa: E402

DAY = 24 * 3600
P4_ADAPTER = {
    "schema": 1,
    "paths": {"stateDir": "state"},
    "guard": {"deny": [{"pattern": r"(?:^|\s)submit(?:\s|$)", "reason": "submit 금지"}], "p4Client": {"require": True}},
    "slots": {"root": "D:/Work/slots", "client": "ws_{name}", "mainClient": "ws_main"},
    "vars": {"main": "D:/Work/main"},
}


def write_active(state, mission_id="m-1", session="s1", age=0.0):
    state.mkdir(parents=True, exist_ok=True)
    path = state / "ACTIVE"
    path.write_text(json.dumps({"id": mission_id, "session": session, "openedAt": "2026-10-15T09:00:00+09:00"}), encoding="utf-8")
    moment = time.time() - age
    os.utime(path, (moment, moment))
    return path


def age_folder(folder, age):
    moment = time.time() - age
    for path in folder.rglob("*"):
        os.utime(path, (moment, moment))


def test_stale_hours_precedence(monkeypatch):
    monkeypatch.delenv(mission_guard.STALE_ENV, raising=False)
    assert mission_guard.stale_hours_of({}) == 24
    assert mission_guard.stale_hours_of({"mission": {"staleHours": 6}}) == 6
    assert mission_guard.stale_hours_of({"mission": {"staleHours": "x"}}) == 24
    monkeypatch.setenv(mission_guard.STALE_ENV, "0")
    assert mission_guard.stale_hours_of({"mission": {"staleHours": 6}}) == 0


def test_staleness_uses_newest_activity(tmp_path):
    state = tmp_path / "state"
    write_active(state, age=3 * DAY)
    folder = state / "m-1"
    folder.mkdir()
    (folder / "mission.md").write_text("x", encoding="utf-8")
    age_folder(folder, 3 * DAY)
    active = mission_guard.read_active(str(state))
    result = mission_guard.staleness(str(state), active, 24)
    assert result.stale and 71 < result.hours < 73
    assert "끊김 의심" in mission_guard.stale_text(result)
    (folder / "note.md").write_text("fresh", encoding="utf-8")
    assert not mission_guard.staleness(str(state), active, 24).stale
    assert not mission_guard.staleness(str(state), active, 0).stale


def test_heartbeat_touches_only_own_session_and_never_resurrects(tmp_path):
    state = tmp_path / "state"
    path = write_active(state, age=DAY)
    before = path.stat().st_mtime
    mission_guard.heartbeat(str(state), {"session_id": "other"})
    assert path.stat().st_mtime == before
    mission_guard.heartbeat(str(state), {"session_id": "s1"})
    assert path.stat().st_mtime > before + DAY / 2
    assert sorted(os.listdir(state)) == ["ACTIVE"]
    path.unlink()
    mission_guard.heartbeat(str(state), {"session_id": "s1"})
    assert not path.exists()


def test_open_denied_by_stale_active_mentions_force_close(tmp_path):
    state = tmp_path / "state"
    write_active(state, age=3 * DAY)
    marker = mission_guard.MARKER.match("echo han-mission open m-2")
    outcome = mission_guard.Outcome()
    mission_guard.handle_marker(marker, str(state), "s2", outcome, stale_hours=24)
    assert "다른 미션" in outcome.deny and "끊김 의심" in outcome.deny and "--force-close-stale" in outcome.deny
    assert mission_guard.read_active(str(state)).id == "m-1"


def test_open_denied_by_live_active_has_no_stale_note(tmp_path):
    state = tmp_path / "state"
    write_active(state)
    marker = mission_guard.MARKER.match("echo han-mission open m-2")
    outcome = mission_guard.Outcome()
    mission_guard.handle_marker(marker, str(state), "s2", outcome, stale_hours=24)
    assert outcome.deny and "끊김" not in outcome.deny


@pytest.mark.parametrize(
    "project, expected",
    [("D:/Work/slots/slot2", "ws_slot2"), ("D:/Work/main", "ws_main"), ("D:/Elsewhere", "")],
)
def test_expected_p4_client(project, expected):
    assert mission_guard.expected_p4_client(project, P4_ADAPTER) == expected


def test_expected_p4_client_from_p4config(tmp_path):
    (tmp_path / ".p4config").write_text("P4PORT=x:1666\nP4CLIENT=ws_cfg\n", encoding="utf-8")
    assert mission_guard.expected_p4_client(str(tmp_path), {}) == "ws_cfg"


def reason(command, project="D:/Work/slots/slot2", tool="Bash", adapter=P4_ADAPTER):
    return mission_guard.judge_command(adapter, command, tool, [], project).reason


@pytest.mark.parametrize(
    "command",
    [
        "p4 sync",
        "p4 edit -c 1 a.cs",
        "p4 -x list.txt add",
        "C:\\Perforce\\p4.exe reconcile -c 1 a",
        "p4 -c ws_slot3 edit -c 1 a.cs",
        "p4 -c ws_main sync",
        "python p4c.py --client ws_slot1 sync",
        "python p4c.py submit -c 1",
    ],
)
def test_p4_writes_need_matching_client(command):
    assert reason(command) != "", command


@pytest.mark.parametrize(
    "command",
    [
        "p4 -c ws_slot2 sync",
        "p4 -cws_slot2 edit -c 1 a.cs",
        "p4 -c WS_SLOT2 edit -c 1 a.cs",
        "p4 -c $WS edit -c 1 a.cs",
        "p4 opened -c 1",
        "p4 changes -s pending",
        "p4 change -o",
        "p4 info",
        "python p4c.py edit -c 1 a.cs",
        'python "D:/x y/p4c.py" --project D:/Work/slots/slot2 sync',
    ],
)
def test_p4_reads_and_pinned_writes_pass(command):
    assert reason(command) == "", command


def test_p4_policy_off_by_default():
    adapter = {key: value for key, value in P4_ADAPTER.items() if key != "guard"}
    assert reason("p4 sync", adapter=adapter) == ""


def test_p4_policy_custom_commands():
    adapter = {**P4_ADAPTER, "guard": {"p4Client": {"require": True, "commands": ["submit"]}}}
    assert reason("p4 sync", adapter=adapter) == ""
    assert reason("p4 submit", adapter=adapter) != ""


def test_p4c_call_is_checked_as_p4():
    assert mission_guard.p4c_as_p4("python p4c.py --which", "D:/Work/slots/slot2", P4_ADAPTER) == "p4 -c ws_slot2"
    assert mission_guard.p4c_as_p4("py -3 C:/h/p4c.py --client w edit a", "", {}) == "p4 -c w edit a"
    assert mission_guard.p4c_as_p4("python other.py edit", "", {}) == ""
