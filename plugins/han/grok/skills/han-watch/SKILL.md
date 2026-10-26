---
name: han-watch
description: "저장소 안내. Grok 스킬로 등록하지 않는다. han 상태 조회는 han-status, 어댑터 진단은 han-doctor를 쓴다."
---

# han-watch

이 폴더는 Grok에 등록하는 스킬이 아니다. 예전에 상태를 보던 이름이다.

- 상태 조회는 `plugins/han/grok/skills/han-status/SKILL.md`
- 어댑터를 못 찾으면 `plugins/han/grok/skills/han-doctor/SKILL.md`
- 메인 또는 슬롯의 실행 봇을 깨우는 인계는 `plugins/han/grok/skills/han-mission-brief/SKILL.md`
- 구현 절차는 `plugins/han/grok/skills/han-run/SKILL.md`
- 미션 상태 쓰기는 `plugins/han/grok/skills/han-mission/SKILL.md`
- 슬롯 조작은 `plugins/han/grok/skills/han-slots/SKILL.md`
- 어댑터 초안은 `plugins/han/grok/skills/han-adapter-init/SKILL.md`
- 피드백 정리는 `plugins/han/grok/skills/han-distill/SKILL.md`

Grok에 등록하는 스킬은 `han-status`, `han-doctor`, `han-mission-brief`, `han-run`, `han-mission`, `han-slots`, `han-adapter-init`, `han-distill`이다. 이 폴더는 등록하지 않는다.
