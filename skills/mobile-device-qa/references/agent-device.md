# agent-device의 runner·캐시 복구

- Author: JunyoungJung
- Created: 2026-10-22
- Updated: 2026-10-22

정상 연결에서는 읽지 않는다. 지정한 runner 캐시·서명·캡처 파일 복사 오류가 있을 때만 읽고 현재 설치 버전의 도움말과 대조한다. 공통 [복구 제한](../SKILL.md#실패와-자동-복구)과 [제어권 인계](../SKILL.md#기기-점유와-제어권-인계)는 유지한다.

- 지정한 Runner 캐시에서 `Refusing to clean AGENT_DEVICE_IOS_RUNNER_DERIVED_PATH automatically`가 나오면 진단의 캐시 불일치 필드를 먼저 확인한다. 자동 삭제 방어를 해제하지 않는다. 유효한 기존 산출물은 서명·대상 기기 프로파일을 검증하고, 설치 버전이 지원하는 `--ios-xctestrun-file`, `--ios-xctest-derived-data-path`, `--ios-xctest-env-dir`로 명시해 재사용할 수 있다. 이 옵션은 후속 snapshot·screenshot 등 Runner 명령에도 전달한다. `.xctestrun`이 `__TESTROOT__` 상대 경로를 쓰면 env overlay도 원본과 같은 `Build/Products`에 생성해 test product 경로를 유지한다. 서명 설정은 CLI가 읽는 `AGENT_DEVICE_IOS_TEAM_ID`·`AGENT_DEVICE_IOS_BUNDLE_ID`를 사용하고, 프로젝트 기본값을 바꾸기 전에 현재 버전의 도움말을 확인한다. 기존 산출물을 쓰면 bundle ID 환경변수를 실제 host/test 앱의 식별자에 맞춘다. snapshot은 되지만 screenshot 파일 복사가 `ContainerLookupErrorDomain`으로 실패하면 앱 컨테이너 식별자 불일치를 확인한다. daemon은 시작 시 환경을 보유하므로 설정 변경 후에는 해당 상태 경로의 자신이 소유한 daemon만 공식 종료 명령으로 종료하고 새 환경에서 다시 시작한다.

최초 runner 준비·새 빌드가 필요하면 별도 준비 작업으로 구분한다. 다른 작업의 daemon·runner를 중지하지 않으며 선택한 저장소의 볼륨·출력 경계 검증과 기존 캐시 보존 규칙을 유지한다.
