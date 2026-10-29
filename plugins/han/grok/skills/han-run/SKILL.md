---
name: han-run
description: "Grok Bot과 Grok CLI가 프로젝트 작업을 실행하거나 이어서 할 때 쓴다. S는 직접 처리하고 M·L은 han-mission 코디네이터로 조사·구현·검증·리뷰를 파견한다. 상태만 읽는 조회에는 쓰지 않는다."
---

# han 실행

Bot과 CLI가 같은 티어별 절차를 쓴다. 상태 조회는 `han-status`, 어댑터 점검은 `han-doctor`다.
다른 작업 루트의 실행 봇을 깨우는 인계만 `han-mission-brief`로 한다.
메인 작업 폴더 `<project>` 또는 슬롯 `<slot-path>`에서 실행 중인 Grok은 그 자리에서 처리하며 SendToAgent로 다시 넘기지 않는다.

프로젝트의 `AGENTS.md`·`CLAUDE.md`와 `.claude/rules/`, 그 문서가 가리키는 workflow·도메인 규약을 읽는다.
전역 실행 규칙은 `~/.grok/rules/`에 두고 실제 로드는 `grok inspect`로 확인한다. 프로젝트·전역 규칙을 이 스킬에 복제하지 않는다.

1. 현재 경로가 메인 프로젝트인지 등록된 슬롯인지 확인한다. 하위 폴더면 그 루트를 찾는다. 어느 쪽도 아니면 사용자에게 묻고 임의 경로를 만들지 않는다.
2. 기존 미션을 먼저 확인한다. `<repo>`는 `HAN_REPO` 또는 실제 체크아웃이다. 출력에 없는 미션 id·단계·목표를 만들지 않는다.

```text
python "<repo>/plugins/han/grok/scripts/han_state.py" snapshot --project "<작업 루트>"
python "<repo>/plugins/han/skills/mission/scripts/mission_state.py" --runtime grok --project "<작업 루트>" active
```

3. 진행 중인 미션이 있거나 M(여러 파일·검증 게이트)·L(여러 계층·PoC·사람 확인)이면 같은 플러그인의 `han-mission/SKILL.md`를 읽고 전체 공통 절차를 적용한다.
   이 세션은 코디네이터이며 구현·검증·독립 리뷰를 native Grok 4.7 역할에 맡긴다. 상태 CLI만 실행하거나 직접 구현으로 끝내지 않는다.
4. S(한두 파일·단일 수정)이고 미션이 없으면 미션 없이 직접 처리한다. 확정된 범위·경로·계약을 따르고 필요한 타깃 검증과 실제 결과를 보고한다.
   명세가 비었거나 설계 판단이 필요하면 먼저 확인한다. 사용자가 S도 미션으로 원하면 `han-mission`의 `--force-tier` 경로를 따른다.
5. 이미 승인된 범위·경로는 그 승인으로 진행한다. 미승인 설계·범위 변경은 확인받고, 기존 승인 범위를 재질문하지 않는다.
   Perforce submit·push·배포·외부 전송은 사용자가 명시한 범위에서만 실행한다. 계획·리뷰 승인을 해당 행동의 승인으로 확대하지 않는다.
6. 미션 기록은 `mission_state.py`와 `slots.py`로만 갱신한다. `ACTIVE`, `mission.md` 머리 필드, 상태 TSV·질문 정본·생성 문서를 손으로 고치지 않는다.
   모델·effort·도구 파견은 `han-mission/references/grok-roles.md`를 따르고, 실제 적용 증거 없이 역할 설정만으로 적용됐다고 보고하지 않는다.
