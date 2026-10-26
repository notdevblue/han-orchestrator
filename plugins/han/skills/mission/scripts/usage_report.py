import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'slots' / 'scripts'))
import slots
import mission_guard
from mission_state import LEDGER_HEADER, StateError, read_table

TOKEN_FIELDS = ('input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens', 'output_tokens')


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('ISO 시각 문자열이 필요함')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('시각에 시간대가 필요함')
    return result


def named_path(value):
    name, separator, path = value.partition('=')
    if not separator or not name.strip() or not path.strip():
        raise argparse.ArgumentTypeError('이름=경로 형식이 필요함')
    return name, Path(path)


def token_counts(usage, diagnostics):
    result = {}
    for field in TOKEN_FIELDS:
        value = usage.get(field, 0)
        if field not in usage and field in ('input_tokens', 'output_tokens'):
            diagnostics['missing_token_fields'] += 1
        if type(value) is not int or value < 0:
            diagnostics['invalid_token_fields'] += 1
            value = 0
        result[field] = value
    return result


def merge_counts(target, source):
    for key, value in source.items():
        target[key] = max(target.get(key, 0), value)


def read_json(path, diagnostics):
    try:
        result = json.loads(path.read_text(encoding='utf-8-sig'))
        if isinstance(result, dict):
            return result
    except (OSError, ValueError):
        pass
    diagnostics['unreadable_metadata'] += 1
    return {}


def collect_usage(projects, home, start, end, diagnostics):
    records = {}
    missing = []
    for label, project in projects:
        directory = home / 'projects' / slots.project_dir_name(str(project.resolve()))
        if not directory.is_dir():
            missing.append(str(directory))
            continue
        for path in sorted(directory.rglob('*.jsonl')):
            child = path.parent.name == 'subagents'
            meta = path.with_suffix('.meta.json')
            role = 'coordinator'
            if child:
                role = str(read_json(meta, diagnostics).get('agentType') or 'unknown-subagent') if meta.exists() else 'unknown-subagent'
            try:
                stream = path.open(encoding='utf-8-sig')
            except OSError:
                diagnostics['unreadable_transcripts'] += 1
                continue
            with stream:
                for number, line in enumerate(stream, 1):
                    try:
                        row = json.loads(line)
                        if not isinstance(row, dict):
                            raise ValueError('record')
                        if row.get('type') != 'assistant':
                            continue
                        at = timestamp(row['timestamp'])
                        if not start <= at < end:
                            continue
                        message = row['message']
                        usage = message['usage']
                        if not isinstance(usage, dict):
                            raise ValueError('usage')
                    except (ValueError, KeyError, TypeError):
                        diagnostics['malformed_records'] += 1
                        continue
                    request = row.get('requestId', '')
                    message_id = message.get('id', '')
                    key = (request, message_id) if request and message_id else (str(path), row.get('uuid', number))
                    if not request or not message_id:
                        diagnostics['fallback_message_keys'] += 1
                    effort = row.get('perTurnEffort') or row.get('effort') or 'unknown'
                    if not isinstance(effort, str):
                        effort = 'unknown'
                    record = records.setdefault(key, {
                        'project': label, 'role': role, 'model': str(message.get('model') or 'unknown'),
                        'effort': effort, 'source': str(path), 'usage': {}, 'advisor': {},
                    })
                    merge_counts(record['usage'], token_counts(usage, diagnostics))
                    for index, iteration in enumerate(usage.get('iterations') or []):
                        if not isinstance(iteration, dict):
                            diagnostics['unknown_iterations'] += 1
                            continue
                        if iteration.get('type') == 'advisor_message':
                            counts = token_counts(iteration, diagnostics)
                            merge_counts(record['advisor'].setdefault(index, {}), counts)
                        elif iteration.get('type') != 'message':
                            diagnostics['unknown_iterations'] += 1
    return list(records.values()), missing


def aggregate(records):
    result = {'responses': 0, 'advisor_calls': 0, 'usage': dict.fromkeys(TOKEN_FIELDS, 0),
              'advisor_usage': dict.fromkeys(TOKEN_FIELDS, 0)}
    for record in records:
        result['responses'] += 1
        result['advisor_calls'] += len(record['advisor'])
        for field in TOKEN_FIELDS:
            result['usage'][field] += record['usage'].get(field, 0)
            result['advisor_usage'][field] += sum(value.get(field, 0) for value in record['advisor'].values())
    return result


def grouped(records, fields):
    groups = defaultdict(list)
    for record in records:
        groups[tuple(record[field] for field in fields)].append(record)
    return [dict(zip(fields, key), **aggregate(values)) for key, values in sorted(groups.items())]


def quality_report(projects, mission_roots, start, end, diagnostics):
    results = []
    for label, project in projects:
        root = mission_roots.get(label)
        if root is None:
            try:
                located = slots.locate(str(project), 'claude')
                adapter = slots.read_adapter(located.adapter_path, 'claude')
                root = Path(mission_guard.state_dir_of(located.project, adapter))
            except (ValueError, OSError, RuntimeError, slots.UsageError, mission_guard.GuardOff) as error:
                results.append({'project': label, 'available': False, 'reason': type(error).__name__})
                continue
        if not root.is_dir():
            results.append({'project': label, 'available': False, 'reason': 'missing mission root'})
            continue
        reviews = []
        retries = []
        for path in sorted(root.glob('*/verification/*-review-*.json')):
            review = read_json(path, diagnostics)
            if review.get('schema') != 1 or 'unit' not in review:
                diagnostics['invalid_review_records'] += 1
                continue
            source = 'recordedAt' if review.get('recordedAt') else 'file_mtime'
            try:
                at = timestamp(review['recordedAt']) if source == 'recordedAt' else datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            except (ValueError, TypeError, OSError):
                diagnostics['invalid_review_records'] += 1
                continue
            if start <= at < end:
                reviews.append({
                    'mission': path.parent.parent.name, 'unit': review['unit'], 'status': review.get('status', 'unknown'),
                    'kind': review.get('kind', 'unknown'), 'findingsCount': review.get('findingsCount', -1),
                    'fixedCount': review.get('fixedCount', -1), 'at': at.isoformat(), 'timeSource': source,
                })
        for path in sorted(root.glob('*/ledger.tsv')):
            try:
                for row in read_table(str(path), LEDGER_HEADER):
                    if row.get('status') != 'retry':
                        continue
                    try:
                        at = timestamp(row['at'])
                    except (ValueError, TypeError, KeyError):
                        diagnostics['invalid_retry_records'] += 1
                        continue
                    if start <= at < end:
                        retries.append({'mission': path.parent.name, 'unit': row.get('unit', ''), 'at': at.isoformat()})
            except (OSError, StateError):
                diagnostics['unreadable_ledgers'] += 1
        results.append({'project': label, 'available': True, 'reviewRounds': len(reviews),
                        'reviews': reviews, 'recordedRetries': retries})
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description='Claude 사용량·Advisor·리뷰 기록을 모델 호출 없이 집계합니다.')
    parser.add_argument('--project', action='append', type=named_path, required=True)
    parser.add_argument('--mission-root', action='append', type=named_path, default=[])
    parser.add_argument('--claude-home', type=Path, default=Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude'))
    parser.add_argument('--start', type=timestamp, required=True)
    parser.add_argument('--end', type=timestamp, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.start >= args.end:
        parser.error('start는 end보다 앞서야 함')
    labels = [label for label, _ in args.project]
    paths = [str(path.resolve()).casefold() for _, path in args.project]
    if len(set(labels)) != len(labels) or len(set(paths)) != len(paths):
        parser.error('프로젝트 이름·경로 중복')
    if any(label not in labels for label, _ in args.mission_root):
        parser.error('mission-root 이름에 해당하는 project가 없음')
    diagnostics = Counter()
    records, missing = collect_usage(args.project, args.claude_home, args.start, args.end, diagnostics)
    quality = quality_report(args.project, dict(args.mission_root), args.start, args.end, diagnostics)
    result = {
        'schema': 1, 'generatedAt': datetime.now(timezone.utc).isoformat(),
        'start': args.start.isoformat(), 'end': args.end.isoformat(),
        'total': aggregate(records), 'byProject': grouped(records, ('project',)),
        'byRole': grouped(records, ('project', 'role')), 'byModel': grouped(records, ('model', 'effort')),
        'quality': quality, 'missingTranscriptDirectories': missing, 'diagnostics': dict(diagnostics),
        'limitations': ['토큰 합계는 요금·구독 한도 소비율이 아님', 'byModel은 호출한 실행 모델 기준이며 Advisor 모델별 집계가 아님',
                        '지적 수 -1은 미기록; 라운드별 지적은 고유 결함 수가 아님',
                        'retry는 명시적으로 기록된 재작업만 포함', 'Advisor 토큰은 일반 메시지 usage와 별도 집계',
                        '실행 중 로그는 나중에 usage가 추가되어 같은 기간을 다시 집계해도 값이 달라질 수 있음'],
    }
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f"responses={len(records)} advisor_calls={result['total']['advisor_calls']} reviews={sum(q.get('reviewRounds', 0) for q in quality)}")
    print(f"report={args.output} missing_projects={len(missing)} diagnostics={sum(diagnostics.values())}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
