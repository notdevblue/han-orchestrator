---
name: han-run
description: "Grok Bot과 Grok CLI가 프로젝트 미션을 구현하거나 이어서 할 때 쓴다. 상태만 읽는 조회에는 쓰지 않는다. 프로젝트 규칙과 전역 실행 규칙을 이 스킬에 복제하지 않는다."
---

# han 실행

Bot과 CLI가 같은 절차를 쓴다. 상태만 보면 `han-status`, 어댑터가 안 맞으면 `han-doctor`다.
다른 봇을 깨우는 인계는 `han-mission-brief`다.

메인 작업 폴더 `<project>` 또는 슬롯 작업 폴더 `<slot-path>`에서 `grok`를 실행 중이면 이 세션이 구현자다. SendToAgent로 넘기지 않는다.

프로젝트 규칙은 그 저장소의 `AGENTS.md`·`CLAUDE.md`와 `.claude/rules/`에 있다.
전역 실행 규칙은 `~/.grok/rules/`에 두고, CLI가 읽은 규칙은 `grok inspect`에서 확인한다. 프로젝트·전역 규칙을 여기에 옮기지 않는다.
Claude·Codex는 선택이다. 없어도 아래만으로 진행한다.

## 순서

1. 작업 루트가 메인 프로젝트인지 등록된 슬롯인지 경로로 확인한다. 하위 폴더면 그 루트까지 올라간다. 어느 쪽도 아니면 사용자에게 묻고, 경로를 만들지 않는다.
2. 미션이 있으면 상태를 읽는다. 출력에 없는 미션 id·단계·목표는 만들지 않는다.

```text
python "<repo>/plugins/han/grok/scripts/han_state.py" snapshot --project "<작업 루트>"
python "<repo>/plugins/han/skills/mission/scripts/mission_state.py" --runtime grok --project "<작업 루트>" active
```

`<repo>`는 `HAN_REPO`이거나 저장소 체크아웃이다. `active` 대신 해당 미션의 `status`를 읽어도 된다.

3. 프로젝트 코드를 고치기 전에 그 프로젝트의 `AGENTS.md`·`CLAUDE.md`와 `.claude/rules/`를 읽는다. 그 문서가 가리키는 도메인 문서도 읽는다.
4. 미션 상태 쓰기는 `mission_state.py`와 `slots.py`만 쓴다. `ACTIVE`, `mission.md`, `*.tsv`는 손으로 고치지 않는다.
5. 파일을 쓰기 전에 이번 변경의 범위와 경로를 말하고 확인을 받는다. 사용자가 그 범위와 경로를 이미 승인했으면 그 승인으로 진행한다.
6. 사용자가 그 명령을 명시하기 전에는 Perforce submit과 가드가 막는 명령을 실행하지 않는다.
7. 프로젝트 규칙이 빠른 모델(fast)을 금지하면 그 모델을 고르지 않는다.
