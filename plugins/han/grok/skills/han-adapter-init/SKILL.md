---
name: han-adapter-init
description: "Grok CLI에서 미션 어댑터(adapter.json · adapter.md) 초안을 만들거나 점검할 때 쓴다. 미션 어댑터 만들어줘, 이 프로젝트에 미션 붙여줘, 어댑터 점검해줘, han-adapter-init 요청에 사용한다. 값을 지어내지 않는다."
---

# han 어댑터 초안

계약은 `<repo>/plugins/han/skills/mission/references/adapter-schema.md`다. 값은 프로젝트 문서·도구 도움말·설정 파일에서 확인한 것만 쓴다. 문서와 코드가 어긋나면 코드를 따른다.
어댑터 본문을 이 스킬 안에 만들지 않는다. 초안을 쓰기 전에 사용자에게 위치를 묻는다.

`<repo>`는 `HAN_REPO`이거나 저장소 체크아웃이다. Grok은 Claude `pluginConfigs`의 `adapters_dir` 주입을 받지 않는다.

## 위치

둘 중 어디인지 확인받기 전에는 파일을 쓰지 않는다.

- 개인 레지스트리: `HAN_ADAPTERS_DIR`의 `<이름>/`과 `projects.json`의 `roots`. VCS에 올리지 않는다. 슬롯을 쓰면 `slots.root`·`slots.client`·`slots.mainClient`는 그 폴더의 `local.json`에 둔다.
- 저장소 안: `<프로젝트>/.grok/orchestrator/`. 팀과 공유할 때만. 개인 경로·작업 공간 이름은 넣지 않는다.

`HAN_ADAPTERS_DIR`가 비어 있으면 `HAN_REPO/adapters`를 레지스트리로 쓸지 사용자에게 묻고, 경로를 정해 달라고 한다. 폴더를 추측해서 만들지 않는다.
이미 어댑터가 있으면 새로 쓰지 않고, 확인한 값과 다른 칸만 제안한다.

## 점검

```text
python "<repo>/plugins/han/skills/adapter-init/scripts/check_guard.py" --doctor
python "<repo>/plugins/han/skills/adapter-init/scripts/check_guard.py" --runtime grok "<adapter.json 또는 레지스트리 이름>" [cases.json]
```

`--doctor`는 이 PC의 python이 실행되는지 본다. 표본을 생략하면 어댑터 옆 `guard-cases.json`을 쓴다.
어긋남이 0이기 전에 초안을 확정하지 않는다. 표본 파일은 VCS에 올리지 않는다.
초안과 미확인 값을 보여 주고 승인받은 것만 쓴다. 슬롯을 쓰기로 했으면 `han-slots`로 첫 슬롯을 만든다.
