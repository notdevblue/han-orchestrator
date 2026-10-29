#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "grok", "scripts")))
import grok_transcript

OK = 0
FAILED = 1

LOG_HEADER = ("at", "session", "until", "verdict", "dest", "summary")
VERDICT_APPLIED = "반영"
VERDICT_REJECTED = "기각"
VERDICT_SEEN = "확인"
VERDICTS = (VERDICT_APPLIED, VERDICT_REJECTED, VERDICT_SEEN)
DESTS = ("guide", "memory", "rules", "plugin")
NONE_CELL = "-"
TRANSCRIPT_ENV = "HAN_TRANSCRIPT"
LOG_ENV = "HAN_DISTILL_LOG"
TAIL_LIMIT = 300

SKIP_FLAGS = ("isMeta", "isCompactSummary", "isSidechain", "isVisibleInTranscriptOnly")
SKIP_PREFIXES = ("<task-notification>", "<local-command-", "[Request interrupted by user")
CODEX_CONTEXT_PREFIXES = ("# AGENTS.md instructions", "<environment_context>", "<permissions instructions>", "<skill>")

CELL_BREAKS = re.compile(r"[\t\r\n]+")
SYSTEM_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
COMMAND_NAME = re.compile(r"<command-name>(.*?)</command-name>", re.S)
COMMAND_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.S)


class DistillError(Exception):
    pass


class OneLineParser(argparse.ArgumentParser):
    def error(self, message):
        raise DistillError(f"{self.prog}: {message} (-h 로 사용법 확인)")


@dataclass
class Message:
    timestamp: str
    previous: str
    text: str


def clean(value):
    return CELL_BREAKS.sub(" ", str(value)).strip()


def fold(text):
    return " ".join(text.split())


def log_path(runtime="claude"):
    if runtime == "codex":
        base = os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")
    elif runtime == "grok":
        base = grok_transcript.grok_home()
    else:
        base = os.path.join(os.path.expanduser("~"), ".claude")
    return os.environ.get(LOG_ENV) or os.path.join(base, "han", "distill", "log.tsv")


def session_of(transcript):
    if grok_transcript.is_transcript(transcript):
        return grok_transcript.session_id(transcript)
    with open(transcript, encoding="utf-8") as f:
        try:
            entry = json.loads(f.readline())
        except ValueError:
            entry = {}
    if isinstance(entry, dict) and entry.get("type") == "session_meta":
        payload = entry.get("payload")
        session = payload.get("id") if isinstance(payload, dict) else ""
        if isinstance(session, str) and session:
            return session
    name = os.path.basename(transcript)
    return name[: -len(".jsonl")] if name.endswith(".jsonl") else name


def read_log(path):
    try:
        with open(path, encoding="utf-8-sig") as f:
            lines = f.read().split("\n")
    except FileNotFoundError:
        return []
    if tuple(lines[0].rstrip().split("\t")) != LOG_HEADER:
        raise DistillError(f"로그 헤더가 다름 (기대: {' '.join(LOG_HEADER)}): {path}")
    rows = []
    for number, line in enumerate(lines[1:], start=2):
        if not line.strip():
            continue
        cells = line.split("\t")
        if len(cells) != len(LOG_HEADER):
            raise DistillError(f"로그 {number}행 칸 수 {len(cells)} (기대 {len(LOG_HEADER)}): {path}")
        rows.append(dict(zip(LOG_HEADER, cells)))
    return rows


def append_log(path, cells):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fresh = not os.path.isfile(path) or os.path.getsize(path) == 0
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        if fresh:
            f.write("\t".join(LOG_HEADER) + "\n")
        f.write("\t".join(clean(cell) for cell in cells) + "\n")


def watermark_of(rows, session):
    marks = [row["until"] for row in rows if row["session"] == session and row["until"] != NONE_CELL]
    return max(marks, default="")


def text_of(content):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for item in content:
        if isinstance(item, dict) and item.get("type") in ("text", "input_text", "output_text") and isinstance(item.get("text"), str):
            parts.append(item["text"])
    return "\n".join(parts)


def user_text(entry):
    message = entry.get("message")
    body = SYSTEM_REMINDER.sub("", text_of(message.get("content") if isinstance(message, dict) else None)).strip()
    if not body or body.startswith(SKIP_PREFIXES):
        return ""
    name = COMMAND_NAME.search(body)
    if name is None:
        return body
    args = COMMAND_ARGS.search(body)
    arguments = args.group(1).strip() if args else ""
    return f"{name.group(1).strip()} {arguments}" if arguments else ""


def read_grok_messages(path):
    messages = []
    previous = ""
    for entry in grok_transcript.read_messages(path):
        if entry["role"] == "assistant":
            previous = fold(entry["text"]) or previous
        else:
            body = entry["text"]
            if body.lstrip().startswith(CODEX_CONTEXT_PREFIXES):
                continue
            text = user_text({"message": {"content": body}})
            if text:
                messages.append(Message(entry["timestamp"], previous[-TAIL_LIMIT:], text))
                previous = ""
    return messages


def read_messages(path):
    if grok_transcript.is_transcript(path):
        return read_grok_messages(path)
    messages = []
    previous = ""
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict) or any(entry.get(flag) for flag in SKIP_FLAGS):
                continue
            kind = entry.get("type")
            if kind == "response_item":
                message = entry.get("payload")
                if not isinstance(message, dict) or message.get("type") != "message":
                    continue
                kind = message.get("role")
                if kind == "user":
                    content = message.get("content")
                    if isinstance(content, list):
                        content = [item for item in content if not text_of([item]).lstrip().startswith(CODEX_CONTEXT_PREFIXES)]
                    elif isinstance(content, str) and content.lstrip().startswith(CODEX_CONTEXT_PREFIXES):
                        content = ""
                    message = {**message, "content": content}
                entry = {**entry, "message": message}
            if kind == "assistant":
                message = entry.get("message")
                text = fold(text_of(message.get("content") if isinstance(message, dict) else None))
                previous = text or previous
            elif kind == "user":
                text = user_text(entry)
                if text:
                    messages.append(Message(str(entry.get("timestamp") or ""), previous[-TAIL_LIMIT:], text))
                    previous = ""
    return messages


def cmd_extract(args):
    transcripts = args.transcript
    if not transcripts and args.runtime == "claude" and os.environ.get(TRANSCRIPT_ENV):
        transcripts = [os.environ[TRANSCRIPT_ENV]]
    if not transcripts:
        requirement = f"--transcript 나 {TRANSCRIPT_ENV}" if args.runtime == "claude" else "--transcript"
        raise DistillError(f"{requirement} 필요")
    rows = read_log(log_path(args.runtime))
    lines = []
    summaries = []
    number = 0
    for transcript in transcripts:
        if not os.path.isfile(transcript):
            raise DistillError(f"대화 기록 없음: {transcript}")
        session = session_of(transcript)
        floor = "" if args.all else watermark_of(rows, session)
        messages = [m for m in read_messages(transcript) if not floor or m.timestamp > floor]
        if messages:
            lines.append(f"## {session}")
            lines.append("")
        for message in messages:
            number += 1
            lines.append(f"### {number} · {message.timestamp}")
            if message.previous:
                lines.append(f"앞 답: > {message.previous}")
            lines.append(f"사용자: {message.text}")
            lines.append("")
        until = messages[-1].timestamp if messages and messages[-1].timestamp else NONE_CELL
        summaries.append(f"{session}: 메시지 {len(messages)}개, until={until}")
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    return [*summaries, args.out]


def cmd_record(args):
    summary = clean(args.summary or "")
    if args.verdict != VERDICT_SEEN and not summary:
        raise DistillError(f"{args.verdict} 은 --summary 필수")
    if args.verdict == VERDICT_APPLIED and not args.dest:
        raise DistillError(f"{VERDICT_APPLIED} 은 --dest 필수 ({', '.join(DESTS)})")
    session = clean(args.session)
    until = clean(args.until)
    if not session or not until:
        raise DistillError("--session, --until 비어 있음")
    cells = [
        datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        session,
        until,
        args.verdict,
        args.dest or NONE_CELL,
        summary or NONE_CELL,
    ]
    append_log(log_path(args.runtime), cells)
    return [f"기록 {session} until={until} {args.verdict}"]


def build_parser(runtime="claude"):
    parser = OneLineParser(prog="distill.py", description="대화 기록에서 사용자 메시지 추출, 처리 지점 기록")
    parser.add_argument("--runtime", choices=("claude", "codex", "grok"), default=runtime)
    sub = parser.add_subparsers(dest="command", required=True, parser_class=OneLineParser)

    extract = sub.add_parser("extract")
    extract.add_argument("--runtime", choices=("claude", "codex", "grok"), default=argparse.SUPPRESS)
    extract.set_defaults(handler=cmd_extract)
    extract.add_argument("--transcript", action="append", default=[])
    extract.add_argument("--out", required=True)
    extract.add_argument("--all", action="store_true")

    record = sub.add_parser("record")
    record.add_argument("--runtime", choices=("claude", "codex", "grok"), default=argparse.SUPPRESS)
    record.set_defaults(handler=cmd_record)
    record.add_argument("--session", required=True)
    record.add_argument("--until", required=True)
    record.add_argument("--verdict", required=True, choices=VERDICTS)
    record.add_argument("--dest", choices=DESTS)
    record.add_argument("--summary")
    return parser


def main(argv=None, runtime="claude"):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        try:
            args = build_parser(runtime).parse_args(argv)
        except SystemExit:
            return OK
        lines = args.handler(args)
    except (DistillError, OSError) as e:
        print(f"오류: {e}", file=sys.stderr)
        return FAILED
    for line in lines:
        print(line)
    return OK


if __name__ == "__main__":
    sys.exit(main())
