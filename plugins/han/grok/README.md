# Grok Bot에서 han 보기

Grok 코디네이터는 바깥에서 상태를 보고, 메인 또는 슬롯의 실행 봇에 작업을 넘긴다. 실행은 Grok만으로 된다.
로컬 상태·슬롯 조회는 han 스크립트로 하고, 저장소 코드 작업은 프로젝트의 실행 규칙을 따른다.
Claude와 Codex는 선택인 안쪽 세션이다. 없어도 미션을 진행할 수 있다.

조회 스킬은 읽기만 한다. 봇을 깨우는 인계는 `han-mission-brief`다. 구현 절차는 `han-run`이고, Bot과 CLI가 같이 쓴다.
Main 또는 slotN에서 `grok`를 실행하면 그 CLI가 구현자다. SendToAgent로 넘기지 않는다.

프로젝트 규칙은 작업 폴더의 `AGENTS.md`·`CLAUDE.md`와 `.claude/rules/`다. Grok CLI는 이를 이미 읽는다.
전역 실행 규칙은 `~/.grok/rules/`에 두며, CLI가 읽은 규칙은 `grok inspect`에서 확인한다. `han-run`은 그 규칙을 복제하지 않는다.

## 실행 역할

| 자리 | 하는 일 | 경로 |
| --- | --- | --- |
| 바깥 코디네이터 | 상태·브리프·미션 대기열을 보고 실행 봇에 인계 | — |
| 메인, 슬롯이 아닌 작업 | 메인 작업 폴더에서 구현 | `<project>` |
| 선택한 슬롯 | 슬롯 작업 폴더에서 구현 | `<slot-path>` |

슬롯 봇은 미션이 붙기 전까지 대기한다. 새 미션은 비어 있는 ready 슬롯에만 붙인다.
메인과 슬롯이 아닌 작업은 메인 작업 폴더의 실행 봇에게 보낸다.

## 머지 뒤에 등록하는 스킬

마켓플레이스 설치는 없다. `claude plugin`과 `codex plugin`으로 Grok 스킬을 넣지 않는다.
등록은 아래 네 폴더뿐이다. `han-status`·`han-doctor`·`han-mission-brief`의 역할은 그대로다. 실행 절차만 `han-run`이 맡는다.

1. 이 저장소를 Grok 셸이 닿는 PC의 `<repo>`에 받는다.
2. 그 셸에 레지스트리를 알린다. `HAN_ADAPTERS_DIR=<저장소>/adapters` 또는 `HAN_REPO=<저장소>`.
   프로젝트의 `.grok/orchestrator/adapter.json`이 있으면 그게 먼저다. 없으면 같은 자리의 `.claude`, 그다음 `.codex` 어댑터를 읽는다.
3. Grok CLI는 `~/.grok/config.toml`의 `[skills].paths`에 등록 폴더 네 개만 적는다.
   부모 `plugins/han/grok/skills`를 넣지 않는다. `han-watch`와 `han-handoff`가 CLI 발견 목록에 들어가면 안 된다.
   같은 네 폴더를 `~/.grok/skills`에 링크해도 된다.

```toml
[skills]
paths = [
  "<repo>/plugins/han/grok/skills/han-status",
  "<repo>/plugins/han/grok/skills/han-doctor",
  "<repo>/plugins/han/grok/skills/han-mission-brief",
  "<repo>/plugins/han/grok/skills/han-run",
]
```

4. Grok Bot 스킬 라이브러리에도 그 네 폴더만 등록하거나, 저장소 파일을 직접 읽게 한다.
   - `plugins/han/grok/skills/han-status/SKILL.md`
   - `plugins/han/grok/skills/han-doctor/SKILL.md`
   - `plugins/han/grok/skills/han-mission-brief/SKILL.md`
   - `plugins/han/grok/skills/han-run/SKILL.md`
5. `plugins/han/grok/skills/han-watch/`와 `plugins/han/grok/skills/han-handoff/`는 위 넷을 가리키는 저장소 안내이다. 등록하지 않는다.
   `han-handoff`는 메인·슬롯의 실행 봇을 깨우는 흐름을 적는다.
6. 스크립트는 복사하지 않는다. 스킬이 `<저장소>/plugins/han/grok/scripts/han_state.py`를 절대 경로로 부른다.

## 직접 확인

```text
python plugins/han/grok/scripts/han_state.py snapshot --project <프로젝트>
python plugins/han/grok/scripts/han_state.py brief --project <프로젝트> --runtime grok --goal "한 줄 목표"
python plugins/han/grok/scripts/han_state.py brief --project <프로젝트> --runtime grok --slot slot1 --goal "한 줄 목표"
python plugins/han/grok/scripts/han_state.py brief --project <프로젝트> --runtime claude --goal "한 줄 목표"
python -m pytest plugins/han/grok/test_han_state.py -q -p no:cacheprovider
```

`--runtime`을 비우면 `grok`다. `snapshot`은 파일을 만들지 않는다.
`brief --runtime grok`는 슬래시 명령 없이 SendToAgent 블록을 출력한다. 블록에는 실행 대상, 목표, 프로젝트 경로, 제약, 쓰기 확인, 코드 작업의 실행 규칙이 있다.
`claude`와 `codex`는 사람이 그 세션에 붙일 문장만 출력한다. Codex는 줄여 둔 선택 경로다.

어댑터 순서는 `plugins/han/skills/mission/references/adapter-schema.md`의 "Grok에서 상태 읽기"와 같다.
