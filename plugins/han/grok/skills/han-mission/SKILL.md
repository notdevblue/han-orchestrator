---
name: han-mission
description: "Grok CLI에서 미션을 열거나 이어서 하거나 닫거나 상태를 볼 때 쓴다. 미션 열어, 미션 이어서, 미션 중단, 미션 상태, han-mission 요청에 사용한다. 한두 파일 수정(S)은 미션을 열지 않는다. 상태 파일은 mission_state.py로만 쓴다."
---

# han 미션

Grok 훅은 Claude와 같은 미션 가드를 적용한다. `open`·`resume`·`close` 마커는 단독 명령으로 실행하고, `open`·`resume` 뒤에는 `ACTIVE`의 미션 id와 현재 세션 id가 일치하는지 확인한다.
`init`은 그 가드가 `ACTIVE`를 기록한 뒤에만 실행한다. 가드 안내나 일치 확인이 없으면 멈춘다. `ACTIVE`, `mission.md`, `*.tsv`의 나머지 변경은 아래 스크립트로만 한다.
파일을 쓰기 전에 이번 변경의 범위와 경로를 말하고 확인을 받는다. 사용자가 그 범위와 경로를 이미 승인했으면 그 승인으로 진행한다.

`<repo>`는 `HAN_REPO`이거나 저장소 체크아웃이다. `--runtime grok`으로 어댑터를 찾는다. 순서는 `.grok` → `.claude` → `.codex` → `HAN_ADAPTERS_DIR` → `HAN_REPO/adapters`다.

```text
python "<repo>/plugins/han/skills/mission/scripts/mission_state.py" --runtime grok --project "<작업 루트>" <명령>
```

다른 미션은 `--mission <id>`를 붙인다. 종료 코드 0이 성공이고 1은 거부다. 출력에 없는 id·단계·목표는 만들지 않는다.
양식·질문 기록·사람용 문서는 `<repo>/plugins/han/skills/mission/references/`를 읽는다. 템플릿을 이 스킬에 복사하지 않는다.

## 티어

S(한두 파일·단일 수정)는 미션을 열지 않는다. 사용자가 그래도 미션을 원할 때만 `init --tier S --force-tier`다.
M·L만 연다. `--tier`가 없으면 스크립트가 경고한다. `init --tier S`는 `--force-tier` 없이 거부된다.

## 열기·이어하기·닫기·상태

1. 먼저 읽는다. `active`는 ACTIVE가 없어도 된다. 진행 중이면 id·세션·열린 시각·끊김 의심을 보여 주고, 다른 세션이 아직 도는지 확인받기 전에는 이어서 쓰거나 닫지 않는다. 이어서 하기로 승인받으면 `echo han-mission resume <id>`를 단독 실행하고, ACTIVE의 id와 session이 현재 미션·세션인지 확인한다.

```text
python "<repo>/plugins/han/skills/mission/scripts/mission_state.py" --runtime grok --project "<작업 루트>" active
python "<repo>/plugins/han/skills/mission/scripts/mission_state.py" --runtime grok --project "<작업 루트>" status
python "<repo>/plugins/han/skills/mission/scripts/mission_state.py" --runtime grok --project "<작업 루트>" context
```

2. 새 id는 `YYYYMMDD-<짧은-slug>`(영문·숫자·`-`)다. 확인 뒤 가드 마커를 실행하고 ACTIVE를 확인한 다음 연다.

```text
echo han-mission open <id>
```

   가드 안내가 나오고 ACTIVE의 id와 session이 현재 미션·세션인지 확인한다.

```text
python "<repo>/plugins/han/skills/mission/scripts/mission_state.py" --runtime grok --project "<작업 루트>" --mission "<id>" init --tier M --goal "<목표>"
```

   목표 첫 줄은 대상과 결과 한 문장이다. `init`은 ACTIVE가 이미 그 id와 현재 session을 가리킬 때만 미션 폴더를 만든다.
3. 이미 열린 미션의 단계·단위·질문·공유·완료는 같은 스크립트의 `mission set`, `unit`, `ledger record`, `decision add`, `question`, `document`, `verify`, `review`, `complete`로만 갱신한다.
   인자 목록은 `python "<repo>/plugins/han/skills/mission/scripts/mission_state.py" -h`와 각 하위 명령의 `-h`다.
4. 닫기 전에 확인한다. `mission set --state 닫힘`, 중단이면 `--state 중단`으로 상태를 바꾼 뒤 같은 세션에서 `echo han-mission close <id>`를 단독 실행한다. 가드가 현재 세션의 ACTIVE만 지운다. 파일을 손으로 지우지 않는다.
   끊김 의심일 때만 `close-stale`로 미리본다. 사용자가 그 id를 닫자고 한 뒤에 `--force-close-stale`을 붙인다. 살아 있으면 스크립트가 거부한다.

질문 정본과 생성 문서는 `question`만 쓴다. 보고·추가 자료 공유는 `document share`, `document hide`, `document list`다.
`guard.p4Client.require`가 켜져 있으면 p4 쓰기는 `<repo>/plugins/han/skills/mission/scripts/p4c.py`로 한다.

구현 절차는 `han-run`이다. 상태만 보면 `han-status`다.
