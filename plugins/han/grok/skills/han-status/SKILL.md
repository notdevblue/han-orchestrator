---
name: han-status
description: "Grok Bot에서 han 미션과 슬롯 상태를 읽을 때 쓴다. ACTIVE, mission.md, slots.tsv를 조회한다. 이 스킬은 읽기 전용이다. 실행은 코디네이터가 메인 또는 슬롯의 실행 봇을 깨우는 다음 단계다."
---

# han 상태

이 대화는 상태를 읽기만 한다. 미션을 열거나 코드를 고치거나 슬롯을 만들지 않는다.
실행은 별도 단계다. 코디네이터가 `han-mission-brief`로 메인 또는 슬롯의 실행 봇을 깨운다.
어댑터를 못 찾으면 `han-doctor`로 간다. 인계 문장이 필요하면 `han-mission-brief`로 간다.

## 경계

- 프로젝트 코드, `adapter.json`, `local.json`, `mission.md`, TSV, `ACTIVE`는 읽기만 한다.
- `mission_state.py` 쓰기 명령과 `slots.py`의 `create`·`refresh`·`run`·`remove`는 부르지 않는다.
- SessionStart 훅은 `new`·`load` 이벤트를 지원한다. 읽기 전용 조회에서는 세션 시작 훅의 상태나 규칙 주입을 미션 진행 근거로 대신하지 않는다.

## 호출

`<repo>`는 `HAN_REPO`이거나 저장소 체크아웃이다. 스크립트는 그 체크아웃의 절대 경로로 부른다.

```text
python "<repo>/plugins/han/grok/scripts/han_state.py" snapshot --project "<프로젝트 폴더>"
```

`--project`가 없으면 현재 작업 폴더에서 찾는다. 종료 코드 0이 성공이고 2는 어댑터를 못 찾거나 사용이 잘못된 것이다.
출력이 곧 근거다. 출력에 없는 미션 상태를 지어내지 않는다.

어댑터는 가까운 폴더의 `.grok/orchestrator/adapter.json`, 이어서 `.claude/orchestrator/adapter.json`, 이어서 `.codex/orchestrator/adapter.json`이다.
없으면 `HAN_ADAPTERS_DIR`, `HAN_REPO/adapters`, 저장소에서 직접 실행할 때 `<repo>/adapters`를 본다.

## 사람에게 알릴 것

- 작업 폴더와 프로젝트 이름
- 메인 `ACTIVE`, 그리고 진행인데 ACTIVE가 아닌 미션
- 슬롯 `state`와 슬롯 ACTIVE
- 비어 있는 ready 슬롯

이미 열린 미션이나 슬롯 ACTIVE가 있으면 그 자리를 새 작업과 겹치지 말라고 말한다.
