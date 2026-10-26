# 어댑터 계약

프로젝트마다 다른 값은 어댑터 두 파일에 둔다.

- `adapter.json` — 훅과 스킬이 기계적으로 읽는 값
- `adapter.md` — 사람과 모델이 읽는 절차(작업 단위 만들기, 검증 순서, 함정)

## 어댑터 찾기

스킬과 가드 훅은 같은 순서로 찾는다. 시작 폴더는 `CLAUDE_PROJECT_DIR`, 없으면 cwd.

1. **저장소 안 어댑터** — 시작 폴더에서 위로 올라가며 `.claude/orchestrator/adapter.json`이 있는 첫 폴더.
   팀과 같이 쓰는 프로젝트에서 어댑터를 저장소에 올릴 때 쓴다.
2. **개인 레지스트리** — 위가 없으면 레지스트리 폴더의 `projects.json`의 `roots`와 시작 폴더·그 상위를
   대조한다. 맞으면 `<레지스트리>/<adapter>/adapter.json`을 쓰고, 맞은 폴더가 프로젝트 폴더다. 어댑터를 VCS에
   올리지 않고 개인적으로만 가질 때 쓴다. 레지스트리 폴더는 다음 순서로 정한다.
   - 환경변수 `HAN_ADAPTERS_DIR`. 스킬·스크립트(Bash 도구)가 읽는 값이다. SessionStart 훅이 플러그인 설정
     `adapters_dir` 값을 `CLAUDE_ENV_FILE`에 `export`로 적어 넣어 주므로, 설치본에서는 설정만 해 두면 된다
   - 플러그인 설정 `adapters_dir`. 설치 때 입력하고 훅에는 `CLAUDE_PLUGIN_OPTION_ADAPTERS_DIR`로 들어간다
     (Bash 도구 환경에는 없다)
   - 플러그인을 저장소에서 바로 돌릴 때의 `<저장소>/adapters`. 설치본은 플러그인 캐시에서 돌아 이 폴백이 없다

```json
[
  { "adapter": "example", "roots": ["D:/Work/Game_Main", "D:/Work/slots/*"] }
]
```

`roots`는 경로나 glob이고, 대소문자와 `\`·`/`를 가리지 않는다. 둘 다 없으면 어댑터 없음이다.
그때 스킬은 멈춘다. 훅은 미션 마커 명령(`echo han-mission open|resume|close <id>`)일 때만
`미션 가드 꺼짐: 어댑터 못 찾음 — <이유>`로 알리고(차단은 안 함), 다른 명령에는 아무것도 하지 않는다.
이유는 레지스트리 경로 미설정, 레지스트리 폴더 없음, `projects.json` 없음, 맞는 `roots` 없음 중 하나다.

어댑터를 찾으면 같은 폴더의 `local.json`(PC별 값, VCS 제외)을 깊게 병합해 읽는다. 객체는 키별로 합치고
배열·스칼라는 `local.json` 값이 통째로 덮는다. `schema`는 병합 뒤 값으로 검사한다.

## Grok에서 상태 읽기

Grok 코디네이터는 상태를 읽고 메인 또는 등록된 슬롯의 실행자에게 작업을 인계할 수 있다. 실제 봇 이름과 실행 도구는 개인 환경에서 연결한다.
Claude(`/han:mission`)와 Codex(`$han-codex:han-mission`)는 선택 경로다.
조회 스크립트 `plugins/han/grok/scripts/han_state.py`의 `snapshot`은 `ACTIVE`·`mission.md`·`slots.tsv`를 읽기만 한다.
`brief --runtime grok`는 SendToAgent 인계 블록을 출력하고, `claude`·`codex`는 붙여 넣을 문장을 출력한다. 조회 명령은 미션 파일을 쓰지 않는다.
Grok Bot에 등록하는 스킬은 `han-status`, `han-doctor`, `han-mission-brief` 세 개다.
`han-watch`와 `han-handoff`는 그 셋을 가리키는 저장소 안내이며 등록하지 않는다.

Grok의 검색만 아래 순서를 쓴다. Claude·Codex의 검색 순서와 `runtimes.codex` 병합은 그대로다.
가까운 폴더가 우선이고, 한 폴더 안에서는 앞에 있는 파일이 우선이다.

1. `.grok/orchestrator/adapter.json`, 이어서 같은 폴더의 `.claude/orchestrator/adapter.json`, 이어서 `.codex/orchestrator/adapter.json`.
2. 없으면 환경변수 `HAN_ADAPTERS_DIR`의 `projects.json`.
3. 없으면 `HAN_REPO/adapters`. `HAN_REPO`는 이 저장소를 받은 폴더다. 폴더를 지정했는데 `adapters`가 없으면 조회가 오류로 끝난다.
4. 그것도 없으면 이 저장소에서 스크립트를 직접 실행할 때 `<저장소>/adapters`.

Claude 플러그인 설정과 Codex `config.toml`은 읽지 않고, SessionStart로 경로를 주입하지 않는다.
상태 경로는 공유 병합인 `adapter.json` 다음 `local.json`만 적용한다. `runtimes.codex`와 별도의 Grok 미션 오버레이는 적용하지 않는다.

## adapter.json

```json
{
  "schema": 1,
  "project": "Game",
  "paths": {
    "vcsRoot": ".",
    "stateDir": "_My/missions"
  },
  "stages": [
    { "id": "content", "kind": "research", "label": "컨텐츠 확인" },
    { "id": "poc", "kind": "design", "label": "PoC", "skill": "game-poc", "optional": true },
    { "id": "impl", "kind": "implement", "label": "구현 진행" },
    { "id": "review", "kind": "review", "label": "검토 & 리뷰" }
  ],
  "guard": {
    "deny": [
      { "pattern": "^(?:&\\s*)?[\"']?(?:\\S*[\\\\/])?p4(?:\\.exe)?[\"']?(?:\\s+-\\w+(?:\\s+[^-\\s]\\S*)?)*\\s+submit\\b",
        "reason": "미션 중 submit 금지 — 확인 C에서 사용자가 요청할 때만" }
    ]
  },
  "roles": {
    "research": "han:researcher",
    "implement": "han:dev-medium",
    "verify": "han:verifier",
    "review": "code-reviewer",
    "reviewLoopSkill": "review-loop"
  },
  "verify": {
    "backend": "mcp",
    "grades": ["editmode-pass", "pytest-pass", "compile-only", "doc-only"]
  },
  "report": {
    "push": true
  },
  "slots": {
    "enabled": true,
    "names": ["slot1", "slot2", "slot3"],
    "vcs": "perforce",
    "client": "ws-yourname_{name}",
    "stream": "//Depot/Main",
    "mainClient": "ws-yourname_Main",
    "prepare": [
      { "id": "sync", "group": 1, "run": "p4 -c {client} sync -q --parallel=threads=8 //Depot/Main/...@{baseline}" },
      { "id": "import", "group": 2, "exclusive": true, "run": "\"{unityExe}\" -batchmode -projectPath \"{slot}\\Unity\\Game\" -quit -logFile \"{log}\"" },
      { "id": "verify", "group": 3, "cwd": "{slot}", "run": "cmd /c \"Build\\project-build.bat code build GameServer\"", "okExit": [0] }
    ],
    "refresh": ["sync", "import"],
    "editor": "\"{unityExe}\" -projectPath \"{slot}\\Unity\\Game\""
  }
}
```

예시에 나오는 `VcsWrapper`·`QualityGate`·`ProjectOps`·`project-build.bat`는 가상의 프로젝트 도구 이름이다. 실제 프로젝트의 명령으로 바꿔 쓴다.

PC마다 다른 값은 같은 폴더의 `local.json`에 둔다. 깊은 병합으로 `adapter.json` 위에 얹히고 VCS에 올리지 않는다.
레지스트리에는 사람마다 고칠 자리를 보이는 `local.example.json`을 두고, 복사해서 `local.json`으로 쓴다.

```json
{
  "slots": {
    "root": "D:/Work/slots",
    "client": "ws-yourname_{name}",
    "mainClient": "ws-yourname_Main"
  },
  "vars": {
    "unityExe": "C:/Program Files/Unity/Hub/Editor/<version>/Editor/Unity.exe",
    "main": "D:/Work/Game_Main"
  }
}
```

| 키 | 뜻 |
| --- | --- |
| `schema` | 계약 버전. 코어가 모르는 값이면 미션을 시작하지 않는다 |
| `project` | 보고서·상태 파일에 쓰는 이름 |
| `paths.vcsRoot` | 프로젝트 폴더 기준 VCS 루트 상대 경로. 프로젝트 폴더 = VCS 루트라고 가정하지 않는다 |
| `paths.stateDir` | 프로젝트 폴더 기준 미션 상태 폴더. VCS에 올리지 않는 곳이어야 한다 |
| `stages[]` | 미션 단계 순서. `kind`는 `research` / `design` / `implement` / `review`. `design`은 `skill`과 `optional`을 가진다. 없으면 `research → implement → review` |
| `guard.deny[]` | 미션 진행 중 막을 명령의 정규식(대소문자 무시)과 이유. 명령 조각마다 검사한다(아래 "가드") |
| `guard.deny[].except` | 선택 문자열 배열. 각 정규식이 같은 조각 전체에 맞을 때 그 deny 규칙만 면제한다. 다른 조각·규칙은 계속 검사한다 |
| `guard.allow[]` | 선택 문자열 정규식 배열. 명령 조각 **전체**가 맞으면(`fullmatch`, 대소문자 무시) 그 조각은 `deny`·`p4Deny`를 보지 않는다. 여러 규칙이 같이 쓰는 검증된 예외(예: `--check-only`)를 한 곳에 둔다. `p4Client` 검사는 그대로 받는다 |
| `guard.p4Deny[]` | 선택. p4 하위 명령 이름으로 막는 규칙 `{"commands": [...], "when": "<정규식>", "unless": "<정규식>", "reason"}`. 실행 파일 경로·따옴표·`&`·전역 옵션(`-c`·`-u`·`-p` 등)은 훅이 해석하므로 정규식에 다시 쓰지 않는다. `when`·`unless`는 하위 명령 뒤 인자(따옴표 벗겨 공백으로 이음)에 `search` |
| `guard.coordinatorWrite` | 선택 glob 배열(프로젝트 폴더 기준, `**` 여러 단계·`*` 한 단계, 대소문자 무시). 있으면 미션 진행 중 ACTIVE 세션의 메인 코디네이터(`agent_id` 없음)가 Write·Edit·MultiEdit·NotebookEdit로 프로젝트 안 이 목록 밖 파일을 쓰면 막는다. 서브에이전트·다른 세션·프로젝트 밖 경로(메모리 등)는 보지 않는다. 예: `["_My/missions/**"]`. Claude 훅만 적용(Codex 훅은 셸 명령만 받음) |
| `guard.p4Client` | 선택. `{"require": true}`면 미션 중 p4 쓰기 명령에 전역 `-c <작업 공간>` 필수, 이 폴더 작업 공간(슬롯 `slots.client` 틀·메인 `slots.mainClient`·`.p4config`)과 달라도 막음. `commands`로 대상 하위 명령 목록을 바꿀 수 있다. 래퍼 `p4c.py`가 자동으로 붙인다 |
| `mission.staleHours` | 선택. ACTIVE 끊김 의심 기준 시간(기본 24, 0이면 끔). 환경변수 `HAN_STALE_HOURS`가 우선. 표시만 하고 자동으로 닫지 않는다(`close-stale --force-close-stale`) |
| `roles.*` | 역할별 실제 에이전트·스킬 이름. 없는 역할은 `general-purpose` |
| `roles.implementSmall` | 승인된 문서·정해진 소규모 수정 담당. 없으면 implement. 구현과 독립 리뷰 분리는 유지 |
| `roles.verify` | 테스트·Unity Play 실행 전담. 없으면 `han:verifier`. Sonnet 5.5 / high 고정, 코드 수정·모델 전환·완료 판정 금지 |
| `roles.audit` | 미션 끝 기록 감사(SKILL §6의 4)를 맡을 읽기 전용 에이전트. 없으면 `roles.review`. 구현 역할과 다른 모델이면 더 좋다 |
| `verify.backend` | `mcp` / `cli` / `dotnet` — 엔진 검증 수단. 명령 자체는 `adapter.md` |
| `verify.grades` | 이 프로젝트에서 낼 수 있는 검증 등급 |
| `report.push` | 마일스톤마다 푸시 알림을 보낼지 |
| `slots.autocompact` | 슬롯 세션 압축 예산(100000~1000000 토큰, 기본 Claude 500000, Codex 200000). Claude와 Codex 실행 옵션에 적용 |
| `slots.*` | 에이전트 슬롯 워크스페이스(아래 "슬롯"). 없거나 `enabled: false`면 슬롯을 안 쓴다 |
| `vars` | 준비 단계·에디터 명령 템플릿에서 `{키}`로 쓰는 PC별 값(아래 "슬롯"의 템플릿 변수). `local.json`에 둔다 |
| `local.json` | 같은 폴더의 PC별 덮어쓰기. 깊은 병합(객체는 키별, 배열·스칼라는 통째로 덮음). git에 올리지 않는다 |

`slots.root`·`slots.client`·`slots.mainClient`·`vars`처럼 PC·사람마다 다른 값은 `adapter.json`이 아니라 `local.json`에 둔다. 저장소 안 어댑터에는 `slots`를 두지 않는다.

## 가드

가드 훅은 Bash·PowerShell 명령마다 돈다(`guard.coordinatorWrite`가 있으면 Claude의 파일 쓰기 도구에도). 어댑터를 못 찾으면 미션 마커 명령에만 `가드 꺼짐` 알림을 주고
(위 "어댑터 찾기"), 다른 명령에는 아무것도 하지 않는다.

- **조각 검사**: 줄 이음(Bash `\`, PowerShell `` ` `` + 줄바꿈)을 합치고 줄바꿈·`;`·`&&`·`||`·`|`·`&`·`{`·`}`로
  나눈다. `cmd /c`·`xargs`·`env` 같은 래퍼를 벗기고, `$(...)`·백틱 안쪽도 조각으로 본다. 같은 명령 안의
  단순 변수 대입(`NAME=값`, `$NAME = 값`)은 뒤 조각에서 치환한다. 규칙은 `^`로 조각 첫머리에 고정해
  "실행 파일 바로 뒤 동사"를 보게 쓴다. 파일 경로 속 단어(`Foo.Sync.cs`)를 동사로 오인하지 않게 한다.
- **규칙 안 예외**: `except`는 문자열 정규식 배열이며 대소문자를 무시한 `fullmatch`로 같은 조각 전체를 검사한다.
  전역 허용이 아니므로 조회 뒤의 `; p4 submit`과 하위 실행 조각, 다른 deny 규칙은 계속 검사한다.
  배열 타입·항목 타입·정규식 중 하나라도 잘못되면 진단을 내고 그 규칙의 예외 전체를 적용하지 않는다.
  예: `{"pattern": "submit", "except": ["rg -n submit Tool"], "reason": "submit 실행 금지"}`는 그 조회 조각만 면제한다.
  정규식은 실행 파일·옵션·인수를 좁게 열거한다. `.*--check-only.*`나 `rg.*`처럼 실행·표현식을 삼킬 수 있는 예외는 두지 않는다.
  예제 어댑터는 제한된 옵션의 `rg` 리터럴 조회(경로는 `/`), 리터럴 `Write-Output`,
  `Tool/ProjectOps/python-runner.bat Tool/ProjectOps/cli.py submit <양수번호CL> --check-only`만 면제한다.
  프로젝트에 적용할 때는 해당 도구의 `--check-only`가 shelve·잠금 변경·CI·submit 전에 반환하는지 먼저 확인한다.
  임의 실행 파일의 `--check-only`와 `--dry-run`은 면제하지 않는다.
- **전역 허용·p4 규칙**: `guard.allow`는 여러 deny 규칙에 같은 예외를 반복하지 않게 하는 조각 단위 허용 목록이다.
  `except`처럼 조각 전체 일치만 보며, 같은 명령의 다른 조각은 계속 검사한다. p4 쓰기 규칙은 긴 접두 정규식 대신
  `guard.p4Deny`로 하위 명령 이름을 적는다. 예: `{"commands": ["shelve"], "when": "(?:^|\\s)-d\\b", "reason": "shelve 삭제 금지"}`.
  어댑터를 바꾸면 `check_guard.py <adapter.json>`로 `guard-cases.json`이 `mismatches=0`인지 확인한다.
- **진행 표시**: `<stateDir>/ACTIVE`는 JSON `{"id", "session", "openedAt"}`. 코디네이터가 마커 명령으로만 바꾼다.
  마커는 그 호출의 유일한 명령일 때만 동작한다.
  - `echo han-mission open <id>` — 없으면 만들고 이 세션에 건다. 다른 미션이 있으면 거부
  - `echo han-mission resume <id>` — 같은 미션을 이 세션에 다시 건다(압축·재시작 뒤)
  - `echo han-mission close <id>` — 지우고 `allow-once`도 지운다. 연 세션에서만(손상된 ACTIVE는 예외)
- **세션 범위**: ACTIVE의 `session`과 같은 세션만 막는다. 서브에이전트는 부모 세션의 `session_id`로 훅에 들어오므로 같이 막힌다(2026-10-15 실측). 다른 세션은 경고만 받는다.
- **1회 허용**: `<stateDir>/<id>/allow-once`에 명령 문자열이 한 줄로 있으면 그 명령은 한 번 통과하고 줄이 지워진다.
  사용자가 그 명령을 그 자리에서 승인했을 때만 쓴다. 읽다가 오류가 나면 막는다.
- **세션 기록**: open·resume 마커 때 `<stateDir>/<id>/sessions.tsv`에 `at action session transcript` 한 줄을 덧붙인다.
  transcript 는 훅 payload 의 `transcript_path`(없으면 빈 값). 미션 끝 기록 감사가 상태 CLI `transcripts`로 읽는다.
- **알림**: 가드가 꺼졌거나 경고가 있으면 사용자(`systemMessage`)와 모델(`additionalContext`) 모두에게 알린다.
- 가드는 걸림줄이다. 문자열을 조립하는 식의 의도적 회피까지 막지는 못한다. 실제 통제는 스킬의 멈춤 규칙이다.
- 훅은 PATH의 `python`으로 돈다. `check_guard.py --doctor`로 이 PC의 python이 진짜 실행되는지 확인한다.

## 슬롯

미션을 동시에 여러 개 돌리려면 워크스페이스를 나눈다. 슬롯 하나는 같은 스트림을 받는 별도 워크스페이스이고,
그 폴더에서 띄운 Claude 세션 하나가 미션 하나를 맡는다. 세션마다 MCP 연결·에디터·`ACTIVE`가 따로라
서로 막지 않는다. 만들고 갱신하는 절차는 `/han:slots`, 프로젝트별 준비 단계는 `adapter.md`의 "슬롯 준비".

| 키 | 뜻 |
| --- | --- |
| `slots.enabled` | 슬롯 사용 여부 |
| `slots.root` | 슬롯들을 두는 상위 폴더(`adapter-init`이 묻는다). PC마다 달라 `local.json`에 둔다 |
| `slots.names` | 슬롯 이름 목록(`[A-Za-z0-9][A-Za-z0-9_-]*`). 폴더는 `<root>/<name>`. 스크립트는 이 목록에 있는 이름만 받는다. 새 어댑터는 2~3개로 시작하고 동시 미션이 늘 때 이름을 더해 `create <이름>`으로 그때 만든다. 있는 슬롯을 줄이려고 지우지 않는다 |
| `slots.vcs` | 슬롯 작업 공간을 만들고 기준 리비전을 조회하는 VCS. `perforce` 또는 `none`(기본). `perforce`면 `stream`·`mainClient` 필수. `none`이면 `{baseline}`을 못 쓴다 |
| `slots.client` | 작업 공간 이름 규칙. `{name}`이 슬롯 이름으로 바뀐다. 사람마다 달라 `local.json`에 둔다 |
| `slots.stream` | 슬롯이 받을 스트림·브랜치 |
| `slots.p4` | `p4` 실행 명령(문자열 또는 인자 배열, 예: 경로·접속 옵션 포함). 없으면 PATH의 `p4` |
| `slots.mainClient` | 메인 워크스페이스 이름. 새 슬롯의 옵션을 따라 만들 원본이자 `{baseline}`을 조회하는 대상. `refresh`는 슬롯을 갱신하기 전에 이 워크스페이스를 먼저 head로 sync한다(`create`는 하지 않음). 사람마다 달라 `local.json`에 둔다 |
| `slots.prepare[]` | 슬롯 준비·갱신 단계 목록(아래) |
| `slots.refresh` | `refresh`가 다시 돌릴 `prepare[].id` 목록. `refresh --keep-local`을 쓰려면 `sync` 단계가 있어야 한다(그 단계 직후 `resolve`) |
| `slots.churn` | 엔진·에디터가 스스로 다시 쓰는 추적 파일의 목록(문자열 배열). 스트림 루트(`slots.stream`) 기준 상대 경로이고 p4 와일드카드 `*`(한 단계)·`...`(여러 단계) 허용, 배열이 아니거나 빈 문자열·절대 경로·`..` 구간이 있으면 오류. 로컬 변경 검사에서 빠지고 `refresh` 때 `#have` 내용으로 되돌린다. `slots.vcs`가 `perforce`일 때만 쓴다 |
| `slots.editor` | 슬롯 에디터를 여는 명령 템플릿. `run --editor`는 사용자가 동시 실행을 명시했을 때만 쓰고, 기본 흐름은 필요 시 실행함. 준비 확인·직접 연 프로세스 식별·정상 종료 절차는 `adapter.md`에 적음 |

`slots.prepare[]` 항목은 다음 키를 가진다. `group` 번호 순서로 돌고, 같은 번호의 단계끼리는 병렬이다.
한 그룹이 실패하면 뒤 그룹은 돌지 않는다.

| 키 | 뜻 |
| --- | --- |
| `id` | 단계 이름. `slots.refresh`와 로그·시간 기록이 이 이름을 쓴다 |
| `group` | 실행 순서 번호(기본 0) |
| `run` | 실행할 명령 템플릿 |
| `cwd` | 작업 폴더 템플릿. 없으면 현재 폴더 |
| `exclusive` | `true`면 같은 그룹이어도 다른 `exclusive` 단계와 동시에 돌지 않는다(에디터 같은 공유 자원) |
| `okExit` | 성공으로 칠 종료 코드 목록(기본 `[0]`, robocopy는 `[0, 1, 2, 3]`) |

`run`·`cwd`·`editor`의 템플릿 변수:

| 변수 | 값 |
| --- | --- |
| `{name}` | 슬롯 이름 |
| `{slot}` | 슬롯 폴더(`<root>/<name>`) |
| `{client}` | `slots.client`로 만든 슬롯 작업 공간 이름 |
| `{main}` | 메인 프로젝트 폴더(`vars.main`, 없으면 어댑터를 찾은 폴더) |
| `{log}` | 이 단계의 로그 파일 경로(`prepare` 단계에서만) |
| `{baseline}` | 메인 워크스페이스가 받은 CL 번호(Perforce: `slots.mainClient`의 `changes -m1 //<mainClient>/...#have`). `create`는 메인이 지금 받은 CL이고, `refresh`·`run --refresh`는 메인을 먼저 head로 sync한 뒤 조회하므로 head다. 템플릿에 있을 때만 한 번 조회하고, `slots.vcs`가 없으면 쓸 수 없다 |
| `{memoryDir}` | 슬롯 세션의 Claude 자동 메모리 폴더 |
| `{mainMemoryDir}` | 메인 세션 쪽 Claude 자동 메모리 폴더 |
| `{<키>}` | `vars`의 키(예: `{unityExe}`). 정의 안 된 변수를 쓰면 실행 전에 오류 |

슬롯 목록과 상태는 메인 프로젝트의 `<stateDir>/slots.tsv`에 둔다. 열은 탭으로 구분한다.

| 열 | 뜻 |
| --- | --- |
| `name` | 슬롯 이름(키) |
| `path` | 슬롯 폴더 |
| `client` | 슬롯 작업 공간 이름 |
| `created` | 만든 시각 |
| `synced` | 마지막 동기화 시각 |
| `state` | `creating`(만드는 중) / `ready` / `failed:<단계 id>`(만들다 실패) / `failed:refresh:<단계 id>`(갱신 실패. `churn`은 churn 되돌림 실패, `resolve`는 `--keep-local` 뒤 남은 충돌). 만들다 실패·중단된 슬롯은 `create --resume`, 갱신 실패는 `refresh`로 복구한다. `run`·`refresh`는 그 밖의 상태면 거부한다. 같은 슬롯 동시 조작은 `<stateDir>/slots/<이름>.lock`으로 막는다. `--discard-local` 플래그를 포함한 동작은 `/han:slots`가 정본이다 |

슬롯 폴더도 레지스트리 `roots`에 넣어야(예: `<root>/*`) 슬롯에서 띄운 세션이 어댑터를 찾는다.

## adapter.md 에 들어갈 절

- VCS: 작업 단위(CL·브랜치) 만들기, 파일 열기·추가, shelve, 설명 인코딩 함정, 남이 연 파일 처리
- submit 정책: 금지 / 사전 위임 시 게이트 명령 / 예외
- 검증: compile·test·console·server-test 명령, 공유 자원(에디터) 규칙, 로컬에서 안 돌리는 검증
- 에디터 수명: 필요 시 실행 명령·프로젝트 연결과 준비 확인·기존 인스턴스 재사용·직접 연 인스턴스 식별과 정상 종료.
  결과 수집 뒤나 실패·질문 대기로 빠질 때 정리하고, 저장 확인·진행 중 작업은 강제 종료하지 않음.
- 사람 확인 항목: 기계로 판정 못 하는 변경 종류(예: 프리팹, 테이블 값, UI 손맛)
- 독점 락 파일 종류
- 하네스 문서 위치(규칙·라우터·워크플로)
- 슬롯 준비(슬롯을 쓰면): 새 워크스페이스가 빌드·검증 가능해지기까지의 단계와 실측 시간
