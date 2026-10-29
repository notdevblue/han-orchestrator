---
name: han-mission-brief
description: "Grok 코디네이터가 프로젝트 작업을 메인 또는 슬롯의 실행 봇에게 넘길 때 쓴다. 기본은 Grok 단독 인계다. Claude·Codex 붙여 넣기는 사용자가 그 런타임을 지정할 때만 쓴다."
---

# han 미션 브리프

코디네이터가 브리프를 만들고 실행 봇을 깨운다.
기본 실행은 Grok 단독이다. 메인과 슬롯이 아닌 작업은 메인 실행 봇, 슬롯 작업은 그 슬롯 실행 봇이 진행한다.
로컬 상태·슬롯 조회는 han 스크립트로 하고, 저장소 코드 작업은 프로젝트의 실행 규칙을 따른다.
Claude와 Codex는 선택 경로다. 이 스킬에서 코디네이터는 프로젝트 코드와 미션 파일을 고치지 않는다. 고치는 쪽은 깨운 봇이다.

## 누구를 깨우나

| 자리 | 실행 대상 | 경로 |
| --- | --- | --- |
| 메인, 슬롯이 아닌 작업 | 메인 실행 봇 | `<project>` |
| 선택한 슬롯 | 해당 슬롯 실행 봇 | `<slot-path>` |

슬롯 봇은 미션이 붙기 전까지 대기한다. 비어 있는 ready 슬롯만 새 미션에 쓴다.
ACTIVE가 있는 슬롯에는 다른 미션을 겹치지 않는다.

## 순서

1. `han-status`와 같은 조회로 현재 상태를 본다. 어댑터 오류면 `han-doctor`로 가고 브리프를 만들지 않는다.

```text
python "<repo>/plugins/han/grok/scripts/han_state.py" snapshot --project "<프로젝트 폴더>"
```

2. 메인 ACTIVE나 슬롯 ACTIVE가 있으면 미션 id·단계·목표를 보여주고, 새 미션으로 열지 사용자에게 묻는다.
   확인 전에는 `brief`를 돌리지 않는다.
3. 기본 런타임은 `grok`다. `--runtime`을 비우거나 `grok`로 둔다.
   슬롯에 붙이려면 `--slot slotN`을 준다. 비우면 작업 폴더가 등록된 슬롯 안에 있는지 확인하고, 해당하지 않으면 메인 작업으로 인계한다.
4. `--goal`은 대상과 결과가 보이는 한 줄이다.

```text
python "<repo>/plugins/han/grok/scripts/han_state.py" brief --project "<프로젝트 폴더>" --runtime grok --goal "<한 줄 목표>"
python "<repo>/plugins/han/grok/scripts/han_state.py" brief --project "<프로젝트 폴더>" --runtime grok --slot slot2 --goal "<한 줄 목표>"
```

5. `인계:` 아래 `SendToAgent:` 블록을 그 봇에게 보낸다. 슬래시 명령으로 바꾸지 않는다.
   도구가 있으면 SendToAgent로, 없으면 그 봇의 입력으로 같은 블록을 전달한다.

`<repo>`는 `HAN_REPO`이거나 저장소 체크아웃이다. 스크립트 복사본을 만들지 않는다.

## 보낼 내용

블록에 아래가 들어 있다. 출력에 없는 봇·경로를 지어내지 않는다.

- 대상: `main` 또는 등록된 슬롯 이름. 실제 봇 이름 연결은 개인 환경에서 정한다
- 목표: `--goal`
- 프로젝트 경로: 메인은 어댑터의 `vars.main`(없으면 발견한 프로젝트 루트), 슬롯은 등록된 슬롯 경로. `slots.tsv`에 경로가 있으면 그 값을 쓴다
- 제약: submit 금지, 범위 밖 수정 금지, 미션 상태 파일은 han 스크립트로만
- 쓰기 확인: 파일을 쓰기 전에 이번 변경의 범위와 경로를 확인하고, 확인 없이 쓰지 않는다
- 실행 규칙: 저장소 코드는 프로젝트의 실행 규칙을 따르고, 로컬 상태·슬롯·작업 폴더는 실행 봇이 han 스크립트로 본다

## 선택: Claude 또는 Codex

사용자가 그 런타임을 지정했을 때만 쓴다. Codex는 줄여 둔 선택 경로다.
기본 인계를 이 문장으로 바꾸지 않는다.

```text
python "<repo>/plugins/han/grok/scripts/han_state.py" brief --project "<프로젝트 폴더>" --runtime claude --goal "<한 줄 목표>"
python "<repo>/plugins/han/grok/scripts/han_state.py" brief --project "<프로젝트 폴더>" --runtime codex --goal "<한 줄 목표>"
```

Claude 문장은 `/han:mission ...`, Codex 문장은 `$han-codex:han-mission ...`이다.
`사람이 그 세션에 붙일 문장`을 그대로 전한다. 슬롯에서 돌려야 하면 비어 있는 ready 슬롯 이름을 같이 적고, 세션은 그 런타임의 slots 명령으로 연다.
이 선택 경로에서는 코디네이터가 Claude·Codex 세션을 띄우지 않는다. Grok 슬롯 세션 실행은 `han-slots`에서 다룬다.
