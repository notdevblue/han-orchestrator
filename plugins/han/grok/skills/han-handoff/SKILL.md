---
name: han-handoff
description: "저장소 안내. Grok 스킬로 등록하지 않는다. 브리프는 han-mission-brief를 쓰고, 기본 인계는 메인 또는 슬롯의 실행 봇을 깨우는 SendToAgent 블록이다."
---

# han-handoff

이 폴더는 Grok에 등록하는 스킬이 아니다. 예전에 인계를 적던 이름이다.
등록하지 않는다. 등록 스킬은 `han-status`, `han-doctor`, `han-mission-brief`, `han-run` 네 개다.

브리프와 절차는 `plugins/han/grok/skills/han-mission-brief/SKILL.md`를 따른다.

기본 인계는 Grok 단독이다. 코디네이터가 실행 봇을 깨운다.

- 메인과 슬롯이 아닌 작업은 `<project>`의 실행 봇에 보낸다
- 슬롯 작업은 등록된 `<slot-path>`의 실행 봇에 보낸다. 슬롯 봇은 미션이 붙기 전까지 대기한다
- 보낼 블록은 `SendToAgent:`다. 목표, 프로젝트 경로, 제약, 쓰기 확인을 넣는다
- 저장소 코드는 프로젝트의 실행 규칙을 따른다. 로컬 상태·슬롯 조회는 실행 봇이 han 스크립트로 한다
- 코디네이터는 그 블록으로 봇을 깨우고, 이 단계에서 프로젝트 코드를 고치지 않는다

Claude·Codex 붙여 넣기는 사용자가 `--runtime claude` 또는 `--runtime codex`를 고른 때의 선택 경로다.
그 문장은 사람이 그 세션에 붙인다. 기본 인계를 슬래시 명령으로 바꾸지 않는다.
