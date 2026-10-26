# 질문 정본과 답 기록

질문·답을 서로 다른 Markdown 절에 나눠 수동 갱신하면 부분 답과 완료 표시가 어긋날 수 있다.
`questions/<id>.json`에 문항 상태와 사용자 원문을 함께 기록하고, 사람용 Markdown은 이 정본에서 생성한다.
대시보드는 읽기 전용이다. 답·승인은 현재 미션 채팅에서 받고 코디네이터가 CLI로 기록한다.
미션 전체 A·B·C 승인, `mission.md` 머리 줄과 `[~]` 결정은 기존 상태 CLI의 별도 경계를 유지한다.

## 정본과 생성본

- 정본: `<미션>/questions/<id>.json`
- 생성본: `<미션>/<id>.md`. `ask-*`·`confirm-*`만 쓰며 id는 경로 구분자 없는 안전한 파일 이름이다.
- 상세: `<미션>/details/<id>.md`. 전체 기술 명세·승인 근거·적용 범위를 보존한다.
- 이전 개정: `<미션>/questions/history/<id>/<revision>.json`

정본과 생성본을 직접 편집하지 않는다. 작성자는 정본 밖의 draft JSON과 상세를 쓰고 코디네이터가 발행한다.
생성본 상단 상태·문항별 답·마지막 답 절도 CLI가 만든다. 사람용 Markdown 양식·예시는 본문 문체·순서·승인 범위에만 참고한다.
예시의 사실·선택지·승인을 새 질문으로 가져오지 않는다.

정본 `schema: 1`의 필드는 다음과 같다.

| 필드 | 값과 의미 |
| --- | --- |
| `id` | `ask-1`, `confirm-A2` 같은 문서 id. `.md` 확장자는 붙이지 않음 |
| `revision` | 양의 정수. 질문·선택지·문체 개정 때 증가하며 답 기록으로는 증가하지 않음 |
| `title` | 사람이 결정할 대상이 드러나는 제목 |
| `intro` | 목적·이번 결정 범위의 짧은 Markdown |
| `items` | 문항 배열. 각 문항은 `id`, `title`, `body`, `status`, `answer`를 가짐 |
| `body` | 승인할 설계·범위·조건 등 나머지 Markdown. 판단에 필요한 표·계약도 여기에 보존 |
| `approval` | `status: none\|pending\|complete`, `reason`. `pending`이면 남은 조건을 반드시 적음 |
| `answers` | 사용자 원문·대상 개정·문항 갱신의 누적 이력 |
| `legacy_markdown` | 기존 질문을 전환할 때 보존하는 원문. 선택 필드 |

문항은 하나 이상이어야 하며 정해진 필드 외의 키는 거부한다. 문항의 선택 필드 `heading`은 기존 절 제목을 보존할 때 쓴다.
문항 `id`는 개정 뒤에도 유지한다. `body`에는 배경·권장·대안과 영향을 짧게 쓰고 `status`에 상태를 적는다.
새 미답 문항은 `pending`과 빈 `answer`로 만들며, `answered`·`withdrawn`에는 답·적용 범위 또는 철회 근거가 필요하다.
`approval`은 이 문서에 추가로 남은 승인 여부다. 문항에 답했다고 미션 전체 단계가 승인되지는 않는다.
조건부 승인은 `approval.status: pending`과 `reason`에 남은 조건을 적는다.

## 처음 발행하기

아래 `<mission-cli>`는 미션 스킬의 상태 CLI 호출에 쓰는 `scripts/mission_state.py`의 실제 절대 경로다.
필요하면 기존 CLI와 같이 `--project`·`--mission`으로 프로젝트와 미션을 지정한다.
공통 검사기는 `<plugin-root>/skills/mission/scripts/check_human_doc.py`다. `<plugin-root>`는 실제 플러그인 루트로 치환하며
Claude에서는 `${CLAUDE_PLUGIN_ROOT}`를 쓸 수 있다. Codex도 이 공통 파일을 호출한다.

1. 작성자는 [정상 초안](templates/question.json)을 정본 밖의 `draft.json`으로 복사해 문서 id·제목·문항·본문을 채운다.
   상세에는 실제 확인한 근거·전체 명세를 쓴다. 기존 문서를 고치면 변환 전 원문과 최신 답·승인 범위를 함께 받는다.
2. 코디네이터가 `question preview`의 출력물을 임시 Markdown으로 받고, 작성자와 별도 검토자가 원문·현재 답·고른 양식·예시에 대조한다.
   공통 검사기에 JSON 초안을 넣어 설계 표·legacy 원문·답 이력 보존을 검사한다. `--source`에는 이전 정본 JSON을 넣고
   최초 이관이면 원본 Markdown을 넣는다. 새 문서는 `--source`를 생략한다.
3. 작성자가 검토 의견을 반영하면 코디네이터가 발행한다. 생성본·상세의 링크와 렌더링을 확인하고 `question check`를 실행한다.
   통과 뒤 생성된 사람용 문서와 상세를 공유한다. 정형 검사는 의미·문체·승인 범위의 독립 검토를 대신하지 않는다.

```sh
python "<mission-cli>" question preview --input draft.json > preview.md
python "<plugin-root>/skills/mission/scripts/check_human_doc.py" draft.json --source original.json
python "<mission-cli>" question publish --input draft.json
python "<mission-cli>" question check
```

`preview`는 초안을 검증해 Markdown을 출력하며 미션 정본·생성본을 쓰지 않는다. `preview.md`는 사람의 독립 검토용 임시 파일이다.
자동 보존 검사는 JSON 초안과 이전 JSON 또는 최초 이관 Markdown을 비교한다. 생성된 답 이력 절을 과거 Markdown 답 절과 직접 비교하지 않는다.
발행된 문서를 고쳐야 하면 생성본을 편집하지 말고 초안으로 돌아간다. 자동 표시 자체는 검토·공유·승인 증거가 아니다.

## 답을 받는 즉시 기록하기

아래는 가상 문항 하나에 답한 입력 예시다. 실제 답의 원문·날짜·대상 개정과 적용 범위로 바꾼다.
다른 문항은 `updates`에 넣지 않아 미답 상태를 그대로 유지한다.

```json
{
  "schema": 1,
  "id": "reply-20261026-01",
  "revision": 1,
  "at": "2026-10-26",
  "text": "1번은 권장안대로 해 주세요.",
  "updates": [
    {
      "id": "1",
      "status": "answered",
      "answer": "1번 권장안에 동의. 이 문항의 범위에만 적용."
    }
  ]
}
```

```sh
python "<mission-cli>" question answer ask-1.md --input reply.json
python "<mission-cli>" question check
```

답 입력의 `id`는 문서 id가 아니라 중복 실행을 식별하는 고유 작업 id다. 같은 id·같은 입력의 재시도는 멱등이고,
같은 id에 다른 내용을 넣으면 거부한다. `revision`은 답한 질문의 현재 개정, `at`은 날짜 또는 ISO 시각, `text`는 사용자 원문이다.
원문은 줄이거나 다듬지 않는다. `updates`에는 명확히 결정된 문항만 넣는다. 현재 개정에 없는 문항 id의 답 갱신은 거부한다. 되묻기·의견만 있으면 원문을 보존하고 상태는 올리지 않는다.
조건부·별도 승인이 남으면 같은 입력에 `"approval": {"status": "pending", "reason": "남은 승인 조건"}`을 추가한다.
명확한 전체 답은 문항별로 대응시키고, 무엇을 승인했는지 불명확한 부분만 다시 확인한다.

`question answer` 한 번으로 원문 이력·문항 상태·문서의 추가 승인 상태와 생성본을 함께 갱신한다.
부분 답도 전체 해결까지 미루지 않는다. 상세에는 승인 근거·적용 범위와 이 이력의 위치를 연결하고,
상단 상태·답 절·상세 원문을 따로 수동 갱신하는 절차를 두지 않는다. 미션 전체 승인 조건이 충족됐는지는 코디네이터가 별도로 확인한다.

## 개정과 복구

```sh
python "<mission-cli>" question show ask-1.md
python "<mission-cli>" question publish --input draft-r2.json --expect-revision 1
python "<mission-cli>" question render ask-1.md
python "<mission-cli>" question check
```

- `show`·`render`에는 `ask-1.md`처럼 `.md`가 붙은 루트 문서 이름을 준다. `show`는 현재 정본 JSON을 출력한다. 이를 읽고 별도 초안을 작성한다.
- 내용 개정은 입력 `revision`을 현재보다 크게 하고 `--expect-revision`에 현재 값을 준다.
  초안·상세를 검토한 뒤 발행하며 이전 개정은 history에 남긴다. 기존 `answers` 이력은 앞부분 그대로 유지하고 legacy 원문도 보존한다.
- 일반 개정에서 문항의 `title`·`body`·`heading`을 바꾸면 그 문항은 `pending` 또는 명시적 `withdrawn`으로 둔다.
  바뀐 질문의 답을 이전 승인에서 가져오지 않고 새 개정에서 `question answer`로 기록한다.
- `intro`·문서 `body`의 전체 계약이 바뀌었는데 해결된 문항을 유지하려면 문서 `approval.status`를 `pending`으로 두고
  `reason`에 남은 확인 이유·범위를 적는다. 개별 답을 유지한 것과 바뀐 전체 계약을 승인한 것은 다르다.
- 문체만 바꾸고 의미·승인 범위가 같으면 `question publish --input draft-r2.json --expect-revision 1 --editorial`로 명시한다.
  기존 문서의 개정에만 쓸 수 있고 문항 id 집합·상태·`answer`, 문서 `approval`·`answers`는 바꿀 수 없다.
  개정 증가·현재 개정 확인 규칙은 같다. 의미 변경을 `--editorial`로 포장하지 않는다.
- 답 기록은 질문 개정을 올리지 않는다. 답이 끝난 ask에 새 결정을 덧붙이지 않고 다음 ask로 묻는다.
- 현재 정본과 내용이 정확히 같은 입력으로 재발행하면 생성본을 복구할 수 있다. 질문 개정을 복구 명목으로 덮어쓰지 않는다.
- `render`는 현재 정본에서 생성 문서를 복구한다. 질문 내용이 바뀌면 초안 개정·검토·발행으로 돌아간다.

## 기록 확인과 기존 문서 전환

`question check`는 미션 전체 질문 Markdown과 정본을 대조하고 누락·손상·불일치가 있으면 종료 코드 1을 반환한다.
발행·답 기록 뒤와 질문을 공유하기 전에 반드시 실행한다. 재개할 때도 검사해 남은 기록 문제를 확인한다.
정본 누락·손상·생성본 직접 수정·등록과 표시의 불일치는 대시보드의 `기록 확인 필요`로 따로 노출한다.
이를 미답이나 완료로 추정하지 않으며 코디네이터가 원본을 대조해 복구한다.
대시보드의 미답 수·문서 추가 승인 대기는 `items[].status`·`approval.status`만 사용하고 답 문장·권장안·대안을 해석하지 않는다.

기존 Markdown은 원문·답 날짜·선택지·승인 범위를 보존하고 명시적으로 JSON으로 전환한다.
원문은 `legacy_markdown`에 보존하고 확인 가능한 문항 상태·답만 옮긴다. 모호한 산문을 자동 승인으로 바꾸지 않는다.
기존 `## 사용자 입력`과 `.dashboard-answers/`는 과거 입력·전달 이력으로 그대로 보존한다.

CLI를 부르지 않은 채 채팅에만 남은 답은 디스크 검사만으로 알아낼 수 없다.
코디네이터가 채팅의 답을 즉시 기록하고 검사하는 절차를 지킨다. 실행 중인 이전 세션은 새 스킬 지침을 다시 읽어야 한다.
