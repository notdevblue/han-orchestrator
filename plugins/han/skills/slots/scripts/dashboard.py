#!/usr/bin/env python3
import argparse
import hashlib
import html
import json
import mimetypes
import os
import re
import sys
import time
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlsplit

SKILLS_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(SKILLS_DIR, "mission", "scripts"))
import mission_state  # noqa: E402
import slots  # noqa: E402
import grok_transcript
import mission_guard  # noqa: E402 (slots 가 hooks 폴더를 sys.path 에 넣음)
import question_summary  # noqa: E402
import question_records

DEFAULT_PORT = 8765
REFRESH_SECONDS = 30
MAIN_LABEL = "main"
RUNNING = "진행"
APPROVED = mission_state.APPROVED
SKIPPED = mission_state.SKIPPED
SKELETON_HEADING = re.compile(r"^##\s.*뼈대", re.M)
CONFIRM_LETTERS = "ABC"
IDLE_SECONDS = 10 * 60
SUBAGENT_STALE_SECONDS = 60 * 60
HANDBACK_TOOL = "SubagentHandback"
SUBAGENT_FILE = re.compile(r"agent-([\w-]+)\.jsonl")
CODEX_ROLLOUT = re.compile(r"rollout-.+-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl")
TASK_NOTIFICATION = re.compile(r"<task-id>([\w-]+)</task-id>")
ACTIVITY_TYPES = ("user", "assistant", "system", "api-request", "event_msg", "response_item")
TRANSCRIPT_TAIL_BYTES = 256 * 1024
CODEX_TURN_DONE = "task_complete"
CODEX_TURN_EVENTS = ("task_started", "user_message", CODEX_TURN_DONE)
STAGE_TOKEN = re.compile(r"^[A-Za-z]+")
MARKED_URL = "https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.2/marked.min.js"
QUESTION_NAME = re.compile(r"(?:ask-\d+|confirm-[ABC]\d*)\.md")
MAX_TEXT_BYTES = 4 * 1024 * 1024
BINARY_PROBE_BYTES = 8192

HTML_TYPE = "text/html; charset=utf-8"
TEXT_TYPE = "text/plain; charset=utf-8"
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
JS_TYPE = "text/javascript; charset=utf-8"
# Windows 는 레지스트리 값이 확장자 유형을 바꿔 놓을 수 있어 웹 자산은 고정 표가 먼저
ASSET_TYPES = {
    ".js": JS_TYPE,
    ".mjs": JS_TYPE,
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml; charset=utf-8",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
}
UTF8_SUFFIXES = ("json", "javascript", "xml")
RAW_PREFIX = "/raw/"

READ_ERRORS = (OSError, ValueError, mission_state.StateError)
FILE_URL = re.compile(r"file:///([A-Za-z]:[^\s)>\]\"'`]*)")
NATURAL_SPLIT = re.compile(r"(\d+)")
DOC_FIRST = ("mission.md", "report.md")
SYSTEM_DOCUMENTS = {"mission.md", mission_state.UNITS_FILE, mission_state.LEDGER_FILE, mission_state.DECISIONS_FILE, mission_guard.SESSIONS_FILE}
TABLES = (
    ("마일스톤", mission_state.UNITS_FILE, mission_state.UNIT_HEADER),
    ("판정 기록", mission_state.LEDGER_FILE, mission_state.LEDGER_HEADER),
    ("결정", mission_state.DECISIONS_FILE, mission_state.DECISIONS_HEADER),
    ("세션", mission_guard.SESSIONS_FILE, mission_guard.SESSIONS_HEADER),
)

STYLE = """
:root{--ink:#181818;--paper:#e8ddca;--line:rgba(24,24,24,.34);--muted:rgba(24,24,24,.76);
--inv-line:rgba(232,221,202,.34);--inv-muted:rgba(232,221,202,.7);--warn:#9b2f1f;
--normal-muted:var(--muted);--normal-line:var(--line);
--serif:Georgia,"Noto Serif CJK KR",Batang,serif;color-scheme:light}
:root[data-theme="dark"]{--ink:#e8ddca;--paper:#181818;--line:rgba(232,221,202,.34);
--muted:rgba(232,221,202,.7);--inv-line:rgba(24,24,24,.34);--inv-muted:rgba(24,24,24,.76);--warn:#e0806a;color-scheme:dark}
*{box-sizing:border-box}
body,body *{transition-property:color,background-color,border-color;transition-duration:320ms;transition-timing-function:ease}
body{margin:0;background:var(--paper);color:var(--ink);font:14px/1.6 "SFMono-Regular",Consolas,"Liberation Mono",monospace}
a{color:inherit}
h1,h2,h3{font-family:var(--serif);font-weight:400;letter-spacing:-.05em;margin:0}
.wrap{width:min(calc(100% - 80px),1400px);margin:0 auto;padding:8px 0 48px}
.mast,.foot{background:var(--ink);color:var(--paper)}
.mast .in,.foot .in{width:min(calc(100% - 80px),1400px);margin:0 auto;display:flex;justify-content:space-between;align-items:flex-end;gap:24px}
.mast .in{padding:18px 0 14px}
.mast h1{font-size:36px;line-height:1.05;overflow-wrap:anywhere}
.mast-r{display:flex;flex-wrap:wrap;gap:20px;align-items:center;font-size:12px;white-space:nowrap}
.mast-r label{cursor:pointer}
.refresh-toggle{display:flex;align-items:center;gap:8px;border:1px solid var(--inv-line);padding:4px 8px}
.refresh-toggle:hover{background:var(--paper);color:var(--ink);border-color:var(--paper)}
.refresh-toggle input{appearance:none;width:13px;height:13px;margin:0;color:inherit;border:1px solid currentColor;display:grid;place-content:center;cursor:pointer}
.refresh-toggle input::after{content:"";width:4px;height:7px;border:solid var(--ink);border-width:0 2px 2px 0;transform:rotate(45deg) scale(0)}
.refresh-toggle input:checked{background:currentColor}
.refresh-toggle input:checked::after{transform:rotate(45deg) scale(1)}
.refresh-toggle:hover input::after{border-color:var(--paper)}
.refresh-toggle input:focus-visible{outline:2px solid currentColor;outline-offset:3px}
.theme-toggle{display:flex;align-items:center;gap:8px;border:1px solid var(--inv-line);padding:4px 8px;cursor:pointer;user-select:none}
.theme-toggle input{position:absolute;width:1px;height:1px;opacity:0}
.theme-toggle:focus-within{outline:2px solid currentColor;outline-offset:3px}
.theme-track{position:relative;display:flex;width:40px;height:22px;align-items:center;padding:2px;border:1px solid currentColor;border-radius:99px}
.theme-thumb{position:absolute;top:2px;left:2px;display:grid;width:16px;height:16px;place-items:center;border-radius:50%;background:var(--paper);transform:translateX(0)}
.theme-toggle input:checked+.theme-track .theme-thumb{transform:translateX(18px)}
:root[data-theme="dark"] .theme-thumb{transform:translateX(18px)}
.theme-icon{position:absolute;width:10px;height:10px;fill:none;stroke:var(--ink);stroke-width:1.5;stroke-linecap:round;stroke-linejoin:round;transition:opacity 320ms ease,transform 320ms ease}
.theme-icon.moon{opacity:0;transform:rotate(-35deg)}
:root[data-theme="dark"] .theme-icon.sun{opacity:0;transform:rotate(35deg)}
:root[data-theme="dark"] .theme-icon.moon{opacity:1;transform:rotate(0)}
.theme-thumb{transition:transform 320ms cubic-bezier(.2,.8,.2,1),background-color 320ms ease}
@media(prefers-reduced-motion:reduce){body,body *, .theme-icon,.theme-thumb{transition-duration:0s!important}}
.foot .in{padding:16px 0;font-size:11px;letter-spacing:.08em;text-transform:uppercase}
.eyebrow,.tag,th,summary,.label{font-size:11px;letter-spacing:.08em;text-transform:uppercase}
.sec{display:flex;gap:14px;align-items:baseline;margin:24px 0 10px;font-size:26px}
.wrap>.sec:first-child{margin-top:0;margin-bottom:5px}
.sec .eyebrow{color:var(--muted);font-family:"SFMono-Regular",Consolas,monospace}
.sub{font-size:22px;margin:28px 0 4px}
.muted{color:var(--muted)}
.list{border-bottom:1px solid var(--line)}
.row{display:grid;grid-template-columns:40px 1fr 180px 24px;gap:16px;padding:14px 0;border-top:1px solid var(--line);text-decoration:none;color:inherit}
.row .no{color:var(--muted)}
.row .meta{font-size:12px;color:var(--muted)}
.row .arrow{font-family:var(--serif);font-size:20px;text-decoration:none;line-height:1.2}
.row.link:hover,.row .arrow:hover{background:var(--ink);color:var(--paper)}
.invert,.card.turn,.row.link:hover{--muted:var(--inv-muted);--line:var(--inv-line)}
.row .name{font-family:var(--serif);font-size:20px;letter-spacing:-.05em;overflow-wrap:anywhere}
.invert{background:var(--ink);color:var(--paper);padding:4px 16px}
.invert .row.link:hover{background:var(--paper);color:var(--ink);--muted:var(--normal-muted);--line:var(--normal-line)}
.invert .list{border-bottom:0}
.tag{display:inline-block;border:1px solid currentColor;padding:1px 7px;margin:0 6px 4px 0;white-space:nowrap;vertical-align:middle}
.tag.on{background:var(--ink);color:var(--paper);border-color:var(--ink)}
.tag.warn,.warn{color:var(--warn)}
.tag.dim{color:var(--muted)}
.tag.chip{max-width:34ch;overflow:hidden;text-overflow:ellipsis}
.clamp{display:-webkit-box;-webkit-line-clamp:3;line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}
.stage{color:var(--muted);font-size:12px}
.now{white-space:pre-wrap;max-height:9em;overflow:auto;border-left:2px solid var(--line);padding-left:10px;margin:6px 0}
.bar{height:8px;border:1px solid var(--line);margin:8px 0 4px;max-width:420px}
.bar span{display:block;height:100%;background:var(--ink)}
.counts{font-size:12px;color:var(--muted)}
.units{margin:4px 0;padding-left:18px;font-size:13px}
.qs a{display:inline-block;margin-right:12px}
.board{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
.idle-board{display:flex;flex-wrap:wrap;gap:10px;margin-top:18px}
.idle-board .card{flex:1 1 220px;padding:10px 16px}
.mission-grid{display:grid;grid-template-columns:1fr;gap:6px}
.mission-grid>section{display:grid;grid-template-columns:72px minmax(0,1fr);align-items:start;gap:4px 8px;min-width:0}
.mission-grid h3{font:inherit;font-size:11px;letter-spacing:.02em;margin:0}
.mission-grid p{margin:0;overflow-wrap:anywhere}
.mission-grid .goal{white-space:pre-wrap;font-family:var(--serif);font-size:15px;line-height:1.4;letter-spacing:-.035em}
.card .mission-name{font-size:12px;overflow-wrap:anywhere}
.work-list{list-style:none;padding:0!important}
.work-list li{margin:0 0 8px;overflow-wrap:anywhere}
.work-list .condition{display:block;font-size:11px;margin-top:3px;opacity:.8}
.work-section>.goal{white-space:pre-wrap;overflow-wrap:break-word}
.work-list,.work-list li,.record-list,.record,.record-body,.record-title{min-width:0}
.work-list>li{overflow-wrap:break-word}
.record-list{list-style:none;padding:0;margin:8px 0}
.record-list>li{border-bottom:1px solid var(--line);padding-bottom:8px}
.record{margin-top:6px}
.record>summary{display:flex;align-items:baseline;flex-wrap:wrap;gap:4px 12px;text-transform:none;letter-spacing:0;font-size:13px}
.record>summary::before{content:"▸";flex:0 0 auto}
.record[open]>summary::before{content:"▾"}
.record-title{flex:1 1 240px;overflow-wrap:break-word;font-weight:bold}
.record-title,.unit-goal{display:-webkit-box;-webkit-line-clamp:3;line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;min-width:0}
.unit-summary{display:flex;align-items:baseline;flex-wrap:wrap;gap:4px 10px;min-width:0}
.unit-summary .unit-goal{flex:1 1 220px;overflow-wrap:break-word}
.record-meta{display:flex;flex-wrap:wrap;gap:4px 10px;min-width:0;color:var(--muted);font-size:12px}
.record-token{display:inline-block;max-width:100%;overflow-x:auto;white-space:nowrap;vertical-align:bottom}
.record-meta time{min-width:0;overflow-wrap:break-word}
.record-body{padding:8px 0 8px 12px}
.record-fields{grid-template-columns:145px minmax(0,1fr);gap:8px 16px}
.record-fields dt,.record-fields dd{min-width:0;overflow-wrap:break-word}
.record-text{margin:0;white-space:pre-wrap;overflow-wrap:break-word;word-break:normal}
.record-scroll{max-height:320px;overflow:auto;border:1px solid var(--line);padding:10px}
.record-full{margin:0}
.record-full>summary{font-size:12px;text-transform:none;letter-spacing:0}
.record-source{font-size:12px;overflow-wrap:break-word}
.record-fields .condition{margin:4px 0}
@media(max-width:680px){.record-fields{grid-template-columns:minmax(0,1fr);gap:3px}.record-fields dd{margin-bottom:8px}.record-body{padding-left:0}}
.summary-meta{display:flex;gap:10px 22px;flex-wrap:wrap;font-size:11px;align-items:center}
.summary-meta .track{max-width:600px}
.freshness{display:flex;gap:8px 28px;flex-wrap:wrap;font-size:11px;color:var(--muted)}
.work-section{border-top:1px solid var(--line);padding-top:12px;margin:20px 0}
.work-section h2{font-size:25px;margin-bottom:10px}
.question-items{margin:6px 0!important}
.question-items li{margin-bottom:5px}
@media(max-width:800px){.board{grid-template-columns:1fr}}
@media(max-width:420px){.mission-grid>section{grid-template-columns:64px minmax(0,1fr);gap:4px}}
.card{border:1px solid var(--ink);padding:12px 16px;display:flex;flex-direction:column;gap:7px;min-width:0}
.card details{margin-top:4px}
.card summary{padding:4px 0;font-size:10px}
.card.idle{border-color:var(--line)}
.card.turn{background:var(--ink);color:var(--paper)}
.card.turn .track .cur{background:var(--paper);color:var(--ink)}
.card.turn .bar span,.card.turn .box.full{background:var(--paper)}
.card.turn .warn{color:var(--paper)}
.card-top{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
.card-top .slot{font-family:var(--serif);font-size:26px;letter-spacing:-.05em}
.state{font-size:11px;letter-spacing:.08em;text-transform:uppercase;text-align:right}
.card .title{font-family:var(--serif);font-size:21px;letter-spacing:-.04em;line-height:1.15;overflow-wrap:anywhere;text-decoration:none}
.card .title:hover{text-decoration:underline}
.track{display:flex;flex-wrap:wrap;align-items:center;gap:2px 5px;font-size:11px}
.track span{border:1px solid transparent;padding:0 5px}
.track i{font-style:normal;color:var(--muted)}
.track .later{color:var(--muted)}
.track .cur{background:var(--ink);color:var(--paper);border-color:var(--ink)}
.boxes{display:flex;gap:12px;font-size:12px}
.box{display:inline-block;width:11px;height:11px;border:1px solid currentColor;margin-left:4px;vertical-align:-1px}
.box.full{background:var(--ink)}
.box.half{background:linear-gradient(135deg,currentColor 50%,transparent 50%)}
.box.skip{border-style:dashed}
.card .bar{margin:0;max-width:none}
.card ul{margin:0;padding-left:16px;font-size:12px}
.card li{white-space:normal;overflow-wrap:anywhere}
.card .qs a{font-size:12px}
.card .brief-clamp{display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;line-clamp:2;overflow:hidden;overflow-wrap:anywhere}
.card .brief-details>summary{display:block;max-height:3.2em;overflow:hidden;text-transform:none;letter-spacing:0;font-size:12px;line-height:1.6}
.card .brief-details>summary.goal{font-size:14px;line-height:1.4}
.card .goal-preview{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.card .brief-details[open]>summary{max-height:none}
.card .goal-details:not([open]) .goal-collapse{display:none}
.card .goal-details[open] .goal-preview{display:none}
.card .mission-grid details>summary{padding:2px 0;font-size:12px}
.card .decision{display:flex;align-items:baseline;flex-wrap:wrap;gap:2px 8px}
.card .decision a{font-size:12px;overflow-wrap:anywhere}
.card .decision .question-items{font-size:11px}
.card .decision-title{flex:1 1 200px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;line-height:1.4}
.card .decision .document-name{font-size:12px}
.card .decision details{display:inline-block;margin:0;flex:0 0 auto}
.card .decision details[open]{flex-basis:100%}
.card .decision details>summary{border:0;padding:0;font-size:12px;letter-spacing:0;text-transform:none}
.card .decision details>p{flex-basis:100%;margin:0}
.card .brief-work{font-size:12px}
.card .brief-work li{display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;line-clamp:2;overflow:hidden;margin:0;padding:0}
.card .remaining-brief{display:flex;align-items:baseline;flex-wrap:wrap;gap:2px 8px}
.card .remaining-brief .brief-work{flex:1 1 220px;margin:0;padding-left:16px!important}
.card .remaining-brief .remaining-more{flex:0 0 auto}
.card .remaining-brief .remaining-more[open]{flex-basis:100%}
.card .remaining-more>summary{border:0;padding:0;font-size:12px;letter-spacing:0;text-transform:none}
.card .remaining-more>div{width:100%}
.card-top .mission-name{font-size:12px;white-space:nowrap}
.card-top .state{display:flex;flex-wrap:wrap;justify-content:flex-end;white-space:normal}
.document-name{display:inline;font-size:10px;color:var(--muted);overflow-wrap:anywhere}
.card .summary-meta{font-size:11px}
.card .card-meta{font-size:11px}
.card .card-meta summary{border-top:0;padding:0;font-size:11px;letter-spacing:0;text-transform:none}
.card-foot{margin-top:auto;display:flex;justify-content:space-between;font-size:11px;color:var(--muted)}
details{margin-top:12px}
summary{cursor:pointer;padding:8px 0;border-top:1px solid var(--line)}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:12px}
th,td{border:1px solid var(--line);padding:4px 8px;text-align:left;vertical-align:top;overflow-wrap:anywhere}
dl{display:grid;grid-template-columns:160px 1fr;gap:4px 16px;margin:0}
dt{color:var(--muted)}
dd{margin:0;overflow-wrap:anywhere}
.thumbs{display:flex;flex-wrap:wrap;gap:12px;padding:14px 0;border-top:1px solid var(--line)}
.thumbs img{height:140px;border:1px solid var(--line);display:block}
.md{max-width:980px}
.md h1,.md h2,.md h3{margin:1.4em 0 .5em}
.md h1{font-size:34px}.md h2{font-size:26px}.md h3{font-size:20px}
.md pre{border:1px solid var(--line);padding:10px;overflow-x:auto}
.md code{font-family:inherit}
.md blockquote{border-left:2px solid var(--line);margin:0;padding-left:14px;color:var(--muted)}
.code{border:1px solid var(--line);overflow-x:auto;padding:8px 0}
.line{white-space:pre;padding:0 12px}
.line .no{display:inline-block;width:5ch;margin-right:12px;color:var(--muted);text-align:right;user-select:none}
.line:target{background:var(--ink);color:var(--paper)}
.line:target .no{color:var(--inv-muted)}
.document-path{overflow-wrap:anywhere}
.document-viewer{width:min(1200px,calc(100% - 48px));height:calc(100dvh - 48px);max-width:none;max-height:none;padding:0;border:1px solid var(--ink);background:var(--paper);color:var(--ink);overflow:hidden}
.document-viewer[open]{display:flex;flex-direction:column}
.document-viewer::backdrop{background:rgba(0,0,0,.65)}
.document-toolbar{display:flex;align-items:center;gap:12px;flex-shrink:0;padding:12px 16px;border-bottom:1px solid var(--line)}
.document-toolbar h2{flex:1;min-width:0;font-size:22px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.document-toolbar button{flex-shrink:0;min-height:44px;padding:8px 14px;border:1px solid var(--ink);background:var(--paper);color:var(--ink);font:inherit;cursor:pointer}
.document-toolbar button:hover{background:var(--ink);color:var(--paper)}
.document-toolbar button:disabled{opacity:.4;cursor:default}
.document-toolbar button:focus-visible{outline:2px solid var(--ink);outline-offset:3px}
.document-close{font-weight:bold!important}
.document-close span{font-size:11px;font-weight:normal}
.document-status{margin:0;padding:8px 16px;color:var(--muted);border-bottom:1px solid var(--line);flex-shrink:0}
.document-status:empty{display:none}
.document-frame{width:100%;flex:1;min-height:0;border:0;background:var(--paper)}
.document-frame[hidden]{display:none}
body:has(.document-viewer[open]){overflow:hidden}
@media(max-width:680px){.document-viewer{width:100%;height:100dvh;margin:0;border:0}.document-toolbar{gap:8px;padding:8px}.document-toolbar h2{font-size:18px}.document-toolbar button{padding:8px}.document-close span{display:none}}
@media (max-width:680px){
.wrap,.mast .in,.foot .in{width:calc(100% - 32px)}
.mast .in{flex-direction:column;align-items:flex-start}
.mast-r{max-width:100%}
.mast h1{font-size:32px}
.row{grid-template-columns:1fr}
.row .no{display:none}
dl{grid-template-columns:1fr}}
"""

REFRESH_SCRIPT = """
<script>
(() => {
  const KEY = "dashboard-auto-refresh", SCROLL = "dashboard-scroll", OPEN = "dashboard-open";
  const read = (store, key) => { try { return store.getItem(key); } catch (e) { return null; } };
  const write = (store, key, value) => { try { store.setItem(key, value); } catch (e) {} };
  const toggle = document.getElementById("auto");
  toggle.checked = read(localStorage, KEY) !== "off";
  toggle.addEventListener("change", () => write(localStorage, KEY, toggle.checked ? "on" : "off"));
  const details = Array.from(document.querySelectorAll("details[data-key]"));
  const opened = (read(sessionStorage, OPEN) || "").split("|");
  details.forEach((d) => { if (opened.includes(d.dataset.key)) d.open = true; });
  const top = Number(read(sessionStorage, SCROLL) || 0);
  if (top) window.scrollTo(0, top);
  write(sessionStorage, SCROLL, "0");
  write(sessionStorage, OPEN, "");
  setInterval(() => {
    const viewer = document.getElementById("document-viewer");
    if (viewer && viewer.open) return;
    if (!toggle.checked) return;
    write(sessionStorage, SCROLL, String(window.scrollY));
    write(sessionStorage, OPEN, details.filter((d) => d.open).map((d) => d.dataset.key).join("|"));
    location.reload();
  }, __REFRESH_MS__);
})();
</script>
"""

THEME_SCRIPT = """
<script>
(() => {
  const KEY = "dashboard-theme";
  const preference = window.matchMedia("(prefers-color-scheme: dark)");
  let selected = "";
  try {
    const store = window.localStorage;
    const saved = store.getItem(KEY);
    if (saved === "light" || saved === "dark") selected = saved;
  } catch (e) {}
  const apply = (theme) => {
    document.documentElement.dataset.theme = theme;
  };
  document.documentElement.dataset.themeChoice = selected;
  if (selected === "") {
    if (preference.matches) apply("dark");
    else apply("light");
  } else apply(selected);
})();
</script>
"""

THEME_CONTROL_SCRIPT = """
<script>
(() => {
  const toggle = document.getElementById("theme");
  const root = document.documentElement;
  const preference = window.matchMedia("(prefers-color-scheme: dark)");
  toggle.checked = root.dataset.theme === "dark";
  toggle.addEventListener("change", () => {
    let selected = "light";
    if (toggle.checked) selected = "dark";
    root.dataset.themeChoice = selected;
    root.dataset.theme = selected;
    try {
      const store = window.localStorage;
      store.setItem("dashboard-theme", selected);
    } catch (e) {}
  });
  preference.addEventListener("change", (event) => {
    if (root.dataset.themeChoice === "") {
      if (event.matches) root.dataset.theme = "dark";
      else root.dataset.theme = "light";
      toggle.checked = event.matches;
    }
  });
})();
</script>
"""

THEME_TOGGLE = """
<label class="theme-toggle"><input id="theme" type="checkbox" role="switch" aria-label="다크 모드"><span class="theme-track" aria-hidden="true"><span class="theme-thumb"><svg class="theme-icon sun" viewBox="0 0 16 16"><circle cx="8" cy="8" r="3"></circle><path d="M8 1v2M8 13v2M1 8h2m10 0h2M3 3l1.5 1.5m7 7L13 13m0-10-1.5 1.5m-7 7L3 13"></path></svg><svg class="theme-icon moon" viewBox="0 0 16 16"><path d="M13.5 10.2A6.1 6.1 0 0 1 5.8 2.5 6.4 6.4 0 1 0 13.5 10.2Z"></path></svg></span></span><span>다크 모드</span></label>
"""

DOCUMENT_VIEWER = """
<dialog id="document-viewer" class="document-viewer" aria-labelledby="document-title">
<div class="document-toolbar"><button id="document-back" type="button" disabled>← 이전 문서</button><h2 id="document-title">문서</h2><button id="document-close" class="document-close" type="button" autofocus>닫기 <span>Esc</span></button></div>
<p id="document-status" class="document-status" role="status" aria-live="polite"></p>
<iframe id="document-frame" class="document-frame" title="문서 미리보기" sandbox="allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox allow-downloads" hidden></iframe>
</dialog>
"""

DOCUMENT_VIEWER_SCRIPT = """
<script>
(() => {
  const viewer = document.getElementById("document-viewer");
  if (!viewer || typeof viewer.showModal !== "function") return;
  const frame = document.getElementById("document-frame");
  const title = document.getElementById("document-title");
  const status = document.getElementById("document-status");
  const back = document.getElementById("document-back");
  const close = document.getElementById("document-close");
  let trigger = close;
  let documents = [];
  let loading = 0;
  const plainClick = (event) => event.button === 0 && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey;
  const documentLink = (url) => url.origin === location.origin && url.pathname === "/view"
    && /[.](?:md|txt|tsv|csv|json|log|yaml|yml|toml|xml|cs|py|js|ts|css|proto|sql|diff|patch)$/i.test(url.searchParams.get("path") || "");
  const syncTheme = () => {
    try {
      frame.contentDocument.documentElement.dataset.theme = document.documentElement.dataset.theme;
    } catch (e) {}
  };
  new MutationObserver(syncTheme).observe(document.documentElement, {attributes: true, attributeFilter: ["data-theme"]});
  const escape = (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      viewer.close();
    }
  };
  const load = () => {
    const current = documents[documents.length - 1];
    title.textContent = current.title;
    back.disabled = documents.length === 1;
    status.textContent = "문서를 불러오는 중…";
    frame.hidden = true;
    clearTimeout(loading);
    loading = setTimeout(() => {
      status.textContent = "문서를 불러오지 못했습니다. 이전 문서로 돌아가거나 닫고 다시 열어 주세요.";
    }, 15000);
    const target = new URL(current.url);
    target.searchParams.set("embedded", "1");
    frame.src = target.href;
  };
  const navigate = (url, label) => {
    if (documents.length) {
      try {
        documents[documents.length - 1].scroll = frame.contentWindow.scrollY;
        documents[documents.length - 1].scrollSaved = true;
      } catch (e) {}
    }
    url.searchParams.delete("embedded");
    documents.push({url: url.href, title: label || "문서", scroll: 0, scrollSaved: false});
    load();
  };
  document.addEventListener("click", (event) => {
    if (!plainClick(event)) return;
    const link = event.target.closest("a[href]");
    if (!link || link.hasAttribute("download") || link.target) return;
    const url = new URL(link.href, location.href);
    if (!documentLink(url)) return;
    event.preventDefault();
    trigger = link;
    documents = [];
    viewer.showModal();
    close.focus({preventScroll: true});
    navigate(url, link.textContent.trim());
  });
  close.addEventListener("click", () => viewer.close());
  viewer.addEventListener("keydown", escape);
  viewer.addEventListener("close", () => {
    clearTimeout(loading);
    frame.src = "about:blank";
    frame.hidden = true;
    documents = [];
    trigger.focus({preventScroll: true});
  });
  back.addEventListener("click", () => {
    if (documents.length > 1) {
      documents.pop();
      load();
    }
  });
  frame.addEventListener("error", () => {
    clearTimeout(loading);
    status.textContent = "문서를 불러오지 못했습니다. 닫고 다시 열어 주세요.";
  });
  frame.addEventListener("load", () => {
    if (!viewer.open || !documents.length) return;
    try {
      const content = frame.contentDocument;
      if (frame.contentWindow.location.href === "about:blank") return;
      clearTimeout(loading);
      syncTheme();
      content.querySelectorAll(".mast,.foot").forEach((element) => { element.hidden = true; });
      content.addEventListener("keydown", escape, true);
      content.addEventListener("click", (event) => {
        if (!plainClick(event)) return;
        const link = event.target.closest("a[href]");
        if (!link || link.hasAttribute("download")) return;
        const url = new URL(link.href, frame.contentWindow.location.href);
        const loaded = new URL(frame.contentWindow.location.href);
        if (url.origin === loaded.origin && url.pathname === loaded.pathname && url.search === loaded.search && url.hash) return;
        if (documentLink(url) && !link.target) {
          const current = new URL(documents[documents.length - 1].url);
          url.searchParams.delete("embedded");
          if (url.pathname === current.pathname && url.search === current.search && url.hash) return;
          event.preventDefault();
          navigate(url, link.textContent.trim());
        } else if (url.protocol === "http:" || url.protocol === "https:") {
          event.preventDefault();
          window.open(url.href, "_blank", "noopener");
        }
      }, true);
      const current = documents[documents.length - 1];
      if (content.title) {
        current.title = content.title;
        title.textContent = current.title;
      }
      frame.title = "문서 미리보기 · " + current.title;
      status.textContent = "";
      if (/^[45][0-9]{2}$/.test(content.title)) status.textContent = "문서를 열지 못했습니다 · " + content.title;
      frame.hidden = false;
      if (current.scrollSaved) frame.contentWindow.scrollTo(0, current.scroll);
    } catch (e) {
      clearTimeout(loading);
      status.textContent = "문서 내용을 표시할 수 없습니다. 닫고 원문 링크를 새 탭에서 열어 주세요.";
    }
  });
})();
</script>
"""

MARKDOWN_SCRIPT = r"""
<script>
const source = JSON.parse(document.getElementById("source").textContent);
const out = document.getElementById("out");
if (window.marked) {
  window.marked.use({
    tokenizer: {
      del(src) {
        if (src.startsWith("~~")) return false;
      }
    }
  });
  out.innerHTML = window.marked.parse(source);
  const documentUrl = new URL(document.getElementById("source").dataset.url, location.href);
  const rootUrl = new URL(document.getElementById("source").dataset.root, location.href);
  for (const [selector, attribute] of [["a[href]", "href"], ["img[src]", "src"]]) {
    for (const element of out.querySelectorAll(selector)) {
      const reference = element.getAttribute(attribute);
      if (!reference || /^(?:[a-z][a-z0-9+.-]*:|\/\/|#|\?)/i.test(reference)) continue;
      if (/^\/(?:$|view(?:\?|$)|mission(?:\?|$))/.test(reference)) continue;
      const target = reference.startsWith("/raw/") ? new URL(reference, location.href)
        : reference.startsWith("/") ? new URL(reference.slice(1), rootUrl) : new URL(reference, documentUrl);
      if (!target.pathname.startsWith("/raw/")) continue;
      if (attribute === "src" || /\.html?$/i.test(target.pathname)) {
        element.setAttribute(attribute, target.href);
      } else {
        try {
          const path = decodeURIComponent(target.pathname.slice("/raw/".length).replace(/%(?![0-9a-f]{2})/gi, "%25"));
          element.setAttribute(attribute, "/view?path=" + encodeURIComponent(path) + target.hash);
        } catch {
          continue;
        }
      }
    }
  }
} else {
  const pre = document.createElement("pre");
  pre.textContent = source;
  out.replaceChildren(pre);
}
</script>
"""


@dataclass
class Mission:
    slot: str
    id: str
    folder: str
    active: bool
    fields: dict = field(default_factory=dict)
    goal: str = ""
    now: str = ""
    units: list = field(default_factory=list)
    last_activity: float = 0.0
    waiting_input: bool = False
    background: bool = False
    unanswered: list = field(default_factory=list)
    skeletons: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    title: str = ""
    questions: dict = field(default_factory=dict)
    question_issues: dict = field(default_factory=dict)
    state_updated: float = 0.0
    report_updated: float = 0.0
    stale: str = ""  # ACTIVE 끊김 의심 문구(slots.stale_note)

    @property
    def state(self):
        return self.fields.get("상태") or "-"

    @property
    def stage(self):
        return self.fields.get("현재 단계") or "-"

    @property
    def confirms(self):
        return {key[3:]: value for key, value in self.fields.items() if key.startswith("확인 ")}

    @property
    def counts(self):
        counts = {name: 0 for name in mission_state.UNIT_STATUSES}
        for row in self.units:
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        return counts

    @property
    def doing(self):
        return [row for row in self.units if row["status"] == "doing"]

    @property
    def remaining(self):
        return [row for row in self.units if row["status"] != "done"]


@dataclass
class Location:
    label: str
    state_dir: str
    row: dict
    active: object
    root: str
    missions: list = field(default_factory=list)
    problem: str = ""
    stale: str = ""


def esc(value):
    return html.escape(str(value), quote=True)


def view_url(path, anchor=""):
    return "/view?path=" + quote(slots.normalize(path), safe="") + (f"#{anchor}" if anchor else "")


# 경로를 URL 경로로 풀어 둬야 브라우저가 상대 경로(../Unity/a.png)로 형제 에셋을 같은 라우트에서 요청함
def raw_url(path):
    return RAW_PREFIX + quote(slots.normalize(path).replace("\\", "/"), safe="/:")


def natural_key(name):
    return [int(part) if part.isdigit() else part for part in NATURAL_SPLIT.split(name)]


def ago(timestamp):
    if not timestamp:
        return "-"
    seconds = max(0, int(time.time() - timestamp))
    if seconds < 60:
        return "방금"
    for unit, size in (("일", 86400), ("시간", 3600), ("분", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit} 전"
    return "방금"


def section_text(lines, title):
    start = next((index for index, line in enumerate(lines) if line.rstrip() == f"## {title}"), -1)
    if start < 0:
        return ""
    body = []
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        body.append(line)
    return mission_state.HTML_COMMENT.sub("", "\n".join(body)).strip()


def attempt(errors, label, path, reader, fallback):
    if not os.path.exists(path):
        return fallback
    try:
        return reader(path)
    except READ_ERRORS:
        errors.append(label)
        return fallback


def parse_time(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def transcript_of(location, mission, sessions):
    session = location.active.session if mission.active else ""
    recorded = [row["transcript"] for row in sessions if not session or row["session"] == session]
    found = next((path for path in reversed(recorded) if path and os.path.isfile(path)), "")
    if found or not mission_state.SESSION_ID.fullmatch(session):
        return found
    home = os.path.expanduser("~")
    return mission_state.search_claude(session, home) or mission_state.search_codex(session, home) or grok_transcript.find_transcript(session, home)


def read_turn(path):
    if grok_transcript.is_transcript(path):
        activity = grok_transcript.activity(path)
        return parse_time(activity["last_timestamp"]), activity["completed"], {}
    last, ended, handed_back = 0.0, False, False
    stopped = {}
    with open(path, "rb") as f:
        f.seek(max(0, os.path.getsize(path) - TRANSCRIPT_TAIL_BYTES))
        lines = f.read().decode("utf-8", errors="replace").splitlines()
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        kind = entry.get("type")
        payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
        if kind in ACTIVITY_TYPES:
            last = max(last, parse_time(entry.get("timestamp")))
        message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
        content = message.get("content") if isinstance(message.get("content"), list) else []
        blocks = [block for block in content if isinstance(block, dict)]
        if kind == "assistant":
            handed_back = any(block.get("type") == "tool_use" and block.get("name") == HANDBACK_TOOL for block in blocks)
            ended = handed_back or message.get("stop_reason") == "end_turn"
        elif kind == "user":
            # 하위 에이전트는 보고 도구의 결과 줄로 끝남
            ended = ended and handed_back and bool(blocks) and all(block.get("type") == "tool_result" for block in blocks)
            for task in TASK_NOTIFICATION.findall(line):
                stopped[task] = parse_time(entry.get("timestamp"))
        elif kind == "event_msg" and payload.get("type") in CODEX_TURN_EVENTS:
            ended = payload.get("type") == CODEX_TURN_DONE
    return last, ended, stopped


def fresh(path):
    return time.time() - os.path.getmtime(path) <= SUBAGENT_STALE_SECONDS


def recent_subagents(transcript):
    claude = os.path.join(os.path.splitext(transcript)[0], "subagents")
    if os.path.isdir(claude):
        found = [(SUBAGENT_FILE.fullmatch(name), os.path.join(claude, name)) for name in os.listdir(claude)]
        return [(agent.group(1), path) for agent, path in found if agent and fresh(path)]
    parent = CODEX_ROLLOUT.fullmatch(os.path.basename(transcript))
    if not parent:
        return []
    # Codex 하위 에이전트는 날짜 폴더(sessions/YYYY/MM/DD)에 따로 rollout 을 만들고 첫 줄에 부모 id 를 적음
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(transcript))))
    days = {time.strftime("%Y/%m/%d", time.localtime(time.time() - back)) for back in (0, SUBAGENT_STALE_SECONDS)}
    children = []
    for folder in (os.path.join(root, day) for day in days):
        names = os.listdir(folder) if os.path.isdir(folder) else []
        for child, path in ((CODEX_ROLLOUT.fullmatch(name), os.path.join(folder, name)) for name in names):
            if child and child.group(1) != parent.group(1) and fresh(path) and codex_parent_of(path) == parent.group(1):
                children.append((child.group(1), path))
    return children


def codex_parent_of(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        first = f.readline()
    try:
        meta = json.loads(first).get("payload") or {}
    except (ValueError, AttributeError):
        return ""
    return str(meta.get("parent_thread_id") or "") if isinstance(meta, dict) else ""


def read_session(mission, transcript):
    if not transcript:
        return
    mission.last_activity, mission.waiting_input, stopped = read_turn(transcript)
    if grok_transcript.is_transcript(transcript):
        children = grok_transcript.subagent_records(transcript)
        mission.background = any(child["status"] == "running" for child in children)
        for child in children:
            last = max(parse_time(child["started_at"]), parse_time(child["completed_at"]))
            path = child["transcript"]
            if path and os.path.isfile(path):
                activity = grok_transcript.activity(path)
                last = max(last, parse_time(activity["last_timestamp"]))
            mission.last_activity = max(mission.last_activity, last)
        return
    for agent, path in recent_subagents(transcript):
        last, ended, _ = read_turn(path)
        notified = agent in stopped and stopped[agent] >= last
        mission.background = mission.background or not (ended or notified)
        mission.last_activity = max(mission.last_activity, last)


def mission_folder_allowed(location, mission_id):
    state_dir = os.path.realpath(location.state_dir)
    expected = os.path.join(state_dir, mission_id)
    real = os.path.realpath(os.path.join(location.state_dir, mission_id))
    return os.path.normcase(real) == os.path.normcase(expected) and slots.inside_root(os.path.realpath(location.root), real)


def load_mission(location, mission_id):
    folder = os.path.join(location.state_dir, mission_id)
    active = not location.active.corrupt and location.active.id == mission_id
    mission = Mission(location.label, mission_id, folder, active)
    mission.stale = location.stale if active else ""
    errors = mission.errors
    if not mission_folder_allowed(location, mission_id):
        errors.append("미션 폴더 경로")
        return mission
    lines = attempt(errors, "mission.md", os.path.join(folder, "mission.md"), lambda p: mission_state.read_text(p).split("\n"), [])
    mission.fields = mission_state.header_fields(lines)
    mission.title = next((line[2:].strip() for line in lines if line.startswith("# ")), mission_id)
    mission.goal = section_text(lines, "목표")
    shared = attempt(errors, mission_state.SHARED_DOCUMENTS_FILE, folder, mission_state.shared_documents, set())
    automatic = attempt(errors, "질문 문서", folder, mission_state.auto_question_documents, set())
    try:
        automatic |= set(question_records.document_names(folder))
    except (OSError, ValueError) as error:
        mission.question_issues["questions/"] = str(error)
    report = []
    if "report.md" in shared:
        report = attempt(errors, "공유 문서", os.path.join(folder, "report.md"), lambda p: read_shared_text(folder, p).split("\n"), [])
    mission.now = section_text(report, "지금")
    if mission.now:
        mission.report_updated = modified_at(os.path.join(folder, "report.md"))
    mission.units = attempt(
        errors, mission_state.UNITS_FILE, os.path.join(folder, mission_state.UNITS_FILE), lambda p: mission_state.read_table(p, mission_state.UNIT_HEADER), []
    )
    sessions = attempt(
        errors,
        mission_guard.SESSIONS_FILE,
        os.path.join(folder, mission_guard.SESSIONS_FILE),
        lambda p: mission_state.read_table(p, mission_guard.SESSIONS_HEADER),
        [],
    )
    try:
        read_session(mission, transcript_of(location, mission, sessions))
    except OSError:
        errors.append("대화 기록")
    for name in sorted(automatic, key=natural_key):
        if "/" in name:
            continue
        try:
            text = read_shared_text(folder, os.path.join(folder, name))
        except READ_ERRORS as error:
            mission.question_issues[name] = f"Markdown 읽기 실패: {error}"
            continue
        summary, issue = question_records.inspect_document(folder, name, text)
        if issue:
            if not os.path.lexists(os.path.join(folder, "questions", f"{name[:-3]}.json")):
                issue = f"질문 JSON 정본 누락 · 정형 전환 필요: {issue}"
            mission.question_issues[name] = issue
            continue
        mission.questions[name] = summary
        if name.startswith("confirm-") and SKELETON_HEADING.search(text):
            mission.skeletons.append(name)
    mission.unanswered = [name for name, summary in mission.questions.items() if summary.pending]
    sources = ["mission.md", mission_state.UNITS_FILE]
    sources.extend(automatic)
    sources.extend(f"questions/{name[:-3]}.json" for name in automatic if "/" not in name)
    mission.state_updated = max((modified_at(os.path.join(folder, name)) for name in sources if name not in errors), default=0.0)
    return mission


def modified_at(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def dated(timestamp):
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M") if timestamp else "기록 없음"


def locations(config):
    problems = []
    rows = attempt(problems, "slots.tsv", config.rows_path, lambda p: slots.Rows(p).read(), [])
    tsv = {row["name"]: row for row in rows if slots.NAME_PATTERN.fullmatch(row["name"])}
    names = sorted({*tsv, *config.names}, key=natural_key)
    found = [Location(MAIN_LABEL, config.state_dir, {}, mission_guard.read_active(config.state_dir), config.main, problem="; ".join(problems))]
    found.extend(
        Location(name, config.slot_state_dir(name), tsv.get(name, {}), slots.active_of(config, name), config.slot_path(name)) for name in names
    )
    for location in found:
        location.stale = stale_of(config, location)
    return found


def stale_of(config, location):
    active = location.active
    if not active.id or active.corrupt:
        return ""
    try:
        state = mission_guard.staleness(location.state_dir, active, getattr(config, "stale_hours", 0))
    except Exception:
        return ""
    return mission_guard.stale_text(state) if state.stale else ""


def stale_tag(text):
    if not text:
        return ""
    short = text.split(",")[0].replace("끊김 의심: 마지막 활동 ", "끊김 ")
    return tag(short, "warn", f"{text} — {mission_guard.FORCE_CLOSE_HINT}")


def collect(config):
    found = locations(config)
    for location in found:
        try:
            ids = [
                entry.name
                for entry in os.scandir(location.state_dir)
                if entry.is_dir() and mission_guard.valid_mission_id(entry.name) and mission_folder_allowed(location, entry.name) and os.path.isfile(os.path.join(entry.path, "mission.md"))
            ]
        except FileNotFoundError:
            continue
        except OSError:
            location.problem = "미션 폴더 읽기 실패"
            continue
        location.missions = [load_mission(location, mission_id) for mission_id in sorted(ids, reverse=True)]
    return found


def tag(text, kind="", title=""):
    return f'<span class="tag {kind}" title="{esc(title or text)}">{esc(text)}</span>'


def question_name(mission, name):
    summary = mission.questions.get(name)
    return esc(question_label(name, summary)) + (" · 뼈대" if name in mission.skeletons else "") if summary else ""


def question_prefix(name):
    if not QUESTION_NAME.fullmatch(name):
        return ""
    if name.startswith("ask-"):
        return f"질문 {name[4:-3]}"
    return f"확인 {name[8:-3]}"


def question_label(name, summary):
    prefix = question_prefix(name)
    title = summary.title.strip()
    normalized = re.sub(r"\s+", "", title).casefold()
    generic = {name.casefold(), prefix.casefold(), re.sub(r"\s+", "", prefix).casefold()}
    generic.add("질문" if name.startswith("ask-") else "확인")
    if name.startswith("confirm-"):
        generic.add(f"확인{name[8:-3]}".casefold())
    if normalized in generic:
        if summary.items:
            remainder = f" 외 {len(summary.items) - 1}개" if len(summary.items) > 1 else ""
            return f"{prefix} · {summary.items[0].title}{remainder}"
        return prefix or title
    if prefix and re.match(rf"^{re.escape(prefix)}(?:\s*[·:：-]\s*|\s+)", title, re.IGNORECASE):
        return title
    return f"{prefix} · {title}" if prefix else title


def markdown_heading(text):
    return next(
        (question_summary.plain_text(match.group(1)) for line in question_summary.markdown_lines(text) if (match := re.match(r"^#{1}[ \t]+(.+?)[ \t]*#*[ \t]*$", line))),
        "",
    )


def document_label(name, text, mission_title, folder=""):
    if QUESTION_NAME.fullmatch(name):
        if folder:
            summary, issue = question_records.inspect_document(folder, name, text)
            if not issue:
                return question_label(name, summary)
        return markdown_heading(text) or name
    title = mission_title if name == "mission.md" and mission_title else markdown_heading(text)
    prefix = "미션" if name == "mission.md" else "보고" if name == "report.md" else ""
    if not title or title.casefold() in (name.casefold(), prefix.casefold()):
        return prefix or name
    if prefix and re.match(rf"^{re.escape(prefix)}(?:\s*[·:：-]\s*|\s+)", title):
        return title
    return f"{prefix} · {title}" if prefix else title


def mission_url(mission):
    return f"/mission?slot={quote(mission.slot, safe='')}&id={quote(mission.id, safe='')}"


def question_count_text(summary):
    if not summary.total_known:
        return "전체 항목 수 미확인"
    total = f"전체 {len(summary.items)}개"
    if not summary.count_known:
        return f"{total} · 미답 수 미확인"
    if summary.pending_count:
        return f"{total} · 미답 {summary.pending_count}개"
    return total


def question_status(summary):
    count_text = question_count_text(summary)
    if not summary.pending:
        return f"답 있음 · {count_text}"
    if not summary.total_known:
        return f"{count_text} · 상세 확인"
    if not summary.count_known or summary.pending_count:
        return count_text
    return f"{count_text} · 전체 승인 확인 대기"


def question_expand_label(summary):
    if not summary.count_known:
        return f"{question_status(summary)} 펼치기"
    if summary.pending_count:
        return f"미답 {summary.pending_count}개 펼치기"
    return "승인 확인 펼치기"


def question_items_html(items):
    return "".join(f'<li>{esc(f"{item.number} {item.title}".strip())}</li>' for item in items)


def question_html(mission, name, compact=False, origin=""):
    summary = mission.questions[name]
    items = summary.items
    if summary.pending:
        items = [item for item in summary.items if item.status != "answered"]
    result = f'<div class="decision"><a class="decision-title" title="{question_name(mission, name)} · {esc(name)}" href="{view_url(os.path.join(mission.folder, name))}">{question_name(mission, name)} →</a>'
    if compact:
        result += f'<span class="document-name">{esc(question_status(summary))}</span>'
    else:
        result += f'<div class="counts">{esc(question_status(summary))}</div>'
    if compact and summary.pending:
        document_name = esc(name)
        if origin:
            document_name += f" · {esc(origin)}"
        result += f'<details data-key="question-{esc(mission.slot)}-{esc(mission.id)}-{esc(name)}"><summary>{question_expand_label(summary)}</summary><p class="document-name">{document_name}</p>'
        if items:
            result += f'<ul class="question-items">{question_items_html(items)}</ul>'
        else:
            result += '<p class="muted">문항 목록 없음 · 상세 확인 필요</p>'
        result += '</details>'
    if not compact and items:
        result += f'<ul class="question-items">{question_items_html(items)}</ul>'
    return result + "</div>"


def question_issue_html(mission, name, origin=""):
    label = name + (f" · {origin}" if origin else "")
    path = os.path.join(mission.folder, name)
    title = f'<a class="decision-title" href="{view_url(path)}">{esc(label)} →</a>' if name.endswith(".md") and os.path.isfile(path) and document_visible(mission.folder, path, set()) else f'<span class="decision-title">{esc(label)}</span>'
    return f'<div class="decision">{title}<p class="warn">{esc(mission.question_issues[name])}</p></div>'


def question_issue_badge(missions):
    count = sum(len(mission.question_issues) for mission in missions)
    return f"기록 확인 필요 {count}건" if count else ""


def known_pending_count(summaries):
    return sum(summary.pending_count for summary in summaries if summary.total_known)


def decision_badge(missions):
    summaries = [mission.questions[name] for mission in missions for name in mission.unanswered]
    if not summaries:
        return ""
    unknown = any(not summary.count_known for summary in summaries)
    count = known_pending_count(summaries)
    if unknown:
        if count:
            return f"답변 필요 {count}건 이상"
        return "답변 필요"
    if count:
        return f"답변 필요 {count}건"
    return "승인 확인 대기"


def waiting_text(mission):
    summaries = [mission.questions[name] for name in mission.unanswered]
    if not summaries:
        return "공유된 미답 질문 없음"
    count = known_pending_count(summaries)
    unknown = sum(not summary.count_known for summary in summaries)
    total_unknown = sum(not summary.total_known for summary in summaries)
    approval = sum(summary.count_known and not summary.pending_count for summary in summaries)
    parts = []
    if count:
        if unknown:
            parts.append(f"확인된 미답 항목 {count}개 이상")
        else:
            parts.append(f"미답 항목 {count}개")
    if total_unknown:
        parts.append(f"전체 항목 수 미확인 {total_unknown}문서")
    if unknown:
        parts.append(f"미답 수 미확인 {unknown}문서")
    if approval:
        parts.append(f"전체 승인 확인 대기 {approval}문서")
    return f"대기 문서 {len(summaries)}개 · " + " · ".join(parts)


def session_text(mission):
    if mission.state != RUNNING:
        return "당시 세션 기록"
    if not mission.active:
        return "세션 없음"
    if mission.background:
        return "하위 작업 중"
    if mission.waiting_input:
        return "입력 기다림"
    if not mission.last_activity:
        return "활동 기록 없음"
    return "작업 중" if time.time() - mission.last_activity <= IDLE_SECONDS else "조용함"


def freshness_html(mission):
    return (
        f'<div class="freshness"><span>업무 자료 갱신 {esc(dated(mission.state_updated))} · {esc(ago(mission.state_updated))}</span>'
        f'<span>세션: {esc(session_text(mission))} · 마지막 활동 {esc(dated(mission.last_activity))}</span></div>'
    )


def unit_status(value, include_token=False):
    meanings = {"todo": "다음 후보", "doing": "진행", "held": "보류", "done": "완료"}
    if include_token:
        return token_text(value, meanings)
    return meanings.get(value, value)


def recorded_value(value, empty="미기록"):
    if value.strip() in ("", "-"):
        return empty
    return value


def token_text(value, meanings, empty="미기록"):
    value = recorded_value(value, empty)
    if value in meanings:
        return f"{meanings[value]} ({value})"
    return value


def recorded_time_html(value, empty="시각 미기록"):
    if value.strip() in ("", "-"):
        return esc(empty)
    return f'<time datetime="{esc(value)}" title="{esc(value)}">{esc(value.replace("T", " ", 1))}</time>'


def record_text_html(value, empty="미기록", key=""):
    value = recorded_value(value, empty)
    text = f'<div class="record-text">{esc(value)}</div>'
    if len(value) > 600:
        return f'<details class="record-full" data-key="{esc(key)}"><summary>전문 펼치기 · {len(value):,}자</summary><div class="record-scroll">{text}</div></details>'
    return text


def record_fields_html(fields):
    return '<dl class="record-fields">' + "".join(f'<dt>{esc(label)}</dt><dd>{value}</dd>' for label, value in fields) + '</dl>'


def unit_dependencies(mission, row):
    conditions = []
    short_conditions = []
    dependencies = {unit["id"]: unit for unit in mission.units}
    for name in row["after"].split(","):
        name = name.strip()
        if name in ("", "-"):
            continue
        if name in dependencies:
            dependency = dependencies[name]
            conditions.append(f"선행 {name} {dependency['goal']} ({unit_status(dependency['status'])})")
            short_conditions.append(f"선행 {name} {unit_status(dependency['status'])}")
        else:
            conditions.append(f"선행 {name}: 등록된 작업을 찾을 수 없음 · 의존 상태 미확인")
            short_conditions.append(f"선행 {name} 미확인")
    return conditions, short_conditions


def unit_fields_html(mission, row, key):
    dependencies, _ = unit_dependencies(mission, row)
    after = record_text_html(row["after"], "선행 작업 미등록", key + "-after")
    after += "".join(f'<span class="condition">{esc(value)}</span>' for value in dependencies)
    timebox = recorded_value(row["timebox"], "시간 한도 미기록")
    if row["timebox"].strip() not in ("", "-"):
        timebox += "분"
    fields = [
        ("마일스톤 번호", esc(recorded_value(row["id"]))),
        ("작업 목표", record_text_html(row["goal"], "작업 목표 미기록", key + "-goal")),
        ("진행 상태", esc(unit_status(row["status"], include_token=True))),
        ("작업 범위 파일", record_text_html(row["files"], "작업 범위 파일 미등록", key + "-files")),
        ("완료 조건", record_text_html(row["done"], "완료 조건 미기록", key + "-done")),
        ("검증 계획", record_text_html(row["verify"], "검증 계획 미기록", key + "-verify")),
        ("시간 한도", esc(timebox)),
        ("금지·제약", record_text_html(row["forbid"], "금지·제약 미기록", key + "-forbid")),
        ("사람 확인 사항", record_text_html(row["human"], "사람 확인 사항 미기록", key + "-human")),
        ("선행 작업", after),
        ("변경 단위 (CL·브랜치)", record_text_html(row["unit"], "변경 단위 미등록", key + "-unit")),
        ("시작·재개 시각", recorded_time_html(row["started"])),
    ]
    return record_fields_html(fields)


def unit_heading_html(row, summarize_conditions):
    status = f'<span class="muted">· <span class="record-token">{esc(unit_status(row["status"]))}</span></span>'
    if summarize_conditions:
        return f'<div class="unit-summary"><b class="record-token">{esc(row["id"])}</b><span class="unit-goal">{esc(row["goal"])}</span>{status}</div>'
    return f'<b>{esc(row["id"])}</b> {esc(row["goal"])} {status}'


def unit_condition_summary(row, dependency_count):
    labels = []
    if dependency_count:
        labels.append(f"선행 작업 {dependency_count}개")
    if row["human"] not in ("", "-"):
        labels.append("사람 확인")
    if row["forbid"] not in ("", "-"):
        labels.append("금지·제약")
    return " · ".join(labels)


def unit_html(mission, row, compact=False, brief=False, key_prefix="work", summarize_conditions=False):
    conditions, short_conditions = unit_dependencies(mission, row)
    dependency_count = len(conditions)
    if row["human"] not in ("", "-"):
        conditions.append(f"사람 확인: {row['human']}")
        short_conditions.append("사람 확인")
    if row["forbid"] not in ("", "-"):
        conditions.append(f"금지: {row['forbid']}")
        short_conditions.append("금지 조건")
    content = unit_heading_html(row, summarize_conditions)
    if row["status"] == "doing" and mission_state.is_overdue(row):
        content += f' <span class="warn">시간 한도 {esc(mission_state.ratio_of(row))}</span>'
    if brief:
        return f"<li>{content}</li>"
    full_conditions = "".join(f'<span class="condition">{esc(condition)}</span>' for condition in conditions)
    if summarize_conditions:
        if conditions:
            content += f'<span class="condition">{esc(unit_condition_summary(row, dependency_count))}</span>'
    elif compact and conditions:
        key = f"unit-conditions-{key_prefix}-{mission.slot}-{mission.id}-{row['id']}"
        content += f'<details data-key="{esc(key)}"><summary>{esc(" · ".join(short_conditions))}</summary>{full_conditions}</details>'
    else:
        content += full_conditions
    key = f"unit-{key_prefix}-{mission.slot}-{mission.id}-{row['id']}"
    content += f'<details class="record unit-detail" data-key="{esc(key)}"><summary>작업 상세 펼치기</summary><div class="record-body">{unit_fields_html(mission, row, key)}</div></details>'
    return f"<li>{content}</li>"


def work_list(mission, rows, compact=False, brief=False, key_prefix="work", summarize_conditions=False):
    classes = "work-list"
    if brief:
        classes += " brief-work"
    return f'<ul class="{classes}">' + "".join(unit_html(mission, row, compact, brief, key_prefix, summarize_conditions) for row in rows) + "</ul>"


def current_html(mission, compact=False, summarize_conditions=False):
    if mission_state.UNITS_FILE in mission.errors:
        return '<p class="warn">작업 계획 읽기 실패 · 현재 작업 미확인</p>'
    if mission.doing:
        if compact:
            previews = " · ".join(f'{esc(row["id"])} {esc(row["goal"])}' for row in mission.doing[:2])
            if len(mission.doing) > 2:
                previews += f' · 진행 작업 {len(mission.doing)}개'
            key = f'current-{mission.slot}-{mission.id}'
            return f'<details class="brief-details" data-key="{esc(key)}"><summary><span class="brief-clamp">{previews}</span></summary>{work_list(mission, mission.doing, key_prefix="current", summarize_conditions=summarize_conditions)}</details>'
        return work_list(mission, mission.doing, compact, key_prefix="current", summarize_conditions=summarize_conditions)
    text = "진행 중으로 기록된 작업 없음"
    if mission.unanswered:
        text += " · 공유된 질문 답변 대기"
    if mission.counts.get("held", 0):
        text += f' · 보류 작업 {mission.counts["held"]}개 확인 필요'
    return f'<p class="muted">{text}</p>'


def remaining_html(mission, compact=False, summarize_conditions=False):
    if not mission.units:
        if mission_state.UNITS_FILE in mission.errors:
            return '<p class="muted">작업 계획 읽기 실패 · 남은 일 미확인</p>'
        return '<p class="muted">작업 계획 미등록</p>'
    rows = mission.remaining
    if not rows:
        return '<p class="muted">등록된 작업 모두 완료</p>'
    if compact:
        preview = next((row for row in rows if row["status"] in ("todo", "held")), next((row for row in rows if row["status"] != "doing"), {}))
        if preview:
            preview_html = work_list(mission, [preview], brief=True)
        else:
            preview_html = '<span class="muted">진행 중 작업만 남아 있음</span>'
        return (
            f'<div class="remaining-brief"><span class="counts">남은 {len(rows)}개</span>{preview_html}'
            + f'<details class="remaining-more" data-key="remaining-{esc(mission.slot)}-{esc(mission.id)}"><summary>작업·조건 펼치기</summary>'
            + f'<div><p class="counts">남은 작업 {len(rows)}개 · 진행 중 포함</p>{work_list(mission, rows, key_prefix="remaining", summarize_conditions=summarize_conditions)}</div></details></div>'
        )
    return f'<p class="counts">남은 작업 {len(rows)}개 · 진행 중 포함</p>{work_list(mission, rows, key_prefix="remaining", summarize_conditions=summarize_conditions)}'


def label_text(label):
    return "메인" if label == MAIN_LABEL else label


def section_head(number, title):
    return f'<h2 class="sec"><span class="eyebrow">{number:02d} /</span>{esc(title)}</h2>'


def mission_fields_html(config, mission):
    meanings = {"A": "설계 확인", "B": "작업 계획 승인", "C": "결과·마지막 확인"}
    fields = []
    for name, value in mission.fields.items():
        label = name
        text = esc(recorded_value(value))
        if name == "현재 단계":
            token = (STAGE_TOKEN.match(value) or [""])[0]
            labels = {stage["id"]: stage.get("label") or stage["id"] for stage in config.stages}
            if token in labels:
                text = f'{esc(labels[token])} ({esc(value)})'
        elif name.startswith("확인 "):
            letter = name[3:4]
            if letter in meanings:
                label += " · " + meanings[letter]
        fields.append((label, text))
    return record_fields_html(fields)


def ledger_unit_text(mission, value):
    for unit in mission.units:
        if unit["id"] == value:
            return f'{value} · {unit["goal"]}'
    return recorded_value(value, "대상 미기록")


def state_record_html(mission, file_name, row, number):
    key = f'record-{mission.slot}-{mission.id}-{file_name}-{number}'
    if file_name == mission_state.LEDGER_FILE:
        status = token_text(row["status"], {"pass": "통과", "fail": "실패", "retry": "재시도", "blocked": "막힘"})
        grade = token_text(row["grade"], {"editmode-pass": "Unity EditMode 검증", "pytest-pass": "pytest 검증", "compile-only": "컴파일만 확인", "doc-only": "문서 검증", "needs-human": "사람 확인 필요"})
        title = ledger_unit_text(mission, row["unit"])
        meta = f'<span class="record-token">판정: {esc(status)}</span><span>검증 수준: {esc(grade)}</span>' + recorded_time_html(row["at"])
        fields = [
            ("기록 시각", recorded_time_html(row["at"])),
            ("검증 대상", esc(title)),
            ("판정 결과", esc(status)),
            ("검증 수준", esc(grade)),
            ("검증 근거", record_text_html(row["evidence"], "검증 근거 미기록", key + "-evidence")),
            ("기록된 관련·검증 대상 파일", record_text_html(row["files"], "관련·검증 대상 파일 미등록", key + "-files")),
        ]
    elif file_name == mission_state.DECISIONS_FILE:
        title = f'{recorded_value(row["id"], "결정 번호 미기록")} · {recorded_value(row["what"], "논의 내용 미기록")}'
        meta = recorded_time_html(row["at"])
        fields = [
            ("기록 시각", recorded_time_html(row["at"])),
            ("결정 번호", esc(recorded_value(row["id"], "결정 번호 미기록"))),
            ("논의 내용", record_text_html(row["what"], "논의 내용 미기록", key + "-what")),
            ("선택 결과", record_text_html(row["chosen"], "선택 결과 미기록", key + "-chosen")),
            ("이유", record_text_html(row["why"], "이유 미기록", key + "-why")),
            ("영향 범위", record_text_html(row["affects"], "영향 범위 미기록", key + "-affects")),
        ]
    else:
        title = token_text(row["action"], {"open": "미션 시작 연결", "resume": "미션 이어서 연결"}, "이벤트 미기록")
        meta = recorded_time_html(row["at"])
        fields = [
            ("연결 기록 시각", recorded_time_html(row["at"])),
            ("연결 이벤트", esc(title)),
            ("대화 ID", record_text_html(row["session"], "대화 ID 미기록", key + "-session")),
            ("로그 경로", record_text_html(row["transcript"], "로그 경로 미기록", key + "-transcript")),
        ]
    return (
        f'<li><details class="record" data-key="{esc(key)}"><summary><span class="record-title">{esc(title)}</span>'
        f'<span class="record-meta">{meta}</span><span class="muted">기록 상세 펼치기</span></summary>'
        f'<div class="record-body">{record_fields_html(fields)}</div></details></li>'
    )


def state_records_html(mission, file_name, rows):
    if file_name == mission_state.UNITS_FILE:
        return work_list(mission, rows, key_prefix="source", summarize_conditions=True)
    return '<ul class="record-list">' + "".join(state_record_html(mission, file_name, row, number) for number, row in reversed(list(enumerate(rows, start=1)))) + '</ul>'


def mission_row(mission, index):
    historical = mission.state != RUNNING
    badges = tag(mission.state, "dim" if historical else "")
    if historical:
        badges += tag("당시 기록", "dim")
    elif mission.active:
        badges += tag("ACTIVE", "on") + stale_tag(mission.stale)
    elif mission.state == RUNNING:
        badges += tag("세션 없음", "warn")
    if mission.errors:
        badges += tag(f"읽기 실패: {', '.join(mission.errors)}", "warn")
    if mission.question_issues:
        badges += tag(question_issue_badge([mission]), "warn")
    body = f'<a class="name" href="{mission_url(mission)}">{esc(mission.title or mission.id)}</a> {badges}'
    plan_state = "작업 계획 읽기 실패" if mission_state.UNITS_FILE in mission.errors else "작업 계획 미등록"
    body += f'<div class="stage">{esc(mission.stage)} · 완료 {mission.counts["done"]}/{len(mission.units)} · 남음 {len(mission.remaining)}</div>' if mission.units else f'<div class="stage">{plan_state}</div>'
    meta = f'업무 자료 {esc(dated(mission.state_updated))}<br>세션 {esc(session_text(mission))}'
    return f'<div class="row"><span class="no">{index:02d}</span><div>{body}</div><div class="meta">{meta}</div><a class="arrow" href="{mission_url(mission)}">→</a></div>'


def slot_section(location):
    row = location.row
    meta = ""
    if location.label != MAIN_LABEL:
        state = row.get("state", "-")
        meta += tag(f"synced {row.get('synced', '-')}", "dim") + tag(f"state {state}", "" if state == "ready" else "warn")
    if location.active.corrupt:
        meta += tag("ACTIVE 손상", "warn")
    elif location.active.id:
        meta += tag(f"ACTIVE {slots.active_text(location.active)}", "on") + stale_tag(location.stale)
    if location.problem:
        meta += tag(location.problem, "warn")
    running = [mission for mission in location.missions if mission.state == RUNNING]
    folded = [mission for mission in location.missions if mission.state != RUNNING]
    rows = "".join(mission_row(mission, number) for number, mission in enumerate(running, start=1))
    out = f'<h3 class="sub">{esc(label_text(location.label))}</h3><div>{meta}</div>'
    out += f'<div class="list">{rows}</div>' if rows else '<p class="muted">진행 중 미션 없음</p>'
    if folded:
        inner = "".join(mission_row(mission, number) for number, mission in enumerate(folded, start=1))
        out += f'<details data-key="{esc(location.label)}"><summary>중단·닫힘 {len(folded)}개</summary><div class="list">{inner}</div></details>'
    return out


def confirm_box(mission, letter):
    main = mission.confirms.get(letter, "")
    parts = {key: value for key, value in mission.confirms.items() if key[0] == letter and key != letter}
    if main.startswith(APPROVED):
        kind = "full"
    elif main.startswith(SKIPPED):
        kind = "skip"
    elif APPROVED in main or any(value.startswith(APPROVED) for value in parts.values()):
        kind = "half"
    else:
        kind = ""
    title = " / ".join(f"{key} {value}" for key, value in mission.confirms.items() if key[0] == letter) or f"{letter} 없음"
    return f'<span title="{esc(title)}">{letter}<span class="box {kind}"></span></span>'


def stage_track(config, mission):
    token = (STAGE_TOKEN.match(mission.stage) or [""])[0]
    ids = [stage["id"] for stage in config.stages]
    current = ids.index(token) if token in ids else -1
    steps = "<i>›</i>".join(
        f'<span class="{"cur" if index == current else "later" if index > current else ""}">{esc(stage.get("label") or stage["id"])}</span>'
        for index, stage in enumerate(config.stages)
    )
    if current < 0:
        steps += f'<span class="cur" title="{esc(mission.stage)}">{esc(token or mission.stage)}</span>'
    return f'<div class="track" title="{esc(mission.stage)}">{steps}</div>'


def slot_card(config, location):
    running = [mission for mission in location.missions if mission.state == RUNNING]
    head = f'<div class="card-top"><span class="slot">{esc(label_text(location.label))}</span>'
    if not running:
        state = "ACTIVE 손상" if location.active.corrupt else "비어 있음"
        return f'<div class="card idle">{head}<span class="state">{state}</span></div><span class="counts">진행 중 미션 없음</span></div>'
    mission = next((item for item in running if item.active), running[0])
    kind = "turn" if any(item.unanswered or item.question_issues for item in running) else ""
    head += f'<a class="mission-name" title="{esc(mission.title or mission.id)}" href="{mission_url(mission)}">미션 상세 →</a>'
    state = tag(mission.state) + (tag("ACTIVE") + stale_tag(mission.stale) if mission.active else tag("세션 없음", "warn"))
    if location.active.corrupt:
        state += tag("ACTIVE 손상", "warn")
    if mission.errors:
        state += tag(f"읽기 실패: {', '.join(mission.errors)}", "warn")
    badge = decision_badge(running)
    if badge:
        state += tag(badge, "warn")
    issue_badge = question_issue_badge(running)
    if issue_badge:
        state += tag(issue_badge, "warn")
    plan_state = "작업 계획 읽기 실패" if mission_state.UNITS_FILE in mission.errors else "작업 계획 미등록"
    progress = f'마일스톤 완료 {mission.counts["done"]}/{len(mission.units)} · 남음 {len(mission.remaining)}' if mission.units else plan_state
    goal_preview = next((line.strip() for line in mission.goal.splitlines() if line.strip()), "")
    goal = f'<details class="brief-details goal-details" data-key="goal-{esc(mission.slot)}-{esc(mission.id)}"><summary class="goal"><span class="goal-preview">{esc(goal_preview)}</span><span class="goal-collapse">목표 접기</span></summary><p class="goal">{esc(mission.goal)}</p></details>' if mission.goal else '<p class="muted">목표 기록 없음</p>'
    questions = "".join(
        question_html(item, name, compact=True, origin=item.id if item is not mission else "")
        for item in running for name in item.unanswered
    )
    issues = "".join(question_issue_html(item, name, item.id if item is not mission else "") for item in running for name in item.question_issues)
    issue_section = '<section><h3>기록 확인 필요</h3><div>' + issues + '</div></section>' if issues else ""
    others = "".join(f'<a href="{mission_url(item)}">{esc(item.title or item.id)} →</a>' for item in running if item is not mission)
    return (
        f'<article class="card {kind}" data-slot="{esc(location.label)}">{head}<span class="state">{state}</span></div>'
        + stage_track(config, mission)
        + '<div class="mission-grid"><section><h3>목표</h3><div>' + goal + '</div></section><section><h3>내 결정</h3><div>'
        + (questions or '<span class="muted">공유된 미답 질문 없음</span>') + '</div></section>' + issue_section + '<section><h3>지금</h3><div>'
        + current_html(mission, compact=True) + '</div></section><section><h3>다음</h3><div>' + remaining_html(mission, compact=True)
        + '</div></section></div>'
        + f'<div class="summary-meta"><span>{progress}</span><span>자료 갱신 {esc(ago(mission.state_updated))}</span><span>세션: {esc(session_text(mission))}</span></div>'
        + '<details class="card-meta"><summary>승인·활동 상세</summary>'
        + '<div class="boxes">' + "".join(confirm_box(mission, letter) for letter in CONFIRM_LETTERS) + '</div>'
        + freshness_html(mission) + '</details>'
        + (f'<div class="other-missions">같은 슬롯의 다른 진행 미션 {len(running) - 1}개: {others}</div>' if others else "")
        + '</article>'
    )


def page(title, body, right="", script="", preview=False, embedded=False):
    mast, foot, theme = "", "", ""
    if not embedded:
        theme = THEME_SCRIPT
        mast = f'<header class="mast"><div class="in"><h1>{esc(title)}</h1><div class="mast-r">{right}{THEME_TOGGLE}</div></div></header>{THEME_CONTROL_SCRIPT}'
        foot = '<footer class="foot"><div class="in"><span>han missions</span><span>local · 127.0.0.1</span></div></footer>'
    if preview:
        body += DOCUMENT_VIEWER
        script += DOCUMENT_VIEWER_SCRIPT
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>{theme}<style>{STYLE}</style></head>
<body>{mast}
<main class="wrap">{body}</main>
{foot}
{script}</body></html>"""


def message(status, text):
    return status, HTML_TYPE, page(str(status), f'<p>{esc(text)}</p><p><a href="/">← 개요</a></p>').encode("utf-8")


def overview_page(config):
    found = collect(config)
    waiting = [(mission, name) for location in found for mission in location.missions if mission.state == RUNNING for name in mission.unanswered]
    visible = [location for location in found if location.label != MAIN_LABEL or location.missions]
    occupied = [location for location in visible if any(mission.state == RUNNING for mission in location.missions)]
    occupied.sort(key=lambda location: not any(mission.state == RUNNING and (mission.unanswered or mission.question_issues) for mission in location.missions))
    idle = [location for location in visible if location not in occupied]
    cards = [slot_card(config, location) for location in occupied]
    body = section_head(1, "슬롯") + f'<div class="board">{"".join(cards)}</div>'
    if idle:
        body += '<div class="idle-board">' + "".join(slot_card(config, location) for location in idle) + "</div>"
    body += section_head(2, "답 기다리는 질문")
    if waiting:
        rows = "".join(
            f'<a class="row link" href="{view_url(os.path.join(mission.folder, name))}"><span class="no">{number:02d}</span>'
            f"<span>{question_name(mission, name)}<br><span class=\"document-name\">{esc(name)}</span><span class=\"counts\">{question_status(mission.questions[name])}</span></span><span class=\"meta\">{esc(label_text(mission.slot))} · {esc(mission.id)}</span>"
            f'<span class="arrow">→</span></a>'
            for number, (mission, name) in enumerate(waiting, start=1)
        )
        body += f'<div class="invert"><div class="list">{rows}</div></div>'
    else:
        body += '<p class="muted">공유된 미답 질문 없음</p>'
    issues = [(mission, name) for location in found for mission in location.missions if mission.state == RUNNING for name in mission.question_issues]
    if issues:
        body += section_head(3, "기록 확인 필요") + f'<p class="counts">{len(issues)}건</p>'
        body += '<div class="list">' + "".join(
            f'<div class="row"><span class="no">{number:02d}</span><div>{question_issue_html(mission, name)}</div><a class="meta" href="{mission_url(mission)}">{esc(label_text(mission.slot))} · {esc(mission.id)} →</a></div>'
            for number, (mission, name) in enumerate(issues, start=1)
        ) + '</div>'
    body += '<details data-key="mission-index"><summary>전체 미션·중단·닫힘 기록</summary>' + "".join(slot_section(location) for location in found) + "</details>"
    right = (
        f'<span>갱신 {datetime.now().strftime("%H:%M:%S")}</span>'
        f'<label class="refresh-toggle"><input type="checkbox" id="auto"> {REFRESH_SECONDS}초 자동 새로고침</label>'
    )
    script = REFRESH_SCRIPT.replace("__REFRESH_MS__", str(REFRESH_SECONDS * 1000))
    return 200, HTML_TYPE, page("Missions", body, right, script, preview=True).encode("utf-8")


def poc_entry(root, poc):
    if os.path.isfile(os.path.join(root, poc)):
        return raw_url(os.path.join(root, poc)), esc(f"PoC · {poc}")
    return "", tag(f"PoC 파일 없음: {poc}", "warn")


def file_links(folder, names, lead=(), mission_title=""):
    entries = [(href, label, "") for href, label in lead]
    for name in names:
        path = os.path.join(folder, name)
        try:
            text = read_shared_text(folder, path) if name.endswith(".md") and name != "mission.md" else ""
        except READ_ERRORS:
            text = ""
        label = document_label(name, text, mission_title, folder) if name.endswith(".md") else name
        entries.append((view_url(path), esc(label), name))
    rows = "".join(
        (f'<a class="row link" href="{href}">' if href else '<div class="row">')
        + f'<span class="no">{number:02d}</span><span>{label}' + (f'<br><span class="document-name">{esc(name)}</span>' if name else "") + '</span><span class="meta"></span>'
        + ('<span class="arrow">→</span></a>' if href else "<span></span></div>")
        for number, (href, label, name) in enumerate(entries, start=1)
    )
    return f'<div class="list">{rows}</div>' if rows else '<p class="muted">없음</p>'


def mission_page(config, slot, mission_id):
    if not (slot == MAIN_LABEL or slots.NAME_PATTERN.fullmatch(slot)) or not mission_guard.valid_mission_id(mission_id):
        return message(400, "slot·id 형식 오류")
    matching = [item for item in locations(config) if item.label == slot]
    if not matching or not mission_folder_allowed(matching[0], mission_id) or not os.path.isfile(os.path.join(matching[0].state_dir, mission_id, "mission.md")):
        return message(404, f"미션 없음: {slot}/{mission_id}")
    location = matching[0]
    mission = load_mission(location, mission_id)
    folder = mission.folder
    shared = attempt(mission.errors, mission_state.SHARED_DOCUMENTS_FILE, folder, mission_state.shared_documents, set())
    automatic = attempt(mission.errors, "질문 문서", folder, mission_state.auto_question_documents, set())
    names = sorted(name for name in shared | automatic | SYSTEM_DOCUMENTS if os.path.isfile(os.path.join(folder, name)) and document_visible(folder, os.path.join(folder, name), shared))
    captures = sorted((name for name in names if name.startswith("captures/") and os.path.splitext(name)[1].lower() in IMAGE_TYPES), key=natural_key)
    badges = tag(label_text(slot), "dim") + tag(mission.state, "" if mission.state == RUNNING else "dim")
    if mission.active:
        badges += tag("ACTIVE", "on") + stale_tag(mission.stale)
    elif mission.state == RUNNING:
        badges += tag("세션 없음", "warn")
    else:
        badges += tag("중단·닫힘 당시 기록", "dim")
    if mission.errors:
        badges += tag(f"읽기 실패: {', '.join(mission.errors)}", "warn")
    body = f"<p>{badges}</p>" + freshness_html(mission)
    body += '<section class="work-section"><h2>목표</h2>' + (f'<p class="goal">{esc(mission.goal)}</p>' if mission.goal else '<p class="muted">목표 기록 없음</p>') + '</section>'
    body += '<section class="work-section" id="current"><h2>지금 하는 일</h2>' + current_html(mission, summarize_conditions=True) + '</section>'
    body += '<section class="work-section" id="remaining"><h2>다음·남은 일</h2>' + remaining_html(mission, summarize_conditions=True) + '</section>'
    body += '<section class="work-section"><h2>내 결정</h2>' + f'<p class="counts">{esc(waiting_text(mission))}</p>'
    body += "".join(question_html(mission, name) for name in mission.unanswered) + '</section>'
    if mission.question_issues:
        body += '<section class="work-section" id="question-issues"><h2>기록 확인 필요</h2>' + f'<p class="counts">{len(mission.question_issues)}건</p>'
        body += "".join(question_issue_html(mission, name) for name in mission.question_issues) + '</section>'
    completed = [row for row in mission.units if row["status"] == "done"]
    if mission_state.UNITS_FILE in mission.errors:
        body += '<details data-key="completed"><summary>완료 작업 미확인</summary><p class="warn">작업 계획 읽기 실패 · 완료 작업 미확인</p></details>'
    else:
        body += f'<details data-key="completed"><summary>완료 작업 {len(completed)}개</summary>{work_list(mission, completed, key_prefix="completed", summarize_conditions=True)}</details>'
    if mission.now and "report.md" in shared:
        body += f'<details data-key="recent-report"><summary>최근 보고 · {esc(dated(mission.report_updated))}</summary><p class="counts">보고 당시 요약</p><div class="now">{esc(mission.now)}</div></details>'
    questions = sorted((name for name in mission.questions if name in names), key=natural_key)
    docs = sorted((name for name in names if name.endswith(".md") and name not in questions and name not in mission.question_issues), key=lambda name: (name not in DOC_FIRST, name))
    others = [name for name in names if name not in questions and name not in mission.question_issues and name not in docs and name not in captures]
    body += '<details data-key="source-documents"><summary>질문·확인 원문·문서·파일</summary>'
    body += section_head(1, "질문·확인 원문")
    marked = "".join(
        f'<div class="row"><span class="no">{number:02d}</span><div>{question_html(mission, name)}</div>'
        f'<span class="meta">{esc(question_status(mission.questions[name]))}</span><a class="arrow" href="{view_url(os.path.join(folder, name))}">→</a></div>'
        for number, name in enumerate(questions, start=1)
    )
    body += f'<div class="list">{marked}</div>' if marked else '<p class="muted">공유된 질문 없음</p>'
    poc = mission.fields.get("PoC", "")
    body += section_head(2, "문서") + file_links(folder, docs, [poc_entry(location.root, poc)] if poc else [], mission.title)
    if others:
        body += section_head(3, "기타 파일") + file_links(folder, others)
    if captures:
        thumbs = "".join(
            f'<a href="{view_url(os.path.join(folder, name))}"><img loading="lazy" src="{view_url(os.path.join(folder, name))}" alt="{esc(name)}"></a>'
            for name in captures
        )
        body += section_head(4, "캡처") + f'<div class="thumbs">{thumbs}</div>'
    body += '</details><details data-key="source-state"><summary>미션 상태·마일스톤·검증·결정·연결 기록</summary>'
    body += mission_fields_html(config, mission)
    descriptions = {
        mission_state.UNITS_FILE: "작업 목표와 진행 상태 · 완료 조건과 검증 계획은 실행 결과가 아닙니다.",
        mission_state.LEDGER_FILE: "최근 기록부터 표시 · 판정 결과와 검증 수준은 별개이며 통과는 제출·승인을 뜻하지 않습니다.",
        mission_state.DECISIONS_FILE: "최근 기록부터 표시 · 당시 논의와 선택 결과입니다.",
        mission_guard.SESSIONS_FILE: "최근 연결 기록부터 표시 · 현재 실행 여부는 상단 세션 상태에서 확인합니다.",
    }
    for number, (title, file_name, header) in enumerate(TABLES, start=1):
        body += f'<section data-table="{esc(file_name)}">' + section_head(number, title)
        body += f'<p class="counts">{esc(descriptions[file_name])}</p>'
        if file_name in names:
            body += f'<p class="record-source"><a href="{view_url(os.path.join(folder, file_name))}">원본 {esc(file_name)} 보기 →</a></p>'
        errors = []
        rows = attempt(errors, file_name, os.path.join(folder, file_name), lambda p, h=header: mission_state.read_table(p, h), [])
        if errors:
            body += tag(f"읽기 실패: {file_name} · 기록 미확인", "warn")
        elif not os.path.isfile(os.path.join(folder, file_name)):
            body += '<p class="muted">파일 미등록 · 기록 미확인</p>'
        elif rows:
            body += f'<p class="counts">기록 {len(rows)}건</p>' + state_records_html(mission, file_name, rows)
        else:
            body += '<p class="muted">기록 0건 · 아직 기록된 항목 없음</p>'
        body += '</section>'
    body += '</details>'
    return 200, HTML_TYPE, page(mission.title or mission_id, body, '<a href="/">← 개요</a>', preview=True).encode("utf-8")


def swap_file_link(match):
    before = match.string[match.start() - 1 : match.start()] if match.start() else ""
    path, _, anchor = match.group(1).partition("#")
    path = unquote(path)
    ext = os.path.splitext(path)[1].lower()
    if ext in (".html", ".htm", *IMAGE_TYPES, *ASSET_TYPES):
        target = raw_url(path) + (f"#{anchor}" if anchor else "")
    else:
        target = view_url(path, anchor)
    if before == "(":
        return target
    if before in ("[", "`"):
        return match.group(0)
    return f"[{match.group(0)}]({target})"


def document_visible(folder, path, shared):
    root, real = os.path.realpath(folder), os.path.realpath(path)
    if not slots.inside_root(root, real):
        return False
    requested = os.path.relpath(os.path.abspath(path), os.path.abspath(folder)).replace("\\", "/")
    if any(part.startswith(".") and part not in (".", "..") for part in requested.split("/")):
        return False
    name = os.path.relpath(real, root).replace("\\", "/")
    if any(part.startswith(".") for part in name.split("/")) or name == mission_state.SHARED_DOCUMENTS_FILE:
        return False
    if slots.inside_root(root, os.path.abspath(path)):
        requested = os.path.relpath(os.path.abspath(path), root).replace("\\", "/")
    if os.path.isfile(os.path.join(folder, "mission.md")) and mission_state.auto_question_document(folder, requested):
        return True
    return name in SYSTEM_DOCUMENTS or any(os.path.normcase(os.path.realpath(os.path.join(folder, item))) == os.path.normcase(real) for item in shared)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def shared_bytes_match(folder, path, data):
    real = os.path.normcase(os.path.realpath(path))
    return any(
        os.path.normcase(os.path.realpath(os.path.join(folder, row["path"]))) == real and digest(data) == row["sha256"]
        for row in mission_state.shared_document_rows(folder)
    )


def read_shared_text(folder, path):
    name = os.path.relpath(path, folder).replace("\\", "/")
    if not document_visible(folder, path, {name}):
        return ""
    with open(path, "rb") as stream:
        data = stream.read()
    if mission_state.auto_question_document(folder, name) or shared_bytes_match(folder, path, data):
        return data.decode("utf-8-sig")
    return ""


def mission_folders(config, raw, real):
    found = set()
    absolute = os.path.abspath(raw)
    for location in locations(config):
        try:
            entries = list(os.scandir(location.state_dir))
        except FileNotFoundError:
            continue
        for entry in entries:
            if not entry.is_dir() or not mission_guard.valid_mission_id(entry.name):
                continue
            folder = os.path.realpath(entry.path)
            requested_inside = slots.inside_root(os.path.abspath(entry.path), absolute)
            if not requested_inside and not slots.inside_root(folder, real):
                continue
            if not mission_folder_allowed(location, entry.name):
                if requested_inside:
                    raise mission_state.StateError("미션 폴더 경로가 잘못됨")
                continue
            found.add(os.path.abspath(entry.path))
    return found


def read_allowed(config, raw):
    try:
        real = ""
        if raw and os.path.isabs(raw):
            real = os.path.realpath(raw)
    except ValueError:
        real = ""
    if not real or not any(slots.inside_root(os.path.realpath(root), real) for root in (config.main, config.root)):
        return real, b"", message(403, "허용된 폴더(메인·슬롯 루트) 밖 경로")
    if not os.path.isfile(real):
        return real, b"", message(404, "파일 없음")
    try:
        folders = mission_folders(config, raw, real)
        if any(not document_visible(folder, raw, set()) and not document_visible(folder, raw, mission_state.shared_documents(folder)) for folder in folders):
            return real, b"", message(404, "공유되지 않은 문서")
        with open(real, "rb") as f:
            data = f.read()
        if any(os.path.relpath(real, os.path.realpath(folder)).replace("\\", "/") not in SYSTEM_DOCUMENTS and not mission_state.auto_question_document(folder, os.path.relpath(real, os.path.realpath(folder))) and not shared_bytes_match(folder, real, data) for folder in folders):
            return real, b"", message(404, "공유되지 않은 문서")
        return real, data, ()
    except (ValueError, mission_state.StateError):
        return real, b"", message(404, "공유되지 않은 문서")
    except OSError as e:
        return real, b"", message(500, f"읽기 실패: {e.strerror or e}")


def raw_type(real):
    ext = os.path.splitext(real)[1].lower()
    if ext in (".html", ".htm"):
        return HTML_TYPE
    if ext in IMAGE_TYPES:
        return IMAGE_TYPES[ext]
    if ext in ASSET_TYPES:
        return ASSET_TYPES[ext]
    guessed = mimetypes.guess_type(real)[0] or "application/octet-stream"
    return f"{guessed}; charset=utf-8" if guessed.startswith("text/") or guessed.endswith(UTF8_SUFFIXES) else guessed


def raw_response(config, raw):
    real, data, failure = read_allowed(config, raw)
    return failure or (200, raw_type(real), data)


def view_response(config, raw, embedded=False):
    real, data, failure = read_allowed(config, raw)
    if failure:
        return failure
    ext = os.path.splitext(real)[1].lower()
    if ext in IMAGE_TYPES:
        return 200, IMAGE_TYPES[ext], data
    if ext in (".html", ".htm"):
        return 200, HTML_TYPE, data
    if b"\0" in data[:BINARY_PROBE_BYTES]:
        return 200, "application/octet-stream", data
    if len(data) > MAX_TEXT_BYTES:
        return 200, TEXT_TYPE, data
    text = data.decode("utf-8-sig", errors="replace")
    title = os.path.basename(real)
    if ext == ".md":
        title = document_label(os.path.basename(real), text, "", os.path.dirname(real))
        root = max((item.root for item in locations(config) if slots.inside_root(os.path.realpath(item.root), real)), key=len, default=config.root)
        source_json = json.dumps(FILE_URL.sub(swap_file_link, text)).replace("<", "\\u003c")
        body = (
            f'<p class="muted document-path">{esc(real)}</p><script type="application/json" id="source" data-url="{esc(raw_url(real))}" data-root="{esc(raw_url(root) + "/")}">{source_json}</script>'
            f'<article id="out" class="md"></article><script src="{MARKED_URL}"></script>{MARKDOWN_SCRIPT}'
        )
        return 200, HTML_TYPE, page(title, body, '<a href="/">← 개요</a>', embedded=embedded).encode("utf-8")
    lines = "".join(
        f'<div class="line" id="L{number}"><span class="no">{number}</span>{esc(line)}</div>' for number, line in enumerate(text.splitlines(), start=1)
    )
    body = f'<p class="muted">{esc(real)}</p><div class="code">{lines}</div>'
    return 200, HTML_TYPE, page(title, body, '<a href="/">← 개요</a>', embedded=embedded).encode("utf-8")


def route(config, target):
    parsed = urlsplit(target)
    query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
    if parsed.path == "/":
        return overview_page(config)
    if parsed.path == "/mission":
        return mission_page(config, query.get("slot", ""), query.get("id", ""))
    if parsed.path == "/view":
        return view_response(config, query.get("path", ""), embedded=query.get("embedded", "") == "1")
    if parsed.path.startswith(RAW_PREFIX):
        return raw_response(config, unquote(parsed.path[len(RAW_PREFIX) :]))
    return message(404, "없는 주소")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        redirect = ""
        try:
            status, content_type, body = route(self.server.config, self.path)
            parsed = urlsplit(self.path)
            path = parse_qs(parsed.query).get("path", [""])[0]
            if status == 200 and parsed.path == "/view" and os.path.splitext(path)[1].lower() in (".html", ".htm"):
                redirect = raw_url(path)
                status, body = 302, b""
        except Exception as e:  # 요청 하나가 터져도 서버는 계속 돌아야 함
            status, content_type, body = 500, TEXT_TYPE, f"내부 오류: {e}".encode("utf-8")
        self.send_response(status)
        if redirect:
            self.send_header("Location", redirect)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


class Server(ThreadingHTTPServer):
    # Windows 는 SO_REUSEADDR 로 쓰는 중인 포트에도 바인드돼 버림
    allow_reuse_address = False

    def __init__(self, config, port):
        self.config = config
        super().__init__(("127.0.0.1", port), Handler)


def build_parser():
    parser = argparse.ArgumentParser(prog="dashboard.py", description="미션 상태·질문 대시보드 (읽기 전용 로컬 서버)")
    parser.add_argument("--runtime", choices=slots.RUNTIMES)
    parser.add_argument("--project", help="어댑터를 찾기 시작할 폴더 (기본: 현재 폴더)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--open", action="store_true", help="브라우저로 열기")
    return parser


def main(argv=None, runtime="claude"):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as e:
        return slots.USAGE if e.code else slots.OK
    runtime = args.runtime or runtime
    try:
        located = slots.locate(args.project, runtime)
        config = slots.Config(located, slots.read_adapter(located.adapter_path, runtime), runtime)
    except slots.UsageError as e:
        print(f"사용 오류: {e}", file=sys.stderr)
        return slots.USAGE
    try:
        server = Server(config, args.port)
    except OSError as e:
        print(f"사용 오류: 포트 {args.port} 를 열 수 없음 ({e.strerror or e})", file=sys.stderr)
        return slots.USAGE
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"대시보드: {url} (Ctrl+C 로 끝냄)", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return slots.OK


if __name__ == "__main__":
    sys.exit(main())
