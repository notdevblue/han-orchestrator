---
name: han-distill
description: Codex 세션이나 미션의 대화 기록에서 사용자 피드백을 뽑아 가이드·메모리·개인 규칙·플러그인 반영 후보로 분류하고, 사용자가 고른 것만 반영한다. "피드백 정리해줘", "지침에 반영할 거 정리해줘", "이번 세션 피드백 정리", "$han-codex:han-distill" 요청과 han-mission의 확인 C 직전에 사용한다.
---

# 피드백 정리

사용자가 작업 중에 준 지적·선호·교정을 다음 작업에서도 쓰이게 옮긴다. 입력은 대화 기록 파일이다.
에이전트가 "피드백을 받으면 적어 둔다"에 기대지 않는다. 압축 전 메시지도 원본 `.jsonl` 에서 읽는다.

반영은 사용자가 고른 것만 한다. 목록 승인은 반영 승인일 뿐 submit·커밋 승인이 아니다.

## 1. 뽑기

`<plugin-root>`는 이 SKILL.md에서 세 단계 위인 플러그인 폴더다. 실제 절대 경로로 바꿔 호출한다.

```
python "<plugin-root>/codex/skills/han-distill/scripts/distill.py" extract --transcript "<대화 기록 경로>" --out "<임시 폴더>/distill.md"
```

- 일반 세션은 SessionStart 훅이 컨텍스트에 넣은 `han-distill 대화 기록 경로`를 `--transcript`로 전달한다.
  Codex 훅은 부모 셸 환경을 바꾸지 않고 `CLAUDE_ENV_FILE`도 쓰지 않는다.
- 훅의 경로가 없으면 현재 세션의 `CODEX_THREAD_ID` 또는 `CODEX_SESSION_ID`와 정확히 일치하는
  `<CODEX_HOME>/sessions/**/rollout-*-<id>.jsonl`을 찾고 첫 `session_meta.payload.id`를 대조한다.
  `CODEX_HOME`이 없으면 `~/.codex`다. 경로를 확정할 수 없으면 사용자에게 묻는다.
  최신 파일이나 작업 폴더만으로 고르지 않는다(동시 세션의 기록을 집을 수 있음).
- 미션은 `mission_state.py transcripts`가 준 경로마다 `--transcript`를 붙인다. 누락된 기록은 알린다.
- Codex의 `response_item` 대화 메시지를 읽고 중복 이벤트·도구 결과·추론·주입된 지침을 제외한다.
  Claude 기록도 읽으므로 런타임을 바꿔 이어받은 미션은 경로 전부를 전달한다.
- 이미 정리한 메시지는 로그의 워터마크로 건너뛴다. 처음부터 다시 보려면 `--all`.
  출력의 세션 id와 `until`을 기록할 때 그대로 쓴다.
- 출력 파일은 UTF-8로 읽는다. stdout은 메시지 수와 처리 지점만 보여 준다.

## 2. 고르기

메시지마다 `앞 답`을 같이 읽고, 다음 작업에서도 행동을 바꿀 말인지 본다.

- 남길 것: 접근·설계 교정("이건 서버가 해야지"), 코드 스타일 지적, 작업 방식 교정("묻지 말고 해", "리뷰 결과 먼저
  보여 줘"), 반복된 불만.
- 버릴 것: 이번 작업만의 지시·선택, 승인·진행 신호, 단순 질문, 이미 이번 작업에서 반영돼 끝난 일회성 수정.

대부분 버린다. 남길 게 없으면 "남길 것 없음"이라고 말하고 `record --verdict 확인`으로 워터마크만 올린다.

## 3. 반영할 곳 정하기

| dest | 대상 | 고르는 기준 |
| --- | --- | --- |
| `guide` | 프로젝트 가이드·규칙 문서 | 팀 누구에게나 적용될 코드·네이밍·설계 규칙. 프로젝트 문서 규칙대로 쓴다 |
| `memory` | 개인 메모리 | 이 사용자의 작업 습관·선호, 프로젝트 문서에 둘 성격이 아닌 사실 |
| `rules` | han 개인 규칙 | 프로젝트를 가리지 않는 사용자의 작업 원칙 |
| `plugin` | han 플러그인(CLI·가드·스킬) | 미션·슬롯 절차 자체의 실수 |

코드 규칙은 메모리가 아니라 `guide`가 기본이다. 프로젝트에 가이드 문서가 없으면 `memory`로 낮추고 이유를 적는다.

각 후보마다 막는 수단을 위에서부터 따진다: 구조(실수가 나올 길 없애기) → 타입·스크립트·가드 → 테스트 → 문서·규칙.
문서 한 줄은 기계로 못 막는 것만이다. 고른 단계와 더 높은 단계가 안 되는 이유를 후보에 적는다.

## 4. 겹침 확인

제안하기 전에 대상마다 검색한다.

- `guide`: AGENTS.md와 프로젝트 규칙·가이드 폴더
- `memory`: 현재 환경이 제공한 메모리 색인과 관련 파일
- `rules`·`plugin`: han 원본 저장소(아래 "han 원본 위치")
- 지난 판정: 로그 TSV(`HAN_DISTILL_LOG`, 기본 `<CODEX_HOME>/han/distill/log.tsv`)의 `기각` 줄.
  같은 내용이면 다시 올리지 않는다.

이미 같은 규칙이 있는데 이번에 또 어겼으면 문장을 더 쓰지 않는다. "있는데 안 지켜짐"으로 표시하고 가드·테스트
후보로 올린다.

## 5. 보여 주기

번호 목록으로 보여 준다. 일반 세션은 채팅에, 미션은 `confirm-C.md`의 `## 피드백 반영 후보` 절에 싣는다.

항목마다:
- 원문 인용(짧게)과 그 말이 나온 맥락 한 줄
- dest와 대상 파일
- 바꿀 문장 초안 또는 바꿀 코드 요지
- 고른 단계(구조·가드·테스트·문서)와 이유
- 겹침 확인 결과(새 항목 / 있는데 안 지켜짐)

`rules` 후보가 있으면 Claude 사본도 같이 고칠지 따로 묻는다.

## 6. 반영과 기록

- 사용자가 고른 번호만 반영한다. 나머지는 기각이다.
- `guide`: 프로젝트의 파일 변경·문서 규칙대로. 버전 관리가 있으면 작업 단위(CL·브랜치)에 담고 제출은 하지 않는다.
  미션 중이면 그 미션의 작업 단위에 같이 담는다.
- `memory`: 현재 환경의 메모리 저장 규칙대로 쓴다. 직접 수정 대신 변경 노트를 요구하면 그 위치에 노트만 남긴다.
- `rules`: 주입 상한이 `CONTEXT_LIMIT`(`hooks/session_rules.py`, 10000자)다. 고친 뒤 8000자를 넘으면 추가 대신
  기존 문장 정리를 먼저 제안한다.
- `rules`·`plugin`을 고쳤으면 버전을 올리고 플러그인을 갱신해야 새 세션에 들어간다고 사용자에게 알린다.
  훅이 바뀌면 `/hooks`에서 다시 신뢰해야 한다. 커밋은 사용자가 요청할 때만.
- 항목마다 로그에 남긴다. `--until`은 extract가 출력한 그 세션의 `until` 값이다.

```
python "<plugin-root>/codex/skills/han-distill/scripts/distill.py" record --session <id> --until <ts> --verdict 반영 --dest guide --summary "<한 줄>"
python "<plugin-root>/codex/skills/han-distill/scripts/distill.py" record --session <id> --until <ts> --verdict 기각 --summary "<한 줄>"
```

남길 것이 없었거나 모두 처리했으면 `--verdict 확인` 한 줄로 워터마크를 올린다.

## han 원본 위치

설치된 `<plugin-root>`는 캐시 사본일 수 있다. `rules`·`plugin`은 원본 저장소를 고친다.
현재 작업 폴더나 마켓플레이스 등록 경로에서 `.agents/plugins/marketplace.json`과
`plugins/han/.codex-plugin/plugin.json`을 확인해 원본을 찾는다. 확정할 수 없으면 사용자에게 원본 경로를 묻는다.

- Codex 개인 규칙: `plugins/han/codex/rules/personal-rules.md`(서브에이전트에도 주입), `plugins/han/codex/rules/main-session.md`(답변·계획·서브에이전트 파견처럼 메인 세션에만 주입)
- Claude 개인 규칙: `plugins/han/rules/` 아래 같은 두 파일(서브에이전트 파견 내용이 Codex 쪽과 다르다)
