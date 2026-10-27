# Simulator·Emulator와 반복 회귀 도구 선택

- Author: JunyoungJung
- Created: 2026-10-27
- Updated: 2026-10-27

[스킬 본문](../SKILL.md)의 역할별 표를 기본 기준으로 적용한다. 프로젝트가 보유한 테스트와 현재 호스트에 설치된 도구를 우선하며, 실제 실행 도구·버전·대상·검증 범위를 리포트와 흐름 사용 계약에 남긴다. 도구 지원 문서는 현재 앱의 실행·로그인·통과 증거와 구분한다.

## iOS Simulator

탐색·캡처는 `simctl`과 설치된 `xcui`/AXe 또는 호스트가 제공한 CUA를 사용한다. 기존 XCUITest가 있으면 상태 확인·회귀에 재사용한다. 정확한 UDID·Simulator 빌드·현재 Xcode 확인과 캡처 절차는 [Simulator QA](simulator.md)를 따른다. UI 제어 도구가 없으면 현재 화면의 캡처·디자인 비교·보고를 진행한다.

`simctl` 서비스가 선택 저장소의 파일 쓰기를 거부하면 같은 저장소에 저장할 수 있는 설치된 `xcui`/AXe 캡처 경로를 확인한다. 저장소 정책을 완화하거나 다른 볼륨으로 자동 우회하지 않는다. CUA의 창 캡처와 디자인 비교용 원본 PNG의 픽셀 크기를 구분한다.

## Android Emulator

탐색·캡처의 기본 경로는 [공식 Android CLI](https://developer.android.com/tools/agents/android-cli)와 ADB다. 현재 설치된 CLI의 도움말에서 기기·화면·입력 명령을 확인하고, `adb devices -l`로 실제 Emulator serial을 고정한다. `booted`나 첫 기기를 임의로 선택하지 않는다. 기존 Espresso·Compose UI 등 Android UI 테스트가 있으면 해당 진입점과 실행 범위를 재사용한다.

Android CLI가 준비되지 않았으면 ADB와 기존 프로젝트 도구로 가능한 설치 상태 확인·캡처·테스트·디자인 보고를 이어간다. SDK·Emulator 이미지의 신규 설치·업데이트와 새 AVD 생성은 현재 작업에 필요한 경우에만 진행한다. 실기기용 기본 doctor의 `agent-device` 미설치를 Emulator QA 금지로 해석하지 않는다.

비밀 입력 정리를 확인한 뒤 검증한 선택 저장소에 원본 PNG를 저장한다.

```bash
adb devices -l
adb -s "$EMULATOR_SERIAL" exec-out screencap -p > "$EMULATOR_SCREENSHOT"
```

캡처의 실제 픽셀 크기·앱 화면·촬영 시점·해시를 확인한다. 디바이스 serial, 앱 패키지·빌드, Android 버전, 조작 도구를 함께 기록한다. 명령 수락과 실제 화면 전환을 각각 확인하며 기존 앱 데이터·다른 작업의 Emulator를 보존한다.

## iOS·Android·Flutter 반복 회귀

기존 XCUITest·Android UI 테스트·Flutter widget/golden/integration 테스트와 저장된 흐름부터 검색한다. 같은 검증을 새 프레임워크로 다시 작성하지 않는다. 여러 스택에 공통인 사용자 흐름이 필요할 때 [Maestro](https://docs.maestro.dev/get-started/supported-platform/flutter)를 선택하고, 해당 프로젝트의 빌드·기기·환경·시작/종료 조건과 assertion을 명시한다.

Maestro CLI와 `agent-device --maestro` 호환 실행은 별도 실행 경로다. 호환 부분집합에서의 성공을 Maestro CLI 전체 문법이나 다른 플랫폼의 성공으로 확장하지 않는다. Flutter에서는 OS에 노출된 Semantics label·identifier를 확인한다. Dart `Key`를 OS 접근성 selector로 간주하지 않는다. 실행 도구·버전이 다른 흐름의 상태·재사용 판정은 [반복 동작 관리](reusable-flows.md)를 따른다.

## Flutter와 네이티브 경계

Flutter 위젯과 앱 내부 동작은 기존 Flutter 테스트를 먼저 사용한다. 권한창·알림·네이티브 화면·WebView/OAuth 등 OS와 앱의 경계를 실제로 검증해야 할 때 [Patrol](https://patrol.leancode.co/documentation/native/overview)을 추가 후보로 선택한다. 현재 버전·플랫폼이 지원하는 기능과 프로젝트 의존성·빌드 준비를 확인한다. Patrol 도입이나 프로젝트 의존성 변경은 해당 경계 검증이 필요한 범위에서 수행한다.

## 인증·디자인·보고

도구가 바뀌어도 [인증](authentication.md)의 계정 범위·보호 입력·환경 확인·정리 기준은 유지한다. 저장된 계정 원문을 일반 CLI 인자·환경변수·Maestro YAML·Patrol 테스트 코드·CUA 입력으로 옮기지 않는다.

원본 PNG 비교와 Markdown·GIF 보고는 조작 도구에서 독립적으로 유지한다. 동일 화면 조건·픽셀 크기의 원본을 [디자인 검수](design-review.md)에 따라 비교하고 [보고 형식](reporting.md)에 따라 결과를 남긴다. 조작 도구 미준비로 수행하지 못한 항목과 이미 수행한 이미지 비교·보고를 구분한다.
