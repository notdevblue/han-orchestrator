import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import slots  # noqa: E402

TICK_HELPER = """\
import os, sys, time
ticks, label, pause = sys.argv[1], sys.argv[2], float(sys.argv[3])
os.makedirs(ticks, exist_ok=True)
fail_name = sys.argv[4] if len(sys.argv) > 4 else ""
exit_code = int(sys.argv[5]) if len(sys.argv) > 5 else 1
def mark(kind):
    with open(os.path.join(ticks, label), "a") as f:
        f.write(f"{label} {kind} {time.time()}\\n")
mark("start")
time.sleep(pause)
mark("end")
sys.exit(exit_code if fail_name and fail_name in label else 0)
"""

SNAPSHOT_HELPER = """\
import os, shutil, sys
os.makedirs(os.path.dirname(sys.argv[2]), exist_ok=True)
shutil.copy(sys.argv[1], sys.argv[2])
"""

FAKE_P4 = """\
import json, os, sys
path = os.environ["FAKE_P4_STATE"]
state = json.load(open(path)) if os.path.exists(path) else {}
state.setdefault("clients", ["Main"])
state.setdefault("specs", {})
state.setdefault("opened", {})
state.setdefault("shelved", {})
state.setdefault("pending", {})
state.setdefault("calls", [])
args = sys.argv[1:]
exit_code = 0
if args[0] == "info":
    configured = ""
    config_path = os.path.join(os.getcwd(), ".p4config")
    if os.path.exists(config_path):
        for line in open(config_path):
            if line.startswith("P4CLIENT="):
                configured = line.split("=", 1)[1].strip()
    print("User name: u")
    print("Client name: " + state.get("info_client", configured or "fake_host"))
    sys.exit(0)
assert args[0] == "-c", args
client, rest = args[1], args[2:]
stdin_files = []
if rest[:2] == ["-x", "-"]:
    stdin_files = [line for line in sys.stdin.read().split("\\n") if line]
    rest = rest[2:]
state["calls"].append(args[:2] + rest)
SPEC = "Client:\\t{c}\\n\\nOwner:\\tu\\n\\nRoot:\\t/old\\n\\nOptions:\\t{o}\\n\\nLineEnd:\\t{l}\\n\\nSubmitOptions:\\t{s}\\n\\nStream:\\t//Depot/Main\\n\\nDescription:\\n\\tCreated by u.\\n\\nView:\\n\\t//Depot/Main/... //{c}/...\\n"
if rest[:2] == ["clients", "-e"]:
    if rest[2] in state["clients"]:
        print(f"Client {rest[2]} 2026/10/15 root /x 'd'")
elif rest[:2] == ["client", "-o"] and rest[2] == "-S":
    sys.stdout.write(SPEC.format(c=rest[4], o="allwrite noclobber", l="unix", s="submitunchanged"))
elif rest[:2] == ["client", "-o"] and rest[2] in state["specs"]:
    sys.stdout.write(state["specs"][rest[2]])
elif rest[:2] == ["client", "-o"]:
    sys.stdout.write(SPEC.format(c=rest[2], o="noallwrite clobber nocompress unlocked modtime rmdir", l="local", s="revertunchanged"))
elif rest == ["client", "-i"]:
    state["specs"][client] = sys.stdin.read()
    state["clients"].append(client)
elif rest[0] == "opened":
    print("\\n".join(state["opened"].get(client, [])))
elif rest[:2] == ["changes", "-m1"] and rest[2].endswith("#have"):
    print(state.setdefault("have", "Change 12345 on 2026/10/15 by u@Main 'd'"))
elif rest[:2] == ["changes", "-m1"]:
    print(state.setdefault("head", "Change 12345 on 2026/10/15 by u@Main 'd'"))
elif rest[:3] == ["changes", "-s", "pending"] and "-l" in rest:
    for cl in state.get("pending_cls", []):
        if cl["client"] == client:
            print("Change " + str(cl["id"]) + " on 2026/10/15 by u@" + client + " *pending*\\n\\n\\t" + cl["desc"] + "\\n")
elif rest == ["change", "-i"]:
    spec = sys.stdin.read()
    cls = state.setdefault("pending_cls", [])
    cls.append({"id": 9000 + len(cls), "client": client, "desc": spec.split("Description:\\n\\t", 1)[1].strip()})
    print("Change " + str(cls[-1]["id"]) + " created.")
elif rest[:2] == ["changes", "-s"]:
    print("\\n".join(state[rest[2]].get(client, [])))
elif rest[:2] == ["resolve", "-am"]:
    pass
elif rest[:2] == ["resolve", "-n"]:
    lines = state.get("conflicts", {}).get(client, [])
    if lines:
        print("\\n".join(lines))
    else:
        sys.stderr.write("No file(s) to resolve.\\n")
elif rest[:2] == ["client", "-d"]:
    state["clients"].remove(rest[2])
    state["specs"].pop(rest[2], None)
elif rest[0] == "sync" and "-n" in rest:
    print("\\n".join(state.get("sync_pending", [])))
elif rest[0] == "sync":
    if state.get("sync_fails"):
        sys.exit("sync failed")
    if client == "fake_Main":
        state["have"] = state["head"]
        state["sync_pending"] = []
        state.setdefault("main_synced", []).append(rest[-1])
elif rest[0] == "reconcile":
    items = state.get("reconcile", {}).get(client, [])
    lines = [item["line"] for item in items if "-e" not in rest or item["kind"] == "edit"]
    if stdin_files and "-n" in rest:
        state.setdefault("reconcile_inputs", []).append(stdin_files)
        lines = [line for line in lines if line.split("#")[0] in stdin_files]
        if not lines:
            exit_code = 1
    if "-n" not in rest:
        changelist = rest[rest.index("-c") + 1]
        state.setdefault("reconciled", []).append({"cl": changelist, "files": stdin_files})
        for item in items:
            item_path = item["line"].split("#")[0]
            if item_path in stdin_files:
                state["opened"].setdefault(client, []).append(item_path + "#2 - edit change " + changelist + " (text)")
        state["reconcile"][client] = [item for item in items if item["line"].split("#")[0] not in stdin_files]
    elif lines:
        print("\\n".join(lines))
    else:
        sys.stderr.write("//c/... - no file(s) to reconcile.\\n")
else:
    sys.exit("unsupported: " + " ".join(rest))
json.dump(state, open(path, "w"))
sys.exit(exit_code)
"""

SLOT_SPEC = "Client:\tfake_{name}\n\nRoot:\t{root}\n\nDescription:\n\t{description}\n"


class Project:
    def __init__(self, tmp_path, capsys, monkeypatch):
        self.dir = tmp_path / "proj"
        self.slot_root = tmp_path / "slot_root"
        self.helper = tmp_path / "tick.py"
        self.snapshot = tmp_path / "snapshot.py"
        self.ticks = tmp_path / "ticks"
        self.fake_p4 = tmp_path / "fake_p4.py"
        self.p4_state = tmp_path / "p4_state.json"
        self.capsys = capsys
        self.dir.mkdir()
        self.helper.write_text(TICK_HELPER)
        self.snapshot.write_text(SNAPSHOT_HELPER)
        self.fake_p4.write_text(FAKE_P4)
        monkeypatch.setenv("FAKE_P4_STATE", str(self.p4_state))
        monkeypatch.setenv("HAN_ADAPTERS_DIR", str(tmp_path / "adapters"))
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        self.write_adapter([])

    @property
    def state_dir(self):
        return self.dir / "_My" / "missions"

    @property
    def rows_path(self):
        return str(self.state_dir / "slots.tsv")

    def lock_path(self, name):
        return self.state_dir / "slots" / f"{name}.lock"

    def tick_step(self, step_id, group, pause=0.0, **extra):
        command = f'"{{py}}" "{{helper}}" "{{ticks}}" {{name}}-{step_id} {pause} {extra.pop("args", "")}'
        return {"id": step_id, "group": group, "run": command, **extra}

    def write_adapter(self, prepare, **slot_extra):
        folder = self.dir / ".claude" / "orchestrator"
        folder.mkdir(parents=True, exist_ok=True)
        adapter = {
            "schema": 1,
            "project": "FAKE",
            "paths": {"vcsRoot": ".", "stateDir": "_My/missions"},
            "slots": {
                "enabled": True,
                "names": ["a", "b", "x", "y", "bad", "good"],
                "client": "fake_{name}",
                "prepare": prepare,
                "refresh": [step["id"] for step in prepare[:1] if isinstance(step, dict) and "id" in step],
                "editor": '"{py}" -c "print(1)"',
                **slot_extra,
            },
        }
        (folder / "adapter.json").write_text(json.dumps(adapter), encoding="utf-8")
        local = {
            "slots": {"root": str(self.slot_root).replace("\\", "/")},
            "vars": {
                "py": sys.executable.replace("\\", "/"),
                "helper": str(self.helper).replace("\\", "/"),
                "ticks": str(self.ticks).replace("\\", "/"),
            },
        }
        (folder / "local.json").write_text(json.dumps(local), encoding="utf-8")

    def use_fake_p4(self):
        (self.dir / ".p4config").write_text("P4PORT=x:1666\nP4CLIENT=fake_Main\nP4USER=u\n")
        extra = {
            "vcs": "perforce",
            "stream": "//Depot/Main",
            "mainClient": "fake_Main",
            "p4": [sys.executable, str(self.fake_p4)],
        }
        adapter_path = self.dir / ".claude" / "orchestrator" / "adapter.json"
        adapter = json.loads(adapter_path.read_text(encoding="utf-8"))
        adapter["slots"].update(extra)
        adapter_path.write_text(json.dumps(adapter), encoding="utf-8")

    def run(self, *args):
        self.capsys.readouterr()
        code = slots.main(["--project", str(self.dir), *args])
        captured = self.capsys.readouterr()
        return code, captured.out, captured.err

    def intervals(self):
        spans = {}
        for line in self.ticks_text().splitlines():
            label, kind, stamp = line.split()
            spans.setdefault(label, {})[kind] = float(stamp)
        return spans

    def ticks_text(self):
        if not self.ticks.exists():
            return ""
        return "".join(path.read_text() for path in sorted(self.ticks.iterdir()))

    def p4_state_data(self):
        return json.loads(self.p4_state.read_text())

    def edit_p4(self, change):
        state = json.loads(self.p4_state.read_text()) if self.p4_state.exists() else {}
        change(state)
        self.p4_state.write_text(json.dumps(state))

    def set_spec_field(self, client, key, value, block=False):
        def change(state):
            state["specs"][client] = slots.set_field(state["specs"][client], key, value, block)

        self.edit_p4(change)

    def rows(self):
        return slots.Rows(self.rows_path).read()

    def state_of(self, name):
        return slots.Rows(self.rows_path).find(name)["state"]

    def p4_calls(self, *words, client=None):
        return [
            call
            for call in self.p4_state_data()["calls"]
            if list(words) == call[2 : 2 + len(words)] and (call[1] == client if client else call[1] != "fake_Main")
        ]


@pytest.fixture
def project(tmp_path, capsys, monkeypatch):
    return Project(tmp_path, capsys, monkeypatch)


@pytest.fixture
def live_pid():
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    yield process.pid
    process.kill()
    process.wait()


def dead_pid():
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


def hold_lock(path, pid):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(f"{pid}\t2026-10-15T00:00:00\n")


def overlaps(first, second):
    return first["start"] < second["end"] and second["start"] < first["end"]


def seed_slot(project, name, active=None, mission_text=None, state=slots.STATE_READY):
    slot = project.slot_root / name
    state_folder = slot / "_My" / "missions"
    state_folder.mkdir(parents=True)
    if active:
        (state_folder / "ACTIVE").write_text(json.dumps({"id": active, "session": "sess1234abcd", "openedAt": "t"}))
        (state_folder / active).mkdir()
        (state_folder / active / "mission.md").write_text(
            mission_text or "# m\n\n상태: 진행\n현재 단계: impl\n", encoding="utf-8"
        )
    if state is not None:
        slots.Rows(project.rows_path).upsert(
            name, path=str(slot), client=f"fake_{name}", created="2026-10-01", synced="2026-10-02", state=state
        )


def seed_perforce_slot(project, name, state=slots.STATE_READY):
    slot = project.slot_root / name
    slot.mkdir(parents=True)
    (slot / ".p4config").write_text(f"P4CLIENT=fake_{name}\n")
    spec = SLOT_SPEC.format(
        name=name, root=os.path.normpath(slot), description=slots.SLOT_DESCRIPTION.format(name=name)
    )

    def add(data):
        data.setdefault("clients", ["Main"]).append(f"fake_{name}")
        data.setdefault("specs", {})[f"fake_{name}"] = spec

    project.edit_p4(add)
    if state is not None:
        slots.Rows(project.rows_path).upsert(
            name, path=str(slot), client=f"fake_{name}", created="2026-10-01", synced="2026-10-02", state=state
        )


def test_expand_replaces_known_variables():
    assert slots.expand("a {x} b {y}", {"x": "1", "y": "2"}) == "a 1 b 2"


def test_expand_rejects_undefined_variable():
    with pytest.raises(slots.UsageError, match=r"\{zzz\}"):
        slots.expand("run {x} {zzz}", {"x": "1"})


def test_undefined_variable_fails_before_running_anything(project):
    project.write_adapter([{"id": "bad", "group": 1, "run": "echo {nope}"}])
    code, _, err = project.run("create", "a")
    assert code == slots.USAGE
    assert "{nope}" in err
    assert not project.slot_root.exists()


def test_dry_run_prints_expanded_commands_and_changes_nothing(project):
    project.write_adapter([project.tick_step("one", 1, exclusive=True)])
    code, out, _ = project.run("create", "a", "--dry-run")
    assert code == slots.OK
    assert "a-one" in out and "{name}" not in out
    assert "exclusive" in out
    assert not project.slot_root.exists()
    assert not project.state_dir.exists()


def test_same_group_steps_overlap_and_groups_run_in_order(project):
    project.write_adapter(
        [project.tick_step("a1", 1, 0.8), project.tick_step("a2", 1, 0.8), project.tick_step("b1", 2, 0.1)]
    )
    code, _, _ = project.run("create", "x")
    spans = project.intervals()
    assert code == slots.OK
    assert overlaps(spans["x-a1"], spans["x-a2"])
    assert spans["x-b1"]["start"] >= max(spans["x-a1"]["end"], spans["x-a2"]["end"])


def test_exclusive_step_never_overlaps_across_slots(project):
    project.write_adapter([project.tick_step("free", 1, 0.5), project.tick_step("solo", 2, 0.5, exclusive=True)])
    code, _, _ = project.run("create", "x", "y", "--jobs", "2")
    spans = project.intervals()
    assert code == slots.OK
    assert not overlaps(spans["x-solo"], spans["y-solo"])
    assert overlaps(spans["x-free"], spans["y-free"])
    assert not (project.state_dir / "slots" / "exclusive.lock").exists()


def test_ok_exit_accepts_listed_codes_only(project):
    steps = [project.tick_step("s", 1, args="s 1")]
    project.write_adapter(steps)
    assert project.run("create", "x")[0] == slots.FAILED

    steps = [project.tick_step("s", 1, args="s 1", okExit=[0, 1])]
    project.write_adapter(steps)
    assert project.run("create", "y")[0] == slots.OK


def test_failure_reports_log_and_other_slots_continue(project):
    project.write_adapter([project.tick_step("s", 1, args="bad")])
    code, out, _ = project.run("create", "bad", "good")
    assert code == slots.FAILED
    assert "bad: failed" in out and "good: created" in out
    assert "bad-s.out.log" in out
    assert {row["name"]: row["state"] for row in project.rows()} == {"bad": "failed:s", "good": "ready"}


def test_failed_group_stops_later_groups(project):
    project.write_adapter([project.tick_step("first", 1, args="bad"), project.tick_step("later", 2)])
    project.run("create", "bad")
    assert "bad-later" not in project.ticks_text()


def test_create_records_row_and_timing(project):
    project.write_adapter([project.tick_step("one", 1), project.tick_step("two", 2)])
    code, out, _ = project.run("create", "x")
    rows = project.rows()
    timing = (project.state_dir / "slots" / "x-timing.tsv").read_text().splitlines()
    assert code == slots.OK
    assert rows[0]["name"] == "x" and rows[0]["client"] == "fake_x" and rows[0]["synced"] != "-"
    assert rows[0]["state"] == "ready"
    assert [line.split("\t")[1] for line in timing] == ["one", "two"]
    assert "레지스트리 roots" in out
    assert not project.lock_path("x").exists()


def test_create_skips_existing_folder_without_touching_it(project):
    project.write_adapter([project.tick_step("one", 1)])
    (project.slot_root / "x").mkdir(parents=True)
    code, out, _ = project.run("create", "x")
    assert code == slots.OK
    assert "x: skipped" in out and "--resume" in out
    assert project.ticks_text() == ""


def test_create_without_names_makes_only_missing_configured_slots(project):
    project.write_adapter([project.tick_step("one", 1)], names=["a", "b"])
    (project.slot_root / "a").mkdir(parents=True)
    project.run("create")
    assert (project.slot_root / "b").is_dir()
    assert "a-one" not in project.ticks_text()


def test_create_collapses_duplicate_names(project):
    project.write_adapter([project.tick_step("one", 1)])
    code, out, _ = project.run("create", "a", "a", "--dry-run")
    assert code == slots.OK
    assert out.count("[a] create (dry-run)") == 1


def test_log_variable_expands_to_state_dir(project):
    project.write_adapter([{"id": "lg", "group": 1, "run": 'echo "{log}"'}])
    code, out, _ = project.run("create", "x", "--dry-run")
    assert code == slots.OK
    assert os.path.join("_My", "missions", "slots", "x-lg.log") in out


def test_row_is_creating_while_prepare_runs_then_ready(project):
    snapshot = project.ticks / "snapshot.tsv"
    command = f'"{{py}}" "{project.snapshot.as_posix()}" "{Path(project.rows_path).as_posix()}" "{snapshot.as_posix()}"'
    project.write_adapter([{"id": "snap", "group": 1, "run": command}])
    assert project.run("create", "x")[0] == slots.OK
    during = [line.split("\t") for line in snapshot.read_text(encoding="utf-8").splitlines()[1:]]
    assert [(cells[0], cells[-1]) for cells in during] == [("x", "creating")]
    assert project.state_of("x") == "ready"


@pytest.mark.parametrize("name", ["..", ".", "Main", "../a", "zzz", "a b"])
@pytest.mark.parametrize("command", ["create", "refresh", "remove", "run"])
def test_unlisted_or_malformed_names_are_rejected_by_every_command(project, command, name):
    project.write_adapter([project.tick_step("one", 1)])
    seed_slot(project, "a")
    args = {
        "create": ["create", name],
        "refresh": ["refresh", name],
        "remove": ["remove", name, "--yes"],
        "run": ["run", name, "go"],
    }[command]
    assert project.run(*args)[0] == slots.USAGE
    assert (project.slot_root / "a").is_dir()
    assert project.ticks_text() == ""


def test_adapter_names_must_be_well_formed(project):
    project.write_adapter([], names=["a", "../x"])
    code, _, err = project.run("status")
    assert code == slots.USAGE and "../x" in err


def test_inside_root_excludes_root_itself_and_outside_paths(tmp_path):
    root = tmp_path / "root"
    assert slots.inside_root(root, root / "a")
    assert not slots.inside_root(root, root)
    assert not slots.inside_root(root, tmp_path)
    assert not slots.inside_root(root, root / ".." / "x")
    assert not slots.inside_root(root, tmp_path / "root2" / "a")


@pytest.mark.skipif(os.name != "nt", reason="junction 은 Windows 만")
def test_remove_refuses_slot_folder_that_is_a_junction(project, tmp_path):
    precious = tmp_path / "precious"
    precious.mkdir()
    (precious / "keep.txt").write_text("x")
    project.write_adapter([project.tick_step("one", 1)])
    project.slot_root.mkdir()
    link = project.slot_root / "a"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(precious)], check=True, capture_output=True)
    slots.Rows(project.rows_path).upsert("a", state="ready")
    code, out, _ = project.run("remove", "a", "--yes")
    assert code == slots.FAILED and "링크" in out
    assert (precious / "keep.txt").exists()
    os.rmdir(link)


def test_slot_client_equal_to_main_client_is_refused(project):
    project.write_adapter([project.tick_step("one", 1)], client="fake_Main")
    project.use_fake_p4()
    project.edit_p4(lambda state: state.update(clients=["fake_Main"]))
    seed_slot(project, "a")
    for args in (["refresh", "a"], ["remove", "a", "--yes"]):
        code, out, _ = project.run(*args)
        assert code == slots.FAILED and "메인 작업 공간" in out
    assert "fake_Main" in project.p4_state_data()["clients"]
    assert project.ticks_text() == ""


def test_remove_refuses_client_whose_root_is_elsewhere(project):
    project.write_adapter([project.tick_step("one", 1)])
    project.use_fake_p4()
    project.run("create", "x")
    project.set_spec_field("fake_x", "Root", "/elsewhere")
    code, out, _ = project.run("remove", "x", "--yes")
    assert code == slots.FAILED and "Root" in out
    assert "fake_x" in project.p4_state_data()["clients"]
    assert (project.slot_root / "x").is_dir()


def test_remove_refuses_client_without_slot_description(project):
    project.write_adapter([project.tick_step("one", 1)])
    project.use_fake_p4()
    project.run("create", "x")
    project.set_spec_field("fake_x", "Description", "someone else's workspace", block=True)
    code, out, _ = project.run("remove", "x", "--yes")
    assert code == slots.FAILED and "Description" in out
    assert "fake_x" in project.p4_state_data()["clients"]
    assert (project.slot_root / "x").is_dir()


def test_legacy_slot_description_is_accepted(project):
    project.write_adapter([project.tick_step("one", 1)])
    project.use_fake_p4()
    project.run("create", "x")
    project.set_spec_field("fake_x", "Description", "AI mission slot 1 (han orchestrator). ", block=True)
    assert project.run("refresh", "x")[0] == slots.OK


@pytest.mark.parametrize("config_text", [None, "P4CLIENT=fake_other\n"])
def test_remove_refuses_when_p4config_is_missing_or_points_elsewhere(project, config_text):
    project.write_adapter([project.tick_step("one", 1)])
    project.use_fake_p4()
    project.run("create", "x")
    p4config = project.slot_root / "x" / ".p4config"
    if config_text is None:
        p4config.unlink()
    else:
        p4config.write_text(config_text)
    code, out, _ = project.run("remove", "x", "--yes")
    assert code == slots.FAILED and "P4CLIENT" in out
    assert "fake_x" in project.p4_state_data()["clients"]
    assert (project.slot_root / "x").is_dir()


def test_refresh_and_run_refuse_workspace_that_is_not_a_slot(project):
    project.write_adapter([project.tick_step("one", 1)])
    project.use_fake_p4()
    project.run("create", "x")
    project.set_spec_field("fake_x", "Description", "someone else's workspace", block=True)
    ticks_before = project.ticks_text()
    code, out, _ = project.run("refresh", "x")
    assert code == slots.FAILED and "Description" in out
    code, out, _ = project.run("run", "x", "go", "--dry-run")
    assert code == slots.FAILED and "거부" in out
    assert project.ticks_text() == ticks_before


def test_status_lists_state_and_marks_unknown_names(project):
    project.write_adapter([project.tick_step("one", 1)])
    seed_slot(project, "a", state="failed:sync")
    _, out, _ = project.run("status")
    line_a = next(line for line in out.splitlines() if line.startswith("a "))
    assert "failed:sync" in line_a
    assert out.splitlines()[1].split()[-2:] == ["state", "비고"]


def test_status_reports_active_and_mission_fields(project):
    project.write_adapter([project.tick_step("one", 1)])
    seed_slot(project, "a", active="m1")
    seed_slot(project, "b")
    code, out, _ = project.run("status")
    line_a = next(line for line in out.splitlines() if line.startswith("a "))
    line_b = next(line for line in out.splitlines() if line.startswith("b "))
    assert code == slots.OK
    assert "m1@sess123" in line_a and "진행" in line_a and "impl" in line_a and "2026-10-02" in line_a
    assert "m1" not in line_b


def test_status_marks_mismatches_without_fixing(project):
    project.write_adapter([project.tick_step("one", 1)])
    slots.Rows(project.rows_path).upsert("a", path="x", client="c")
    (project.slot_root / "b").mkdir(parents=True)
    _, out, _ = project.run("status")
    assert "폴더 없음" in out
    assert "slots.tsv 에 없음" in out
    assert [row["name"] for row in project.rows()] == ["a"]


def test_status_lists_configured_names_missing_from_tsv(project):
    project.write_adapter([project.tick_step("one", 1)])
    _, out, _ = project.run("status")
    assert any(line.startswith("a ") for line in out.splitlines())
    assert any(line.startswith("b ") for line in out.splitlines())


def test_status_survives_malformed_name_in_tsv(project):
    project.write_adapter([project.tick_step("one", 1)])
    path = Path(project.rows_path)
    path.parent.mkdir(parents=True)
    path.write_text(
        "name\tpath\tclient\tcreated\tsynced\tstate\n../evil\t/p\tc\t-\t-\tready\na\t/p\tc\t-\t-\tready\n",
        encoding="utf-8",
    )
    code, out, _ = project.run("status")
    assert code == slots.OK
    assert "이름 형식 오류" in next(line for line in out.splitlines() if line.startswith("../evil"))
    assert any(line.startswith("a ") for line in out.splitlines())


def test_refresh_refuses_active_slot_and_runs_nothing(project):
    project.write_adapter([project.tick_step("one", 1)])
    seed_slot(project, "a", active="m1")
    code, out, _ = project.run("refresh", "a")
    assert code == slots.FAILED
    assert "m1" in out
    assert project.ticks_text() == ""


def test_refresh_runs_only_listed_steps_and_updates_synced(project):
    project.write_adapter([project.tick_step("one", 1), project.tick_step("two", 2)])
    seed_slot(project, "a")
    code, _, _ = project.run("refresh", "a")
    ticks = project.ticks_text()
    assert code == slots.OK
    assert "a-one" in ticks and "a-two" not in ticks
    assert project.rows()[0]["synced"] == slots.date.today().isoformat()
    assert project.state_of("a") == "ready"
    assert not project.lock_path("a").exists()


def test_refresh_dry_run_shows_refusal_for_unprepared_slot(project):
    project.write_adapter([project.tick_step("one", 1)])
    seed_slot(project, "a", state=None)
    code, out, _ = project.run("refresh", "a", "--dry-run")
    assert code == slots.OK
    assert "거부" in out and "create --resume a" in out and "a-one" not in out


@pytest.mark.parametrize("state", [None, "creating", "failed:s", "weird"])
def test_refresh_refuses_slot_unless_ready_or_refresh_failed(project, state):
    project.write_adapter([project.tick_step("one", 1)])
    seed_slot(project, "a", state=state)
    code, out, _ = project.run("refresh", "a")
    assert code == slots.FAILED and "갱신 거부" in out
    assert project.ticks_text() == ""


def test_refresh_failure_is_marked_and_recovered_by_refresh(project):
    project.write_adapter([project.tick_step("one", 1)])
    assert project.run("create", "x")[0] == slots.OK
    project.write_adapter([project.tick_step("one", 1, args="x-one 1")])
    assert project.run("refresh", "x")[0] == slots.FAILED
    assert project.state_of("x") == "failed:refresh:one"
    code, out, _ = project.run("run", "x", "go", "--dry-run")
    assert code == slots.FAILED and "refresh" in out
    project.write_adapter([project.tick_step("one", 1)])
    assert project.run("run", "x", "go", "--dry-run", "--refresh")[0] == slots.OK
    assert project.run("refresh", "x")[0] == slots.OK
    assert project.state_of("x") == "ready"


def test_create_failure_needs_resume_not_refresh_and_can_be_removed(project):
    project.write_adapter([project.tick_step("one", 1, args="one 1")])
    project.run("create", "x")
    assert project.state_of("x") == "failed:one"
    code, out, _ = project.run("refresh", "x")
    assert code == slots.FAILED and "create --resume x" in out
    assert project.run("run", "x", "go", "--dry-run", "--refresh")[0] == slots.FAILED
    assert project.run("remove", "x", "--yes")[0] == slots.OK
    assert not (project.slot_root / "x").exists()


@pytest.mark.parametrize("state", [None, "creating", "failed:s", "failed:refresh:s", "weird"])
def test_run_refuses_slot_unless_ready(project, state):
    project.write_adapter([project.tick_step("one", 1)])
    seed_slot(project, "a", state=state)
    code, out, _ = project.run("run", "a", "go")
    assert code == slots.FAILED and "거부" in out


def test_run_with_refresh_accepts_refresh_failed_slot_only(project):
    project.write_adapter([project.tick_step("one", 1)])
    seed_slot(project, "a", state="failed:refresh:s")
    seed_slot(project, "b", state="failed:s")
    assert project.run("run", "a", "go", "--dry-run", "--refresh")[0] == slots.OK
    assert project.run("run", "b", "go", "--dry-run", "--refresh")[0] == slots.FAILED


def test_run_refuses_active_slot(project):
    project.write_adapter([project.tick_step("one", 1)])
    seed_slot(project, "a", active="m1")
    code, out, _ = project.run("run", "a", "do", "it")
    assert code == slots.FAILED
    assert "거부" in out


def test_run_dry_run_prints_wt_command_with_escaped_semicolon(project):
    project.write_adapter([project.tick_step("one", 1)])
    seed_slot(project, "a")
    code, out, _ = project.run("run", "a", "fix", "x;", "y", "--dry-run", "--editor", "--refresh")
    assert code == slots.OK
    assert "wt.exe -w 0 nt --title FAKE:a -d" in out
    assert "/han:mission fix x\\; y" in out
    assert '-n "a: fix x\\; y"' in out
    assert "editor:" in out and "a-one" in out


def test_run_session_name_is_cut_to_limit(project):
    project.write_adapter([project.tick_step("one", 1)])
    seed_slot(project, "a")
    code, out, _ = project.run("run", "a", "x" * 60, "--dry-run")
    assert code == slots.OK
    assert f'-n "a: {"x" * (slots.SESSION_NAME_LIMIT - 4)}…"' in out


def test_run_sets_compaction_budget_for_new_and_resumed_sessions(project):
    project.write_adapter([project.tick_step("one", 1)], autocompact=180000)
    seed_slot(project, "a")
    code, out, _ = project.run("run", "a", "task", "--dry-run")
    assert code == slots.OK and "--autocompact 180000" in out
    active = project.slot_root / "a" / "_My/missions/ACTIVE"
    active.write_text(json.dumps({"id": "m1", "session": "s1"}), encoding="utf-8")
    code, out, _ = project.run("run", "a", "--resume", "--dry-run")
    assert code == slots.OK and "--autocompact 180000" in out


@pytest.mark.parametrize("budget", [True, 99999, 1000001, "200k"])
def test_compaction_budget_must_be_a_supported_token_count(project, budget):
    project.write_adapter([], autocompact=budget)
    assert project.run("run", "a", "task", "--dry-run")[0] == slots.USAGE


def test_run_requires_task(project):
    project.write_adapter([project.tick_step("one", 1)])
    assert project.run("run", "a")[0] == slots.USAGE


@pytest.mark.parametrize("task", ['say "hi"', "line1\nline2", "a\rb"])
def test_run_rejects_quotes_and_newlines_in_task(project, task):
    project.write_adapter([project.tick_step("one", 1)])
    seed_slot(project, "a")
    code, _, err = project.run("run", "a", task, "--dry-run")
    assert code == slots.USAGE and "큰따옴표" in err


def test_run_dry_run_also_shows_refusal(project):
    project.write_adapter([project.tick_step("one", 1)])
    seed_slot(project, "a", active="m1")
    code, out, _ = project.run("run", "a", "go", "--dry-run")
    assert code == slots.FAILED
    assert "거부" in out and "wt:" not in out


def test_run_resume_dry_run_prints_resume_wt_command(project):
    project.write_adapter([project.tick_step("one", 1)])
    seed_slot(project, "a", active="m1")
    code, out, _ = project.run("run", "a", "--resume", "--dry-run", "--editor")
    assert code == slots.OK
    assert "wt.exe -w 0 nt --title FAKE:a -d" in out
    assert "/han:mission 미션 이어서" in out
    assert '-n "a: m1 이어서"' in out
    assert "editor:" in out


@pytest.fixture
def recorded_session(project, tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    seed_slot(project, "a", active="mission-1")
    folder = project.slot_root / "a" / "_My" / "missions" / "mission-1"
    session = str(uuid.uuid4())
    (folder.parent / "ACTIVE").write_text(json.dumps({"id": folder.name, "session": session}), encoding="utf-8")
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(json.dumps({"type": "session_meta", "payload": {"id": session}}) + "\n", encoding="utf-8")
    (folder / "sessions.tsv").write_text("at\taction\tsession\ttranscript\nnow\topen\t" + session + "\t" + str(transcript) + "\n", encoding="utf-8")
    located = slots.locate(str(project.dir))
    config = slots.Config(located, slots.read_adapter(located.adapter_path))
    return config, folder, session, transcript


def test_resume_preserves_recorded_session_and_rejects_wrong_runtime(recorded_session):
    config, folder, session, _ = recorded_session
    with pytest.raises(slots.UsageError, match="codex"):
        slots.resume_command(config, "a", folder.name)
    config.runtime = "codex"
    command = slots.resume_command(config, "a", folder.name)
    assert command[-3:] == ["resume", session, "$han-codex:han-mission 미션 이어서"]


def test_new_session_takes_over_mission_owned_by_other_runtime(recorded_session):
    config, folder, session, _ = recorded_session
    command = slots.resume_command(config, "a", folder.name, new_session=True)
    assert "--session-id" in command and "--resume" not in command and session not in command
    assert command[-1] == "/han:mission 미션 이어서"


def test_resume_finds_archived_codex_session(recorded_session, tmp_path):
    config, folder, session, _ = recorded_session
    (tmp_path / "session.jsonl").unlink()
    archived = tmp_path / "codex" / "archived_sessions" / f"rollout-2026-10-21T15-49-56-{session}.jsonl"
    archived.parent.mkdir(parents=True)
    archived.write_text(json.dumps({"type": "session_meta", "payload": {"id": session}}) + "\n", encoding="utf-8")
    with pytest.raises(slots.UsageError, match="codex.*--new-session"):
        slots.resume_command(config, "a", folder.name)


def test_resume_refuses_session_without_any_transcript(recorded_session, tmp_path):
    config, folder, _, _ = recorded_session
    (tmp_path / "session.jsonl").unlink()
    with pytest.raises(slots.UsageError, match="못 찾음"):
        slots.resume_command(config, "a", folder.name)


def test_corrupt_session_record_refuses_resume_with_a_clear_error(recorded_session):
    config, folder, _, _ = recorded_session
    (folder / "sessions.tsv").write_text("broken header\n", encoding="utf-8")
    with pytest.raises(slots.UsageError, match="세션 기록 확인 실패"):
        slots.resume_command(config, "a", folder.name)


def test_session_runtime_uses_latest_matching_existing_transcript(recorded_session, tmp_path):
    _, folder, session, transcript = recorded_session
    latest = tmp_path / "latest.jsonl"
    latest.write_text('{"type":"user"}\n', encoding="utf-8-sig")
    with (folder / "sessions.tsv").open("a", encoding="utf-8") as stream:
        stream.write(f"later\tresume\t{session}\t{latest}\n")
        stream.write(f"last\tresume\t{session}\t{tmp_path / 'missing.jsonl'}\n")
        stream.write(f"other\topen\tother-session\t{transcript}\n")
    assert slots.session_runtime(str(folder), session) == "claude"


def test_session_runtime_finds_claude_fallback(recorded_session, tmp_path):
    _, folder, session, transcript = recorded_session
    transcript.unlink()
    fallback = tmp_path / "claude" / "projects" / "project" / f"{session}.jsonl"
    fallback.parent.mkdir(parents=True)
    fallback.write_text('{"type":"user"}\n', encoding="utf-8")
    assert slots.session_runtime(str(folder), session) == "claude"


def test_session_runtime_rejects_invalid_id_and_missing_transcript(recorded_session):
    _, folder, session, transcript = recorded_session
    assert slots.session_runtime(str(folder), "../invalid") == ""
    transcript.unlink()
    assert slots.session_runtime(str(folder), session) == ""


@pytest.mark.parametrize("resume", [False, True])
def test_terminal_launch_has_no_answer_channel_configuration(recorded_session, monkeypatch, resume):
    config, folder, session, _ = recorded_session
    command = slots.wt_command(config, "a", "미션 이어서", resume_session=session if resume else "")
    assert command[-2:] == ["--", "/han:mission 미션 이어서"]
    assert "--mcp-config" not in command and "--dangerously-load-development-channels" not in command
    before = {path: path.read_bytes() for path in folder.parent.rglob("*") if path.is_file()}
    calls = []
    monkeypatch.setattr(slots.subprocess, "Popen", lambda argv, **kwargs: calls.append((argv, kwargs)))
    slots.launch_mission(config, "a", command)
    assert calls[0][0] == command
    assert {path: path.read_bytes() for path in folder.parent.rglob("*") if path.is_file()} == before


def test_run_resume_refuses_slot_without_active_mission(project):
    project.write_adapter([project.tick_step("one", 1)])
    seed_slot(project, "a")
    code, out, _ = project.run("run", "a", "--resume", "--dry-run")
    assert code == slots.FAILED
    assert "거부" in out and "진행 중 미션 없음" in out and "wt:" not in out


def test_run_resume_refuses_slot_unless_ready(project):
    project.write_adapter([project.tick_step("one", 1)])
    seed_slot(project, "a", active="m1", state="failed:s")
    code, out, _ = project.run("run", "a", "--resume", "--dry-run")
    assert code == slots.FAILED and "거부" in out


def test_run_new_session_needs_resume(project):
    project.write_adapter([project.tick_step("one", 1)])
    seed_slot(project, "a")
    assert project.run("run", "a", "go", "--new-session", "--dry-run")[0] == slots.USAGE


@pytest.mark.parametrize("extra", [["go"], ["--refresh"], ["--keep-local"], ["--discard-local"]])
def test_run_resume_rejects_task_and_sync_flags(project, extra):
    project.write_adapter([project.tick_step("one", 1)])
    seed_slot(project, "a", active="m1")
    assert project.run("run", "a", "--resume", "--dry-run", *extra)[0] == slots.USAGE


def test_remove_requires_yes(project):
    project.write_adapter([project.tick_step("one", 1)])
    seed_slot(project, "a")
    code, out, _ = project.run("remove", "a")
    assert code == slots.FAILED
    assert "--yes" in out
    assert (project.slot_root / "a").is_dir()


def test_remove_refuses_active_slot(project):
    project.write_adapter([project.tick_step("one", 1)])
    seed_slot(project, "a", active="m1")
    code, out, _ = project.run("remove", "a", "--yes")
    assert code == slots.FAILED
    assert "ACTIVE" in out
    assert (project.slot_root / "a").is_dir()


def test_remove_deletes_folder_and_row(project):
    project.write_adapter([project.tick_step("one", 1)])
    seed_slot(project, "a")
    read_only = project.slot_root / "a" / "ro.txt"
    read_only.write_text("x")
    os.chmod(read_only, 0o400)
    code, _, _ = project.run("remove", "a", "--yes")
    assert code == slots.OK
    assert not (project.slot_root / "a").exists()
    assert project.rows() == []
    assert not project.lock_path("a").exists()


@pytest.mark.parametrize("state", [None, "ready", "failed:s", "failed:refresh:s"])
def test_remove_accepts_ready_failed_and_unrecorded_slots(project, state):
    project.write_adapter([project.tick_step("one", 1)])
    seed_slot(project, "a", state=state)
    assert project.run("remove", "a", "--yes")[0] == slots.OK
    assert not (project.slot_root / "a").exists()


@pytest.mark.parametrize("state", ["creating", "weird"])
def test_remove_refuses_creating_and_unknown_states(project, state):
    project.write_adapter([project.tick_step("one", 1)])
    seed_slot(project, "a", state=state)
    code, out, _ = project.run("remove", "a", "--yes")
    assert code == slots.FAILED and "state" in out
    assert (project.slot_root / "a").is_dir()


def test_remove_drops_row_when_nothing_is_left(project):
    project.write_adapter([project.tick_step("one", 1)])
    slots.Rows(project.rows_path).upsert("a", state="failed:workspace")
    code, out, _ = project.run("remove", "a", "--yes")
    assert code == slots.OK and "a: removed" in out
    assert project.rows() == []
    assert "a: skipped" in project.run("remove", "a", "--yes")[1]


def test_missing_slots_section_is_usage_error(project):
    adapter_path = project.dir / ".claude" / "orchestrator" / "adapter.json"
    adapter = json.loads(adapter_path.read_text(encoding="utf-8"))
    adapter["slots"]["enabled"] = False
    adapter_path.write_text(json.dumps(adapter), encoding="utf-8")
    assert project.run("status")[0] == slots.USAGE


def test_unknown_subcommand_is_usage_error(project):
    assert project.run("bogus")[0] == slots.USAGE


def test_perforce_create_builds_spec_from_stream_and_main_client(project):
    project.write_adapter([project.tick_step("one", 1)])
    project.use_fake_p4()
    code, _, _ = project.run("create", "x")
    state = project.p4_state_data()
    spec = state["specs"]["fake_x"]
    slot = str(project.slot_root / "x")
    assert code == slots.OK
    assert slots.get_field(spec, "Root") == os.path.normpath(slot)
    assert slots.get_field(spec, "Options") == "noallwrite clobber nocompress unlocked modtime rmdir"
    assert slots.get_field(spec, "LineEnd") == "local"
    assert slots.get_field(spec, "SubmitOptions") == "revertunchanged"
    assert slots.get_field(spec, "Description") == "AI mission slot x (han)"
    assert slots.get_field(spec, "Stream") == "//Depot/Main"
    assert "//fake_x/..." in spec
    assert all(call[0] == "-c" for call in state["calls"])
    config = (project.slot_root / "x" / ".p4config").read_text()
    assert "P4CLIENT=fake_x" in config and "P4PORT=x:1666" in config and "fake_Main" not in config


def test_perforce_create_skips_when_workspace_exists(project):
    project.write_adapter([project.tick_step("one", 1)])
    project.use_fake_p4()
    project.run("create", "x")
    code, out, _ = project.run("create", "x")
    assert code == slots.OK and "x: skipped" in out


def test_perforce_create_skips_existing_client_even_without_folder(project):
    project.write_adapter([project.tick_step("one", 1)])
    project.use_fake_p4()
    project.p4_state.write_text(json.dumps({"clients": ["Main", "fake_x"]}))
    code, out, _ = project.run("create", "x")
    assert code == slots.OK and "작업 공간이 이미 있음" in out
    assert not (project.slot_root / "x").exists()


def test_create_warns_when_p4_info_reports_another_workspace(project):
    project.write_adapter([project.tick_step("one", 1)])
    project.use_fake_p4()
    project.edit_p4(lambda state: state.update(info_client="other_ws"))
    code, out, _ = project.run("create", "x")
    assert code == slots.OK
    assert "경고" in out and "other_ws" in out
    assert "other_ws" in project.run("status", "x")[1]


def test_perforce_remove_refuses_open_shelved_and_pending(project):
    project.write_adapter([project.tick_step("one", 1)])
    project.use_fake_p4()
    project.run("create", "x")
    state = project.p4_state_data()
    state["opened"] = {"fake_x": ["//Depot/Main/a.cs#1 - edit change 5 (text)"]}
    state["shelved"] = {"fake_x": ["Change 5 on d by u@fake_x *pending* 's'"]}
    state["pending"] = {"fake_x": ["Change 5 on d by u@fake_x *pending* 's'"]}
    project.p4_state.write_text(json.dumps(state))
    code, out, _ = project.run("remove", "x", "--yes")
    assert code == slots.FAILED
    assert "열린 파일 1개" in out and "shelved CL 1개" in out and "pending CL 1개" in out
    assert (project.slot_root / "x").is_dir()
    assert "fake_x" in project.p4_state_data()["clients"]


def test_perforce_remove_deletes_workspace_and_folder(project):
    project.write_adapter([project.tick_step("one", 1)])
    project.use_fake_p4()
    project.run("create", "x")
    code, _, _ = project.run("remove", "x", "--yes")
    assert code == slots.OK
    assert "fake_x" not in project.p4_state_data()["clients"]
    assert not (project.slot_root / "x").exists()
    assert project.rows() == []


def test_perforce_remove_handles_slot_without_row(project):
    project.write_adapter([project.tick_step("one", 1)])
    project.use_fake_p4()
    seed_perforce_slot(project, "x", state=None)
    code, _, _ = project.run("remove", "x", "--yes")
    assert code == slots.OK
    assert "fake_x" not in project.p4_state_data()["clients"]
    assert not (project.slot_root / "x").exists()


def test_remove_refuses_unrecorded_local_changes_unless_discarded(project):
    project.write_adapter([project.tick_step("one", 1)])
    project.use_fake_p4()
    project.run("create", "x")
    items = [
        {"kind": "add", "line": "//fake_x/new.cs - reconcile to add"},
        {"kind": "edit", "line": "//fake_x/a.cs - reconcile to edit"},
    ]
    project.edit_p4(lambda state: state.update(reconcile={"fake_x": items}))
    code, out, _ = project.run("remove", "x", "--yes")
    assert code == slots.FAILED
    assert "로컬 변경 2개" in out and "new.cs" in out and "--discard-local" in out
    assert "fake_x" in project.p4_state_data()["clients"]
    assert (project.slot_root / "x").is_dir()
    code, _, _ = project.run("remove", "x", "--yes", "--discard-local")
    assert code == slots.OK
    assert "fake_x" not in project.p4_state_data()["clients"]


def test_perforce_refresh_refuses_open_files_unless_kept(project):
    project.write_adapter([project.tick_step("sync", 1)])
    project.use_fake_p4()
    project.run("create", "x")
    state = project.p4_state_data()
    state["opened"] = {"fake_x": ["//Depot/Main/a.cs#1 - edit change 5 (text)"]}
    project.p4_state.write_text(json.dumps(state))
    ticks_before = project.ticks_text()
    code, out, _ = project.run("refresh", "x")
    assert code == slots.FAILED and "a.cs" in out and "--keep-local" in out
    assert project.ticks_text() == ticks_before
    assert project.p4_calls("resolve") == []
    assert project.run("refresh", "x", "--keep-local")[0] == slots.OK


def test_refresh_refuses_unopened_edits_but_not_new_files(project):
    project.write_adapter([project.tick_step("one", 1)])
    project.use_fake_p4()
    project.run("create", "x")
    added = [{"kind": "add", "line": "//fake_x/new.cs - reconcile to add"}]
    project.edit_p4(lambda state: state.update(reconcile={"fake_x": added}))
    assert project.run("refresh", "x")[0] == slots.OK
    edited = [{"kind": "edit", "line": "//fake_x/a.cs - reconcile to edit"}]
    project.edit_p4(lambda state: state.update(reconcile={"fake_x": edited}))
    ticks_before = project.ticks_text()
    code, out, _ = project.run("refresh", "x")
    assert code == slots.FAILED and "로컬 변경 1개" in out and "a.cs" in out
    assert project.ticks_text() == ticks_before
    assert project.run("refresh", "x", "--discard-local")[0] == slots.OK
    reconciles = project.p4_calls("reconcile")
    assert reconciles and all("-e" in call for call in reconciles)


CHURN_ENTRY = "Unity/Game/Assets/Resources/Fonts/font.asset"
CHURN_DEPOT = f"//Depot/Main/{CHURN_ENTRY}"
CHURN_EDIT = {"kind": "edit", "line": f"{CHURN_DEPOT}#3 - reconcile to edit (text)"}
OTHER_EDIT = {"kind": "edit", "line": "//Depot/Main/Unity/Game/Assets/a.cs#2 - reconcile to edit (text)"}


def churn_project(project, churn=(CHURN_ENTRY,)):
    project.write_adapter([project.tick_step("sync", 1)], churn=list(churn))
    project.use_fake_p4()
    assert project.run("create", "x")[0] == slots.OK


def test_refresh_ignores_churn_file_edits_and_restores_it_to_have(project):
    churn_project(project)
    project.edit_p4(lambda state: state.update(reconcile={"fake_x": [CHURN_EDIT]}))
    ticks_before = project.ticks_text()
    code, out, _ = project.run("refresh", "x")
    assert code == slots.OK
    assert "엔진이 다시 쓰는 파일 1개는 검사에서 뺌" in out
    assert [call[3:] for call in project.p4_calls("sync")] == [["-f", f"//fake_x/{CHURN_ENTRY}#have"]]
    assert project.ticks_text() != ticks_before


def test_refresh_still_refuses_other_edits_next_to_churn_file(project):
    churn_project(project)
    project.edit_p4(lambda state: state.update(reconcile={"fake_x": [CHURN_EDIT, OTHER_EDIT]}))
    ticks_before = project.ticks_text()
    code, out, _ = project.run("refresh", "x")
    assert code == slots.FAILED and "로컬 변경 1개" in out and "a.cs" in out and "font.asset" not in out
    assert "엔진이 다시 쓰는 파일 1개는 검사에서 뺌" in out
    assert project.p4_calls("sync") == []
    assert project.ticks_text() == ticks_before


def test_refresh_without_churn_never_syncs_forcibly(project):
    project.write_adapter([project.tick_step("one", 1)])
    project.use_fake_p4()
    project.run("create", "x")
    project.edit_p4(lambda state: state.update(reconcile={"fake_x": [CHURN_EDIT]}))
    assert project.run("refresh", "x")[0] == slots.FAILED
    assert project.run("refresh", "x", "--discard-local")[0] == slots.OK
    assert project.p4_calls("sync") == []


def test_refresh_refuses_when_churn_file_is_opened(project):
    churn_project(project)
    opened = {"fake_x": [f"{CHURN_DEPOT}#3 - edit change 5 (text)"]}
    project.edit_p4(lambda state: state.update(opened=opened))
    ticks_before = project.ticks_text()
    for extra in ([], ["--keep-local"], ["--discard-local"]):
        code, out, _ = project.run("refresh", "x", *extra)
        assert code == slots.FAILED and "slots.churn" in out and "font.asset" in out
    assert project.p4_calls("sync") == []
    assert project.ticks_text() == ticks_before


def test_refresh_marks_state_when_churn_restore_fails(project):
    churn_project(project)
    project.edit_p4(lambda state: state.update(sync_fails=True))
    ticks_before = project.ticks_text()
    code, out, err = project.run("refresh", "x")
    assert code == slots.FAILED and "sync" in out
    assert project.state_of("x") == "failed:refresh:churn"
    assert project.ticks_text() == ticks_before
    project.edit_p4(lambda state: state.update(sync_fails=False))
    assert project.run("refresh", "x")[0] == slots.OK
    assert project.state_of("x") == "ready"


def test_remove_check_also_excludes_churn_files(project):
    churn_project(project)
    project.edit_p4(lambda state: state.update(reconcile={"fake_x": [CHURN_EDIT]}))
    code, out, _ = project.run("remove", "x", "--yes")
    assert code == slots.OK and "엔진이 다시 쓰는 파일 1개는 검사에서 뺌" in out
    assert "fake_x" not in project.p4_state_data()["clients"]


def test_remove_still_refuses_non_churn_changes_beside_churn(project):
    churn_project(project)
    project.edit_p4(lambda state: state.update(reconcile={"fake_x": [CHURN_EDIT, OTHER_EDIT]}))
    code, out, _ = project.run("remove", "x", "--yes")
    assert code == slots.FAILED and "로컬 변경 1개" in out and "font.asset" not in out
    assert "엔진이 다시 쓰는 파일 1개는 검사에서 뺌" in out
    assert (project.slot_root / "x").is_dir()


def test_refresh_dry_run_lists_churn_restore_before_steps(project):
    churn_project(project)
    code, out, _ = project.run("refresh", "x", "--dry-run")
    assert code == slots.OK
    restore = f"p4 -c fake_x sync -f //fake_x/{CHURN_ENTRY}#have"
    assert restore in out and out.index(restore) < out.index("x-sync")
    code, out, _ = project.run("run", "x", "go", "--dry-run", "--refresh")
    assert code == slots.OK and restore in out
    assert project.p4_calls("sync") == []


def test_churn_is_ignored_without_vcs(project):
    seed_slot(project, "a")
    project.write_adapter([project.tick_step("one", 1)], churn=["../bad", 5])
    assert project.run("refresh", "a")[0] == slots.OK


@pytest.mark.parametrize(
    "pattern, path, matches",
    [
        ("Assets/*.asset", "//Depot/Main/Assets/f.asset", True),
        ("Assets/*.asset", "//Depot/Main/Assets/sub/f.asset", False),
        ("Assets/.../*.asset", "//Depot/Main/Assets/sub/deep/f.asset", True),
        ("Assets/.../*.asset", "//Depot/Main/Assets/f.asset", False),
        ("Assets/.../f.asset", "//Depot/Main/Assets/sub/deep/f.asset", True),
        ("Assets/f.asset", "//depot/main/assets/F.ASSET", True),
        ("Assets/f.asset", "//Depot/Main/Assets/f.asset.meta", False),
        ("Assets/f.asset", "//Depot/Main/Other/Assets/f.asset", False),
        ("Assets/f(1).asset", "//Depot/Main/Assets/f(1).asset", True),
        ("Assets/f(1).asset", "//Depot/Main/Assets/f1.asset", False),
    ],
)
def test_churn_pattern_wildcards(pattern, path, matches):
    assert bool(slots.churn_pattern("//Depot/Main", pattern).fullmatch(path)) is matches


def test_churn_lines_match_by_depot_path_only(project):
    vcs = slots.build_vcs(
        {"vcs": "perforce", "stream": "//Depot/Main/", "mainClient": "m", "churn": ["Assets/*.asset"]}, str(project.dir)
    )
    assert vcs.is_churn("//Depot/Main/Assets/f.asset#3 - edit change 5 (text)")
    assert vcs.is_churn("//Depot/Main/Assets/f.asset - reconcile to add")
    assert not vcs.is_churn("//Depot/Main/Assets/f.asset.meta#1 - edit change 5 (text)")
    assert not vcs.is_churn("not a depot line")


@pytest.mark.parametrize(
    "churn",
    ["Assets/f.asset", {"a": 1}, [""], ["  "], [5], ["/Assets/f"], ["//Depot/Main/f"], ["C:/x/f"], ["Assets\\f"], ["../f"], ["a/../f"]],
)
def test_malformed_churn_is_usage_error(project, churn):
    project.write_adapter([project.tick_step("one", 1)], churn=churn)
    project.use_fake_p4()
    code, _, err = project.run("status")
    assert code == slots.USAGE and "slots.churn" in err


def test_churn_accepts_wildcards_and_dots_in_names(project):
    entries = slots.churn_entries({"churn": ["Assets/.../*.asset", "a..b/f", "x", "x"]})
    assert entries == ["Assets/.../*.asset", "a..b/f", "x"]
    assert slots.churn_entries({}) == []


def keep_project(project, **extra):
    project.write_adapter(
        [project.tick_step("sync", 1), project.tick_step("two", 2)], refresh=["sync", "two"], **extra
    )
    project.use_fake_p4()
    assert project.run("create", "x")[0] == slots.OK


def set_reconcile(project, *items):
    project.edit_p4(lambda state: state.update(reconcile={"fake_x": list(items)}))


def test_keep_local_without_changes_resolves_and_creates_no_keep_changelist(project):
    keep_project(project)
    code, out, _ = project.run("refresh", "x", "--keep-local")
    assert code == slots.OK and "보관 CL" not in out
    assert project.p4_calls("change", "-i") == []
    assert [call[3] for call in project.p4_calls("resolve")] == ["-am", "-n"]
    assert project.state_of("x") == "ready"


def test_keep_local_opens_unopened_edits_in_keep_changelist_and_reuses_it(project):
    keep_project(project, churn=[CHURN_ENTRY])
    set_reconcile(project, OTHER_EDIT, CHURN_EDIT)
    code, out, _ = project.run("refresh", "x", "--keep-local")
    state = project.p4_state_data()
    assert code == slots.OK
    assert "보관 CL 9000 에 열리지 않은 변경 파일 1개를 엶" in out and "엔진이 다시 쓰는 파일 1개는 검사에서 뺌" in out
    assert state["pending_cls"] == [{"id": 9000, "client": "fake_x", "desc": "han slot keep-local x"}]
    assert state["reconciled"] == [{"cl": "9000", "files": ["//Depot/Main/Unity/Game/Assets/a.cs"]}]
    assert state["opened"]["fake_x"] == ["//Depot/Main/Unity/Game/Assets/a.cs#2 - edit change 9000 (text)"]
    assert [call[3:] for call in project.p4_calls("sync")] == [["-f", f"//fake_x/{CHURN_ENTRY}#have"]]
    set_reconcile(project, {"kind": "edit", "line": "//Depot/Main/b.cs#1 - reconcile to edit (text)"})
    code, out, _ = project.run("refresh", "x", "--keep-local")
    state = project.p4_state_data()
    assert code == slots.OK and "보관 CL 9000" in out
    assert len(state["pending_cls"]) == 1 and len(project.p4_calls("change", "-i")) == 1
    assert [entry["cl"] for entry in state["reconciled"]] == ["9000", "9000"]
    assert len(state["opened"]["fake_x"]) == 2


def test_keep_local_leaves_already_opened_files_alone(project):
    keep_project(project)
    opened = ["//Depot/Main/a.cs#1 - edit change 5 (text)"]
    project.edit_p4(lambda state: state.update(opened={"fake_x": opened}))
    code, _, _ = project.run("refresh", "x", "--keep-local")
    state = project.p4_state_data()
    assert code == slots.OK
    assert state["opened"]["fake_x"] == opened
    assert "reconciled" not in state and "pending_cls" not in state


def test_keep_local_stops_at_resolve_conflicts_before_later_steps(project):
    keep_project(project)
    created_two = project.ticks_text().count("x-two start")
    created_sync = project.ticks_text().count("x-sync start")
    conflict = "//fake_x/Unity/Game/Assets/a.cs - merging //Depot/Main/Unity/Game/Assets/a.cs#9"
    project.edit_p4(lambda state: state.update(conflicts={"fake_x": [conflict]}))
    code, out, _ = project.run("refresh", "x", "--keep-local")
    assert code == slots.FAILED and "충돌 1개" in out and "a.cs" in out
    assert project.state_of("x") == "failed:refresh:resolve"
    assert project.ticks_text().count("x-sync start") == created_sync + 1
    assert project.ticks_text().count("x-two start") == created_two
    project.edit_p4(lambda state: state.update(conflicts={}))
    assert project.run("refresh", "x", "--keep-local")[0] == slots.OK
    assert project.state_of("x") == "ready"
    assert project.ticks_text().count("x-two start") == created_two + 1


def test_keep_local_dry_run_shows_keep_changelist_reconcile_and_resolve_after_sync(project):
    keep_project(project, churn=[CHURN_ENTRY])
    code, out, _ = project.run("refresh", "x", "--keep-local", "--dry-run")
    assert code == slots.OK
    order = ["change -i", "reconcile -e -c", "sync -f", "x-sync", "resolve -am", "resolve -n", "x-two"]
    assert [out.index(word) for word in order] == sorted(out.index(word) for word in order)
    assert "han slot keep-local x" in out
    code, out, _ = project.run("run", "x", "go", "--dry-run", "--refresh", "--keep-local")
    assert code == slots.OK and "resolve -am" in out and "change -i" in out
    plain = project.run("refresh", "x", "--dry-run")[1]
    assert "resolve" not in plain and "keep-local" not in plain
    assert project.p4_calls("change", "-i") == [] and project.p4_calls("resolve") == []


@pytest.mark.parametrize("command", [["refresh", "x"], ["run", "x", "go", "--refresh"]])
def test_keep_local_and_discard_local_together_is_usage_error(project, command):
    keep_project(project)
    code, _, err = project.run(*command, "--keep-local", "--discard-local")
    assert code == slots.USAGE and "--keep-local" in err and "--discard-local" in err


@pytest.mark.parametrize("command", [["refresh", "x"], ["run", "x", "go"]])
def test_allow_open_option_is_gone(project, command):
    keep_project(project)
    assert project.run(*command, "--allow-open")[0] == slots.USAGE


def test_keep_local_needs_sync_step_in_refresh_with_perforce(project):
    project.write_adapter([project.tick_step("one", 1)])
    project.use_fake_p4()
    project.run("create", "x")
    code, _, err = project.run("refresh", "x", "--keep-local")
    assert code == slots.USAGE and "sync" in err
    assert project.run("refresh", "x")[0] == slots.OK


def test_keep_local_without_vcs_just_refreshes(project):
    seed_slot(project, "a")
    project.write_adapter([project.tick_step("one", 1)])
    assert project.run("refresh", "a", "--keep-local")[0] == slots.OK


def test_perforce_status_counts_opened_files(project):
    project.write_adapter([project.tick_step("one", 1)])
    project.use_fake_p4()
    project.run("create", "x")
    state = project.p4_state_data()
    state["opened"] = {"fake_x": ["//Depot/Main/a.cs#1 - edit", "//Depot/Main/b.cs#1 - add"]}
    project.p4_state.write_text(json.dumps(state))
    _, out, _ = project.run("status", "x")
    row = next(line for line in out.splitlines() if line.startswith("x "))
    assert row.split()[1:4] == ["yes", "yes", "2"]


def test_resume_finishes_slot_without_row_and_skips_workspace_creation(project):
    project.write_adapter([project.tick_step("one", 1)])
    project.use_fake_p4()
    project.run("create", "x")
    slots.Rows(project.rows_path).remove("x")
    before = project.ticks_text().count("x-one start")
    code, out, _ = project.run("create", "x", "--resume")
    assert code == slots.OK and "x: resumed" in out
    assert project.ticks_text().count("x-one start") == before + 1
    assert len(project.p4_calls("client", "-i")) == 1
    assert project.state_of("x") == "ready"
    code, out, _ = project.run("create", "x", "--resume")
    assert code == slots.OK and "x: skipped" in out and "이미 ready" in out


def test_failed_create_is_recovered_by_resume(project):
    project.write_adapter([project.tick_step("s", 1, args="s 1")])
    project.use_fake_p4()
    assert project.run("create", "x")[0] == slots.FAILED
    assert project.state_of("x") == "failed:s"
    project.write_adapter([project.tick_step("s", 1)])
    project.use_fake_p4()
    code, out, _ = project.run("create", "x", "--resume")
    assert code == slots.OK and "x: resumed" in out
    assert project.state_of("x") == "ready"


def test_resume_picks_up_slot_left_in_creating_state(project):
    project.write_adapter([project.tick_step("one", 1)])
    seed_slot(project, "a", state="creating")
    code, out, _ = project.run("create", "a", "--resume")
    assert code == slots.OK and "a: resumed" in out
    assert project.state_of("a") == "ready"


def test_resume_refuses_workspace_that_is_not_a_slot(project):
    project.write_adapter([project.tick_step("one", 1)])
    project.use_fake_p4()
    seed_perforce_slot(project, "a", state=None)
    project.set_spec_field("fake_a", "Description", "someone else's workspace", block=True)
    code, out, _ = project.run("create", "a", "--resume")
    assert code == slots.FAILED and "Description" in out
    assert project.ticks_text() == ""


def test_resume_needs_names_and_an_existing_slot(project):
    project.write_adapter([project.tick_step("one", 1)])
    project.use_fake_p4()
    assert project.run("create", "--resume")[0] == slots.USAGE
    code, out, _ = project.run("create", "x", "--resume")
    assert code == slots.FAILED and "슬롯 폴더 없음" in out


def test_resume_dry_run_reports_plan_or_refusal_and_changes_nothing(project):
    project.write_adapter([project.tick_step("one", 1)])
    project.use_fake_p4()
    seed_perforce_slot(project, "a", state=None)
    code, out, _ = project.run("create", "a", "--resume", "--dry-run")
    assert code == slots.OK
    assert "create --resume (dry-run)" in out and "prepare 전체" in out and "a-one" in out
    project.set_spec_field("fake_a", "Description", "someone else's workspace", block=True)
    code, out, _ = project.run("create", "a", "--resume", "--dry-run")
    assert code == slots.OK and "거부" in out and "a-one" not in out
    assert project.ticks_text() == "" and project.rows() == []


def test_concurrent_operation_on_same_slot_is_refused(project, live_pid):
    project.write_adapter([project.tick_step("one", 1)])
    seed_slot(project, "a")
    hold_lock(project.lock_path("a"), live_pid)
    for args in (["create", "a"], ["refresh", "a"], ["remove", "a", "--yes"]):
        code, out, _ = project.run(*args)
        assert code == slots.FAILED and "잠금" in out
    code, _, err = project.run("run", "a", "go")
    assert code == slots.FAILED and "잠금" in err
    assert (project.slot_root / "a").is_dir()
    assert project.ticks_text() == ""
    assert project.lock_path("a").exists()


@pytest.mark.parametrize("holder", ["dead", "self"])
def test_lock_left_behind_is_reclaimed(project, holder):
    project.write_adapter([project.tick_step("one", 1)])
    seed_slot(project, "a")
    hold_lock(project.lock_path("a"), dead_pid() if holder == "dead" else os.getpid())
    assert project.run("refresh", "a")[0] == slots.OK
    assert not project.lock_path("a").exists()


def test_exclusive_step_fails_when_other_process_holds_the_lock(project, live_pid, monkeypatch):
    monkeypatch.setattr(slots, "EXCLUSIVE_LOCK_TIMEOUT", 0.3)
    monkeypatch.setattr(slots, "LOCK_POLL_SECONDS", 0.05)
    project.write_adapter([project.tick_step("solo", 1, exclusive=True)])
    hold_lock(project.state_dir / "slots" / "exclusive.lock", live_pid)
    code, out, _ = project.run("create", "x")
    assert code == slots.FAILED and "x-solo.out.log" in out
    assert "잠금" in (project.state_dir / "slots" / "x-solo.out.log").read_text(encoding="utf-8")
    assert project.ticks_text() == ""
    assert project.state_of("x") == "failed:solo"


def test_rows_write_times_out_while_other_process_holds_the_lock(project, live_pid, monkeypatch):
    monkeypatch.setattr(slots, "ROWS_LOCK_TIMEOUT", 0.2)
    monkeypatch.setattr(slots, "LOCK_POLL_SECONDS", 0.05)
    hold_lock(project.rows_path + ".lock", live_pid)
    with pytest.raises(slots.LockBusy, match="slots.tsv"):
        slots.Rows(project.rows_path).upsert("x", state="ready")


def test_process_alive_tells_running_from_finished(live_pid):
    assert slots.process_alive(os.getpid())
    assert slots.process_alive(live_pid)
    assert not slots.process_alive(dead_pid())


def test_rows_read_legacy_header_as_ready_and_rewrite_with_state_column(project):
    path = Path(project.rows_path)
    path.parent.mkdir(parents=True)
    path.write_text(
        "name\tpath\tclient\tcreated\tsynced\tmission\nold\t/p\tc\t2026-10-01\t2026-10-02\t-\n", encoding="utf-8"
    )
    rows = slots.Rows(str(path))
    assert rows.read()[0]["state"] == "ready" and rows.read()[0]["synced"] == "2026-10-02"
    rows.upsert("new", state="creating")
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "name\tpath\tclient\tcreated\tsynced\tstate"
    assert lines[1].endswith("\tready") and lines[2].endswith("\tcreating")


def test_rows_read_follows_header_order_and_ignores_unknown_columns(project):
    path = Path(project.rows_path)
    path.parent.mkdir(parents=True)
    path.write_text("state\tname\textra\tpath\nfailed:s\ta\tzzz\t/p\n", encoding="utf-8")
    row = slots.Rows(str(path)).read()[0]
    assert (row["name"], row["state"], row["path"], row["client"]) == ("a", "failed:s", "/p", "-")


def test_project_dir_name_replaces_non_alphanumerics():
    assert slots.project_dir_name("D:\\Work\\Game_Main") == "D--Work-Game-Main"
    assert slots.project_dir_name("D:\\Work\\slots\\slot1") == "D--Work-slots-slot1"
    assert slots.project_dir_name("D:\\한글") == "D----"


def test_claude_memory_dir_follows_config_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    expected = os.path.join(str(tmp_path / "claude"), "projects", "D--Work-slots-slot1", "memory")
    assert slots.claude_memory_dir("D:\\Work\\slots\\slot1") == os.path.normpath(expected)


def test_memory_dir_variables_expand_for_slot_and_main(project, monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    project.write_adapter([{"id": "mem", "group": 1, "run": 'echo "{memoryDir}" "{mainMemoryDir}"'}])
    code, out, _ = project.run("create", "x", "--dry-run")
    assert code == slots.OK
    slot_memory = slots.claude_memory_dir(os.path.normpath(project.slot_root / "x"))
    main_memory = slots.claude_memory_dir(os.path.normpath(project.dir))
    assert f'"{slot_memory}" "{main_memory}"' in out
    assert slot_memory != main_memory


def test_registry_warning_without_registry_does_not_read_cwd(project, monkeypatch, tmp_path):
    monkeypatch.setattr(slots.mission_guard, "registry_dir", lambda: "")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    roots = [str(project.slot_root).replace("\\", "/") + "/*"]
    (cwd / "projects.json").write_text(json.dumps([{"adapter": "x", "roots": roots}]))
    monkeypatch.chdir(cwd)
    project.write_adapter([project.tick_step("one", 1)])
    code, out, _ = project.run("create", "x")
    assert code == slots.OK and "레지스트리를 찾지 못해" in out


@pytest.mark.parametrize(
    "step",
    [
        {"id": "g", "group": "x", "run": "echo"},
        {"id": "n", "group": 1},
        {"group": 1, "run": "echo"},
        "text",
        {"id": "c", "run": "echo", "cwd": 5},
    ],
)
def test_malformed_prepare_entry_is_usage_error(project, step):
    project.write_adapter([step])
    code, _, err = project.run("create", "x", "--dry-run")
    assert code == slots.USAGE and "slots.prepare" in err


def test_read_adapter_merges_local_json_deeply(project):
    adapter = slots.read_adapter(str(project.dir / ".claude" / "orchestrator" / "adapter.json"))
    assert adapter["slots"]["client"] == "fake_{name}"
    assert adapter["slots"]["root"].endswith("slot_root")
    assert "py" in adapter["vars"]


def test_unreadable_local_json_is_usage_error(project):
    (project.dir / ".claude" / "orchestrator" / "local.json").write_text("{", encoding="utf-8")
    code, _, err = project.run("status")
    assert code == slots.USAGE and "local.json" in err


def baseline_step():
    return {"id": "sync", "group": 1, "run": "p4 sync //Depot/Main/...@{baseline}"}


def changes_calls(project):
    return [call for call in project.p4_state_data()["calls"] if "changes" in call and "-m1" in call]


def test_baseline_expands_to_main_workspace_have_in_create_and_refresh(project):
    project.write_adapter([baseline_step()])
    project.use_fake_p4()
    code, out, _ = project.run("create", "x", "--dry-run")
    assert code == slots.OK
    assert "p4 sync //Depot/Main/...@12345" in out
    assert "기준 CL: 12345 (메인 워크스페이스 fake_Main 의 have)" in out
    assert changes_calls(project)[0][1:] == ["fake_Main", "changes", "-m1", "//fake_Main/...#have"]
    seed_perforce_slot(project, "x")
    code, out, _ = project.run("refresh", "x", "--dry-run")
    assert code == slots.OK
    assert "p4 sync //Depot/Main/...@12345" in out
    assert "기준 CL: 12345" in out


def test_baseline_is_queried_once_for_several_slots(project):
    project.write_adapter([baseline_step()])
    project.use_fake_p4()
    code, out, _ = project.run("create", "x", "y", "--dry-run")
    assert code == slots.OK
    assert out.count("@12345") == 2
    assert out.count("기준 CL:") == 1
    assert len(changes_calls(project)) == 1


def test_run_refresh_dry_run_reports_baseline(project):
    project.write_adapter([baseline_step()])
    project.use_fake_p4()
    seed_perforce_slot(project, "a")
    code, out, _ = project.run("run", "a", "go", "--dry-run", "--refresh")
    assert code == slots.OK
    assert "기준 CL: 12345" in out and "@12345" in out


def test_empty_have_fails_instead_of_syncing_head(project):
    project.write_adapter([baseline_step()])
    project.use_fake_p4()
    project.p4_state.write_text(json.dumps({"have": ""}))
    code, out, err = project.run("create", "x")
    assert code == slots.FAILED
    assert "기준 CL" in err
    assert "created" not in out
    assert not (project.slot_root / "x").exists()


def test_unexpected_have_output_fails(project):
    project.write_adapter([baseline_step()])
    project.use_fake_p4()
    project.p4_state.write_text(json.dumps({"have": "garbage"}))
    code, _, err = project.run("create", "x", "--dry-run")
    assert code == slots.FAILED and "garbage" in err


def test_template_without_baseline_never_calls_changes(project):
    project.write_adapter([project.tick_step("one", 1)])
    project.use_fake_p4()
    code, out, _ = project.run("create", "x")
    assert code == slots.OK
    assert changes_calls(project) == []
    assert "기준 CL" not in out
    project.run("status", "x")
    assert changes_calls(project) == []


def test_baseline_without_vcs_is_usage_error(project):
    project.write_adapter([baseline_step()])
    code, _, err = project.run("create", "x", "--dry-run")
    assert code == slots.USAGE
    assert "{baseline}" in err and "slots.vcs" in err


def test_project_option_is_accepted_after_the_subcommand(project, capsys):
    project.write_adapter([project.tick_step("one", 1)])
    code = slots.main(["status", "--project", str(project.dir)])
    assert code == slots.OK
    assert "요약" in capsys.readouterr().out


def test_child_env_points_pwd_at_the_child_cwd(monkeypatch):
    monkeypatch.setenv("PWD", "D:/elsewhere")
    assert slots.child_env("D:/Work/slots/slot2")["PWD"] == "D:/Work/slots/slot2"
    assert slots.child_env("")["PWD"] == os.getcwd()


def test_launch_env_drops_parent_session_markers(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_CHILD_SESSION", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "parent")
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_MOUSE_CLICKS", "1")
    monkeypatch.setenv("AI_AGENT", "claude-code_agent")
    monkeypatch.setenv("MSYSTEM", "MINGW64")
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    env = slots.launch_env()
    assert "CLAUDE_CODE_CHILD_SESSION" not in env
    assert "CLAUDE_CODE_SESSION_ID" not in env
    assert "AI_AGENT" not in env and "MSYSTEM" not in env
    assert "NO_COLOR" not in env and "GIT_TERMINAL_PROMPT" not in env
    assert env["CLAUDE_CODE_DISABLE_MOUSE_CLICKS"] == "1"


MAIN_A = "//Depot/Main/Unity/Game/Assets/a.cs"
MAIN_B = "//Depot/Main/Unity/Game/Assets/b.cs"
MAIN_LOCK = "main.lock"


def at_baseline_step():
    return {"id": "sync", "group": 1, "run": '"{py}" "{helper}" "{ticks}" {name}-at{baseline} 0'}


def main_project(project, **extra):
    project.write_adapter([at_baseline_step()], **extra)
    project.use_fake_p4()
    assert project.run("create", "x")[0] == slots.OK


def lag_main(project, *files, head=12400, edited=(), opened=()):
    def change(state):
        state["head"] = f"Change {head} on 2026/10/16 by u@Main 'd'"
        state["sync_pending"] = [f"{path}#3 - updating /ws/{path.rsplit('/', 1)[1]}" for path in files]
        state.setdefault("reconcile", {})["fake_Main"] = [
            {"kind": "edit", "line": f"{path}#2 - reconcile to edit (text)"} for path in edited
        ]
        state.setdefault("opened", {})["fake_Main"] = [f"{path}#1 - edit change 5 (text)" for path in opened]

    project.edit_p4(change)


def main_syncs(project):
    return project.p4_state_data().get("main_synced", [])


def main_lock_left(project):
    return (project.state_dir / "slots" / MAIN_LOCK).exists()


def test_refresh_skips_main_sync_when_main_is_already_current(project):
    main_project(project)
    code, out, _ = project.run("refresh", "x")
    assert code == slots.OK
    assert "이미 head 12345" in out and "생략" in out
    assert main_syncs(project) == []
    assert [call[3:] for call in project.p4_calls("sync", client="fake_Main")] == [["-n", "//fake_Main/...@12345"]]
    assert project.ticks_text().count("x-at12345 start") == 2
    assert not main_lock_left(project)


def test_refresh_syncs_lagging_main_to_head_before_slot_uses_baseline(project):
    main_project(project)
    lag_main(project, MAIN_A, MAIN_B)
    code, out, _ = project.run("refresh", "x")
    assert code == slots.OK
    assert "메인 sync: 12345 → 12400 (파일 2개)" in out
    assert main_syncs(project) == ["//fake_Main/...@12400"]
    assert project.ticks_text().count("x-at12400 start") == 1
    assert [call[3] for call in project.p4_calls("resolve", client="fake_Main")] == ["-am", "-n"]
    assert "기준 CL: 12400 (메인 워크스페이스 fake_Main 의 have)" in out
    assert project.state_of("x") == "ready"
    assert project.p4_state_data()["reconcile_inputs"] == [[MAIN_A, MAIN_B]]
    assert not main_lock_left(project)


def test_refresh_pins_head_number_before_listing_and_syncing(project):
    main_project(project)
    lag_main(project, MAIN_A)
    project.run("refresh", "x")
    head_queries = [call[2:] for call in project.p4_calls("changes", client="fake_Main") if call[-1] == "//Depot/Main/..."]
    assert head_queries == [["changes", "-m1", "-s", "submitted", "//Depot/Main/..."]]
    syncs = [call[3:] for call in project.p4_calls("sync", client="fake_Main")]
    assert syncs == [["-n", "//fake_Main/...@12400"], ["-q", "--parallel=threads=8", "//fake_Main/...@12400"]]


def test_refresh_syncs_main_once_for_several_slots(project):
    main_project(project)
    seed_perforce_slot(project, "y")
    lag_main(project, MAIN_A)
    code, out, _ = project.run("refresh", "x", "y")
    assert code == slots.OK
    assert len(main_syncs(project)) == 1 and out.count("메인 sync: 12345 → 12400") == 1
    assert project.ticks_text().count("x-at12400 start") == 1 and project.ticks_text().count("y-at12400 start") == 1


def test_refresh_refuses_when_main_has_unopened_edits_and_changes_nothing(project):
    main_project(project)
    lag_main(project, MAIN_A, MAIN_B, edited=[MAIN_A])
    ticks_before = project.ticks_text()
    code, out, _ = project.run("refresh", "x")
    assert code == slots.FAILED
    assert "a.cs" in out and "b.cs" not in out and "--main-keep-local" in out and "메인 sync·슬롯 갱신 안 함" in out
    assert main_syncs(project) == []
    assert project.p4_calls("change", "-i", client="fake_Main") == []
    assert project.p4_calls("resolve", client="fake_Main") == []
    assert project.ticks_text() == ticks_before
    assert not main_lock_left(project)


def test_main_refusal_lists_files_up_to_preview_limit(project):
    main_project(project)
    files = [f"//Depot/Main/f{index}.cs" for index in range(slots.PREVIEW_LIMIT + 5)]
    lag_main(project, *files, edited=files)
    code, out, _ = project.run("refresh", "x")
    assert code == slots.FAILED
    assert "f0.cs" in out and f"f{slots.PREVIEW_LIMIT - 1}.cs" in out and f"f{slots.PREVIEW_LIMIT}.cs" not in out
    assert "외 5개" in out


def test_main_keep_local_opens_edits_in_main_keep_changelist_and_reuses_it(project):
    main_project(project)
    lag_main(project, MAIN_A, MAIN_B, edited=[MAIN_A])
    code, out, _ = project.run("refresh", "x", "--main-keep-local")
    state = project.p4_state_data()
    assert code == slots.OK
    assert "메인 보관 CL 9000 에 열리지 않은 변경 파일 1개를 엶" in out and "메인 sync: 12345 → 12400" in out
    assert state["pending_cls"] == [{"id": 9000, "client": "fake_Main", "desc": "han main keep-local"}]
    assert state["reconciled"] == [{"cl": "9000", "files": [MAIN_A]}]
    assert main_syncs(project) == ["//fake_Main/...@12400"]
    assert [call[3] for call in project.p4_calls("resolve", client="fake_Main")] == ["-am", "-n"]
    lag_main(project, MAIN_B, head=12500, edited=[MAIN_B], opened=[MAIN_A])
    code, out, _ = project.run("refresh", "x", "--main-keep-local")
    state = project.p4_state_data()
    assert code == slots.OK and "메인 보관 CL 9000" in out
    assert len(state["pending_cls"]) == 1
    assert [entry["cl"] for entry in state["reconciled"]] == ["9000", "9000"]


def test_main_keep_local_without_edits_creates_no_changelist(project):
    main_project(project)
    lag_main(project, MAIN_A)
    code, out, _ = project.run("refresh", "x", "--main-keep-local")
    assert code == slots.OK and "메인 보관 CL" not in out
    assert "pending_cls" not in project.p4_state_data()


def test_main_overwrite_check_skips_churn_files_and_files_opened_in_main(project):
    main_project(project, churn=[CHURN_ENTRY])
    lag_main(project, CHURN_DEPOT, MAIN_B, MAIN_A, edited=[CHURN_DEPOT, MAIN_B], opened=[MAIN_B])
    code, _, _ = project.run("refresh", "x")
    assert code == slots.OK
    assert project.p4_state_data()["reconcile_inputs"] == [[MAIN_A]]
    assert main_syncs(project) == ["//fake_Main/...@12400"]


def test_main_conflict_after_sync_stops_before_slot_refresh(project):
    main_project(project)
    lag_main(project, MAIN_A)
    conflict = f"//fake_Main/Unity/Game/Assets/a.cs - merging {MAIN_A}#9"
    project.edit_p4(lambda state: state.update(conflicts={"fake_Main": [conflict]}))
    ticks_before = project.ticks_text()
    code, out, _ = project.run("refresh", "x")
    assert code == slots.FAILED
    assert "메인 충돌 1개" in out and "a.cs" in out and "슬롯 갱신 안 함" in out
    assert main_syncs(project) == ["//fake_Main/...@12400"]
    assert project.ticks_text() == ticks_before
    assert project.state_of("x") == "ready"
    assert not main_lock_left(project)


def test_main_sync_dry_run_reports_plan_and_changes_nothing(project):
    main_project(project)
    lag_main(project, MAIN_A, MAIN_B, edited=[MAIN_A])
    ticks_before = project.ticks_text()
    code, out, _ = project.run("refresh", "x", "--dry-run", "--main-keep-local")
    assert code == slots.OK
    assert "have 12345 → head 12400" in out and "바뀔 파일 2개" in out and "a.cs" in out
    assert "han main keep-local" in out and "sync -q --parallel=threads=8 //fake_Main/...@12400" in out
    assert "resolve -am" in out and "기준 CL: 12400 (메인 sync 뒤 head 12400 기준" in out and "x-at12400" in out
    assert main_syncs(project) == []
    state = project.p4_state_data()
    assert "pending_cls" not in state and "reconciled" not in state
    assert project.ticks_text() == ticks_before
    assert not main_lock_left(project)


def test_main_sync_dry_run_shows_refusal_without_flag(project):
    main_project(project)
    lag_main(project, MAIN_A, edited=[MAIN_A])
    for command in (["refresh", "x"], ["run", "x", "go", "--refresh"]):
        code, out, _ = project.run(*command, "--dry-run")
        assert code == slots.FAILED and "a.cs" in out and "--main-keep-local" in out
        assert "have 12345 → head 12400" in out and "바뀔 파일 1개" in out
    assert main_syncs(project) == []


def test_run_refresh_dry_run_reports_main_sync_with_head_baseline(project):
    main_project(project)
    lag_main(project, MAIN_A)
    code, out, _ = project.run("run", "x", "go", "--refresh", "--dry-run")
    assert code == slots.OK
    assert "have 12345 → head 12400" in out and "기준 CL: 12400" in out and "wt:" in out
    assert main_syncs(project) == []


def test_run_refresh_syncs_main_before_refreshing_slot(project, monkeypatch):
    main_project(project)
    lag_main(project, MAIN_A)
    monkeypatch.setattr(slots, "wt_command", lambda *args: [sys.executable, "-c", "pass"])
    code, out, _ = project.run("run", "x", "go", "--refresh")
    assert code == slots.OK
    assert "메인 sync: 12345 → 12400" in out and "미션 시작 요청" in out
    assert project.ticks_text().count("x-at12400 start") == 1


def test_run_refresh_refuses_on_main_overwrite_and_does_not_launch(project, monkeypatch):
    main_project(project)
    lag_main(project, MAIN_A, edited=[MAIN_A])
    monkeypatch.setattr(slots, "wt_command", lambda *args: [sys.executable, "-c", "pass"])
    code, out, _ = project.run("run", "x", "go", "--refresh")
    assert code == slots.FAILED and "--main-keep-local" in out and "미션 시작 요청" not in out
    assert main_syncs(project) == []


def test_main_sync_is_skipped_when_every_slot_is_refused(project):
    main_project(project)
    lag_main(project, MAIN_A)
    slots.Rows(project.rows_path).upsert("x", state="creating")
    code, out, _ = project.run("refresh", "x")
    assert code == slots.FAILED and "갱신 거부" in out
    assert project.p4_calls("sync", client="fake_Main") == []
    assert main_syncs(project) == []


def test_main_sync_runs_when_only_some_slots_are_refused(project):
    main_project(project)
    seed_perforce_slot(project, "y", state="creating")
    lag_main(project, MAIN_A)
    code, out, _ = project.run("refresh", "x", "y")
    assert code == slots.FAILED and "y: failed" in out and "x: refreshed" in out
    assert len(main_syncs(project)) == 1


def test_run_main_keep_local_requires_refresh(project):
    main_project(project)
    lag_main(project, MAIN_A)
    code, _, err = project.run("run", "x", "go", "--main-keep-local")
    assert code == slots.USAGE and "--refresh" in err
    assert project.p4_calls("sync", client="fake_Main") == []


@pytest.mark.parametrize(
    "command, adapter_extra",
    [
        (["refresh", "x", "--keep-local"], {"refresh": ["one"]}),
        (["refresh", "x"], {"refresh": ["nope"]}),
        (["run", "x", "go", "--refresh", "--keep-local", "--discard-local"], {}),
    ],
)
def test_usage_errors_surface_before_main_sync(project, command, adapter_extra):
    main_project(project)
    project.write_adapter([project.tick_step("one", 1)], **adapter_extra)
    project.use_fake_p4()
    lag_main(project, MAIN_A)
    assert project.run(*command)[0] == slots.USAGE
    assert project.p4_calls("sync", client="fake_Main") == []


def test_undefined_variable_in_refresh_step_fails_before_main_sync(project):
    main_project(project)
    project.write_adapter([{"id": "sync", "group": 1, "run": "echo {nope} {baseline}"}])
    project.use_fake_p4()
    lag_main(project, MAIN_A)
    code, _, err = project.run("refresh", "x")
    assert code == slots.USAGE and "{nope}" in err
    assert project.p4_calls("sync", client="fake_Main") == []


def test_create_never_syncs_main_and_uses_main_have_as_baseline(project):
    main_project(project)
    lag_main(project, MAIN_A)
    code, _, _ = project.run("create", "y")
    assert code == slots.OK
    assert project.p4_calls("sync", client="fake_Main") == []
    assert [call for call in project.p4_calls("changes", client="fake_Main") if call[-1] == "//Depot/Main/..."] == []
    assert project.ticks_text().count("y-at12345 start") == 1


def test_main_sync_is_skipped_without_vcs(project):
    seed_slot(project, "a")
    project.write_adapter([project.tick_step("one", 1)])
    assert project.run("refresh", "a", "--main-keep-local")[0] == slots.OK
