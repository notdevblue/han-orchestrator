---
name: han-distill
description: "Grok CLI 세션이나 미션 대화 기록에서 사용자 피드백을 뽑아 반영 후보로 분류할 때 쓴다. 피드백 정리해줘, 지침에 반영할 거 정리해줘, 이번 세션 피드백 정리, han-distill 요청에 사용한다. 기록에 없는 피드백을 만들지 않는다."
---

# han 피드백 정리

반영은 사용자가 고른 항목만 한다. 목록 승인은 반영 승인일 뿐 submit·커밋 승인이 아니다.
회사·고객·비공개 프로젝트의 지침은 공개 플러그인의 규칙·스킬 후보로 올리지 않는다. 원본 대화 기록과 추출 출력은 공개 체크아웃에 복사하지 않는다.

`<repo>`는 `HAN_REPO`이거나 저장소 체크아웃이다.

```text
python "<repo>/plugins/han/skills/distill/scripts/distill.py" extract --transcript "<대화 기록>" --out "<저장소 밖 비공개 경로>/distill.md"
python "<repo>/plugins/han/skills/distill/scripts/distill.py" record --session <id> --until <ts> --verdict 반영 --dest guide --summary "<한 줄>"
python "<repo>/plugins/han/skills/distill/scripts/distill.py" record --session <id> --until <ts> --verdict 기각 --summary "<한 줄>"
```

Grok에는 SessionStart가 없고 Claude의 `HAN_TRANSCRIPT`도 없다. 그 변수를 Grok 기록으로 쓰지 않는다.
자동 경로가 없으면 `--transcript`가 필수다. 최신 파일을 고르거나 경로를 추측하지 않는다. 사용자가 경로를 주기 전에는 extract를 돌리지 않는다.
미션이면 `mission_state.py transcripts`가 출력한 경로마다 `--transcript`를 붙인다. 출력에 없는 경로는 넣지 않는다.
추출 출력에 없는 문장을 피드백으로 만들지 않는다. 남길 것이 없으면 그렇게 말하고 `record --verdict 확인`으로 워터마크만 올린다.

`--until`은 extract가 출력한 그 세션의 until이다. 로그는 `HAN_DISTILL_LOG`다. 비어 있으면 스크립트 기본값(`~/.claude/han/distill/log.tsv`)에 기록된다. Grok 기록을 그 파일에 섞지 않으려면 쓰기 전에 `HAN_DISTILL_LOG`를 저장소 밖 비공개 경로로 둔다.
처음부터 다시 보려면 `--all`이다.

고를 곳: `guide`(프로젝트 규칙), `memory`(이 사용자 선호), `rules`(han 개인 규칙), `plugin`(han 절차). 코드 규칙은 `guide`가 기본이다.
같은 규칙이 이미 있으면 문장을 더하지 않는다. `rules`·`plugin`은 `https://github.com/notdevblue/han-orchestrator` 개발 체크아웃만 고친다. 경로를 확정할 수 없으면 묻는다.
