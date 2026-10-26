import hashlib
import json
import math
import os
import platform
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


class VerificationError(Exception):
    pass


def read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise VerificationError(f"JSON 읽기 실패: {path}: {error}") from error
    if not isinstance(value, dict):
        raise VerificationError(f"JSON 객체가 필요함: {path}")
    return value


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def file_hash(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def executable_identity(path):
    try:
        return file_hash(path)
    except OSError:
        info = Path(path).stat()
        return digest([info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns])


def runtime_key(project, plan):
    tools = {"verification": file_hash(__file__)}
    for command in plan["commands"]:
        cwd = project_path(project, command.get("cwd", "."))
        argv = command["argv"]
        executable = Path(shutil.which(argv[0]) or cwd / argv[0])
        if executable.is_file():
            tools[str(executable.resolve())] = executable_identity(executable)
        if len(argv) > 1 and not argv[1].startswith("-"):
            script = (cwd / argv[1]).resolve()
            if script.is_file():
                tools[str(script)] = file_hash(script)
    return digest({"tools": tools, "python": platform.python_version(), "platform": platform.platform()})


def project_path(project, value):
    if not isinstance(value, str) or not value:
        raise VerificationError("비어 있지 않은 문자열 경로가 필요함")
    root = Path(project).resolve()
    path = (root / value).resolve()
    if not path.is_relative_to(root):
        raise VerificationError(f"프로젝트 밖 경로: {value}")
    return path


def snapshot(project, inputs):
    if not isinstance(inputs, list) or not inputs or not all(isinstance(p, str) and p for p in inputs):
        raise VerificationError("inputs 에 검증에 영향을 주는 파일·폴더를 지정할 것")
    files = {}
    root = Path(project).resolve()
    for value in inputs:
        path = project_path(project, value)
        candidates = sorted(path.rglob("*")) if path.is_dir() else [path]
        for candidate in candidates:
            resolved = project_path(project, str(candidate))
            if resolved.is_dir():
                continue
            key = candidate.relative_to(root).as_posix()
            files[key] = file_hash(resolved) if resolved.is_file() else "missing"
    if not files or all(value == "missing" for value in files.values()):
        raise VerificationError("검증 inputs 에 실제 파일이 없음")
    return files


def load_plan(project, path, grades):
    plan = read_json(path)
    if plan.get("grade") not in grades:
        raise VerificationError("plan.grade 가 어댑터 verify.grades 에 없음")
    commands = plan.get("commands")
    if not isinstance(commands, list) or not commands:
        raise VerificationError("plan.commands 가 비어 있음")
    for command in commands:
        argv = command.get("argv") if isinstance(command, dict) else []
        if not isinstance(argv, list) or not argv or not all(isinstance(v, str) and v for v in argv):
            raise VerificationError("command.argv 는 비어 있지 않은 문자열 배열이어야 함")
        project_path(project, command.get("cwd", "."))
        timeout = command.get("timeoutSeconds", 600)
        if type(timeout) is not int or not 1 <= timeout <= 3600:
            raise VerificationError("timeoutSeconds 는 1~3600 정수여야 함")
    if not isinstance(plan.get("environment"), dict) or not plan["environment"]:
        raise VerificationError("plan.environment 에 런타임·대상 환경 식별자를 지정할 것")
    assertions = plan.get("results", [])
    if not isinstance(assertions, list):
        raise VerificationError("plan.results 는 배열이어야 함")
    for result in assertions:
        if not isinstance(result, dict) or not isinstance(result.get("path"), str):
            raise VerificationError("result.path 가 필요함")
        project_path(project, result["path"])
        if (not isinstance(result.get("equals", {}), dict) or not isinstance(result.get("positive", []), list)
                or not all(isinstance(field, str) and field for field in result.get("positive", []))):
            raise VerificationError("result.equals 는 객체, positive 는 필드 이름 배열이어야 함")
    if plan["grade"] == "editmode-pass" and not any(
        result.get("projectField") and result.get("positive") and result.get("equals", {}).get("status") == "passed"
        for result in assertions
    ):
        raise VerificationError("editmode-pass 는 status=passed·projectField·양수 테스트 수 검사가 필요함")
    snapshot(project, plan.get("inputs"))
    return plan


def result_field(value, name):
    try:
        for part in name.split("."):
            value = value[int(part)] if isinstance(value, list) else value[part]
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise VerificationError(f"결과 필드 없음: {name}") from error
    return value


def validate_results(project, plan):
    hashes = {}
    for result in plan.get("results", []):
        path = project_path(project, result["path"])
        value = read_json(path)
        for name, expected in result.get("equals", {}).items():
            if result_field(value, name) != expected:
                raise VerificationError(f"결과 불일치: {result['path']} {name} != {expected}")
        for name in result.get("positive", []):
            actual = result_field(value, name)
            if isinstance(actual, bool) or not isinstance(actual, (int, float)) or not math.isfinite(actual) or actual <= 0:
                raise VerificationError(f"실행 수가 양수가 아님: {name}")
        field = result.get("projectField")
        if field:
            actual_path = result_field(value, field)
            if not isinstance(actual_path, str) or not actual_path:
                raise VerificationError(f"결과 프로젝트 경로가 비어 있음: {field}")
            actual = Path(actual_path).resolve()
            expected = project_path(project, result.get("project", "."))
            if actual != expected:
                raise VerificationError(f"검증 프로젝트 불일치: {actual} != {expected}")
        hashes[str(path)] = file_hash(path)
    return hashes


def validate_receipt(project, folder, unit, receipt, plan=None):
    if receipt.get("schema") != 1 or receipt.get("status") != "pass" or receipt.get("unit") != unit:
        raise VerificationError("통과한 같은 마일스톤의 검증 기록이 필요함")
    if receipt.get("project") != str(Path(project).resolve()) or receipt.get("mission") != str(Path(folder).resolve()):
        raise VerificationError("다른 프로젝트·미션의 검증 기록임")
    recorded_plan = receipt.get("plan", {})
    if (not isinstance(recorded_plan, dict) or not isinstance(recorded_plan.get("commands"), list)
            or recorded_plan.get("grade") != receipt.get("grade")):
        raise VerificationError("검증 기록의 계획·등급이 올바르지 않음")
    if plan is not None and recorded_plan != plan:
        raise VerificationError("검증 계획·환경이 바뀜")
    if receipt.get("runner") != runtime_key(project, recorded_plan):
        raise VerificationError("검증 실행기 런타임이 바뀜")
    if receipt.get("snapshot") != snapshot(project, recorded_plan.get("inputs")):
        raise VerificationError("검증 후 입력 파일·데이터가 바뀜")
    if receipt.get("results") != validate_results(project, recorded_plan):
        raise VerificationError("검증 결과 파일이 바뀜")
    log = Path(receipt.get("log", ""))
    if not log.is_file() or file_hash(log) != receipt.get("logHash"):
        raise VerificationError("검증 로그가 없거나 바뀜")


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def run(project, folder, unit, plan, guard, force=False):
    directory = Path(folder) / "verification"
    directory.mkdir(exist_ok=True)
    latest = directory / f"{unit}.latest.json"
    if latest.exists() and not force:
        try:
            previous = read_json(latest)
            path = Path(previous.get("receipt", ""))
            if path.is_file():
                receipt = read_json(path)
                validate_receipt(project, folder, unit, receipt, plan)
                return str(path), receipt, True
        except (VerificationError, TypeError, KeyError):
            pass
    for command in plan["commands"]:
        guard(subprocess.list2cmdline(command["argv"]))
    before = snapshot(project, plan["inputs"])
    runtime = runtime_key(project, plan)
    prior_results = {
        result["path"]: project_path(project, result["path"]).stat().st_mtime_ns
        if project_path(project, result["path"]).exists() else -1
        for result in plan.get("results", [])
    }
    key = f"{unit}-{uuid.uuid4().hex}"
    log_path = directory / f"{key}.log"
    receipt_path = directory / f"{key}.json"
    status = "pass"
    error_text = ""
    codes = []
    started = time.time()
    with log_path.open("wb") as log:
        for command in plan["commands"]:
            try:
                done = subprocess.run(
                    command["argv"], cwd=project_path(project, command.get("cwd", ".")),
                    stdout=log, stderr=subprocess.STDOUT, timeout=command.get("timeoutSeconds", 600), shell=False,
                )
                codes.append(done.returncode)
                if done.returncode:
                    status, error_text = "fail", f"명령 종료 코드 {done.returncode}"
                    break
            except (OSError, subprocess.TimeoutExpired) as error:
                status, error_text = "fail", str(error)
                break
    results = {}
    try:
        if before != snapshot(project, plan["inputs"]) or runtime != runtime_key(project, plan):
            raise VerificationError("검증 실행 중 입력 파일·데이터가 바뀜")
        if status == "pass":
            for result in plan.get("results", []):
                path = project_path(project, result["path"])
                if not path.exists() or path.stat().st_mtime_ns == prior_results[result["path"]]:
                    raise VerificationError(f"이번 실행에서 결과가 갱신되지 않음: {result['path']}")
            results = validate_results(project, plan)
    except VerificationError as error:
        status, error_text = "fail", str(error)
    receipt = {
        "schema": 1, "project": str(Path(project).resolve()), "mission": str(Path(folder).resolve()),
        "unit": unit, "status": status, "grade": plan["grade"], "plan": plan, "snapshot": before,
        "runner": runtime, "results": results, "exitCodes": codes,
        "log": str(log_path), "logHash": file_hash(log_path), "seconds": round(time.time() - started, 2),
        "error": error_text,
    }
    write_json(receipt_path, receipt)
    write_json(latest, {"receipt": str(receipt_path)})
    return str(receipt_path), receipt, False


def record_review(project, folder, unit, receipt_path, evidence, reviewer, kind, status, findings_count=-1, fixed_count=-1):
    if findings_count < -1 or fixed_count < -1:
        raise VerificationError("지적·해소 수는 0 이상 또는 미기록(-1)이어야 함")
    if status == "clean" and findings_count > 0 or status == "findings" and findings_count == 0:
        raise VerificationError("리뷰 상태와 남은 지적 수가 다름")
    receipt = read_json(receipt_path)
    validate_receipt(project, folder, unit, receipt)
    validate_latest(folder, unit, receipt_path)
    report = Path(evidence).resolve()
    if not report.is_file() or not reviewer.strip():
        raise VerificationError("독립 리뷰어 이름과 실제 리뷰 결과 파일이 필요함")
    review = {
        "schema": 1, "unit": unit, "kind": kind, "status": status, "reviewer": reviewer,
        "recordedAt": datetime.now(timezone.utc).isoformat(),
        "findingsCount": findings_count, "fixedCount": fixed_count,
        "receipt": str(Path(receipt_path).resolve()), "receiptHash": file_hash(receipt_path),
        "evidence": str(report), "evidenceHash": file_hash(report),
        "snapshot": receipt["snapshot"], "planHash": digest(receipt["plan"]),
    }
    path = Path(folder) / "verification" / f"{unit}-review-{uuid.uuid4().hex}.json"
    write_json(path, review)
    write_json(Path(folder) / "verification" / f"{unit}.review.latest.json", {"review": str(path)})
    return str(path)


def validate_latest(folder, unit, receipt_path):
    latest = read_json(Path(folder) / "verification" / f"{unit}.latest.json")
    if Path(latest.get("receipt", "")).resolve() != Path(receipt_path).resolve():
        raise VerificationError("마지막 검증 기록이 아님: 이후 실패·재실행 결과를 먼저 확인할 것")


def validate_review(folder, unit, receipt_path, review_path):
    latest = read_json(Path(folder) / "verification" / f"{unit}.review.latest.json")
    if Path(latest.get("review", "")).resolve() != Path(review_path).resolve():
        raise VerificationError("마지막 리뷰 기록이 아님: 이후 리뷰 결과를 먼저 확인할 것")
    review = read_json(review_path)
    if review.get("schema") != 1 or review.get("unit") != unit or review.get("status") != "clean":
        raise VerificationError("같은 마일스톤의 clean 독립 리뷰가 필요함")
    if review.get("receipt") != str(Path(receipt_path).resolve()) or review.get("receiptHash") != file_hash(receipt_path):
        raise VerificationError("리뷰 이후 검증 기록이 바뀜")
    evidence = Path(review.get("evidence", ""))
    if not evidence.is_file() or file_hash(evidence) != review.get("evidenceHash"):
        raise VerificationError("리뷰 근거가 없거나 바뀜")


def review_scope(project, folder, unit, plan):
    current = snapshot(project, plan["inputs"])
    latest = Path(folder) / "verification" / f"{unit}.review.latest.json"
    prior = {}
    if latest.exists():
        review = read_json(read_json(latest)["review"])
        evidence = Path(review.get("evidence", ""))
        if (review.get("planHash") == digest(plan) and evidence.is_file()
                and file_hash(evidence) == review.get("evidenceHash")):
            prior = review.get("snapshot", {})
    return [name for name in sorted(set(current) | set(prior)) if current.get(name) != prior.get(name)]
