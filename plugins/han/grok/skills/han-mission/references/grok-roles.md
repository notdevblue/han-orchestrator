# Grok 역할 파견

native manifest는 `<plugin-root>/grok/agents` 폴더를 등록한다. 각 `.md` 파일의 YAML frontmatter가 역할·모델·effort를 정의한다.
`grok inspect --json`의 실제 `agents` 목록에서 아래 다섯 이름과 정의 파일 경로를 확인한다.
`plugins[].provides.agents`는 등록 폴더 수여서 이 플러그인에서는 1이다. 그것을 실제 역할 수로 읽지 않는다.

| 미션 역할 | native subagent_type | 정의 파일 | effort |
| --- | --- | --- | --- |
| research | `han:researcher` | `grok/agents/researcher.md` | medium |
| implementSmall | `han:dev-small` | `grok/agents/dev-small.md` | medium |
| implement | `han:dev-medium` | `grok/agents/dev-medium.md` | high |
| verify | `han:verifier` | `grok/agents/verifier.md` | low |
| review, audit, 좁은 재리뷰 | `han:reviewer` | `grok/agents/reviewer.md` | high |

다섯 정의는 `model: inherit`로 현재 선택된 native Grok 4.7을 상속한다. 부모의 현재 effort는 바꾸지 않고 역할의 effort만 적용한다.
다른 모델을 선택하거나 Claude·Codex CLI를 대신 실행하지 않는다. 실행 환경의 모델 핀·역할 설정이 상속을 바꿀 수 있으므로 실제 자식의 모델도 확인한다.

## 어댑터 역할 해석

`runtimes.grok.roles`와 `local.runtimes.grok.roles`의 명시적 설정을 포함해 병합된 어댑터를 읽는다.
아래는 공통 어댑터와 공통 스킬의 기존 이름을 Grok 역할로 옮기는 호환 매핑이다.

- research: 누락 또는 `han:researcher`, `han_researcher` → `han:researcher`.
- implementSmall: 누락 또는 `han:dev-small`, `han_developer_small` → `han:dev-small`.
- implement: 누락 또는 `han:dev-medium`, `han_developer_medium` → `han:dev-medium`.
- verify: 누락 또는 `han:verifier`, `han_verifier` → `han:verifier`.
- review: 누락 또는 `code-reviewer`, `han:reviewer`, `han:reviewer-small` → `han:reviewer`.
- audit: 누락이면 review 매핑을 따르고, 명시된 `han:reviewer` 또는 `code-reviewer`도 `han:reviewer`다.

다른 사용자 정의 역할은 실제 native 등록·지시·권한·모델·effort 계약을 읽고 확인한다.
지원되지 않는 명시적 역할을 표의 기본값이나 `general-purpose`로 조용히 바꾸지 않는다. 필요한 역할을 사용할 수 없으면 그 단계를 미확인으로 반환한다.
프로젝트의 reviewLoopSkill은 실제 설치 파일을 읽되, 그 안의 Claude 모델·에이전트·파견·중첩 실행 지시는 이 Grok 매핑과 깊이 제한으로 대체한다.

## 실제 도구로 실행

도구의 현재 스키마가 기준이다. native Grok의 모델 노출 이름은 다음과 같다.

- 생성: `spawn_subagent`에 `subagent_type`, 짧은 `description`, 완결된 `prompt`, 실제 프로젝트 `cwd`, 필요하면 `run_in_background: true`를 준다.
  기본 공유 작업 공간을 쓰고, 프로젝트 어댑터가 정하지 않은 Git worktree를 임의로 만들지 않는다.
- 수집: 반환된 작업 id를 `get_command_or_subagent_output`의 `task_ids` 배열에 넣는다. `timeout_ms`로 도구의 완료 대기를 쓴다.
  `wait_commands_or_subagents`가 노출된 환경이면 실제 스키마의 작업 id·대기 옵션을 쓴다. 아직 실행 중인 응답은 실패나 완료가 아니다.
- 중단: 사용자 중단·명시된 timebox 등 실제 중단 사유가 있을 때 `kill_command_or_subagent`의 현재 스키마로 요청한다.
  대기 응답이 일찍 돌아왔다는 이유만으로 종료하지 않는다. 종료 결과와 남은 작업을 확인한다.
- 후속 지시: `send_subagent_message`는 선택 기능이므로 존재를 가정하지 않는다. 없으면 결과 수집 뒤 남은 범위를 새 에이전트에 맡긴다.
  fresh 리뷰에는 `resume_from`을 넣지 않는다. 수정·후속 구현도 공통 절차가 정한 새 에이전트 브리프로 맡긴다.
- 파일·셸: `read_file`, `grep`, `list_dir`, `run_terminal_command` 등 현재 노출된 도구와 MCP의 `search_tool`·`use_tool`을 쓴다.
  설치본 내부의 `run_terminal_cmd`, `task`, `get_task_output`, `wait_tasks`, `kill_task`는 정규화 이름일 수 있다. 모델에 노출되지 않은 내부 이름을 호출하지 않는다.

Claude의 `Task`·`TaskStop`·`SendMessage`, Codex의 `spawn_agent`·`wait_agent` 등은 이 Grok 도구를 뜻하는 것으로만 읽는다.
공통 규칙·하위 스킬이 그 이름을 직접 호출하라고 해도 현재 Grok 도구로 치환한다. 없는 도구를 만들어 호출하거나 셸 루프로 대기하지 않는다.

브리프에는 역할·목표·소유 파일·확정 계약·승인 범위·금지 범위·타깃 검증·관련 finding·반환 항목과 필요한 자료의 실제 경로를 담는다.
다른 작업자의 변경을 되돌리지 말라고 명시한다. 전체 부모 대화·큰 로그·완료한 작업을 상속하지 않는다.
사람용 문서 작성·검토는 공통 규칙·작성 안내·선택한 양식과 예시의 실제 경로, 원문·현재 답·JSON 초안·상세 경로를 함께 넘긴다.

han은 Grok의 기본 깊이 1에서 동작하도록 루트만 spawn한다. 런타임 설정이 더 깊은 파견을 허용해도 다섯 역할 모두 자식을 띄우지 않는다.
분할이 필요하면 파일 경계·계약·선행 조건·검증을 담은 분할 브리프를 부모에게 반환하고, 부모가 새 단위를 파견한다.
부모는 전체 파일 소유·각 결과·남은 범위를 모아 판정한다. 일부 반환·실패·턴 상한을 전체 완료나 새 승인 필요로 바꾸지 않는다.

## 적용 증거와 독립성

YAML의 `effort`는 native 역할 해석에서 자식 런타임의 `reasoning_effort`로 전달된다. 모델이 effort를 지원하는 경우 자식 sampling에 적용된다.
설정 파일 파싱이나 inspect에 이름이 보이는 것만으로 실제 적용을 증명하지 않는다.
실행 뒤 실제 자식 세션의 `summary.json`에 기록된 `current_model_id`·`reasoning_effort`와 연결된 실행 결과를 확인한다.
모델이 effort를 지원하지 않거나 적용 기록을 확인할 수 없으면 요청값과 미확인 이유를 보고한다. 다른 모델로 바꾸거나 절감이 확인됐다고 말하지 않는다.

리뷰·감사는 새 `han:reviewer`로 실행한다. 구현자를 재사용하거나 `resume_from`으로 구현 대화를 물려주지 않는다.
읽기 전용은 역할 지시이며 별도 샌드박스 보장이 아니다. reviewer가 반환한 완성 본문은 코디네이터가 실제 근거 파일에 반영하고 `review`로 기록한다.
검증자는 지정된 실행기의 receipt·결과·로그만 만들고 코디네이터의 상태·승인·완료 기록을 쓰지 않는다.
최신 유효 receipt와 독립 clean review·완료 조건 증거를 모두 확인한 코디네이터만 `complete`를 호출한다.
