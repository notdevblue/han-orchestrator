---
name: han-doctor
description: "Grok Bot이 han 어댑터를 못 찾거나 조회 환경이 안 맞을 때 쓴다. 발견 순서와 HAN_ADAPTERS_DIR, HAN_REPO를 확인하고 읽기 전용으로 끝낸다. 실행은 이 진단 다음의 별도 단계다."
---

# han 조회 진단

`han-status`의 `snapshot`이 종료 코드 2이거나, 어느 어댑터를 읽는지 불확실할 때 이 스킬로 원인을 가른다.
파일을 만들거나 고치지 않는다. Claude·Codex 플러그인 갱신으로 이 문제를 풀지 않는다.
이 스킬은 봇을 깨우지 않는다. 실행이 필요하면 진단 뒤에 코디네이터가 `han-mission-brief`로 메인 또는 슬롯의 실행 봇을 깨운다.

## 순서

1. `<repo>/plugins/han/grok/scripts/han_state.py`가 있는지 본다. 없으면 저장소 체크아웃 경로가 잘못된 것이다.
2. 상태 조회와 같은 명령으로 다시 읽는다.

```text
python "<repo>/plugins/han/grok/scripts/han_state.py" snapshot --project "<프로젝트 폴더>"
```

3. stderr의 `오류:` 한 줄을 그대로 알리고, 아래 중 어디에 걸렸는지 말한다.
   - 가까운 폴더에 `.grok/orchestrator/adapter.json`, `.claude/orchestrator/adapter.json`, `.codex/orchestrator/adapter.json`이 없음
   - `HAN_ADAPTERS_DIR`가 비었거나 그 폴더에 `projects.json`이 없거나 `roots`가 프로젝트와 안 맞음
   - `HAN_REPO`를 지정했는데 `<HAN_REPO>/adapters`가 없음
   - 저장소에서 직접 실행하는데 `<repo>/adapters`가 없음
4. 계약은 `plugins/han/skills/mission/references/adapter-schema.md`의 "Grok에서 상태 읽기"다.
   Claude 플러그인 설정과 Codex `config.toml`은 이 조회가 읽지 않는다. SessionStart 주입도 없다.

진단은 여기서 끝난다. 어댑터 초안을 쓰지 않는다. 이 스킬은 미션을 열지 않는다.

레지스트리·guard-cases·끊김 의심·버전을 더 보려면 공유 doctor를 읽기 전용으로 돌린다. 파일을 고치거나 미션을 닫지 않는다.
Grok 어댑터를 못 찾는 문제의 정본은 위 snapshot이다.

```text
python "<repo>/plugins/han/skills/doctor/scripts/doctor.py" --runtime grok
```

`active` WARN은 닫지 않는다. 미리보기를 보여 주고, 닫기는 사용자가 그 미션을 승인한 뒤 `han-mission`의 `close-stale`로 한다.
