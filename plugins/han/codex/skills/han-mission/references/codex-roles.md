# Codex 역할 파견

플러그인 루트의 `codex/agents/*.toml`에 역할별 지시가 있다. 프로젝트의 AGENTS.md와 함께 적용한다.
Codex는 플러그인의 이 폴더를 사용자 에이전트 폴더로 자동 등록하지 않는다.

| 역할 | 기본 지시 |
| --- | --- |
| research, 기존 han:researcher | `codex/agents/han-researcher.toml`의 developer_instructions |
| implement, 기존 han:dev-medium | `codex/agents/han-developer-medium.toml`의 developer_instructions, GPT-6.1 Sol / high 고정 |
| implementSmall | `codex/agents/han-developer-small.toml`의 developer_instructions, GPT-6 Luna / high 고정 |
| verify | `codex/agents/han-verifier.toml`의 developer_instructions, GPT-6 Luna / high 고정 |
| review | 변경 범위·명세·완료 조건을 받아 결함과 근거만 보고한다. 파일을 편집하지 않는다. |

등록된 에이전트가 있으면 사용한다. 없으면 해당 TOML의 developer_instructions와 마일스톤 브리프를
새 서브에이전트의 프롬프트로 넘긴다. 이름만으로 Claude 에이전트를 실행할 수 있다고 가정하지 않는다.
사용자·프로젝트가 지정한 다른 역할은 실제 정의를 읽는다. 필수 정의를 찾지 못하면 알리고 멈춘다.
리뷰는 구현 에이전트를 재사용하거나 구현 대화 내역을 fork하지 않고 새 에이전트로 실행한다.

research와 review의 모델·추론 수준은 부모 설정을 상속한다. implement는 `gpt-6.1-sol`과 high,
implementSmall과 verify는 `gpt-6-luna`와 high로 고정한다.
등록된 `han_developer_small` 또는 `han_verifier`가 없으면 해당 역할의 지시·브리프와
`model: "gpt-6-luna"`, `reasoning_effort: "high"`, `fork_turns: "none"`을 넘긴다.
등록된 `han_developer_medium`이 없으면 해당 역할의 지시·브리프와 `model: "gpt-6.1-sol"`,
`reasoning_effort: "high"`, `fork_turns: "none"`을 넘긴다. 중첩 구현 파견에도 같은 모델·추론 수준을 지정한다.
고정 모델·추론 수준을 지정할 수 없으면 다른 모델로 대신 실행하지 않고 코디네이터에게 미확인으로 반환한다.
실행 중 모델 전환·자동 승격은 하지 않는다.
그 밖의 별도 모델은 사용자나 프로젝트 설정이 정한 경우만 쓴다.
읽기 전용은 역할 지시이며 별도 샌드박스를 뜻하지 않는다. 권한은 부모 세션에서 상속한다.

선택적으로 `codex/agents/*.toml`을 `~/.codex/agents/`에 복사하면 해당 이름을 등록할 수 있다.
기존 파일이 있으면 먼저 차이를 보고하고 덮어쓰지 않는다. 이 플러그인 설치만으로 전역 파일을 쓰지는 않는다.
