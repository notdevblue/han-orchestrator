import json
import os
from collections import Counter
from pathlib import Path

import pytest

import usage_report as report

START = report.timestamp('2026-10-23T00:00:00+09:00')
END = report.timestamp('2026-10-24T00:00:00+09:00')


def row(request, at='2026-10-23T00:10:00+09:00', output=7):
    return {'type': 'assistant', 'timestamp': at, 'requestId': request, 'effort': 'high',
            'message': {'id': request, 'model': 'claude-sonnet-5-5', 'usage': {
                'input_tokens': 3, 'cache_creation_input_tokens': 11,
                'cache_read_input_tokens': 100, 'output_tokens': output,
                'iterations': [{'type': 'advisor_message', 'input_tokens': 150, 'output_tokens': 9}]}}}


def transcript(home, project, rows, child=False):
    root = home / 'projects' / report.slots.project_dir_name(str(project))
    path = root / ('s/subagents/agent-a.jsonl' if child else 's.jsonl')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(r) + '\n' for r in rows), encoding='utf-8')
    return path


def test_streaming_duplicates_cache_and_advisor_are_counted_separately(tmp_path):
    project = tmp_path / 'game'
    first, last = row('one', output=1), row('one', output=7)
    transcript(tmp_path, project, [first, last])
    diagnostics = Counter()
    records, missing = report.collect_usage([('main', project)], tmp_path, START, END, diagnostics)
    result = report.aggregate(records)
    assert result['responses'] == 1 and result['advisor_calls'] == 1
    assert result['usage'] == dict(zip(report.TOKEN_FIELDS, (3, 11, 100, 7)))
    assert result['advisor_usage'] == dict(zip(report.TOKEN_FIELDS, (150, 0, 0, 9)))
    assert not missing and not diagnostics


def test_timezone_boundaries_child_role_and_corrupt_tail(tmp_path):
    project = tmp_path / 'game'
    path = transcript(tmp_path, project, [row('before', '2026-10-22T14:59:59Z'),
        row('start', '2026-10-22T15:00:00Z'), row('end', '2026-10-23T15:00:00Z')], True)
    path.with_suffix('.meta.json').write_text('{"agentType":"code-reviewer"}', encoding='utf-8')
    with path.open('a', encoding='utf-8') as stream:
        stream.write('{unfinished')
    diagnostics = Counter()
    records, _ = report.collect_usage([('slot1', project)], tmp_path, START, END, diagnostics)
    assert len(records) == 1 and records[0]['role'] == 'code-reviewer'
    assert records[0]['effort'] == 'high'
    assert diagnostics['malformed_records'] == 1


def test_missing_metadata_unknown_iteration_and_no_request_id(tmp_path):
    project = tmp_path / 'game'
    record = row('one')
    del record['requestId']
    record['message']['usage']['iterations'].append({'type': 'future_tool'})
    transcript(tmp_path, project, [record], True)
    diagnostics = Counter()
    records, _ = report.collect_usage([('main', project)], tmp_path, START, END, diagnostics)
    assert records[0]['role'] == 'unknown-subagent'
    assert diagnostics['fallback_message_keys'] == 1
    assert diagnostics['unknown_iterations'] == 1


def test_quality_retains_unknown_counts_and_marks_legacy_time(tmp_path):
    root = tmp_path / 'state'
    directory = root / 'm1' / 'verification'
    directory.mkdir(parents=True)
    legacy = directory / 'M1-review-old.json'
    legacy.write_text(json.dumps({'schema': 1, 'unit': 'M1', 'status': 'findings'}), encoding='utf-8')
    os.utime(legacy, (START.timestamp(), START.timestamp()))
    (directory / 'M1-review-new.json').write_text(json.dumps({
        'schema': 1, 'unit': 'M1', 'status': 'clean', 'recordedAt': '2026-10-23T05:00:00+09:00',
        'findingsCount': 0, 'fixedCount': 2}), encoding='utf-8')
    (root / 'm1' / 'ledger.tsv').write_text('\t'.join(report.LEDGER_HEADER) +
        '\n2026-10-23T05:00:00+09:00\tM1\tretry\tpytest-pass\t"literal quote\tsrc.py\n', encoding='utf-8')
    quality = report.quality_report([('main', tmp_path)], {'main': root}, START, END, Counter())[0]
    assert quality['reviewRounds'] == 2
    assert len(quality['recordedRetries']) == 1
    old = next(r for r in quality['reviews'] if r['timeSource'] == 'file_mtime')
    assert old['findingsCount'] == -1 and old['fixedCount'] == -1


def test_cli_reports_missing_projects_and_does_not_export_content(tmp_path, capsys):
    project = tmp_path / 'game'
    record = row('one')
    record['message']['content'] = [{'type': 'text', 'text': 'private-content-sentinel'}]
    transcript(tmp_path, project, [record])
    output = tmp_path / 'report.json'
    assert report.main(['--project', f'main={project}', '--project', f'slot={tmp_path / "missing"}',
        '--mission-root', f'main={tmp_path / "no-state"}', '--mission-root', f'slot={tmp_path / "no-state"}',
        '--claude-home', str(tmp_path), '--start', START.isoformat(), '--end', END.isoformat(),
        '--output', str(output)]) == 0
    text = output.read_text(encoding='utf-8')
    assert 'private-content-sentinel' not in text
    assert len(json.loads(text)['missingTranscriptDirectories']) == 1
    assert 'responses=1' in capsys.readouterr().out


def test_timestamp_requires_timezone():
    with pytest.raises(ValueError):
        report.timestamp('2026-10-23T00:00:00')


def test_missing_adapter_is_reported_without_losing_usage(tmp_path, monkeypatch):
    def unavailable(*args):
        raise report.slots.UsageError('missing')
    monkeypatch.setattr(report.slots, 'locate', unavailable)
    quality = report.quality_report([('main', tmp_path)], {}, START, END, Counter())
    assert quality == [{'project': 'main', 'available': False, 'reason': 'UsageError'}]


def test_relative_project_path_resolves_to_the_same_transcript_directory(tmp_path, monkeypatch):
    project = tmp_path / 'game'
    project.mkdir()
    transcript(tmp_path, project, [row('one')])
    monkeypatch.chdir(project)
    records, missing = report.collect_usage([('main', Path('.'))], tmp_path, START, END, Counter())
    assert len(records) == 1 and not missing


def test_invalid_adapter_state_path_keeps_usage_available(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(report.slots, 'locate', lambda *args: SimpleNamespace(project=str(tmp_path), adapter_path='unused'))
    monkeypatch.setattr(report.slots, 'read_adapter', lambda *args: {'schema': 1})
    quality = report.quality_report([('main', tmp_path)], {}, START, END, Counter())
    assert quality == [{'project': 'main', 'available': False, 'reason': 'GuardOff'}]
