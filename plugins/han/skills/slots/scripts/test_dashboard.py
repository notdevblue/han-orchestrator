import json
import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, quote, urljoin, urlsplit

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dashboard  # noqa: E402
import mission_guard  # noqa: E402
import mission_state  # noqa: E402
import question_records
import slots  # noqa: E402

UNITS_HEADER = "\t".join(mission_state.UNIT_HEADER)
UNIT_ROW = "M1\t목표\ta.cs\t테스트\tpytest\t60\tsubmit\t-\t-\t123456\t2026-10-19T09:00:00+09:00\tdoing"


class DetailElement:
    def __init__(self, tag, attrs=()):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children = []

    def elements(self, tag="", **attrs):
        found = []
        for child in self.children:
            if isinstance(child, DetailElement):
                if (not tag or child.tag == tag) and all(child.attrs.get(key, "") == value for key, value in attrs.items()):
                    found.append(child)
                found.extend(child.elements(tag, **attrs))
        return found

    def text(self, visible=False):
        if visible and self.tag in ("script", "style"):
            return ""
        children = self.children
        if visible and self.tag == "details" and "open" not in self.attrs:
            children = [child for child in children if isinstance(child, DetailElement) and child.tag == "summary"]
        pieces = []
        for child in children:
            if isinstance(child, DetailElement):
                pieces.append(child.text(visible))
            else:
                pieces.append(child)
        return "".join(pieces)


class DetailParser(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.root = DetailElement("document")
        self.stack = [self.root]
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        element = DetailElement(tag, attrs)
        self.stack[-1].children.append(element)
        if tag not in ("area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"):
            self.stack.append(element)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def detail_dom(source):
    return DetailParser(source).root


def detail_fields(element):
    return {term.text(): value.text() for term, value in zip(element.elements("dt"), element.elements("dd"))}


def write_detail_records(folder, records):
    headers = {name: header for _, name, header in dashboard.TABLES}
    for name, rows in records.items():
        mission_state.write_table(str(folder / name), headers[name], rows)


def detail_records():
    return {
        "units.tsv": [{
            "id": "M7", "goal": '계약 <img src=x onerror="alert(1)"> & 원문',
            "files": "src/한글 파일.cs;tests/범위.cs", "done": "완료 조건 고유 원문",
            "verify": "pytest 계획 고유 원문", "timebox": "45", "forbid": "승인 전 submit 금지",
            "human": "확인 B2 승인 필요", "after": "M6", "unit": "CL-427777",
            "started": "2026-10-23T09:01:02+09:00", "status": "doing",
        }],
        "ledger.tsv": [{
            "at": "2026-10-23T10:01:02+09:00", "unit": "M7-review", "status": "pass",
            "grade": "needs-human", "evidence": '검증 결과 <script>alert("판정")</script> & 원문',
            "files": "snapshot/src.cs;연관 파일.cs",
        }],
        "decisions.tsv": [{
            "at": "2026-10-23T10:02:03+09:00", "id": "D8", "what": "결정 주제 <b>원문</b>",
            "chosen": '선택 "옵션 A"', "why": "이유 & 근거", "affects": "M7, M8 영향",
        }],
        "sessions.tsv": [{
            "at": "2026-10-23T10:03:04+09:00", "action": "resume", "session": "recorded-session-7",
            "transcript": "private/session-7.jsonl",
        }],
    }


def write_question(folder, name, title, items, approval="none", answer="", intro="", body=""):
    previous = {}
    if question_records.source_path(folder, name).exists():
        previous = question_records.read_document(folder, name)
    revision = previous.get("revision", 0) + 1
    record = {
        "schema": 1, "id": name[:-3], "revision": revision, "title": title, "intro": intro, "body": body,
        "items": [{"id": str(number), "title": item_title, "body": "", "status": "pending", "answer": ""}
                  for number, (item_title, status) in enumerate(items, 1)],
        "approval": previous.get("approval", {"status": "none", "reason": ""}),
        "answers": previous.get("answers", []),
    }
    if "legacy_markdown" in previous:
        record["legacy_markdown"] = previous["legacy_markdown"]
    elif not previous and (folder / name).exists():
        record["legacy_markdown"] = (folder / name).read_bytes().decode("utf-8")
    expected_revision = ""
    if previous:
        expected_revision = previous["revision"]
    question_records.publish_document(folder, record, expected_revision)
    reason = ""
    if approval == "pending":
        reason = "최종 승인 필요"
    raw_answer = answer
    if not raw_answer:
        raw_answer = "Record the fictional question states."
    updates = []
    for item, (_, status) in zip(record["items"], items):
        value = ""
        if status != "pending":
            value = answer
        updates.append({"id": item["id"], "status": status, "answer": value})
    return question_records.answer_document(folder, name, {
        "schema": 1, "id": f"answer-{revision}", "revision": revision,
        "at": "2026-10-26T10:00:00+09:00", "text": raw_answer, "updates": updates,
        "approval": {"status": approval, "reason": reason},
    })


class World:
    def __init__(self, tmp_path):
        self.main = tmp_path / "main"
        self.slot_root = tmp_path / "slot_root"
        adapter_dir = self.main / ".claude" / "orchestrator"
        adapter_dir.mkdir(parents=True)
        adapter = {
            "schema": 1,
            "project": "FAKE",
            "paths": {"vcsRoot": ".", "stateDir": "_My/missions"},
            "slots": {"enabled": True, "names": ["slot1", "slot2"], "client": "fake_{name}", "prepare": []},
        }
        (adapter_dir / "adapter.json").write_text(json.dumps(adapter), encoding="utf-8")
        local = {"slots": {"root": str(self.slot_root).replace("\\", "/")}}
        (adapter_dir / "local.json").write_text(json.dumps(local), encoding="utf-8")
        self.outside = tmp_path / "outside"
        self.outside.mkdir()
        (self.outside / "secret.md").write_text("비밀", encoding="utf-8")

    @property
    def config(self):
        located = slots.locate(str(self.main))
        return slots.Config(located, slots.read_adapter(located.adapter_path))

    def mission(self, slot, mission_id, state="진행", files=None, shared=()):
        base = self.main if slot == "main" else self.slot_root / slot
        folder = base / "_My" / "missions" / mission_id
        folder.mkdir(parents=True)
        (folder / "mission.md").write_text(
            f"# 미션 {mission_id}\n\n상태: {state}\n현재 단계: impl\n확인 A: 승인\n확인 A1: 대기\n\n## 목표\n\n{mission_id} 목표 첫 줄\n", encoding="utf-8"
        )
        (folder / "report.md").write_text("# 보고\n\n## 지금\n\n보고에만 있는 과거 진행 요약\n\n## 마일스톤\n", encoding="utf-8")
        (folder / "units.tsv").write_text(f"{UNITS_HEADER}\n{UNIT_ROW}\n", encoding="utf-8")
        for name, text in (files or {}).items():
            (folder / name).parent.mkdir(parents=True, exist_ok=True)
            (folder / name).write_text(text, encoding="utf-8")
        if shared:
            self.share(folder, *shared)
        return folder

    def share(self, folder, *names):
        rows = {row["path"]: row for row in mission_state.shared_document_rows(str(folder))}
        for name in names:
            canonical, target = mission_state.canonical_document_path(str(folder), name)
            rows[canonical] = {"path": canonical, "sha256": dashboard.digest(Path(target).read_bytes()), "at": "2026-10-26T10:00:00+09:00"}
        mission_state.write_table(str(folder / mission_state.SHARED_DOCUMENTS_FILE), mission_state.SHARED_DOCUMENTS_HEADER, [rows[name] for name in sorted(rows)])
        return names

    def get(self, target):
        status, _, body = dashboard.route(self.config, target)
        return status, body.decode("utf-8", errors="replace")


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("HAN_ADAPTERS_DIR", str(tmp_path / "no-registry"))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    return World(tmp_path)


def test_running_mission_without_active_file_is_collected(world):
    world.mission("slot2", "20261015-pet-gacha-server", shared=["report.md"])
    (world.slot_root / "slot1" / "_My" / "missions").mkdir(parents=True)
    found = {location.label: location for location in dashboard.collect(world.config)}
    mission = found["slot2"].missions[0]
    assert (mission.id, mission.state, mission.active) == ("20261015-pet-gacha-server", "진행", False)
    status, text = world.get("/")
    assert status == 200
    assert "세션 없음" in text and "20261015-pet-gacha-server" in text
    assert "<h3>지금</h3>" in text and "보고에만 있는 과거 진행 요약" not in text


def test_only_running_missions_list_unanswered_questions_on_top(world):
    folder = world.mission("slot1", "run-1")
    write_question(folder, "ask-1.md", "질문", [("남은 결정", "pending")])
    write_question(folder, "confirm-A.md", "확인", [("완료 결정", "answered")], answer="승인")
    world.mission("slot1", "stopped-1", state="중단", files={"ask-9.md": "# 질문\n\n## 답\n"}, shared=["ask-9.md"])
    status, text = world.get("/")
    top = text.split('<h2 class="sec"><span class="eyebrow">02')[0]
    assert status == 200
    assert "ask-1.md" in top and "run-1" in top
    assert "ask-9.md" not in top and "confirm-A.md" not in top
    assert "stopped-1" in text


@pytest.mark.parametrize("runtime", ["claude", "codex"])
def test_answer_waiting_slot_is_first_on_overview_and_keeps_other_slot_order(world, runtime):
    world.mission("main", "main-running")
    world.mission("slot1", "slot1-running")
    world.mission("slot1", "slot1-stopped", state="중단", files={"ask-9.md": "# 닫힌 질문\n\n답변 대기\n\n## 결정할 것\n\n### 1. 중단 범위\n"}, shared=["ask-9.md"])
    world.mission("slot2", "primary-running")
    other = world.mission("slot2", "other-running", files={"ask-2.md": "# 업데이트 전략\n\n답변 대기\n\n## 결정할 것\n\n### 1. 문서 범위\n"}, shared=["ask-2.md"])
    write_question(other, "ask-2.md", "업데이트 전략", [("문서 범위", "pending")])
    (other.parent / "ACTIVE").write_text(json.dumps({"id": "primary-running", "session": "s1"}), encoding="utf-8")
    config = runtime_config(world, runtime)
    overview = dashboard.route(config, "/")[2].decode("utf-8")
    card_order = re.findall(r'<article class="card[^\"]*" data-slot="([^\"]+)"', overview)
    assert card_order == ["slot2", "main", "slot1"]
    assert 'title="답변 필요 1건"' in overview and "질문 2 · 업데이트 전략" in overview
    assert "닫힌 질문" not in overview.split('<h2 class="sec"><span class="eyebrow">02')[0]


def test_view_refuses_paths_outside_allowed_roots(world):
    inside = world.mission("slot1", "run-1")
    assert world.get("/view?path=" + quote(str(inside / "mission.md"), safe=""))[0] == 200
    assert world.get("/view?path=" + quote(str(world.outside / "secret.md"), safe=""))[0] == 403
    sneaky = str(inside / ".." / ".." / ".." / ".." / ".." / "outside" / "secret.md")
    assert world.get("/view?path=" + quote(sneaky, safe=""))[0] == 403
    assert world.get("/view?path=" + quote("relative/mission.md", safe=""))[0] == 403
    assert world.get("/view?path=")[0] == 403
    assert world.get("/view?path=" + quote(str(world.slot_root), safe=""))[0] == 403


def test_view_renders_markdown_text_and_images(world):
    folder = world.mission("slot1", "run-1")
    link = "file:///" + str(folder / "report.md").replace("\\", "/")
    (folder / "ask-1.md").write_text(f"# 질문\n\n[보고]({link}#L3)\n", encoding="utf-8")
    (folder / "shot.png").write_bytes(b"\x89PNG\r\n")
    world.share(folder, "ask-1.md", "report.md", "shot.png")
    status, text = world.get("/view?path=" + quote(str(folder / "ask-1.md"), safe=""))
    assert status == 200 and "file:///" not in text and "/view?path=" in text and "#L3" in text
    status, text = world.get("/view?path=" + quote(str(folder / "units.tsv"), safe=""))
    assert status == 200 and 'id="L2"' in text
    status, content_type, body = dashboard.route(world.config, "/view?path=" + quote(str(folder / "shot.png"), safe=""))
    assert (status, content_type, body) == (200, "image/png", b"\x89PNG\r\n")


def test_broken_files_do_not_break_the_overview(world):
    folder = world.mission("slot1", "broken-1")
    (folder / "units.tsv").write_text("엉터리 헤더\nM1\t하나\n", encoding="utf-8")
    (folder / "sessions.tsv").write_bytes(b"\xff\xfe\x00broken")
    (world.slot_root / "slot2" / "_My" / "missions" / "no-mission-md").mkdir(parents=True)
    bad = world.mission("slot2", "bad-md")
    (bad / "mission.md").write_bytes(b"\xff\xfe\x00\x00")
    status, text = world.get("/")
    assert status == 200
    assert "읽기 실패: units.tsv" in text and "broken-1" in text and "bad-md" in text and "no-mission-md" not in text
    status, text = world.get("/mission?slot=slot1&id=broken-1")
    assert status == 200 and "읽기 실패" in text


@pytest.mark.parametrize("suffix", [".html", ".png", ".css"])
def test_markdown_file_urls_use_raw_routes_for_browser_assets(world, suffix):
    folder = world.mission("slot1", "run-1")
    asset = folder / ("한글 image" + suffix)
    asset.write_text("asset", encoding="utf-8")
    reference = "file:///" + quote(str(asset).replace("\\", "/"), safe="/:")
    document = folder / "ask-1.md"
    document.write_text(f"[파일]({reference}#preview)\n\n![그림]({reference})\n", encoding="utf-8")
    world.share(folder, "ask-1.md", asset.name)
    status, text = world.get(dashboard.view_url(document))
    assert status == 200
    embedded = text.split('id="source"', 1)[1].split('>', 1)[1].split('</script>', 1)[0]
    assert json.loads(embedded).splitlines() == f"[파일]({dashboard.raw_url(asset)}#preview)\n\n![그림]({dashboard.raw_url(asset)})\n".splitlines()
    assert world.get(dashboard.raw_url(asset))[0] == 200


def test_markdown_links_use_the_document_location_without_rewriting_source(world):
    folder = world.mission("slot1", "한글-1")
    (folder / "details").mkdir()
    (folder / "details" / "ask-1.md").write_text("# 상세\n\n[사람용](../ask-1.md)\n", encoding="utf-8")
    source = "# 질문\n\n[상세](details/ask-1.md#근거)\n\n```text\n[예제](missing.md)\n```\n"
    (folder / "ask-1.md").write_text(source, encoding="utf-8")
    world.share(folder, "ask-1.md", "details/ask-1.md")
    status, text = world.get(dashboard.view_url(folder / "ask-1.md"))
    assert status == 200
    assert f'data-url="{dashboard.raw_url(folder / "ask-1.md")}"' in text
    embedded = text.split('id="source"', 1)[1].split('>', 1)[1].split('</script>', 1)[0]
    assert json.loads(embedded) == (folder / "ask-1.md").read_bytes().decode("utf-8")
    assert world.get(dashboard.view_url(folder / "details" / "ask-1.md", "근거"))[0] == 200
    assert world.get(dashboard.view_url(world.outside / "secret.md"))[0] == 403


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js is required to execute the Markdown link renderer")
@pytest.mark.parametrize("slot", ["main", "slot1", "slot2"])
def test_rendered_markdown_links_open_files_in_the_source_workspace(world, slot):
    folder = world.mission(slot, "한글-1")
    root = world.main if slot == "main" else world.slot_root / slot
    poc = add_poc(folder, root)
    image = root / "Wiki" / "한글 image#1.png"
    image.write_bytes(b"image")
    details = folder / "details"
    details.mkdir()
    document = details / "confirm.md"
    document.write_text("# 확인\n", encoding="utf-8")
    (folder / "50%.md").write_text("# 백분율\n", encoding="utf-8")
    world.share(folder, "details/confirm.md", "50%.md", "report.md")
    status, text = world.get(dashboard.view_url(document))
    assert status == 200
    assert f'data-root="{dashboard.raw_url(root)}/"' in text
    links = [
        ("a", "../../../../" + POC_REL, dashboard.raw_url(poc)),
        ("a", "/" + POC_REL + "?mode=preview#tree", dashboard.raw_url(poc) + "?mode=preview#tree"),
        ("a", "../mission.md#목표", dashboard.view_url(folder / "mission.md") + "#%EB%AA%A9%ED%91%9C"),
        ("a", dashboard.raw_url(folder / "report.md"), dashboard.view_url(folder / "report.md")),
        ("img", "/Wiki/" + quote(image.name), dashboard.raw_url(image)),
        ("img", "../../../../Wiki/" + quote(image.name), dashboard.raw_url(image)),
        ("a", "#목표", "#목표"),
        ("a", "?mode=preview", "?mode=preview"),
        ("a", "/", "/"),
        ("a", "/view?path=original", "/view?path=original"),
        ("a", "/mission?slot=slot1&id=run-1", "/mission?slot=slot1&id=run-1"),
        ("a", "https://example.com/document.md", "https://example.com/document.md"),
        ("a", "//example.com/document.md", "//example.com/document.md"),
        ("a", "mailto:user@example.com", "mailto:user@example.com"),
        ("a", "../50%.md", dashboard.view_url(folder / "50%.md")),
        ("a", "../invalid%FF.md", "../invalid%FF.md"),
        ("a", "../report.md", dashboard.view_url(folder / "report.md")),
        ("img", "../../../../Wiki/" + quote(image.name), dashboard.raw_url(image)),
    ]
    script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const elements = input.links.map(([tag, reference]) => ({
  tag, reference,
  getAttribute() { return this.reference; },
  setAttribute(attribute, value) { this.reference = value; }
}));
const source = {textContent: '"# 확인"', dataset: input.dataset};
const out = {querySelectorAll(selector) { return elements.filter(element => selector.startsWith(element.tag + '[')); }};
vm.runInNewContext(input.script, {
  URL, location: {href: 'http://127.0.0.1:8765/view?path=source'},
  document: {getElementById(id) { return id === 'source' ? source : out; }},
  window: {marked: {use() {}, parse() { return ''; }}}
});
console.log(JSON.stringify(elements.map(element => element.reference)));
"""
    result = subprocess.run(
        [shutil.which("node"), "-e", script],
        input=json.dumps({"links": links, "dataset": {"url": dashboard.raw_url(document), "root": dashboard.raw_url(root) + "/"},
                          "script": dashboard.MARKDOWN_SCRIPT.removeprefix("\n<script>\n").removesuffix("</script>\n")}),
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    references = json.loads(result.stdout)
    for (tag, original, expected), reference in zip(links, references):
        actual = reference.removeprefix("http://127.0.0.1:8765")
        if expected.startswith("/view?path="):
            actual_path = parse_qs(urlsplit(actual).query)["path"][0]
            expected_path = parse_qs(urlsplit(expected).query)["path"][0]
            assert slots.normalize(actual_path) == slots.normalize(expected_path), (tag, original)
            assert urlsplit(actual).fragment == urlsplit(expected).fragment
        else:
            assert actual == expected, (tag, original)
    for reference in references[:6]:
        assert world.get(reference)[0] == 200
    assert world.get(dashboard.view_url(world.outside / "secret.md"))[0] == 403


def test_mission_page_validates_slot_and_id(world):
    folder = world.mission("slot1", "run-1")
    write_question(folder, "ask-10.md", "질문", [("남은 결정", "pending")])
    write_question(folder, "ask-2.md", "질문", [("완료 결정", "answered")], answer="권장")
    status, text = world.get("/mission?slot=slot1&id=run-1")
    assert status == 200
    originals = text.split("질문·확인 원문</h2>")[1]
    assert originals.index("ask-2.md") < originals.index("ask-10.md") and "답 있음" in originals and "전체 1개 · 미답 1개" in originals
    assert world.get("/mission?slot=slot1&id=..%2F..")[0] == 400
    assert world.get("/mission?slot=..&id=run-1")[0] == 400
    assert world.get("/mission?slot=slot9&id=run-1")[0] == 404
    assert world.get("/mission?slot=slot1&id=nope")[0] == 404
    assert world.get("/elsewhere")[0] == 404


POC_REL = "Wiki/design/prototypes/x/index.html"


def add_poc(folder, root, rel=POC_REL, write=True):
    mission_md = folder / "mission.md"
    mission_md.write_text(mission_md.read_text(encoding="utf-8").replace("확인 A1: 대기\n", f"확인 A1: 대기\nPoC: {rel}\n"), encoding="utf-8")
    poc = root / rel
    if write:
        poc.parent.mkdir(parents=True)
        poc.write_text("<img src='../../../../Unity/a.png'>", encoding="utf-8")
    return poc


def raw_get(world, path):
    return dashboard.route(world.config, path)


def test_mission_page_lists_poc_first_and_links_through_raw(world):
    slot_poc = add_poc(world.mission("slot1", "run-1"), world.slot_root / "slot1")
    main_poc = add_poc(world.mission("main", "run-2"), world.main)
    status, text = world.get("/mission?slot=slot1&id=run-1")
    assert status == 200 and f"PoC: {POC_REL}" not in text
    assert f'href="{dashboard.raw_url(slot_poc)}"' in text and f"PoC · {POC_REL}" in text
    assert text.index(f"PoC · {POC_REL}") < text.index("mission.md")
    status, text = world.get("/mission?slot=main&id=run-2")
    assert status == 200 and f'href="{dashboard.raw_url(main_poc)}"' in text


def test_mission_page_warns_when_poc_file_is_missing(world):
    add_poc(world.mission("slot1", "run-1"), world.slot_root / "slot1", write=False)
    status, text = world.get("/mission?slot=slot1&id=run-1")
    assert status == 200 and f"PoC 파일 없음: {POC_REL}" in text and "/raw/" not in text
    world.mission("slot1", "run-2")
    assert "PoC" not in world.get("/mission?slot=slot1&id=run-2")[1]


def test_raw_serves_file_bytes_with_content_type_by_extension(world):
    root = world.slot_root / "slot1"
    poc = add_poc(world.mission("slot1", "run-1"), root)
    status, content_type, body = raw_get(world, dashboard.raw_url(poc))
    assert (status, content_type, body) == (200, "text/html; charset=utf-8", poc.read_bytes())
    expected = {
        "a.png": "image/png",
        "a.js": "text/javascript; charset=utf-8",
        "a.mjs": "text/javascript; charset=utf-8",
        "a.css": "text/css; charset=utf-8",
        "a.json": "application/json; charset=utf-8",
        "a.svg": "image/svg+xml; charset=utf-8",
        "a.woff2": "font/woff2",
        "a.ttf": "font/ttf",
        "a.txt": "text/plain; charset=utf-8",
        "a.unknownext": "application/octet-stream",
    }
    for name, want in expected.items():
        (root / name).write_bytes(b"x")
        assert raw_get(world, dashboard.raw_url(root / name))[:2] == (200, want), name


def test_raw_resolves_sibling_assets_from_relative_urls_in_the_page(world):
    root = world.slot_root / "slot1"
    poc = add_poc(world.mission("slot1", "run-1"), root)
    (root / "Unity").mkdir()
    (root / "Unity" / "한글 a#1.png").write_bytes(b"\x89PNG\r\n")
    page_url = dashboard.raw_url(poc)
    sibling = urljoin(page_url, "../../../../Unity/" + quote("한글 a#1.png"))
    assert sibling.startswith("/raw/") and "/Wiki/" not in sibling
    assert raw_get(world, sibling) == (200, "image/png", b"\x89PNG\r\n")


def test_raw_refuses_outside_roots_and_reports_missing_files(world):
    root = world.slot_root / "slot1"
    poc = add_poc(world.mission("slot1", "run-1"), root)
    assert raw_get(world, dashboard.raw_url(world.outside / "secret.md"))[0] == 403
    sneaky = (str(root) + "/../../outside/secret.md").replace("\\", "/")
    assert raw_get(world, "/raw/" + quote(sneaky, safe="/:"))[0] == 403
    assert raw_get(world, "/raw/" + quote(sneaky.replace("/..", "/%2e%2e"), safe="/:%"))[0] == 403
    assert raw_get(world, "/raw/relative/index.html")[0] == 403
    assert raw_get(world, "/raw/")[0] == 403
    assert raw_get(world, dashboard.raw_url(world.slot_root))[0] == 403
    assert raw_get(world, dashboard.raw_url(root / "nope.png"))[0] == 404
    assert raw_get(world, dashboard.raw_url(poc.parent))[0] == 404


def test_unanswered_questions_uses_only_structured_status(tmp_path):
    write_question(tmp_path, "ask-1.md", "미답", [("결정", "pending")], answer="권장", body="## 부록\n답변 완료")
    write_question(tmp_path, "ask-2.md", "완료", [("결정", "answered")], answer="대안", intro="답변 대기")
    (tmp_path / "ask-3.md").write_text("# 과거 질문\n\n## 답\n권장\n", encoding="utf-8")
    assert mission_state.unanswered_questions(str(tmp_path), {}) == ["ask-1.md"]


def test_confirm_files_keep_typed_pending_when_mission_header_is_approved(tmp_path):
    write_question(tmp_path, "confirm-A2.md", "확인 A2", [("결정", "pending")], answer="조건부 승인")
    write_question(tmp_path, "confirm-C.md", "확인 C", [("결정", "answered")], approval="pending", answer="문항 승인")
    fields = {"확인 A": "승인", "확인 A2": "승인", "확인 C": "승인"}
    assert mission_state.unanswered_questions(str(tmp_path), fields) == ["confirm-A2.md", "confirm-C.md"]


def test_skeleton_confirm_is_waiting_and_labelled(world):
    skeleton = "# 확인 A2\n\n## 뼈대 — 바꾸는 Model\n\n| 칸 |\n\n## 답\n\n- 2026-10-19 사용자: 이름 일반화\n"
    folder = world.mission("slot1", "run-1", files={"confirm-A1.md": "# 확인 A1\n\n## 답\n", "confirm-A2.md": skeleton}, shared=["confirm-A1.md", "confirm-A2.md"])
    write_question(folder, "confirm-A1.md", "확인 A1", [("이전 결정", "answered")], answer="승인")
    record = write_question(folder, "confirm-A2.md", "확인 A2", [("뼈대 확인", "pending")], approval="pending", body="## 뼈대 — 바꾸는 Model\n\n| 칸 |")
    record["items"][0]["id"] = "scope"
    record["answers"][-1]["updates"][0]["id"] = "scope"
    (folder / "questions/confirm-A2.json").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    question_records.render_saved_document(folder, "confirm-A2.md")
    (folder / "mission.md").write_text("# 미션 run-1\n\n상태: 진행\n현재 단계: poc\n확인 A: A1승인\n", encoding="utf-8")
    status, text = world.get("/")
    assert status == 200 and "확인 A2 · 뼈대" in text and "confirm-A1.md" not in text


@pytest.mark.parametrize("runtime", ["claude", "codex"])
def test_server_serves_on_loopback_and_refuses_a_busy_port(world, runtime):
    if runtime == "codex":
        shutil.copytree(world.main / ".claude", world.main / ".codex")
    located = slots.locate(str(world.main), runtime)
    config = slots.Config(located, slots.read_adapter(located.adapter_path, runtime), runtime)
    folder = world.mission("slot1", "run-1")
    poc = add_poc(folder, world.slot_root / "slot1")
    server = dashboard.Server(config, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        assert host == "127.0.0.1"
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10) as response:
            assert response.status == 200
        with urllib.request.urlopen(f"http://127.0.0.1:{port}" + dashboard.view_url(poc), timeout=10) as response:
            assert response.status == 200
            assert urlsplit(response.url).path == dashboard.raw_url(poc)
            assert response.read() == poc.read_bytes()
        with urllib.request.urlopen(f"http://127.0.0.1:{port}" + dashboard.view_url(folder / "mission.md"), timeout=10) as response:
            assert b"const documentUrl" in response.read()
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/view?path=x", timeout=10)
        assert caught.value.code == 403
        with pytest.raises(OSError):
            dashboard.Server(world.config, port)
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("runtime", ["claude", "codex"])
def test_dashboard_is_read_only_and_preserves_answer_history(world, runtime):
    folder = world.mission("slot1", "run-1", files={
        "ask-1.md": "# 질문\n\n## 사용자 입력\n\n이전 답변\n\n## 답\n",
        ".dashboard-answers/saved.json": '{"answer":"이전 답변"}',
    }, shared=["ask-1.md"])
    (folder.parent / "ACTIVE").write_text(json.dumps({"id": folder.name, "session": "session-1"}), encoding="utf-8")
    config = runtime_config(world, runtime)
    status, _, page = dashboard.route(config, dashboard.view_url(folder / "ask-1.md"))
    assert status == 200
    source = page.decode("utf-8").split('id="source"', 1)[1].split('>', 1)[1].split('</script>', 1)[0]
    assert "이전 답변" in json.loads(source)
    assert all(markup not in page for markup in [b"<textarea", b'<form id="answer-form"', b"X-Han-Answer-Token", b'fetch("/answer"'])
    before = {path: path.read_bytes() for path in folder.parent.rglob("*") if path.is_file()}
    server = dashboard.Server(config, 0)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        request = urllib.request.Request(base + "/answer", data=json.dumps({
            "slot": "slot1", "mission": folder.name, "document": "ask-1.md", "answer": "새 답변",
        }).encode("utf-8"), headers={"Content-Type": "application/json", "Origin": base})
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=5)
        assert caught.value.code == 501
        assert not hasattr(server.config, "answer_token")
        assert {path: path.read_bytes() for path in folder.parent.rglob("*") if path.is_file()} == before
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


def write_lines(path, entries):
    path.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")


def claude_turn(stop_reason, at="2026-10-20T02:31:42Z"):
    return [
        {"type": "user", "timestamp": "2026-10-20T02:30:00Z", "message": {"role": "user", "content": "진행해"}},
        {"type": "assistant", "timestamp": at, "message": {"role": "assistant", "stop_reason": stop_reason, "content": [{"type": "text", "text": "ask-8 올림"}]}},
        {"type": "system", "subtype": "turn_duration", "timestamp": at},
        {"type": "last-prompt"},
    ]


def test_read_session_tells_turn_end_from_work_in_progress(tmp_path):
    transcript = tmp_path / "s1.jsonl"
    write_lines(transcript, claude_turn("end_turn"))
    mission = dashboard.Mission("slot1", "run-1", str(tmp_path), True)
    dashboard.read_session(mission, str(transcript))
    assert mission.waiting_input and not mission.background
    assert mission.last_activity == dashboard.parse_time("2026-10-20T02:31:42Z")

    write_lines(transcript, claude_turn("tool_use"))
    mission = dashboard.Mission("slot1", "run-1", str(tmp_path), True)
    dashboard.read_session(mission, str(transcript))
    assert not mission.waiting_input

    subagents = tmp_path / "s1" / "subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent-a.jsonl").write_text("{}\n", encoding="utf-8")
    write_lines(transcript, claude_turn("end_turn"))
    mission = dashboard.Mission("slot1", "run-1", str(tmp_path), True)
    dashboard.read_session(mission, str(transcript))
    assert mission.background


def test_read_session_understands_codex_turn_events(tmp_path):
    transcript = tmp_path / "rollout-2026-10-20T15-56-23-abc.jsonl"
    started = {"timestamp": "2026-10-20T07:00:00Z", "type": "event_msg", "payload": {"type": "task_started"}}
    done = {"timestamp": "2026-10-20T07:00:22Z", "type": "event_msg", "payload": {"type": "task_complete"}}
    write_lines(transcript, [started, done])
    mission = dashboard.Mission("slot1", "run-1", str(tmp_path), True)
    dashboard.read_session(mission, str(transcript))
    assert mission.waiting_input
    write_lines(transcript, [done, started])
    mission = dashboard.Mission("slot1", "run-1", str(tmp_path), True)
    dashboard.read_session(mission, str(transcript))
    assert not mission.waiting_input


def test_card_shows_input_waiting_for_active_session(world, tmp_path):
    transcript = tmp_path / "s1.jsonl"
    write_lines(transcript, claude_turn("end_turn"))
    folder = world.mission("slot1", "run-1")
    (folder / "units.tsv").write_text(f"{UNITS_HEADER}\n", encoding="utf-8")
    header = "\t".join(mission_guard.SESSIONS_HEADER)
    (folder / "sessions.tsv").write_text(f"{header}\n2026-10-20T11:00:00+09:00\topen\ts1\t{transcript}\n", encoding="utf-8")
    (folder.parent / "ACTIVE").write_text(json.dumps({"id": "run-1", "session": "s1", "openedAt": "2026-10-20T11:00:00+09:00"}), encoding="utf-8")
    status, text = world.get("/")
    assert status == 200 and "입력 기다림" in text


def test_answer_heading_variants_do_not_supply_structured_status(tmp_path):
    for number, heading in enumerate(("답변", "답 (2026-10-20)", "답하는 법"), 1):
        write_question(tmp_path, f"ask-{number}.md", "질문", [("결정", "pending")], body=f"## {heading}\n권장")
    assert mission_state.unanswered_questions(str(tmp_path), {}) == ["ask-1.md", "ask-2.md", "ask-3.md"]


def runtime_config(world, runtime):
    if runtime == "codex":
        shutil.copytree(world.main / ".claude", world.main / ".codex", dirs_exist_ok=True)
    located = slots.locate(str(world.main), runtime)
    return slots.Config(located, slots.read_adapter(located.adapter_path, runtime), runtime)


@pytest.mark.parametrize("runtime", ["claude", "codex"])
def test_question_documents_are_automatic_and_other_documents_require_current_sharing(world, runtime):
    files = {
        "ask-1.md": "# 비공개 질문\n\n## 답\n",
        "confirm-B.md": "# 비공개 확인\n\n## 답\n",
        "details/evidence.md": "비공개 상세 원문",
        "captures/x.png": "비공개 캡처 원문",
        "artifact.json": "비공개 생성물 원문",
        ".dashboard-answers/secret.json": "비공개 답변 기록",
    }
    folder = world.mission("slot1", "sharing-1", files=files)
    write_question(folder, "ask-1.md", "비공개 질문", [("남은 범위", "pending")])
    write_question(folder, "confirm-B.md", "비공개 확인", [("남은 승인", "pending")])
    config = runtime_config(world, runtime)
    def get(url):
        status, _, body = dashboard.route(config, url)
        return status, body.decode("utf-8", errors="replace")
    overview = get("/")[1]
    detail = get("/mission?slot=slot1&id=sharing-1")[1]
    assert "보고에만 있는 과거 진행 요약" not in overview and "보고에만 있는 과거 진행 요약" not in detail
    automatic = {"ask-1.md", "confirm-B.md"}
    for name in automatic:
        assert name in overview and name in detail
        assert get(dashboard.view_url(folder / name))[0] == 200
        assert get(dashboard.raw_url(folder / name))[0] == 200
    for name in ("details/history/ask-1.md", "details/history/confirm-B.md"):
        assert name in detail
        assert get(dashboard.view_url(folder / name))[0] == 200
        assert get(dashboard.raw_url(folder / name))[0] == 200
    assert get(dashboard.raw_url(folder / "questions/ask-1.json"))[0] == 404
    for name in [name for name in [*files, "report.md"] if name not in automatic]:
        assert name not in overview and name not in detail
        for url in (dashboard.view_url(folder / name), dashboard.raw_url(folder / name)):
            status, body = get(url)
            assert status == 404 and "공유되지 않은 문서" in body
            assert "비공개" not in body
    assert get(dashboard.view_url(folder / "mission.md"))[0] == 200
    assert get(dashboard.raw_url(folder / "units.tsv"))[0] == 200
    published = {"ask-1.md", "report.md", "details/evidence.md", "captures/x.png", "artifact.json"}
    world.share(folder, *sorted(published))
    overview = get("/")[1]
    detail = get("/mission?slot=slot1&id=sharing-1")[1]
    assert "비공개 질문" in overview and "보고에만 있는 과거 진행 요약" not in overview
    assert "최근 보고" in detail and "보고에만 있는 과거 진행 요약" in detail
    for name in published:
        assert name in detail
        assert get(dashboard.view_url(folder / name))[0] == 200
        assert get(dashboard.raw_url(folder / name))[0] == 200
    assert "confirm-B.md" in detail
    assert mission_state.SHARED_DOCUMENTS_FILE not in detail
    assert get(dashboard.raw_url(folder / mission_state.SHARED_DOCUMENTS_FILE))[0] == 404
    for name in published:
        (folder / name).write_text("수정한 비공개 원문", encoding="utf-8")
    overview = get("/")[1]
    detail = get("/mission?slot=slot1&id=sharing-1")[1]
    for name in published:
        if name in automatic:
            assert name in overview and name in detail
            assert get(dashboard.view_url(folder / name))[0] == 200
            assert get(dashboard.raw_url(folder / name))[0] == 200
            continue
        assert name not in overview and name not in detail
        for url in (dashboard.view_url(folder / name), dashboard.raw_url(folder / name)):
            status, body = get(url)
            assert status == 404 and "수정한 비공개 원문" not in body
    assert "보고에만 있는 과거 진행 요약" not in overview and "보고에만 있는 과거 진행 요약" not in detail


@pytest.mark.parametrize("runtime", ["claude", "codex"])
def test_corrupt_manifest_preserves_auto_questions_and_refuses_other_documents(world, runtime):
    folder = world.mission("slot1", "sharing-1", files={"ask-1.md": "비공개 질문", "details/proof.md": "비공개 근거"})
    config = runtime_config(world, runtime)
    world.share(folder, "ask-1.md", "details/proof.md")
    (folder / mission_state.SHARED_DOCUMENTS_FILE).write_text("잘못된 헤더\n", encoding="utf-8")
    for url in (dashboard.view_url(folder / "ask-1.md"), dashboard.raw_url(folder / "ask-1.md")):
        assert dashboard.route(config, url)[0] == 200
    for url in (dashboard.view_url(folder / "details/proof.md"), dashboard.raw_url(folder / "details/proof.md")):
        assert dashboard.route(config, url)[0] == 404
    status, _, body = dashboard.route(config, "/mission?slot=slot1&id=sharing-1")
    assert status == 200 and b"ask-1.md" in body and b"details/proof.md" not in body
    assert mission_state.SHARED_DOCUMENTS_FILE in body.decode("utf-8")
    assert dashboard.route(config, dashboard.view_url(folder / "mission.md"))[0] == 200
    assert dashboard.route(config, dashboard.raw_url(folder / "units.tsv"))[0] == 200


@pytest.mark.parametrize("runtime", ["claude", "codex"])
@pytest.mark.parametrize("slot", ["main", "slot1"])
def test_auto_questions_and_matching_details_survive_edits_hide_and_manifest_failure(world, runtime, slot):
    folder = world.mission(slot, "automatic", files={
        "ask-7.md": "# 배포 범위 선택\n\n## 답\n",
        "confirm-B.md": "# 구현 범위 승인\n\n## 답\n",
        "details/ask-7.md": "# 질문 상세 근거\n",
        "details/confirm-B.md": "# 확인 상세 근거\n",
        "details/ask-orphan.md": "비공개 단독 상세",
        "nested/ask-private.md": "비공개 중첩 질문",
        ".secret/ask-hidden.md": "비공개 숨김 질문",
    })
    write_question(folder, "ask-7.md", "배포 범위 선택", [("배포 범위", "pending")])
    write_question(folder, "confirm-B.md", "구현 범위 승인", [("구현 범위", "pending")])
    config = runtime_config(world, runtime)
    manifest = folder / mission_state.SHARED_DOCUMENTS_FILE
    assert not manifest.exists()
    automatic = {"ask-7.md", "confirm-B.md", "details/ask-7.md", "details/confirm-B.md", "details/history/ask-7.md", "details/history/confirm-B.md"}
    detail_url = f"/mission?slot={slot}&id=automatic"

    def check_documents():
        overview = dashboard.route(config, "/")[2].decode("utf-8")
        detail = dashboard.route(config, detail_url)[2].decode("utf-8")
        assert "ask-7.md" in overview and "confirm-B.md" in overview
        assert all(name in detail for name in automatic)
        for name in automatic:
            assert dashboard.route(config, dashboard.view_url(folder / name))[0] == 200
            assert dashboard.route(config, dashboard.raw_url(folder / name))[0] == 200
        for name in ("report.md", "details/ask-orphan.md", "nested/ask-private.md", ".secret/ask-hidden.md"):
            assert name not in detail
            assert dashboard.route(config, dashboard.view_url(folder / name))[0] == 404
            assert dashboard.route(config, dashboard.raw_url(folder / name))[0] == 404
        return overview, detail

    check_documents()
    assert not manifest.exists()
    world.share(folder, "ask-7.md", "details/ask-7.md")
    write_question(folder, "ask-7.md", "바뀐 배포 범위", [("배포 범위", "pending")])
    (folder / "details/ask-7.md").write_text("# 바뀐 상세 근거\n", encoding="utf-8")
    assert "바뀐 배포 범위" in check_documents()[0]
    assert dashboard.route(config, dashboard.raw_url(folder / "questions/history/ask-7/1.json"))[0] == 404
    assert "바뀐 상세 근거" in dashboard.route(config, dashboard.raw_url(folder / "details/ask-7.md"))[2].decode("utf-8")
    mission_state.hide_documents(str(folder), ["ask-7.md", "details/ask-7.md"])
    check_documents()
    manifest.write_text("잘못된 헤더\n", encoding="utf-8")
    assert mission_state.SHARED_DOCUMENTS_FILE in check_documents()[1]
    write_question(folder, "ask-7.md", "바뀐 배포 범위", [("배포 범위", "answered")], answer="권장안 승인")
    location = next(item for item in dashboard.locations(config) if item.label == slot)
    mission = dashboard.load_mission(location, "automatic")
    assert mission.unanswered == ["confirm-B.md"] and set(mission.questions) == {"ask-7.md", "confirm-B.md"}
    assert "ask-7.md" in dashboard.route(config, detail_url)[2].decode("utf-8")
    for url in (dashboard.view_url(folder / "ask-7.md"), dashboard.raw_url(folder / "ask-7.md")):
        assert dashboard.route(config, url)[0] == 200
    header = folder / "mission.md"
    header.write_text(header.read_text(encoding="utf-8").replace("\n## 목표", "\n확인 B: 승인\n\n## 목표"), encoding="utf-8")
    assert dashboard.load_mission(location, "automatic").unanswered == ["confirm-B.md"]


def directory_alias(link, target):
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


@pytest.mark.parametrize("runtime", ["claude", "codex"])
def test_auto_question_junction_aliases_cannot_publish_private_documents(world, runtime):
    folder = world.mission("slot1", "aliases", files={
        "ask-1.md": "# 공개 질문\n",
        "private/ask-1.md": "비공개 상세",
        "private/secret.md": "비공개 원문",
        ".hidden/ask-1.md": "숨김 원문",
    })
    config = runtime_config(world, runtime)
    directory_alias(folder / "details", folder / "private")
    directory_alias(folder / "ask-alias.md", folder / "private")
    directory_alias(folder / "ask-escape.md", world.outside)
    directory_alias(folder / ".hidden-alias", folder)
    external = world.slot_root / "slot1" / "private-alias"
    directory_alias(external, folder)
    paths = [folder / "details/ask-1.md", folder / "ask-alias.md/ask-1.md", folder / "ask-alias.md/secret.md", folder / ".hidden-alias/ask-1.md", external / "ask-1.md"]
    assert mission_state.auto_question_documents(str(folder)) == {"ask-1.md"}
    for path in paths:
        for url in (dashboard.view_url(path), dashboard.raw_url(path)):
            assert dashboard.route(config, url)[0] == 404
    escape = folder / "ask-escape.md/secret.md"
    for url in (dashboard.view_url(escape), dashboard.raw_url(escape)):
        assert dashboard.route(config, url)[0] == 403
    assert dashboard.route(config, dashboard.raw_url(folder / "ask-1.md"))[0] == 200
    world.share(folder, "private/secret.md")
    assert dashboard.route(config, dashboard.raw_url(external / "private/secret.md"))[0] == 200


@pytest.mark.parametrize("runtime", ["claude", "codex"])
def test_nested_mission_questions_do_not_publish_unshared_enclosing_content(world, runtime):
    outer = world.mission("slot1", "outer")
    inner = outer / "nested"
    inner.mkdir()
    (inner / "mission.md").write_text("# 중첩 미션\n", encoding="utf-8")
    (inner / "ask-1.md").write_text("# 비공개 중첩 질문\n", encoding="utf-8")
    config = runtime_config(world, runtime)
    assert mission_state.auto_question_documents(str(outer)) == set()
    for url in (dashboard.view_url(inner / "ask-1.md"), dashboard.raw_url(inner / "ask-1.md")):
        assert dashboard.route(config, url)[0] == 404


@pytest.mark.parametrize("runtime", ["claude", "codex"])
@pytest.mark.parametrize("target_kind", ["outside", "private", "mission"])
def test_mission_root_junctions_cannot_publish_another_directory(world, runtime, target_kind):
    original = world.mission("slot1", "original", files={"ask-1.md": "# 기존 미션 질문\n"})
    target = world.outside
    if target_kind == "private":
        target = original.parents[2] / "private"
        target.mkdir()
    elif target_kind == "mission":
        target = original
    if target_kind in ("outside", "private"):
        (target / "mission.md").write_text("# 비공개 대상 미션\n\n상태: 진행\n", encoding="utf-8")
        (target / "ask-7.md").write_text("# 비공개 대상 질문\n\n## 답\n", encoding="utf-8")
    alias = original.parent / "linked"
    directory_alias(alias, target)
    config = runtime_config(world, runtime)
    location = next(item for item in dashboard.locations(config) if item.label == "slot1")
    mission = dashboard.load_mission(location, "linked")
    assert mission.fields == {} and mission.questions == {} and "미션 폴더 경로" in mission.errors
    collected = next(item for item in dashboard.collect(config) if item.label == "slot1")
    assert [item.id for item in collected.missions] == ["original"]
    overview = dashboard.route(config, "/")[2].decode("utf-8")
    assert "비공개 대상" not in overview
    assert dashboard.route(config, "/mission?slot=slot1&id=linked")[0] == 404
    for name in ("ask-7.md", "ask-1.md", "mission.md"):
        for url in (dashboard.view_url(alias / name), dashboard.raw_url(alias / name)):
            assert dashboard.route(config, url)[0] in (403, 404)
    assert dashboard.route(config, dashboard.raw_url(original / "ask-1.md"))[0] == 200


@pytest.mark.parametrize("runtime", ["claude", "codex"])
def test_configured_parent_state_directory_alias_keeps_automatic_questions(world, runtime):
    original = world.mission("slot1", "automatic-parent", files={"ask-1.md": "# 부모 별칭 질문\n\n## 답\n"})
    write_question(original, "ask-1.md", "부모 별칭 질문", [("부모 경로 결정", "pending")])
    state_dir = original.parent
    store = original.parents[2] / "mission-store"
    state_dir.rename(store)
    directory_alias(state_dir, store)
    config = runtime_config(world, runtime)
    overview = dashboard.route(config, "/")[2].decode("utf-8")
    assert "부모 별칭 질문" in overview
    assert dashboard.route(config, "/mission?slot=slot1&id=automatic-parent")[0] == 200
    for path in (original / "ask-1.md", store / "automatic-parent/ask-1.md"):
        for url in (dashboard.view_url(path), dashboard.raw_url(path)):
            assert dashboard.route(config, url)[0] == 200


@pytest.mark.parametrize("runtime", ["claude", "codex"])
def test_unshared_mission_documents_cannot_be_downloaded_through_symlinks(world, runtime):
    folder = world.mission("slot1", "sharing-1", files={"details/proof.md": "비공개 근거"})
    config = runtime_config(world, runtime)
    alias = world.slot_root / "slot1" / "alias.md"
    outside = world.slot_root / "slot1" / "Wiki" / "guide.md"
    outside.parent.mkdir()
    outside.write_text("외부 위키 원문", encoding="utf-8")
    escape = folder / "details" / "wiki.md"
    try:
        alias.symlink_to(folder / "details" / "proof.md")
        escape.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    for path in (alias, escape):
        for url in (dashboard.view_url(path), dashboard.raw_url(path)):
            status, _, body = dashboard.route(config, url)
            assert status == 404 and "공유되지 않은 문서" in body.decode("utf-8")
    assert dashboard.route(config, dashboard.view_url(outside))[0] == 200
    world.share(folder, "details/proof.md")
    assert dashboard.route(config, dashboard.view_url(alias))[0] == 200


@pytest.mark.parametrize("runtime", ["claude", "codex"])
def test_mission_documents_stay_private_while_mission_header_is_missing(world, runtime):
    folder = world.mission("slot1", "sharing-1", files={"ask-1.md": "비공개 질문"})
    config = runtime_config(world, runtime)
    (folder / "mission.md").unlink()
    for url in (dashboard.view_url(folder / "ask-1.md"), dashboard.raw_url(folder / "ask-1.md")):
        status, _, body = dashboard.route(config, url)
        assert status == 404 and "공유되지 않은 문서" in body.decode("utf-8")


def subagent_entries(*, last):
    work = [
        {"type": "user", "timestamp": "2026-10-20T08:30:00Z", "message": {"role": "user", "content": "리뷰해"}},
        {"type": "assistant", "timestamp": "2026-10-20T08:30:05Z", "message": {"role": "assistant", "stop_reason": "tool_use", "content": [{"type": "tool_use", "name": "Grep"}]}},
        {"type": "user", "timestamp": "2026-10-20T08:30:06Z", "message": {"role": "user", "content": [{"type": "tool_result", "content": "57:## Data"}]}},
    ]
    endings = {
        "waiting_model": [{"type": "api-request", "timestamp": "2026-10-20T08:30:07Z"}],
        "handed_back": [
            {"type": "assistant", "timestamp": "2026-10-20T08:31:00Z", "message": {"role": "assistant", "stop_reason": "tool_use", "content": [{"type": "tool_use", "name": "SubagentHandback"}]}},
            {"type": "user", "timestamp": "2026-10-20T08:31:01Z", "message": {"role": "user", "content": [{"type": "tool_result", "content": "delivered"}]}},
        ],
        "resumed": [
            {"type": "assistant", "timestamp": "2026-10-20T08:31:00Z", "message": {"role": "assistant", "stop_reason": "tool_use", "content": [{"type": "tool_use", "name": "SubagentHandback"}]}},
            {"type": "user", "timestamp": "2026-10-20T08:31:01Z", "message": {"role": "user", "content": [{"type": "tool_result", "content": "delivered"}]}},
            {"type": "user", "timestamp": "2026-10-20T08:40:00Z", "message": {"role": "user", "content": "하나 더 봐 줘"}},
        ],
    }
    return work + endings[last]


@pytest.mark.parametrize("last, notified, background", [
    ("waiting_model", False, True),
    ("handed_back", False, False),
    ("resumed", False, True),
    ("waiting_model", True, False),
])
def test_subagent_running_is_judged_by_its_own_ending(tmp_path, last, notified, background):
    transcript = tmp_path / "s1.jsonl"
    main = claude_turn("end_turn", at="2026-10-20T08:29:00Z")
    if notified:
        notice = "<task-notification>\n<task-id>abc123</task-id>\n<status>killed</status>\n</task-notification>"
        main.append({"type": "user", "timestamp": "2026-10-20T08:35:00Z", "message": {"role": "user", "content": notice}})
        main.append({"type": "assistant", "timestamp": "2026-10-20T08:35:05Z", "message": {"role": "assistant", "stop_reason": "end_turn", "content": [{"type": "text", "text": "멈춤"}]}})
    write_lines(transcript, main)
    subagents = tmp_path / "s1" / "subagents"
    subagents.mkdir(parents=True)
    write_lines(subagents / "agent-abc123.jsonl", subagent_entries(last=last))
    mission = dashboard.Mission("slot1", "run-1", str(tmp_path), True)
    dashboard.read_session(mission, str(transcript))
    assert mission.waiting_input and mission.background is background


@pytest.mark.parametrize("child_events, background", [
    (["task_started"], True),
    (["task_started", "task_complete"], False),
])
def test_codex_subagent_rollouts_are_found_by_parent_id(tmp_path, child_events, background):
    day = tmp_path / "sessions" / dashboard.time.strftime("%Y/%m/%d")
    day.mkdir(parents=True)
    parent_id = "01a13e0e-f5ad-71f1-9431-954ce57aee7c"
    child_id = "01a13e10-ec8f-7a22-923d-8d8922cacfd4"
    other_id = "01a13e12-3a68-7623-90b7-48d1bc918e54"
    parent = day / f"rollout-2026-10-20T14-35-30-{parent_id}.jsonl"
    write_lines(parent, [
        {"timestamp": "2026-10-20T05:35:30Z", "type": "session_meta", "payload": {"id": parent_id, "thread_source": "user"}},
        {"timestamp": "2026-10-20T05:36:00Z", "type": "event_msg", "payload": {"type": "task_complete"}},
    ])
    child = [{"timestamp": "2026-10-20T05:37:39Z", "type": "session_meta", "payload": {"id": child_id, "parent_thread_id": parent_id}}]
    child += [{"timestamp": "2026-10-20T05:40:00Z", "type": "event_msg", "payload": {"type": event}} for event in child_events]
    write_lines(day / f"rollout-2026-10-20T14-37-39-{child_id}.jsonl", child)
    stranger = [{"timestamp": "2026-10-20T05:39:04Z", "type": "session_meta", "payload": {"id": other_id, "parent_thread_id": "someone-else"}}]
    write_lines(day / f"rollout-2026-10-20T14-39-04-{other_id}.jsonl", stranger + [{"timestamp": "2026-10-20T05:40:00Z", "type": "event_msg", "payload": {"type": "task_started"}}])
    mission = dashboard.Mission("slot1", "run-1", str(tmp_path), True)
    dashboard.read_session(mission, str(parent))
    assert mission.waiting_input and mission.background is background


def write_units(folder, *specs):
    rows = []
    for spec in specs:
        row = dict(zip(mission_state.UNIT_HEADER, UNIT_ROW.split("\t")))
        row.update(spec)
        rows.append("\t".join(row[name] for name in mission_state.UNIT_HEADER))
    (folder / "units.tsv").write_text(UNITS_HEADER + "\n" + "\n".join(rows) + "\n", encoding="utf-8")


def slot_summary(text, name="slot1"):
    return re.search(r'<article[^>]*data-slot="' + name + r'">(.*?)</article>', text, re.S).group(1)


@pytest.mark.parametrize("runtime", ["claude", "codex"])
def test_slot_summary_keeps_goal_current_remaining_conditions_and_other_missions(world, runtime):
    folder = world.mission("slot1", "primary")
    goal = "긴 목표를 끊지 않고 전부 보여야 함 " * 12 + "목표 마지막 문장"
    header = folder / "mission.md"
    header.write_text(header.read_text(encoding="utf-8").replace("primary 목표 첫 줄", goal + "\n목표의 두 번째 줄"), encoding="utf-8")
    write_units(folder,
        {"id": "M1", "goal": "첫 번째 진행 작업", "status": "doing"},
        {"id": "M2", "goal": "병렬 진행 작업", "status": "doing"},
        {"id": "M3", "goal": "완료한 기반 작업", "status": "done"},
        {"id": "M4", "goal": "승인 조건을 가진 다음 작업", "status": "todo", "after": "M3", "human": "확인 B 승인 필요", "forbid": "사람 승인 전 제출 금지"},
        {"id": "M5", "goal": "선행을 기다리는 작업", "status": "todo", "after": "M2"},
        {"id": "M6", "goal": "보류 상태 작업", "status": "held"},
        {"id": "M7", "goal": "잘못된 선행 기록", "status": "todo", "after": "M99"},
    )
    world.mission("slot1", "another")
    (folder.parent / "ACTIVE").write_text(json.dumps({"id": "primary", "session": "s1"}), encoding="utf-8")
    config = runtime_config(world, runtime)
    overview = dashboard.route(config, "/")[2].decode("utf-8")
    card = slot_summary(overview)
    assert goal in card and "목표의 두 번째 줄" in card and 'goal clamp' not in card
    current = card.split('<h3>지금</h3>')[1].split('</section>')[0]
    assert "첫 번째 진행 작업" in current and "병렬 진행 작업" in current
    assert "남은 작업 6개 · 진행 중 포함" in card and "다음 후보" in card and "보류 상태 작업" in card
    assert "선행 M3 완료" in card and "확인 B 승인 필요" in card and "사람 승인 전 제출 금지" in card
    assert "선행 M2 병렬 진행 작업 (진행)" in card
    assert "선행 M99: 등록된 작업을 찾을 수 없음 · 의존 상태 미확인" in card
    assert '같은 슬롯의 다른 진행 미션 1개' in card and 'id=another' in card
    assert overview.index('data-slot="slot1"') < overview.index('class="idle-board"')
    assert 'class="board"' in overview and 'grid-template-columns:repeat(2,minmax(0,1fr))' in overview
    assert '@media(max-width:800px){.board{grid-template-columns:1fr}}' in overview
    assert '<details class="brief-details goal-details" data-key="goal-slot1-primary"><summary class="goal">' in card
    assert 'data-key="current-slot1-primary"' in card and 'data-key="remaining-slot1-primary"' in card
    detail = dashboard.route(config, "/mission?slot=slot1&id=primary")[2].decode("utf-8")
    assert detail.index('id="current"') < detail.index('id="remaining"') < detail.index('data-key="source-documents"')
    completed = re.search(r'<details data-key="completed">(.*?)</details>', detail, re.S).group(1)
    assert "완료 작업 1개" in completed and "완료한 기반 작업" in completed
    assert '<details data-key="completed" open' not in detail
    assert 'M99' in detail and '완료한 기반 작업 (완료)' in detail


def test_missing_plan_and_failed_plan_do_not_claim_zero_percent_or_no_current_work(world):
    folder = world.mission("slot1", "empty-plan")
    write_units(folder)
    overview = world.get("/")[1]
    card = slot_summary(overview)
    assert "작업 계획 미등록" in card and "진행 중으로 기록된 작업 없음" in card
    assert "width:0%" not in card
    (folder / "units.tsv").write_text("잘못된 헤더\n", encoding="utf-8")
    card = slot_summary(world.get("/")[1])
    assert "현재 작업 미확인" in card and "남은 일 미확인" in card


def test_no_doing_shows_shared_question_and_held_waits_without_using_session_as_work(world):
    folder = world.mission("slot1", "waiting", files={"ask-1.md": "# 작업 범위 선택\n\n## 답\n"}, shared=["ask-1.md"])
    write_question(folder, "ask-1.md", "작업 범위 선택", [("범위", "pending")])
    write_units(folder, {"id": "M1", "goal": "결정을 기다리는 작업", "status": "held"})
    card = slot_summary(world.get("/")[1])
    assert "진행 중으로 기록된 작업 없음 · 공유된 질문 답변 대기 · 보류 작업 1개 확인 필요" in card
    assert "작업 범위 선택" in card and "전체 1개 · 미답 1개" in card
    assert 'title="답변 필요 1건"' in card
    assert "세션: 세션 없음" in card and "내 결정" in card


@pytest.mark.parametrize("runtime", ["claude", "codex"])
def test_question_summary_excludes_answered_items_and_keeps_explicit_confirm_approval_wait(world, runtime):
    folder = world.mission("slot1", "questions")
    write_question(folder, "ask-1.md", "저장 정책 선택", [("이미 끝난 결정", "answered"), ("남은 보관 기간 결정", "pending")], answer="첫 항목 승인")
    write_question(folder, "confirm-B.md", "최종 범위 승인", [("개별 답은 완료", "answered")], approval="pending", answer="개별 답 완료")
    config = runtime_config(world, runtime)
    card = slot_summary(dashboard.route(config, "/")[2].decode("utf-8"))
    assert "저장 정책 선택" in card and "남은 보관 기간 결정" in card
    assert "이미 끝난 결정" not in card and "개별 답은 완료" not in card
    assert 'title="답변 필요 1건"' in card
    assert "전체 승인 확인 대기" in card
    assert '<p class="document-name">ask-1.md</p>' in card and '<p class="document-name">confirm-B.md</p>' in card


@pytest.mark.parametrize("runtime", ["claude", "codex"])
@pytest.mark.parametrize("answer", ["권장", "대안"])
@pytest.mark.parametrize("name", ["ask-1.md", "confirm-A2.md", "confirm-B.md", "confirm-C.md"])
def test_complete_structured_answers_leave_decisions_and_remain_in_originals(world, runtime, answer, name):
    folder = world.mission("slot1", "answered")
    write_question(folder, name, "완료한 결정", [("완료 항목", "answered")], answer=answer)
    original = (folder / name).read_bytes()
    config = runtime_config(world, runtime)
    mission = dashboard.collect(config)[1].missions[0]
    assert not mission.unanswered and not mission.question_issues
    assert mission.questions[name].count_known and mission.questions[name].total_known
    assert "완료한 결정" not in slot_summary(dashboard.route(config, "/")[2].decode("utf-8"))
    detail = dashboard.route(config, "/mission?slot=slot1&id=answered")[2].decode("utf-8")
    decisions = re.search(r'<section class="work-section"><h2>내 결정</h2>(.*?)</section>', detail, re.S).group(1)
    assert "완료한 결정" not in decisions and "답 있음 · 전체 1개" in detail
    assert dashboard.route(config, dashboard.view_url(folder / name))[0] == 200
    assert (folder / name).read_bytes() == original


@pytest.mark.parametrize("runtime", ["claude", "codex"])
@pytest.mark.parametrize("intro", ["답변 완료", "답변 대기", "질문 1 답변 접수 · 승인", "답변 완료 · 승인 대기"])
@pytest.mark.parametrize("status,approval,pending", [("answered", "none", False), ("pending", "none", True), ("answered", "pending", True), ("withdrawn", "complete", False)])
def test_typed_status_ignores_prose_and_mission_approval_headers(world, runtime, intro, status, approval, pending):
    folder = world.mission("slot1", "typed-status")
    write_question(folder, "confirm-A2.md", "문서 답변 상태 회귀", [("범위 결정", status)], approval=approval, answer="조건 확인 후 권장으로", intro=intro)
    header = folder / "mission.md"
    header.write_text(header.read_text(encoding="utf-8").replace("확인 A1: 대기", "확인 A1: 대기\n확인 A2: 승인"), encoding="utf-8")
    config = runtime_config(world, runtime)
    before = {path: path.read_bytes() for path in folder.rglob("*") if path.is_file()}
    mission = dashboard.collect(config)[1].missions[0]
    assert mission.questions["confirm-A2.md"].pending == pending
    assert bool(mission.unanswered) == pending and not mission.question_issues
    card = slot_summary(dashboard.route(config, "/")[2].decode("utf-8"))
    detail = dashboard.route(config, "/mission?slot=slot1&id=typed-status")[2].decode("utf-8")
    decisions = re.search(r'<section class="work-section"><h2>내 결정</h2>(.*?)</section>', detail, re.S).group(1)
    assert ("문서 답변 상태 회귀" in card) == pending
    assert ("문서 답변 상태 회귀" in decisions) == pending
    assert {path: path.read_bytes() for path in folder.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize("runtime", ["claude", "codex"])
def test_partial_and_conditional_answers_keep_typed_pending_counts(world, runtime):
    folder = world.mission("slot1", "partial")
    write_question(folder, "ask-1.md", "부분 결정", [("끝난 결정", "answered"), ("조건부 결정", "pending"), ("남은 결정", "pending")], answer="1은 권장, 2는 조건 확인 후 진행")
    write_question(folder, "ask-2.md", "철회 결정", [("철회 항목", "withdrawn")], answer="이 항목은 철회")
    write_question(folder, "confirm-B.md", "최종 승인", [("답한 항목", "answered")], approval="pending", answer="문항만 승인")
    config = runtime_config(world, runtime)
    mission = dashboard.collect(config)[1].missions[0]
    assert dashboard.decision_badge([mission]) == "답변 필요 2건"
    assert dashboard.waiting_text(mission) == "대기 문서 2개 · 미답 항목 2개 · 전체 승인 확인 대기 1문서"
    card = slot_summary(dashboard.route(config, "/")[2].decode("utf-8"))
    assert "조건부 결정" in card and "남은 결정" in card and "끝난 결정" not in card
    assert "철회 결정" not in card and "전체 승인 확인 대기" in card


@pytest.mark.parametrize("runtime", ["claude", "codex"])
@pytest.mark.parametrize("problem", ["legacy", "malformed", "malformed-source-only", "missing-markdown", "changed-markdown", "wrong-source-id"])
def test_invalid_question_records_are_separate_from_pending_and_answered(world, runtime, problem):
    folder = world.mission("slot1", "record-issues")
    write_question(folder, "ask-1.md", "유효한 미답", [("남은 정책", "pending")])
    write_question(folder, "ask-2.md", "오류 문서", [("이미 답한 정책", "answered")], answer="권장")
    source = folder / "questions/ask-2.json"
    if problem == "legacy":
        source.unlink()
    elif problem in ("malformed", "malformed-source-only"):
        source.write_text("{invalid", encoding="utf-8")
        if problem == "malformed-source-only":
            (folder / "ask-2.md").unlink()
    elif problem == "missing-markdown":
        (folder / "ask-2.md").unlink()
    elif problem == "changed-markdown":
        (folder / "ask-2.md").write_text("# 변경한 원문\n\n답변 완료\n- 답: 대안\n", encoding="utf-8")
    else:
        record = json.loads(source.read_text(encoding="utf-8"))
        record["id"] = "ask-9"
        source.write_text(json.dumps(record), encoding="utf-8")
    config = runtime_config(world, runtime)
    before = {path: path.read_bytes() for path in folder.rglob("*") if path.is_file()}
    mission = dashboard.collect(config)[1].missions[0]
    assert mission.unanswered == ["ask-1.md"] and set(mission.questions) == {"ask-1.md"}
    assert set(mission.question_issues) == {"ask-2.md"}
    overview = dashboard.route(config, "/")[2].decode("utf-8")
    card = slot_summary(overview)
    assert 'title="답변 필요 1건"' in card and 'title="기록 확인 필요 1건"' in card
    assert "<h3>내 결정</h3>" in card and "<h3>기록 확인 필요</h3>" in card
    assert "기록 확인 필요</h2>" in overview
    detail = dashboard.route(config, "/mission?slot=slot1&id=record-issues")[2].decode("utf-8")
    decisions = re.search(r'<section class="work-section"><h2>내 결정</h2>(.*?)</section>', detail, re.S).group(1)
    issues = detail_dom(detail).elements("section", id="question-issues")[0]
    assert "ask-2.md" not in decisions and "ask-2.md" in issues.text()
    assert "답 있음" not in issues.text() and "답 있음" not in detail
    if problem == "legacy":
        assert "정형 전환 필요" in issues.text()
    if problem not in ("missing-markdown", "malformed-source-only"):
        assert dashboard.route(config, dashboard.view_url(folder / "ask-2.md"))[0] == 200
        assert dashboard.route(config, dashboard.raw_url(folder / "ask-2.md"))[0] == 200
    assert {path: path.read_bytes() for path in folder.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize("runtime", ["claude", "codex"])
@pytest.mark.parametrize("problem", ["directory-file", "external-directory", "invalid-source-name"])
def test_question_source_directory_errors_remain_visible(world, runtime, problem):
    folder = world.mission("slot1", "source-errors")
    if problem == "directory-file":
        (folder / "questions").write_text("invalid source directory", encoding="utf-8")
    elif problem == "external-directory":
        directory_alias(folder / "questions", world.outside)
    else:
        (folder / "questions").mkdir()
        (folder / "questions/unknown.json").write_text("{}", encoding="utf-8")
    config = runtime_config(world, runtime)
    mission = dashboard.collect(config)[1].missions[0]
    assert mission.questions == {} and mission.unanswered == [] and mission.question_issues
    overview = dashboard.route(config, "/")[2].decode("utf-8")
    detail = dashboard.route(config, "/mission?slot=slot1&id=source-errors")[2].decode("utf-8")
    assert "기록 확인 필요 1건" in overview and 'id="question-issues"' in detail
    assert "비밀" not in overview and "비밀" not in detail


@pytest.mark.parametrize("runtime", ["claude", "codex"])
def test_closed_legacy_questions_remain_available_as_originals(world, runtime):
    folder = world.mission("slot1", "closed-legacy", state="닫힘", files={"ask-1.md": "# 과거 질문\n\n- 답: 권장\n"})
    config = runtime_config(world, runtime)
    assert "과거 질문" not in dashboard.route(config, "/")[2].decode("utf-8").split('data-key="mission-index"')[0]
    detail = dashboard.route(config, "/mission?slot=slot1&id=closed-legacy")[2].decode("utf-8")
    assert "기록 확인 필요" in detail and "ask-1.md" in detail
    assert dashboard.route(config, dashboard.view_url(folder / "ask-1.md"))[0] == 200
    assert "- 답: 권장" in dashboard.route(config, dashboard.raw_url(folder / "ask-1.md"))[2].decode("utf-8")


def test_reports_remain_dated_history_and_do_not_change_work_or_session_freshness(world):
    folder = world.mission("slot1", "freshness", shared=["report.md"])
    state_time = dashboard.parse_time("2026-10-20T03:00:00Z")
    report_time = dashboard.parse_time("2026-10-18T03:00:00Z")
    for name in ("mission.md", "units.tsv"):
        os.utime(folder / name, (state_time, state_time))
    os.utime(folder / "report.md", (report_time, report_time))
    transcript = folder / "session.jsonl"
    write_lines(transcript, claude_turn("end_turn", at="2026-10-22T03:00:00Z"))
    (folder / "sessions.tsv").write_text("\t".join(mission_guard.SESSIONS_HEADER) + f"\n2026-10-22T12:00:00+09:00\topen\ts1\t{transcript}\n", encoding="utf-8")
    (folder.parent / "ACTIVE").write_text(json.dumps({"id": "freshness", "session": "s1"}), encoding="utf-8")
    mission = dashboard.collect(world.config)[1].missions[0]
    assert mission.state_updated == state_time and mission.report_updated == report_time
    assert mission.last_activity > mission.state_updated
    card = slot_summary(world.get("/")[1])
    assert "보고에만 있는 과거 진행 요약" not in card and "세션: 입력 기다림" in card
    card_meta = re.search(r'<details class="card-meta">(.*?)</details>', card, re.S).group(1)
    assert f'업무 자료 갱신 {dashboard.dated(state_time)}' in card_meta
    detail = world.get("/mission?slot=slot1&id=freshness")[1]
    report = re.search(r'<details data-key="recent-report">(.*?)</details>', detail, re.S).group(1)
    assert "최근 보고" in report and dashboard.dated(report_time) in report and "보고에만 있는 과거 진행 요약" in report
    assert "보고에만 있는 과거 진행 요약" not in detail.split('data-key="recent-report"')[0]


def test_summary_escapes_work_and_flags_changed_question_markdown(world, monkeypatch):
    folder = world.mission("slot1", "escaping")
    write_question(folder, "ask-1.md", '<script>질문</script>', [("범위", "pending")])
    write_units(folder, {"goal": '<img src=x onerror="alert(1)">', "human": "A & B"})
    card = slot_summary(world.get("/")[1])
    assert '<img src=x' not in card and '&lt;img src=x' in card
    assert '<script>질문</script>' not in card and '&lt;script&gt;질문&lt;/script&gt;' in card
    assert 'A &amp; B' in card
    original = dashboard.read_shared_text
    def concurrent_update(root, path):
        if Path(path).name == "ask-1.md":
            Path(path).write_text("# 변경한 제목\n", encoding="utf-8")
        return original(root, path)
    monkeypatch.setattr(dashboard, "read_shared_text", concurrent_update)
    overview = world.get("/")[1]
    assert "기록 확인 필요 1건" in overview and "&lt;script&gt;질문" not in overview
    assert "생성 Markdown과 질문 JSON 불일치" in overview


@pytest.mark.parametrize("runtime", ["claude", "codex"])
def test_one_unreadable_shared_question_does_not_answer_other_questions(world, runtime):
    folder = world.mission("slot1", "unreadable", files={"ask-1.md": ""})
    write_question(folder, "confirm-B.md", "정상 승인 질문", [("아직 결정할 배포 범위", "pending")])
    write_question(folder, "ask-2.md", "정상 일반 질문", [("정상 미답", "pending")])
    (folder / "ask-1.md").write_bytes(b"\xff\xfe")
    config = runtime_config(world, runtime)
    card = slot_summary(dashboard.route(config, "/")[2].decode("utf-8"))
    assert "정상 승인 질문" in card and "아직 결정할 배포 범위" in card
    assert "정상 일반 질문" in card and 'title="답변 필요 2건"' in card
    assert 'title="기록 확인 필요 1건"' in card and "ask-1.md" in card


def test_reshared_question_pending_is_computed_from_the_single_verified_snapshot(world, monkeypatch):
    folder = world.mission("slot1", "snapshot")
    write_question(folder, "ask-1.md", "정책 결정", [("이전 정책", "answered")], answer="승인")
    read_text = dashboard.read_shared_text
    calls = []
    def reshare_before_read(root, path):
        if Path(path).name == "ask-1.md":
            calls.append(path)
            write_question(folder, "ask-1.md", "새 정책 결정", [("재검토할 정책", "pending")])
            world.share(folder, "ask-1.md")
        return read_text(root, path)
    monkeypatch.setattr(dashboard, "read_shared_text", reshare_before_read)
    card = slot_summary(world.get("/")[1])
    assert len(calls) == 1
    assert "새 정책 결정" in card and "재검토할 정책" in card and "답변 필요 1건" in card


def test_question_titles_and_answer_badge_cover_all_running_missions(world):
    primary = world.mission("slot1", "primary", files={"ask-1.md": "# 질문 1 · 보관 기간\n\n질문 1 답변 대기\n\n## 결정할 것\n\n### 1. 보관 기간\n"}, shared=["ask-1.md"])
    other = world.mission("slot1", "other", files={"ask-2.md": "# 질문 2\n\n질문 1 답변 대기 · 질문 2 답변 대기\n\n## 결정할 것\n\n### 1. 배포 범위\n\n### 2. 검증 범위\n"}, shared=["ask-2.md"])
    write_question(primary, "ask-1.md", "질문 1 · 보관 기간", [("보관 기간", "pending")])
    write_question(other, "ask-2.md", "질문 2", [("배포 범위", "pending"), ("검증 범위", "pending")])
    (primary.parent / "ACTIVE").write_text(json.dumps({"id": "primary", "session": "s1"}), encoding="utf-8")
    card = slot_summary(world.get("/")[1])
    assert 'title="답변 필요 3건"' in card
    assert "질문 1 · 보관 기간" in card and "질문 2 · 배포 범위 외 1개" in card
    assert '<p class="document-name">ask-2.md · other</p>' in card
    detail = world.get("/view?path=" + quote(str(other / "ask-2.md"), safe=""))[1]
    assert "<title>질문 2 · 배포 범위 외 1개</title>" in detail
    confirmation = world.mission("slot2", "confirmation", files={"confirm-B2.md": "# 확인 B2\n\n질문 1 답변 대기 · 질문 2 답변 대기\n\n## 결정할 것\n\n### 1. 서버 범위\n\n### 2. 데이터 범위\n"}, shared=["confirm-B2.md"])
    write_question(confirmation, "confirm-B2.md", "확인 B2", [("서버 범위", "pending"), ("데이터 범위", "pending")])
    detail = world.get("/view?path=" + quote(str(confirmation / "confirm-B2.md"), safe=""))[1]
    assert "<title>확인 B2 · 서버 범위 외 1개</title>" in detail


def test_overview_previews_one_candidate_and_questions_without_repeating_doing(world):
    items = "\n\n".join(f"### {number}. 결정할 항목 {number}" for number in range(1, 7))
    question = "# 여섯 가지 결정\n\n답변 대기\n\n## 결정할 것\n\n" + items
    folder = world.mission("slot1", "compact", files={"ask-1.md": question}, shared=["ask-1.md"])
    write_question(folder, "ask-1.md", "여섯 가지 결정", [(f"결정할 항목 {number}", "pending") for number in range(1, 7)])
    write_units(folder,
        {"id": "M1", "goal": "진행 작업은 현재에만", "status": "doing"},
        {"id": "M2", "goal": "첫 후보", "status": "todo", "after": "M1"},
        {"id": "M3", "goal": "보류 후보", "status": "held"},
        {"id": "M4", "goal": "세 번째 후보", "status": "todo"},
    )
    card = slot_summary(world.get("/")[1])
    card_root = detail_dom(card)
    next_section = [node for node in card_root.elements("section") if any(heading.text() == "다음" for heading in node.elements("h3"))][0]
    preview = next_section.text(visible=True)
    assert "첫 후보" in preview and "보류 후보" not in preview and "세 번째 후보" not in preview
    assert "진행 작업은 현재에만" not in preview
    assert "남은 4개" in preview and "진행 중 포함" not in preview
    remaining = next_section.elements("details", **{"data-key": "remaining-slot1-compact"})[0].text()
    assert "남은 작업 4개 · 진행 중 포함" in remaining
    assert "첫 후보" in remaining and "보류 후보" in remaining and "세 번째 후보" in remaining
    decisions = card.split('<h3>내 결정</h3>')[1]
    question_preview = decisions.split('<details data-key="question-')[0]
    assert "질문 1 · 여섯 가지 결정" in question_preview
    assert all(f"결정할 항목 {number}" not in question_preview for number in range(1, 7))
    question_details = re.search(r'<details data-key="question-[^"]+">.*?</details>', decisions, re.S).group(0)
    assert "미답 6개 펼치기" in question_details
    assert all(f"결정할 항목 {number}" in question_details for number in range(1, 7))
    keys = re.findall(r'<details data-key="([^"]+)"', card)
    assert len(keys) == len(set(keys))
    assert any(key.startswith("question-") for key in keys)
    detail = world.get("/mission?slot=slot1&id=compact")[1]
    visible_detail = detail.split('data-key="completed"')[0]
    assert all(f"결정할 항목 {number}" in visible_detail for number in range(1, 7))
    assert "세 번째 후보" in visible_detail and "진행 작업은 현재에만" in visible_detail


def test_expanded_goal_shows_full_goal_once_without_repeating_the_preview(world):
    goal = "첫 줄 목표\n둘째 줄 목표\n셋째 줄 목표"
    folder = world.mission("slot1", "goal")
    mission_file = folder / "mission.md"
    mission_file.write_text(mission_file.read_text(encoding="utf-8").replace("goal 목표 첫 줄", goal), encoding="utf-8")
    card = slot_summary(world.get("/")[1])
    details = re.search(r'<details class="brief-details goal-details"[^>]*>(.*?)</details>', card, re.S).group(1)
    summary = re.search(r'<summary class="goal">(.*?)</summary>', details, re.S).group(1)
    assert summary == '<span class="goal-preview">첫 줄 목표</span><span class="goal-collapse">목표 접기</span>'
    assert f'<p class="goal">{goal}</p>' in details
    assert 'data-key="goal-slot1-goal"' in card


def test_embedded_document_uses_shared_renderer_and_access_rules(world):
    folder = world.mission("slot1", "preview", files={"ask-1.md": "# Preview\n\n[Details](details.md)"}, shared=["ask-1.md"])
    direct = dashboard.view_url(folder / "ask-1.md")
    status, text = world.get(direct + "&embedded=1")
    assert status == 200
    assert 'id="source"' in text and 'id="out"' in text
    assert '<header class="mast">' not in text and '<footer class="foot">' not in text
    assert '<dialog id="document-viewer"' not in text
    assert '<header class="mast">' in world.get(direct)[1]
    assert world.get(dashboard.view_url(world.outside / "secret.md") + "&embedded=1")[0] == 403


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js is required to execute document navigation")
def test_document_viewer_navigation_escape_focus_and_refresh():
    script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const origin = 'http://127.0.0.1:8765';
const element = () => ({
  listeners: {}, textContent: '', dataset: {},
  addEventListener(type, callback) { this.listeners[type] = callback; },
  focus(options) { this.focused = options; },
  fire(type, event = {}) { this.listeners[type](event); }
});
const ids = Object.fromEntries(['document-viewer', 'document-frame', 'document-title', 'document-status', 'document-back', 'document-close', 'auto'].map(id => [id, element()]));
const viewer = ids['document-viewer'];
viewer.open = false;
viewer.showModal = () => { viewer.open = true; };
viewer.close = () => { viewer.open = false; viewer.fire('close'); };
const frame = ids['document-frame'];
const content = element();
content.title = 'Question';
content.documentElement = {dataset: {}};
content.querySelectorAll = () => [];
frame.contentDocument = content;
frame.contentWindow = {location: {href: ''}, scrollY: 0, scrollTo(x, y) { this.scrollY = y; }};
const document = element();
document.documentElement = {dataset: {theme: 'dark'}};
document.getElementById = id => ids[id];
document.querySelectorAll = () => [];
let reloads = 0;
let tick = () => {};
const storage = {getItem() { return ''; }, setItem() {}};
const opened = [];
const window = {scrollY: 420, scrollTo() {}, open(...args) { opened.push(args); }};
const context = {URL, document, window, localStorage: storage, sessionStorage: storage,
  location: {href: origin + '/', origin, reload() { reloads++; }},
  setTimeout() { return 1; }, clearTimeout() {}, setInterval(callback) { tick = callback; },
  MutationObserver: class { observe() {} }
};
vm.runInNewContext(input.viewer, context);
vm.runInNewContext(input.refresh, context);
const link = href => ({href, target: '', textContent: 'Question', hasAttribute() { return false; }, focus(options) { this.focused = options; }});
const question = link(origin + '/view?path=question.md#L100');
const click = (target, changes = {}) => ({button: 0, target: {closest() { return target; }},
  preventDefault() { this.prevented = true; }, ...changes});
for (const changes of [{ctrlKey: true}, {metaKey: true}, {shiftKey: true}, {button: 1}]) {
  const event = click(question, changes);
  document.fire('click', event);
  assert.equal(event.prevented, undefined);
  assert.equal(viewer.open, false);
}
for (const suffix of ['zip', 'xlsx', 'html', 'htm', 'png']) {
  const event = click(link(origin + '/view?path=file.' + suffix));
  document.fire('click', event);
  assert.equal(event.prevented, undefined);
  assert.equal(viewer.open, false);
}
document.fire('click', click(question));
assert.equal(viewer.open, true);
assert.equal(new URL(frame.src).searchParams.get('embedded'), '1');
assert.equal(ids['document-back'].disabled, true);
tick();
assert.equal(reloads, 0);
assert.equal(ids.auto.checked, true);
frame.contentWindow.location.href = frame.src;
frame.contentWindow.scrollY = 100;
frame.fire('load');
assert.equal(frame.hidden, false);
assert.equal(frame.contentWindow.scrollY, 100);
assert.equal(content.documentElement.dataset.theme, 'dark');
const fragment = click(link(frame.src + '#section'));
content.fire('click', fragment);
assert.equal(fragment.prevented, undefined);
const originalFrameUrl = frame.contentWindow.location.href;
frame.contentWindow.location.href = origin + '/raw/poc.html';
const htmlFragment = click(link(origin + '/raw/poc.html#section'));
content.fire('click', htmlFragment);
assert.equal(htmlFragment.prevented, undefined);
assert.equal(opened.length, 0);
frame.contentWindow.location.href = originalFrameUrl;
content.fire('click', click(link(origin + '/view?path=archive.zip')));
assert.equal(opened[0][0], origin + '/view?path=archive.zip');
assert.equal(new URL(frame.src).searchParams.get('path'), 'question.md');
frame.contentWindow.scrollY = 650;
content.fire('click', click(link(origin + '/view?path=details.md')));
assert.equal(ids['document-back'].disabled, false);
assert.equal(new URL(frame.src).searchParams.get('path'), 'details.md');
frame.contentWindow.location.href = frame.src;
content.title = 'Details';
frame.fire('load');
content.fire('click', click(link('https://example.com/reference')));
assert.equal(opened[1][0], 'https://example.com/reference');
ids['document-back'].fire('click');
assert.equal(new URL(frame.src).searchParams.get('path'), 'question.md');
frame.contentWindow.location.href = frame.src;
frame.contentWindow.scrollY = 0;
content.title = 'Question';
frame.fire('load');
assert.equal(frame.contentWindow.scrollY, 650);
frame.contentWindow.scrollY = 0;
content.fire('click', click(link(origin + '/view?path=details.md')));
frame.contentWindow.location.href = frame.src;
frame.fire('load');
ids['document-back'].fire('click');
frame.contentWindow.location.href = frame.src;
frame.contentWindow.scrollY = 100;
frame.fire('load');
assert.equal(frame.contentWindow.scrollY, 0);
content.fire('keydown', {key: 'Escape', preventDefault() {}});
assert.equal(viewer.open, false);
assert.equal(question.focused.preventScroll, true);
assert.equal(window.scrollY, 420);
tick();
assert.equal(reloads, 1);
ids.auto.checked = false;
document.fire('click', click(question));
ids['document-close'].fire('click');
tick();
assert.equal(reloads, 1);
"""
    subprocess.run(
        [shutil.which("node"), "-e", script],
        input=json.dumps({
            "viewer": dashboard.DOCUMENT_VIEWER_SCRIPT.removeprefix("\n<script>\n").removesuffix("</script>\n"),
            "refresh": dashboard.REFRESH_SCRIPT.replace("__REFRESH_MS__", "30000").removeprefix("\n<script>\n").removesuffix("</script>\n"),
        }),
        capture_output=True, text=True, encoding="utf-8", check=True,
    )


@pytest.mark.parametrize("file_name,labels", [
    ("units.tsv", {"완료 조건": "done", "진행 상태": "status", "검증 계획": "verify", "변경 단위 (CL·브랜치)": "unit", "시작·재개 시각": "started"}),
    ("ledger.tsv", {"검증 대상": "unit", "판정 결과": "status", "검증 수준": "grade", "검증 근거": "evidence", "기록된 관련·검증 대상 파일": "files"}),
    ("decisions.tsv", {"결정 번호": "id", "논의 내용": "what", "선택 결과": "chosen", "이유": "why", "영향 범위": "affects"}),
    ("sessions.tsv", {"연결 이벤트": "action", "대화 ID": "session", "로그 경로": "transcript", "연결 기록 시각": "at"}),
])
def test_detail_records_explain_field_meaning_and_preserve_escaped_originals(world, file_name, labels):
    folder = world.mission("slot1", "record-contract")
    records = detail_records()
    write_detail_records(folder, records)
    status, source = world.get("/mission?slot=slot1&id=record-contract")
    assert status == 200
    root = detail_dom(source)
    section = root.elements("section", **{"data-table": file_name})[0]
    fields = detail_fields(section)
    for label, column in labels.items():
        assert label in fields
        value = records[file_name][0][column]
        if column in ("started", "at"):
            assert value in [node.attrs.get("datetime", "") for node in section.elements("time")]
        else:
            assert value in fields[label]
    all_values = section.text() + "".join(str(value) for node in section.elements() for value in node.attrs.values())
    for value in records[file_name][0].values():
        assert value in all_values
    assert not section.elements("table")
    assert section.elements("details")
    assert all("open" not in node.attrs for node in section.elements("details"))
    assert not section.elements("img") and not section.elements("script")
    assert all(node.text() != "원문" for node in section.elements("b"))
    assert any(link.attrs.get("href", "") == dashboard.view_url(folder / file_name) for link in section.elements("a"))
    if file_name == "ledger.tsv":
        assert "통과 (pass)" in fields["판정 결과"]
        assert "사람 확인 필요 (needs-human)" in fields["검증 수준"]
        assert "사람 확인 완료" not in section.text() and "승인 완료" not in section.text()
        assert "CL-427777" not in fields["검증 대상"]
    if file_name == "sessions.tsv":
        assert "미션 이어서 연결 (resume)" in fields["연결 이벤트"]


def test_detail_large_record_fields_are_folded_without_losing_any_text(world):
    folder = world.mission("slot1", "large-records")
    records = detail_records()
    long_values = {
        "evidence": '근거 시작 <script>alert("x")</script> & ' + "evidence-원문 " * 10000 + " 근거 끝",
        "files": "파일 시작 src/" + "아주긴경로.cs;" * 15000 + " 파일 끝",
        "verify": "검증 계획 시작 " + "계획-원문 " * 20000 + " 계획 끝",
        "human": "사람 확인 시작 " + "확인-원문 " * 20000 + " 사람 확인 끝",
    }
    assert all(len(value) >= 100000 for value in long_values.values())
    records["ledger.tsv"][0].update({"evidence": long_values["evidence"], "files": long_values["files"]})
    records["units.tsv"][0]["verify"] = long_values["verify"]
    records["units.tsv"][0]["human"] = long_values["human"]
    long_what = "결정 주제 시작 <b>원문</b> & " + "논의-원문 " * 20000 + " 결정 주제 끝"
    assert len(long_what) >= 100000
    records["decisions.tsv"][0]["what"] = long_what
    write_detail_records(folder, records)
    root = detail_dom(world.get("/mission?slot=slot1&id=large-records")[1])
    summaries = [node.text() for node in root.elements("summary")]
    for value in long_values.values():
        assert value in root.text()
        assert value[:80] not in root.text(visible=True)
        assert all(value[:80] not in summary for summary in summaries)
        folded = [node for node in root.elements("details") if "record-full" in node.attrs.get("class", "").split() and value in node.text()]
        assert folded
        assert all("open" not in node.attrs for node in folded)
        assert all("전문 펼치기" in node.elements("summary")[0].text() for node in folded)
    assert not root.elements("section", **{"data-table": "ledger.tsv"})[0].elements("script")
    decision = root.elements("section", **{"data-table": "decisions.tsv"})[0]
    assert long_what in detail_fields(decision)["논의 내용"]
    full_what = [node for node in decision.elements("details") if "record-full" in node.attrs.get("class", "").split() and long_what in node.text()]
    assert full_what and all("open" not in node.attrs for node in full_what)
    assert not decision.elements("b")


@pytest.mark.parametrize("file_name,column,unknown", [
    ("units.tsv", "status", "custom-status<&>"),
    ("ledger.tsv", "status", "custom-verdict<&>"),
    ("ledger.tsv", "grade", "custom-grade<&>"),
    ("sessions.tsv", "action", "custom-action<&>"),
])
def test_detail_unknown_record_tokens_keep_the_original_value(world, file_name, column, unknown):
    folder = world.mission("slot1", "unknown-record")
    records = detail_records()
    records[file_name][0][column] = unknown
    write_detail_records(folder, records)
    status, source = world.get("/mission?slot=slot1&id=unknown-record")
    assert status == 200
    section = detail_dom(source).elements("section", **{"data-table": file_name})[0]
    assert unknown in section.text()
    assert "custom-" not in "".join(node.tag for node in section.elements())


def test_detail_history_uses_recent_first_and_stable_unique_record_keys(world):
    folder = world.mission("slot1", "record-order")
    records = detail_records()
    for name in ("ledger.tsv", "decisions.tsv", "sessions.tsv"):
        original = records[name][0]
        records[name] = [dict(original, at=f"2026-10-23T10:0{number}:00+09:00") for number in range(1, 4)]
    write_detail_records(folder, records)
    first = detail_dom(world.get("/mission?slot=slot1&id=record-order")[1])
    second = detail_dom(world.get("/mission?slot=slot1&id=record-order")[1])
    keys = [node.attrs["data-key"] for node in first.elements("details") if "data-key" in node.attrs]
    assert len(keys) == len(set(keys))
    assert keys == [node.attrs["data-key"] for node in second.elements("details") if "data-key" in node.attrs]
    ids = [node.attrs["id"] for node in first.elements() if "id" in node.attrs]
    assert len(ids) == len(set(ids))
    existing = {}
    for name in ("ledger.tsv", "decisions.tsv", "sessions.tsv"):
        section = first.elements("section", **{"data-table": name})[0]
        entries = [node for node in section.elements("details") if node.attrs.get("class", "") == "record"]
        assert len(entries) == 3
        assert [node.elements("time")[0].attrs["datetime"] for node in entries] == [row["at"] for row in reversed(records[name])]
        existing.update({node.attrs["data-key"]: node.text() for node in entries})
        records[name].append(dict(records[name][-1], at="2026-10-23T10:04:00+09:00"))
    write_detail_records(folder, records)
    appended = detail_dom(world.get("/mission?slot=slot1&id=record-order")[1])
    after = {node.attrs["data-key"]: node.text() for node in appended.elements("details") if "data-key" in node.attrs}
    assert all(after[key] == value for key, value in existing.items())


@pytest.mark.parametrize("file_name", ["units.tsv", "ledger.tsv", "decisions.tsv", "sessions.tsv"])
def test_detail_empty_and_malformed_records_are_distinct_and_reads_are_immutable(world, file_name):
    folder = world.mission("slot1", "record-errors", files={"ask-1.md": "# 질문\n\n## 답\n\n기존 답변\n"}, shared=["ask-1.md"])
    write_detail_records(folder, {name: [] for _, name, _ in dashboard.TABLES})
    empty = detail_dom(world.get("/mission?slot=slot1&id=record-errors")[1]).elements("section", **{"data-table": file_name})[0]
    assert "읽기 실패" not in empty.text()
    assert not empty.elements("details")
    assert "없음" in empty.text() or "0건" in empty.text()
    (folder / file_name).write_text("잘못된 헤더\n깨진 행\n", encoding="utf-8")
    before = {path: path.read_bytes() for path in folder.rglob("*") if path.is_file()}
    status, source = world.get("/mission?slot=slot1&id=record-errors")
    assert status == 200
    root = detail_dom(source)
    broken = root.elements("section", **{"data-table": file_name})[0]
    assert f"읽기 실패: {file_name}" in broken.text()
    assert not broken.elements("details")
    assert "기록 0건" not in broken.text()
    if file_name == "units.tsv":
        completed = root.elements("details", **{"data-key": "completed"})[0]
        assert "미확인" in completed.text() and "완료 작업 0개" not in completed.text()
    assert {path: path.read_bytes() for path in folder.rglob("*") if path.is_file()} == before


def test_detail_record_paths_are_plain_text_without_sharing_private_evidence_or_logs(world):
    folder = world.mission("slot1", "record-private", files={
        "details/private.md": "공유 안 된 근거 내용",
        "private/session-7.jsonl": "공유 안 된 대화 내용",
    })
    records = detail_records()
    evidence = '근거 [비공개](details/private.md) <a href="/raw/private">원문</a>'
    records["ledger.tsv"][0]["evidence"] = evidence
    write_detail_records(folder, records)
    before = {path: path.read_bytes() for path in folder.rglob("*") if path.is_file()}
    status, source = world.get("/mission?slot=slot1&id=record-private")
    assert status == 200
    root = detail_dom(source)
    state = root.elements("details", **{"data-key": "source-state"})[0]
    assert evidence in state.text() and "private/session-7.jsonl" in state.text()
    assert "공유 안 된 근거 내용" not in root.text() and "공유 안 된 대화 내용" not in root.text()
    targets = {link.attrs.get("href", "") for link in state.elements("a")}
    assert "/raw/private" not in targets
    for name in ("details/private.md", "private/session-7.jsonl"):
        assert name not in targets
        assert dashboard.view_url(folder / name) not in targets
        assert dashboard.raw_url(folder / name) not in targets
        assert world.get(dashboard.view_url(folder / name))[0] == 404
        assert world.get(dashboard.raw_url(folder / name))[0] == 404
    assert {path: path.read_bytes() for path in folder.rglob("*") if path.is_file()} == before


def test_detail_question_originals_show_partial_answer_count_and_multiline_goal(world):
    question = "# 저장 정책\n\n질문 1 답변 완료 · 질문 2 답변 대기\n\n## 결정할 것\n\n### 1. 이미 답한 정책\n\n### 2. 아직 답할 정책\n"
    folder = world.mission("slot1", "partial-detail", files={"ask-1.md": question}, shared=["ask-1.md"])
    write_question(folder, "ask-1.md", "저장 정책", [("이미 답한 정책", "answered"), ("아직 답할 정책", "pending")], answer="권장")
    question = (folder / "ask-1.md").read_text(encoding="utf-8")
    mission_file = folder / "mission.md"
    goal = "첫 줄 목표\n둘째 줄 목표\n셋째 줄 목표"
    mission_file.write_text(mission_file.read_text(encoding="utf-8").replace("partial-detail 목표 첫 줄", goal), encoding="utf-8")
    root = detail_dom(world.get("/mission?slot=slot1&id=partial-detail")[1])
    originals = root.elements("details", **{"data-key": "source-documents"})[0]
    assert "미답 1개" in originals.text() and "답 없음" not in originals.text()
    assert "아직 답할 정책" in originals.text() and "이미 답한 정책" not in originals.text()
    assert any(node.text() == goal for node in root.elements("p"))
    assert (folder / "ask-1.md").read_text(encoding="utf-8") == question
