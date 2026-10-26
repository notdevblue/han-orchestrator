---
name: han-slots
description: "Grok CLI에서 슬롯 워크스페이스를 조회·생성·갱신·삭제할 때 쓴다. 슬롯 만들어줘, 슬롯 상태, 슬롯 갱신, 슬롯 지워줘, 슬롯에서 미션 돌려줘, han-slots 요청에 사용한다. Grok 구현은 슬롯 폴더의 grok 와 han-run 이다."
---

# han 슬롯

슬롯 만들기·동기화·삭제·상태 기록은 `slots.py`만 쓴다. p4·폴더 복사·링크·삭제를 손으로 하지 않는다.
`slots.tsv`는 손으로 고치지 않는다. 실패하면 출력의 로그 경로(`<stateDir>/slots/<이름>-<단계>.out.log`)와 요약을 알리고 멈춘다. 같은 명령을 반복하거나 다른 명령으로 우회하지 않는다.

`<repo>`는 `HAN_REPO`이거나 저장소 체크아웃이다. `--runtime grok`으로 어댑터를 찾는다. 다른 폴더에서 부르면 `--project <메인 프로젝트>`를 준다.
미션이 열린 작업 공간에서는 `create`·`refresh`·`remove`·`run`을 돌리지 않는다. `status`는 읽기 전용이라 언제든 된다.
슬롯 이름은 어댑터 `slots.names`에 있는 것만이다. 종료 코드 0 성공, 1 실패, 2 사용 오류.
`state`가 `ready`가 아니면 `run`은 거부한다.

```text
python "<repo>/plugins/han/skills/slots/scripts/slots.py" --runtime grok --project "<메인 프로젝트>" status
python "<repo>/plugins/han/skills/slots/scripts/slots.py" --runtime grok --project "<메인 프로젝트>" create <이름> --dry-run
python "<repo>/plugins/han/skills/slots/scripts/slots.py" --runtime grok --project "<메인 프로젝트>" refresh <이름> --dry-run
python "<repo>/plugins/han/skills/slots/scripts/slots.py" --runtime grok --project "<메인 프로젝트>" remove <이름> --yes
```

- `create`·`refresh`는 먼저 `--dry-run`으로 기준과 거부 사유를 본다. 사용자가 그 실행을 승인한 뒤에만 본명령을 돌린다.
- `refresh`는 슬롯에 ACTIVE가 있으면 거부한다. 열린 파일이나 로컬 변경이 있으면 거부한다. `--keep-local`·`--discard-local`·`--main-keep-local`은 사용자가 그 처리를 승인한 뒤에만 붙인다.
- `remove`는 되돌릴 수 없다. 그 자리에서 확인받은 뒤에만 `--yes`를 붙인다. `creating`은 먼저 `create <이름> --resume`이다.
- 중간에 끊긴 슬롯은 `create <이름> --resume`이다.

## Grok으로 슬롯 작업

새 미션은 비어 있는 ready 슬롯에만 붙인다. ACTIVE가 있는 슬롯에 다른 미션을 겹치지 않는다.
Grok 단독이면 세션을 띄우지 않는다. 등록된 슬롯 경로에서 `grok`를 열고 `han-run`으로 구현한다.

`slots.py run`은 Claude 세션을 Windows Terminal에 띄우는 선택 경로다. 사용자가 Claude 세션을 띄우라고 할 때만 `--runtime` 없이 호출한다. `ready`이고 ACTIVE가 없어야 한다. `--runtime grok`를 `run`에 붙이면 스크립트가 거부한다.
Codex 세션은 사용자가 그 런타임을 지정할 때만 `<repo>/plugins/han/codex/skills/han-slots/scripts/slots.py`의 `run`이다.

대시보드가 필요하면 `<repo>/plugins/han/skills/slots/scripts/dashboard.py`를 읽기 전용으로 띄운다. 답과 승인은 대시보드에 쓰지 않는다.
