# Game 미션 어댑터 (예시)

`/han:mission`이 Unity + Perforce 프로젝트에서 쓰는 절차의 예시임. 기계가 읽는 값은 같은 폴더의 `adapter.json`,
계약은 플러그인의 `skills/mission/references/adapter-schema.md`. 이 폴더를 `adapters/<내 프로젝트>/`로 복사해
이름·경로·명령을 내 프로젝트에 맞게 고쳐 씀. 여기 적힌 절차가 프로젝트 문서와 어긋나면 문서와 코드가 맞고, 이 파일을 고침.

`VcsWrapper`·`QualityGate`·`ProjectOps`·`project-build.bat`는 가상의 프로젝트 도구 이름이며 이 저장소에 포함되지 않음. 실제 프로젝트의 명령으로 바꿔 씀.

## 하네스 문서

미션 시작 때 읽을 프로젝트 문서의 경로를 적음. 예: `CLAUDE.md`, `AGENTS.md`, 규칙·워크플로 문서.
작업 종류별 규약(코드 스타일·테스트·데이터)은 구현 에이전트 브리프에 해당 문서 경로를 넣음.
Codex 슬롯은 `runtimes.codex.slots.prepare`를 쓰고 Claude 설정·메모리는 복사하지 않음.

## VCS — Perforce

작업 단위(CL) 만들기, 파일 열기, 체크포인트, 남이 연 파일 처리를 적음.

- 명령은 워크스페이스 루트에서 실행하고, CL을 만든 뒤 `p4 describe -s <CL>`의 클라이언트가 `p4 info`의 `Client name`과 같은지 봄.
- 환경변수 `P4CLIENT`가 다른 작업 공간으로 잡혀 있으면 쓰기 명령 전에 `p4 -c <작업 공간>`으로 명시함.
- CL 설명은 한 줄 `[Game/{이름}] 무슨 작업인지`.
- 번호 CL 만들기: `Tool/VcsWrapper/vcs-wrapper.exe pending new "Game mission <id> <Mn>"`. 한글 설명은 UTF-8 파일로 넣음
  (`Tool/QualityGate/qualitygate.bat change-description <CL> --description-file <절대경로>`).
- 파일 열기: 기존 `p4 edit -c <CL> <경로>`, 새 파일 `p4 add -c <CL> <경로>`. default CL은 쓰지 않음.
- `Assets/` 아래 새 파일·이동·삭제는 `.meta`와 짝으로 함. 승인된 작업의 신규 원본과 Unity Refresh로 만든 신규 `.meta`를
  같은 작업 CL에 정리하는 것은 통상 완료 작업임. 원본·짝 파일 소유와 대상 CL이 확정된 명시 파일만 프로젝트 도구로 등록함.
  기존 추적 `.meta`·GUID 변경, 다른 번호 CL·소유 불명 파일 이동은 이 범위가 아님. raw `p4 reopen` 허용으로 넓히지 않음.
- 생성물은 CL에 넣지 않고 원본(예: xlsx)을 바꾸면 파생 파일을 같은 CL에 넣음.
- `slots.churn` 파일(에디터가 스스로 다시 쓰는 에셋)은 CL에 넣지 않음. `refresh`가 `#have`로 되돌림.
- 체크포인트: `p4 shelve -f -c <CL>`. 상태 확인: `p4 opened -c <CL>`, 파일 소유 확인 `p4 opened -a <파일>`.
- 다른 번호 CL에 열린 파일은 건드리지 않음. 같은 파일은 두 CL에 못 열고 `p4 reopen`은 그 CL을 깨뜨림.
- 가드가 막은 명령은 사용자가 그 자리에서 승인했을 때만 `<stateDir>/<id>/allow-once`로 한 번 엶.

## submit

- 확인 C에서 사용자가 그 자리에서 요청할 때만 함. 가드 훅이 막으므로 요청한 CL마다 게이트 명령
  (예: `Tool/QualityGate/qualitygate.bat submit <CL>`)을 1회 허용으로 미션을 연 채 돌리고, 모두 제출된 뒤 닫음(스킬 §7 4항).
- 게이트는 depot HEAD에 CL을 얹어 검증함. 내가 안 건드린 테스트가 실패하면 head와 리비전을 대조하고,
  sync·resolve·재제출은 다시 사용자 확인을 받음.

## 검증

등급별 명령과 비고를 표로 적음. 등급 이름은 `adapter.json`의 `verify.grades`와 맞춤.

| 등급 | 명령 | 비고 |
| --- | --- | --- |
| `editmode-pass` | Unity MCP의 EditMode 테스트 실행(필터 지정) | 시작된 테스트가 0개면 실패 |
| `compile-only` | 도메인 리로드 → 준비 대기 → 컴파일 상태 확인 | 테스트가 없는 C# 변경 |
| `pytest-pass` | `Build/project-build.bat code test GameServer <파일·-k 필터>` | 필터 없는 전체 실행 금지 |
| `doc-only` | 문서 린트 | 문서 CL |

- Unity 에디터는 세션 공용임. 응답의 `projectPath`가 이 체크아웃인지 보고, 남이 켠 Play 모드를 끄지 않음.
- 승인된 계약을 고정한 뒤 파일(.meta 포함)·공유 생성물·독점 자원 소유가 겹치지 않고 선행 단위가 `done`인
  구현 단위는 병렬로 진행할 수 있음.
- Unity 조작·Refresh·컴파일·EditMode·Play 검증은 코디네이터가 관련 구현과 하위 작업의 반환 또는 정지를 모은 뒤
  순차 실행함. 검증 준비와 Refresh 뒤 `.meta`·생성물·CL 정리를 포함한 입력을 확정하고, verify 스냅샷부터
  완료까지 입력을 바꾸는 작업을 멈춤. 다른 작업과 충돌하면 기다리고, 입력이 바뀐 결과는 통과로 인정하지 않음.
- 로컬에서 안 돌리는 검증(예: 전체 회귀)은 submit 게이트가 맡는다고 적음.
- 실패는 파일 위치만으로 다른 세션 탓으로 돌리지 않음. 현재 CL diff·head 기준선·`p4 opened -a` 근거로 원인을 구분함.
  내 변경이 만든 회귀는 고치고, 기준선·다른 작업 문제는 근거와 미검증 영향을 보고함.

### 에디터 수명

- 조사·계획·서버 작업에는 에디터를 열지 않음. Unity 검증·Refresh가 필요할 때만 코디네이터가 엶.
- 먼저 대상 프로젝트의 기존 프로세스·MCP 연결을 확인하고, 있으면 재사용하되 종료하지 않음.
  없으면 `vars.unityExe`에 `-projectPath <root>/Unity/Game`을 주어 실행하고 PID·시작 시각·프로젝트 경로를 기록함.
  슬롯에서는 `slots.editor`를 해당 슬롯 경로로 펼친 명령을 씀. 진행 중 미션에서 `slots run`을 다시 부르지 않음.
- MCP의 `projectPath`와 준비 상태를 확인한 뒤 검증함. 프로젝트별 준비 대기 도구·시간 상한을 이 절에 적음.
  연결 실패로 사람에게 묻기 전 현재 프로세스·연결 상태를 다시 확인하고, 승인 범위 안의 복구는 코디네이터가 처리함.
- 연속된 검증·임포트와 결과 수집이 끝나거나 실패·질문 대기로 빠지면, 기록한 인스턴스만 정상 종료함.
  Windows에서는 PID·시작 시각·프로젝트 경로를 다시 확인하고 `CloseMainWindow()`로 종료 요청한 뒤 종료 여부를 확인함.
  저장 확인·진행 중 작업으로 남으면 자동 저장·버리기·강제 종료하지 않고 보고함. 기존 에디터·소유권을 잃은 인스턴스는 유지함.

## 사람 확인 항목

기계로 판정 못 하는 변경 종류를 `human` 칸으로 빼서 확인 C에 모음. 예:

- 화풍·재미·식별 난이도·기획 확정
- 프리팹·씬의 장식·색·Sprite·hierarchy
- 밸런스 수치와 기획 데이터 값
- 되돌리기 비싼 결정(VCS, 외부 계약, 배포 대상, 데이터 스키마 방향)

## 독점 락 파일

`+l` 타입이라 한 번에 한 클라이언트만 여는 파일 종류를 적음. 예: xlsx, png·psd·fbx, 오디오. 같은 파일을
건드리는 마일스톤은 병렬로 돌리지 않고, 남이 열고 있으면(`p4 opened -a`) 사용자 확인 대상으로 올림.
이 제한은 해당 `+l` 파일의 소유가 겹치는 작업에 적용하며, 서로 독립된 파일 작업은 허용함.

## PoC 단계

`adapter.json`의 PoC 단계(`game-poc` 스킬)를 쓰는 프로젝트만 적음. 그 스킬이 말하는 승인 순서대로 단계마다 확인 A를 받고,
스킬이 사용자 확인을 요구하는 지점에서는 미션의 멈춤 규칙대로 멈춰 물음. PoC 단계를 안 쓰면 `stages`에서 빼고 이 절을 지움.

## 슬롯 준비

슬롯 워크스페이스(`/han:slots`)를 쓰는 프로젝트만 적음. 새 워크스페이스가 빌드·검증 가능해지기까지의 단계와 실측 시간.

| 단계 | 명령 | 비고 |
| --- | --- | --- |
| 작업 공간 | 메인 작업 공간의 옵션을 따라 슬롯용 클라이언트를 만듦 | 스크립트가 함 |
| 동기화 | `p4 -c <client> sync -q --parallel=threads=8 //Depot/Main/...@<기준 CL>` | 기준 CL은 메인이 받은 `#have`(`{baseline}`). `create`는 head가 아닐 수 있고, `refresh`는 메인을 먼저 head로 sync한 뒤라 head |
| Library 복사 | `robocopy`로 메인의 `Unity/Game/Library`를 슬롯에 복사 | 임포트 시간을 줄임 |
| junction | `Code/setup.bat` | 슬롯 안을 가리키는 링크 |
| 임포트·csproj | Unity 배치 모드로 `SyncSolution` 실행 | 에디터 라이선스 확인 |
| 확인 | `Build/project-build.bat code build GameServer` | 서버 빌드 |

- 슬롯의 csproj는 그 슬롯 경로를 가리킴. 메인 csproj를 복사하지 않음.
- EditMode·플레이 검증 직전에만 그 슬롯의 Unity 에디터를 열고, 세션마다 `projectPath`로 구분함. 끝나면 위 에디터 수명 절차로 정리함.
- 슬롯끼리도 `+l` 파일은 한 작업 공간만 열고, submit은 그 슬롯 세션에서 슬롯 작업 공간의 CL로 함.
- 슬롯 폴더는 `adapters/projects.json`의 `roots`에도 넣어야(예: `D:/Work/slots/*`) 슬롯 세션이 어댑터를 찾음.
