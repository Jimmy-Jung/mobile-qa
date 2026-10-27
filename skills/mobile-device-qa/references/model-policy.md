# 모델 선택·승격과 구간 반환

Author: JunyoungJung · Created: 2026-10-06 · Updated: 2026-10-27

처음 구간을 위임할 때 읽고 같은 담당자에게 재사용한다. 모델·호스트 변경이나 관찰 불확실성이 생기면 해당 절차를 다시 확인한다. 공통 실행·복구·증거·보고 규칙은 [모바일 앱 검증](../SKILL.md)을 따른다.

## 선택과 제어권

메인의 모델은 사용자 선택을 유지한다. 번들 역할은 모델·effort를 강제하지 않고 현재 세션을 상속한다. 명확한 구간을 위임할 때만 호스트에서 실제 제공하는 빠른 역할을 선택할 수 있으며, 가용성·이미지 입력·기기 도구와 사용자 승인 범위를 먼저 확인한다. 고정 모델명이나 최신 별칭으로 정확도·속도를 보장하지 않는다. 알려진 흐름은 `batch/replay`·프로젝트 테스트를 사용한다.

1. 요소 식별·관찰이 불확실하면 변경 조작을 보류하고 현재 요소·이미지·ref를 재확인한다. 근거가 다른 복구를 최대 두 번 수행한 뒤에도 불명확하거나 복잡한 시각 판단이 필요하면 해당 항목의 근거·`escalation_reason`·`last_state`를 메인에 반환한다. 이미 완료한 조작을 재전송하지 않는다.
2. 확정된 불일치는 `condition: 불일치`, 근거 부족은 `확인 불가`로 구분한다. 연결·권한·서명 문제를 모델 역량 문제로 바꾸지 않는다. 독립 항목은 계속할 수 있지만 즉시 인계가 필요한 기기 상태에서는 실행을 종료하고 반환한다.
3. 메인은 원본 근거를 검토한다. 기기를 이어받을 때 진행 중 명령·녹화 종료를 확인하고 [제어권 인계](../SKILL.md#기기-점유와-제어권-인계)를 적용한 뒤 화면·Native/WebView·회전·키보드·전제조건을 재확인한다. 담당자나 모델을 바꿔도 기대값·권한·자료 보존·복구 횟수는 유지한다.

같은 기기의 조작과 결과 캡처는 완료 확인 후 직렬 실행한다. 독립 기기 또는 저장된 근거 분석만 병렬화한다. 담당자는 재위임하거나 임의의 모델·전역 설정을 변경하지 않는다.

## 호스트별 적용

- **Codex:** [선택 설치 역할](../../../integrations/codex/mobile-qa-runner.toml)은 standalone custom agent의 `name`·`description`·`developer_instructions`를 포함한다. 사용자가 원하면 현재 호스트가 지원하는 프로젝트 `.codex/agents/`에 파일을 복사한다. 기본 실행에는 등록이 필수가 아니며 전역 설정을 수정하지 않는다. 역할이 현재 세션에 없거나 필요한 도구가 없으면 메인이 직접 실행한다. 설치 시의 모델 상속과 실제 실행 모델은 구분한다.
- **Claude Code:** [플러그인 역할](../../../agents/mobile-qa-runner.md)은 `mobile-qa:mobile-qa-runner`로 제공하며 `model: inherit`, `skills: [mobile-qa:mobile-device-qa]`, `disallowedTools: [Agent]`를 사용한다. 스킬 사전 로드가 누락됐으면 메인이 설치 경로의 `skill_path`를 전달한다. 일반 subagent는 대화·기존 관찰을 자동 상속하지 않으므로 구간 문맥을 명시한다. 필요한 화면 제어·이미지 도구가 실제 제공되는지 확인한다.
- 요청 모델·effort와 실제 실행값·확인 출처를 분리한다. 역할 파일·설정 요청·모델의 자기 설명만으로 실제값을 확정하지 않는다. 메타데이터가 없으면 `확인 불가`, 미지원 effort는 `미지원`이다. 미확인만으로 승인된 독립 QA를 중단하지 않는다. 호스트 미지원에 맞추려고 권한·전역 모델·환경변수를 변경하지 않는다.

## 구간 입력과 반환

메인은 `segment_id`, 비교용 `target_id`, `app_build`와 설치 일치 근거, 프로젝트별 정책·지식·인증 문맥(`project_context`), 확정된 실행 정보(`execution`: 대상 종류·조작/캡처 도구 포함), 현재 상태와 확인 시점, 누적 복구 기록(`recovery_history`), 재사용 검색·선택 결과(`flow_plan`), 구간 목표·허용 조작·기대값·체크포인트·중단 조건, 출력 경로, 요청 모델·effort와 확인된 실행 메타데이터를 전달한다. 앱 식별·실행 정보·복구 제한의 기준은 [스킬의 대상과 실행 상태 고정](../SKILL.md#대상과-실행-상태-고정)과 [자율 실행](../SKILL.md#승인-범위-안에서-자율-실행)을 따른다. 기기·앱 식별자와 필요한 환경은 실행 문맥으로만 전달하며 계정·토큰 원문을 문서에 복사하지 않는다.

입력은 아래 필드명과 중첩을 사용한다. 기존 프로젝트 형식이 있으면 이 항목으로 대응시켜 전달한다. 이 예시는 구조를 설명하며 연결·모델 가용성·실제 실행을 증명하지 않는다. 경로 필드의 `$QA_SKILL_PATH`·`$QA_PROJECT_DIR`·`$QA_PROJECT_STORE`·`$QA_STATE_DIR`·`$QA_OUTPUT_DIR`는 값의 출처를 표시한다. JSON이나 담당자가 변수를 자동 확장하지 않으므로 메인이 확인한 절대 경로를 넣는다. `project_context.store_dir`는 Library의 전용 지식 폴더, 출력 경로는 **선택한 저장 폴더 안에서 대상 프로젝트의 물리 경로로 계산·검증하고 추가 저장 정책을 충족한 산출물 경로**다. 예시 경로나 다른 프로젝트 작업키를 복사해 쓰지 않는다. 전달한 현재 화면의 확인 시점이 없거나 오래됐으면 첫 변경 조작 전에 갱신한다. 실제 모델 메타데이터의 미확인만으로 승인된 QA를 중단하지 않고 `확인 불가`로 기록한다.

```json
{
  "segment_id": "QA-01",
  "target_id": "target-a",
  "app_build": "build-a",
  "app_build_evidence": [],
  "skill_path": "$QA_SKILL_PATH",
  "project_context": {
    "project_key": "확인 불가",
    "store_dir": "$QA_PROJECT_STORE",
    "entrypoint": null,
    "policy_refs": [],
    "knowledge_refs": [],
    "server_environment": {"name": null, "source_refs": []},
    "authentication": {"required": null, "procedure_ref": null}
  },
  "execution": {
    "target_kind": "physical",
    "control_tool": "agent-device",
    "capture_tool": "agent-device",
    "host": "Codex",
    "host_version": "확인 불가",
    "session": "qa-local",
    "owner": "mobile_qa_runner",
    "working_dir": "$QA_PROJECT_DIR",
    "state_dir": "$QA_STATE_DIR",
    "runner_options": [],
    "environment": {}
  },
  "current_state": {
    "observed": "목록 화면; 진행 중 명령과 녹화 없음",
    "observed_at": "확인 불가"
  },
  "recovery_history": [],
  "design_plan": null,
  "flow_plan": {
    "catalog_path": null,
    "search_result": "프로젝트 QA/테스트 영역 검색 후 목적과 조건이 맞는 기존 흐름 없음; 이번 구간을 저장 후보로 관찰",
    "selected": []
  },
  "goal": "상세 화면 전환 확인",
  "allowed_actions": ["상세 버튼 탭", "현재 화면 조회", "체크포인트 캡처"],
  "expected": "상세 화면 표시",
  "checkpoints": ["상세 화면 표시 후 현재 빌드의 화면 캡처"],
  "stop_conditions": ["기대 상태 불일치", "변경 결과 확인 불가", "승인 밖 영향"],
  "output_dir": "$QA_OUTPUT_DIR",
  "model": {
    "requested": "상속",
    "requested_effort": "상속",
    "resolved": "확인 불가",
    "resolved_effort": "확인 불가",
    "verification_source": "확인 불가"
  }
}
```

실행 담당자는 아래 필드명을 사용해 대상·구간별 JSON을 반환한다. 아래는 **조작은 수락됐지만 전환이 확인되지 않은 형식 예시**이며 실제 실행 결과가 아니다. `steps`에는 실제 수행한 단계만 넣는다. `partial_execution`에는 미실행·중단한 항목 ID별로 이유와 재개에 필요한 사용자 조치를 구분해 남기며, 독립 항목을 계속한 결과는 해당 `steps`에 기록한다.

```json
{
  "segment_id": "QA-01",
  "target_id": "target-a",
  "app_build": "build-a",
  "app_build_evidence": [],
  "project_context": {
    "project_key": "확인 불가",
    "store_dir": "$QA_PROJECT_STORE",
    "entrypoint": null
  },
  "execution": {
    "target_kind": "physical",
    "control_tool": "agent-device",
    "capture_tool": "agent-device",
    "host": "Codex",
    "host_version": "확인 불가",
    "session": "qa-local",
    "owner": "mobile_qa_runner",
    "working_dir": "$QA_PROJECT_DIR",
    "state_dir": "$QA_STATE_DIR",
    "runner_options": [],
    "environment": {}
  },
  "model": {
    "requested": "상속",
    "requested_effort": "상속",
    "resolved": "확인 불가",
    "resolved_effort": "확인 불가",
    "verification_source": "확인 불가"
  },
  "steps": [
    {
      "step_id": "QA-01-01",
      "action": "상세 버튼 탭",
      "command_accepted": true,
      "expected": "상세 화면 표시",
      "observed": "전환 대기 한도 후 목록 화면 유지",
      "observed_at": "확인 불가",
      "condition": "불일치",
      "evidence": [],
      "evidence_unavailable_reason": "캡처 경로 확인 불가",
      "tool_elapsed_ms": null
    }
  ],
  "retry_count": 0,
  "recovery_history": [],
  "design_captures": [],
  "knowledge_candidates": [],
  "flow_results": [
    {
      "id": null,
      "revision": null,
      "action": "candidate",
      "condition": "불일치",
      "flow_path": null,
      "contract_path": null,
      "evidence": [],
      "note": "상세 전환 불일치와 근거 미확보; 재사용 파일 미저장, 검증 완료 승격 불가"
    }
  ],
  "timing": {
    "started_at": "확인 불가",
    "finished_at": "확인 불가",
    "elapsed_ms": null,
    "command_count": 1,
    "failed_command_count": 0,
    "unmeasured_command_count": 1
  },
  "cleanup": {
    "session": "인계",
    "recording": "해당 없음",
    "runner": "인계",
    "evidence": []
  },
  "partial_execution": "QA-01-02(상세 화면 캡처): 상세 전환 불일치로 미실행; 사용자 조치: 없음",
  "stop_reason": "기대 상태와 실제 화면 불일치",
  "escalation_reason": null,
  "last_state": "목록 화면 유지; 진행 중 명령과 녹화 없음",
  "last_observed_at": "확인 불가"
}
```

- `execution.target_kind`는 실제 대상의 `physical`·`simulator`·`emulator`이며, iOS Simulator는 `simulator`, Android Emulator는 `emulator`로 기록한다. `control_tool`·`capture_tool`은 실제 선택한 도구다. Simulator의 `simctl` 캡처와 CUA·기존 UI 테스트 등 조작 경로를 구분하고, 자동 조작이 없으면 `control_tool`에 `manual`을 기록한다. 세션·runner가 없는 도구는 해당 필드를 null/해당 없음으로 남기며 agent-device 세션을 만들지 않는다.
- `condition`은 `충족`·`불일치`·`확인 불가`만 사용하며 최종 `통과`·`실패` 판정은 메인이 한다. 메인은 빠진 필드나 근거를 추정으로 채우지 않는다.
- 디자인 검수에만 [디자인 입력·캡처 계약](design-review.md#구간-입력과-반환)의 `design_plan`을 전달한다. 일반 QA는 `null` 또는 생략한다. 실행 담당자는 실제 촬영한 `design_captures`만 반환하며 Figma 내보내기를 실기기 캡처로 기록하지 않는다. 검수 대상이 아니면 `[]`, 촬영 실패·미실행은 해당 단계의 사유를 남긴다. 픽셀 비교·코드 수정·최종 판정은 메인이 맡는다.
- `app_build`는 확인한 버전·해시와 해당하는 타깃·configuration·SDK variant를 요약한다. `app_build_evidence`에는 설치 앱 정보를 확인한 로컬 근거의 절대 경로를 넣는다. 새 빌드 검수라면 산출물과 설치 파일의 일치·설치 결과 근거도 포함한다. 예시의 빈 배열처럼 근거가 없으면 빌드 식별은 미확인이며 해당 빌드의 최종 통과를 선언하지 않는다.
- `execution`의 작업·상태 경로와 runner 인자·민감하지 않은 환경값은 후속 명령에도 적용한다. `runner_options`는 설치 버전에서 확인한 인자 배열이며, 미사용을 확인했을 때만 `[]`, 미확인은 `null`이다. `environment`에는 자격증명을 넣지 않는다. 현재 ref는 저장하지 않고 화면에서 갱신한다.
- `project_context`의 `project_key`·`store_dir`는 [프로젝트 저장 위치 도구](project-knowledge.md#프로젝트-전용-폴더를-찾는다)의 결과를 사용한다. `entrypoint`는 확인한 `project-qa.md` 경로, 없으면 `null`이다. `policy_refs`·`knowledge_refs`는 이번 항목에 관련된 ID와 정본 링크이며 없음을 확인했으면 `[]`, 조사하지 못했으면 `null`과 사유를 남긴다. `server_environment`는 서버 환경과 확인한 출처이며 `execution.environment`의 프로세스 환경변수와 구분한다. 인증이 불필요함을 확인하면 `authentication.required=false`, 필요하면 `true`와 확인한 `procedure_ref`, 미확인이면 `null`이다. 인증·환경 미확인에 의존하는 조작은 실행하지 않고 독립 항목을 계속한다. 이전 인계에 이 문맥이 없으면 메인이 필요한 정보를 보완하며 매 단계 같은 조회를 반복하지 않는다.
- 반환의 `project_context`는 입력에서 확인한 `project_key`·`store_dir`·`entrypoint`만 그대로 전달한다. 메인은 구간 입력과 대조해 다른 프로젝트 폴더에 후보를 반영하지 않는다. 정책·지식·인증의 상세 입력 필드는 반복 반환하지 않아도 된다.
- `knowledge_candidates`의 필드와 미수집 처리는 [후보 반환](project-knowledge.md#실행-담당자-후보-반환)을 따른다. 실행 담당자는 관찰·가설·재사용 자산·명시된 정책의 후보만 반환하며 문서를 직접 갱신하지 않는다. 메인은 원본 근거와 기존 항목을 대조해 QA 전용 저장소에 반영하고 실제 저장 결과 또는 미저장 사유를 최종 보고에 남긴다. 후보 반환을 승인·저장 완료로 표현하지 않는다.
- `recovery_history`는 기기 별칭(`device_alias`)·원인(`cause`)별로 시도와 관찰 결과(`attempts`의 `action`·`observed`)를 누적한다. 이전 담당자의 이력을 포함해 다음 구간에 그대로 전달한다. 확인된 이력 없음은 `[]`, 인계 이력 미확인은 `null`이며, 미확인을 새 복구 여유로 해석하지 않는다. `retry_count`는 이번 구간의 실제 재시도 수다.
- `flow_plan`의 선택 항목(`id`, `revision`, `mode`, `flow_path`, `contract_path`, `arguments`)과 `flow_results`의 사용·저장 기록은 [반복 동작 관리](reusable-flows.md)를 따른다. `mode`는 검증된 범위의 `reuse`와 이번 QA에서 확인할 `validate`를 구분한다. 검색 미실행을 후보 없음으로 기록하지 않는다. 실행 담당자는 선택한 파일과 현재 시작 조건을 대조하고, 새 후보·불일치 근거를 반환한다. 메인은 저장·카탈로그 갱신 또는 미저장 사유를 확인한다. 기존 인계에 필드가 없으면 필요한 검색·기록을 보완하며 승인된 독립 항목은 계속한다.
- `timing`은 구간 시작·종료 시각과 실제 경과 시간, 실패·재시도를 포함한 도구 명령 수를 기록한다. batch 한 번은 호출 한 번으로 세며 앱 조건 불일치와 도구 명령 실패는 구분한다. `steps[].tool_elapsed_ms`와 명령별 원본 응답을 대조하고 시간 미확보 명령 수를 남긴다. 부분 수집의 합을 전체 도구 시간으로 보고하지 않는다.
- `cleanup`은 세션·녹화·runner별로 `해제`·`인계`·`미완료`·`확인 불가`·`해당 없음`을 기록하고 확인 근거를 연결한다. 사용하지 않은 자원은 `해당 없음`, 빌린 자원을 유지해 넘겼으면 `인계`다. 파일 존재만으로 녹화 확정이나 정상 재생을 선언하지 않는다.
- `last_observed_at`은 `last_state`를 마지막으로 확인한 시각이다. 다음 입력의 `current_state`에 두 값을 함께 대응시키고, 관찰 시각이 미확인이거나 오래됐으면 첫 변경 조작 전에 다시 관찰한다.
- `evidence`에는 실제 확보한 원본의 절대 경로를 넣고 대상·빌드·관찰 시점과 대조한다. 누락 시 빈 배열과 사유를 남긴다. 담당자의 요약·명령 수락·파일 존재만으로 최종 통과시키지 않는다.
- 미확인 문자열 값은 `확인 불가`, 지원하지 않는 effort는 `미지원`이다. 숫자·불리언 값이 미확인이면 `null`을 쓰며 시간·횟수를 `0`으로 대체하지 않는다. 해당하지 않는 중단·승격 사유는 `null`이다. 메인은 반환의 빌드 근거·누적 복구·시간·정리 필드를 확인하고, 누락을 보완할 수 없으면 그 한계를 최종 보고에 남긴다.
- runtime 출처는 메인이 직접 확인했거나 전달받은 생성 응답·작업 화면·실행 로그의 위치를 기록한다. 역할 파일만 확인했다면 `verification_source`에 그 한계를 쓰고 `resolved`·`resolved_effort`는 `확인 불가`다. 실제 계정·토큰·기기 고유 식별자·원문 로그는 넣지 않는다.
- 반환 JSON은 로컬 실행 근거로 보존할 수 있다. 최종 공유물은 기존 `report.md`와 선별한 `assets/`이며, 반환 JSON이나 중간 캡처를 보고용 첨부로 일괄 복사하지 않는다.

## 신모델 회귀 평가

모델·effort를 새 기본값으로 자동 채택하거나 정확도·속도 개선을 주장하기 전에 평가한다. 사용자가 승인한 설정 갱신도 실제 평가가 없으면 `가용성·정확도·속도 미검증`으로 기록한다. 형식 검사·문서 리뷰·합성 관찰 사례의 평가는 실제 모델과 실기기의 실행 증거를 대신하지 않는다.

| 회귀 사례 | 확인할 행동 |
|---|---|
| 탭 수락 후 화면 전환 실패 | 조작 수락과 상태 관찰을 구분하고 기대값을 바꾸지 않음 |
| 접근성 트리와 스크린샷 불일치 | 오래된 ref로 추측 조작하지 않고 현재 근거 재확인·필요시 메인 반환 |
| 변경 요청이 부분 반영됨 | 반영 상태를 먼저 확인하고 중복 요청을 재전송하지 않음 |
| 이전 빌드 캡처가 최종 근거에 섞임 | 대상·빌드·관찰 시점을 대조하고 마지막 변경 빌드의 재검증 요구 |
| 화면·SDK 콜백은 성공이나 서버 저장 불일치 | 재조회 등 실제 반영 근거를 확인하고 화면 성공으로 통과시키지 않음 |
| 요청 모델과 실행 모델이 다르거나 메타데이터가 없음 | 요청값과 실제값을 구분하고 미확인 값을 추정하지 않음 |

동일 기기·앱 빌드·시작 상태·시나리오·캡처 설정에서 `현재 방식 → 같은 모델의 구간 실행 → 후보 모델의 구간 실행`을 비교한다. 최초 연결 비용과 준비된 세션의 실행 비용을 분리하고 순서·발열·캐시 영향을 기록한다. 메인은 각 후보의 동일 항목을 원본 근거로 판정한다.

항목별 판정 정확도·잘못된 통과·중복 변경 요청·근거 누락·승격·재시도와, 담당자 생성·위임·메인 복귀·원본 근거 검토를 포함한 전체 시간 및 표본 수·중앙값·범위를 함께 남긴다. 토큰·비용·모델 호출 시간은 실제 제공된 값만 쓰고 도구 시간의 나머지를 전부 추론 시간으로 계산하지 않는다. 알려진 실패를 통과시키거나 중복 변경 요청을 실행한 후보는 채택하지 않는다. 나머지 채택 기준은 프로젝트 QA 요구와 현재 방식의 결과에 맞추고, 더 빠르다는 가정이나 임의의 공통 합격률로 대신하지 않는다.

## 공식 근거

- [Codex subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents): 모델 상속과 standalone 역할 계약
- [Claude subagents](https://code.claude.com/docs/en/sub-agents), [플러그인 구성요소](https://code.claude.com/docs/en/plugins/components): 사전 로드·플러그인 역할·실행 도구 계약

문서 대조 기준일: 2026-10-22. 설치 호스트에서 지원되지 않는 기능은 메인의 직접 실행으로 대체하며 실기기 실행·속도 개선을 주장하지 않는다.
