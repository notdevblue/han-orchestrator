# Grok 바깥 층 초안

등록하는 Grok 스킬은 `plugins/han/grok/`의 `han-status`, `han-doctor`, `han-mission-brief`, `han-run`, `han-mission`, `han-slots`, `han-adapter-init`, `han-distill`이다.
이 폴더의 초안은 그 절차를 가리키는 메모이며 Grok 스킬로 등록하지 않는다.
구현 절차의 등록본은 `plugins/han/grok/skills/han-run/SKILL.md`다. 이 초안 폴더에는 실행 스킬을 두지 않는다.

코디네이터는 상태를 읽고, 메인 또는 슬롯의 실행 봇에 작업을 넘겨 Grok만으로 진행할 수 있다.
로컬 상태·슬롯 조회는 han 스크립트로 하고, 저장소 코드 작업은 프로젝트의 실행 규칙을 따른다. Claude·Codex는 선택 경로다.
상태의 원본은 언제나 어댑터의 `<stateDir>`이고, 아래 스크립트 호출은 machine Shell(PowerShell)로 출력만 읽는다.
봇을 깨우는 인계는 `han-mission-brief`의 SendToAgent다. Main 또는 slotN에서 도는 Grok CLI는 `han-run`으로 그 자리에서 구현한다.
CLI가 읽은 전역 실행 규칙은 `grok inspect`에서 확인한다. 이 초안이 적는 명령으로 미션 파일을 쓰지 않는다.

## 공통 실행 환경 (PowerShell)

```powershell
[Console]::OutputEncoding=[Text.Encoding]::UTF8; $env:PYTHONUTF8='1'
$env:HAN_ADAPTERS_DIR='<repo>/adapters'
$han=(Get-Content "$env:USERPROFILE\.claude\plugins\installed_plugins.json" -Raw | ConvertFrom-Json).plugins.'han@han-orchestrator'[0].installPath
```

`$han`은 설치본(커밋된 배포본)이다. 저장소 작업 트리(`<repo>/plugins/han`)에는
작성 중 변경이 섞일 수 있어 기본으로 쓰지 않는다.

## 스킬별 분류

| han 스킬 | 코디네이터 셸에서 써도 됨(읽기) | 코디네이터 셸에서 하지 않음(깨운 봇 또는 선택 세션) |
| --- | --- | --- |
| mission (`mission_state.py`) | `active`, `status`, `context`, `transcripts`, `unit list`, `review-scope`, `document list` | 바깥 코디네이터의 미션 파일 쓰기. 실행 세션의 open/resume/close 마커는 가드가 처리하며, `init`은 ACTIVE와 현재 세션 확인 뒤 실행 |
| mission `close-stale`(플래그 없음) | 미리보기만. 사용자에게 **제안**까지. 실행 전 `doctor`/`active`로 충분하면 그것을 쓴다 | 실제 닫기는 사용자가 미션 id를 지정해 승인한 뒤 깨운 봇, 선택 세션, 또는 사용자가 직접 |
| slots (`slots.py`) | `status [이름...]` | `create`, `refresh`, `run`, `remove`(미션 중 가드가 막음, 워크스페이스를 바꿈) |
| slots 대시보드 (`dashboard.py`) | 이미 떠 있으면 `GET http://127.0.0.1:8765/` 읽기 | 띄우기·재시작은 사용자 요청 때만 |
| adapter-init (`check_guard.py`) | `check_guard.py <어댑터>`, `check_guard.py --doctor` | 어댑터 초안 작성·수정 |
| distill (`distill.py`) | 없음(`extract`도 파일을 씀) | `extract`, `record` |
| doctor (`doctor.py`) | 전체. 읽기 전용 | — |

## 초안 (등록하지 않음)

- `grok/skills/han-outer-status/SKILL.md` — 상태·끊김·설치 점검을 읽고 요약, 끊김은 제안만
- `grok/skills/han-outer-brief/SKILL.md` — 티어 메모. 실행 인계는 `plugins/han/grok/skills/han-mission-brief/SKILL.md`

## 하지 않는 것

- ACTIVE·`mission.md`·`*.tsv`·`allow-once` 직접 쓰기, 바깥 코디네이터에서 마커 실행, `--force-close-stale`
- 슬롯 만들기·갱신·실행·삭제, p4 쓰기 명령
- Grok 쪽에 미션 상태 사본·별도 상태 기계를 만드는 것
