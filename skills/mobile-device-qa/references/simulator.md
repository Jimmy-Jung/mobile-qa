# iOS Simulator QA와 디자인 검수

- Author: JunyoungJung
- Created: 2026-10-27
- Updated: 2026-10-27

Simulator에서 기능 QA·디자인 검수·리포트를 요청받았을 때 읽는다. **`agent-device` 없이도 실행하는 지원 경로**다. `simctl`로 대상·앱·캡처를 관리하고 기존 UI 테스트나 사용 가능한 화면 제어 도구로 조작한다. 조작 도구가 없어도 현재 화면의 캡처·디자인 비교·Markdown 보고를 진행하고 필요한 화면 이동만 사용자에게 요청한다. Simulator 근거를 실기기 실행으로 보고하지 않는다.

## 대상과 준비

1. 공통 저장소 설정과 프로젝트·호스트의 산출물 정책을 적용한다. 플러그인 루트에서 `python3 -B scripts/doctor.py --simulator --project-dir "$PROJECT_DIR"`로 패키지·선택 저장소·`simctl`·이미지 비교 의존성을 확인한다. 이 모드의 `ready`는 캡처·비교 도구 준비이며 UI 제어·앱 실행·로그인 성공을 뜻하지 않는다. 기본 doctor의 `agent-device` 미설치를 Simulator QA 금지로 해석하지 않는다.
2. 현재 Xcode와 `DEVELOPER_DIR`, 프로젝트 deployment target·지원 runtime·Simulator용 앱 산출물을 확인한다. 새 빌드가 필요하면 프로젝트의 기존 빌드 절차와 출력 정책을 따른다. 실기기용 `.app`을 Simulator에 설치하지 않는다. 선택한 Xcode의 다음 명령으로 대상 목록을 확인한다.

   ```bash
   xcodebuild -version
   xcrun --find simctl
   xcrun simctl list devices available --json
   xcrun simctl list runtimes --json
   ```

3. 요청한 모델·runtime과 일치하는 **정확한 UDID**를 `SIMULATOR_UDID`로 고정한다. 여러 대상에서 임의로 선택하는 `booted` 별칭을 사용하지 않는다. 기존 부팅 대상은 재사용하고 종료된 대상만 `xcrun simctl bootstatus "$SIMULATOR_UDID" -b`로 부팅·준비 확인한다. 다른 작업의 Simulator를 종료·초기화하거나 새 runtime을 자동 설치하지 않는다.
4. `xcrun simctl appinfo "$SIMULATOR_UDID" "$APP_ID"`로 설치 앱을 확인한다. 필요하면 `xcrun simctl get_app_container "$SIMULATOR_UDID" "$APP_ID" app`으로 **앱 번들만** 찾아 Info.plist·실행 파일과 준비한 앱의 버전·해시를 대조한다. 계정·토큰이 있을 수 있는 data 컨테이너를 일반 출력으로 읽지 않는다. 설치가 필요한 승인 범위에서는 `simctl install`로 Simulator 산출물을 설치하고 다시 대조하며 선행 uninstall·erase로 기존 자료를 지우지 않는다.
5. `xcrun simctl launch "$SIMULATOR_UDID" "$APP_ID"` 뒤 실제 화면을 확인한다. 명령 수락만으로 기대 화면 도착을 통과 처리하지 않는다. 대상 종류(`simulator`)·모델·runtime·앱 빌드·조작/캡처 도구를 실행 문맥과 리포트에 남긴다.

## 조작 경로 선택

기본 경로는 `simctl`과 설치된 `xcui`/AXe·CUA다. 기존 XCUITest는 필요한 상태 검증·회귀 구간에 추가한다. 현재 프로젝트·호스트에서 사용할 수 있는 경로를 선택하고 같은 대상의 입력을 직렬 실행한다. 전체 스택의 선택 기준은 [도구 선택 상세](tool-selection.md)를 따른다.

| 사용 가능한 경로 | 실행과 확인 |
|---|---|
| 필요할 때 기존 프로젝트 XCUITest·UI 테스트 | 현재 Simulator destination과 검증된 테스트 진입점을 사용한다. 상태 조건·실제 assertion·실행 결과를 확인하고 로그·xcresult는 검증한 출력 경로에 둔다. |
| 호스트의 Computer Use/CUA | 실제 Simulator 또는 해당 Xcode의 Device Hub 앱·창을 확인하고 현재 화면을 조회해 조작한다. 회전·화면 전환 후 좌표를 다시 확인하고 기대 상태 도착을 별도 관찰한다. GUI 앱 이름을 고정하거나 CUA 도구가 있다고 가정하지 않는다. |
| 이미 설치된 `xcui`/AXe/`idb` 또는 Simulator 스킬 | 호스트가 제공한 경로·실행 권한·`--help`와 대상 지원을 확인한 뒤 정확한 UDID로 사용한다. 외부 스킬의 축소 스크린샷은 탐색용으로만 쓰고 디자인 비교에는 원본 PNG를 확보한다. 설치·업데이트는 승인된 준비 범위를 따른다. |
| 자동 UI 조작 경로 없음 | 필요한 화면 이동만 사용자에게 요청하고 `simctl` 캡처·디자인 비교·리포트를 계속한다. 직접 관찰하지 않은 조작·동작은 사용자 관찰과 구분하며 자동화 통과로 만들지 않는다. |

`simctl` 자체는 일반적인 탭·텍스트 입력 도구가 아니다. 존재하지 않는 `simctl tap/type`을 만들거나 모든 QA에 `agent-device` runner·기기 서명을 요구하지 않는다. 기존 deep link·시험 hook은 프로젝트가 정의하고 승인한 구간에서만 사용하며 생략한 UI 흐름을 실행했다고 보고하지 않는다. 고정 sleep 대신 선택한 도구의 상태 대기·assertion과 현재 화면 관찰을 사용한다.

## 로그인

[프로젝트별 인증](authentication.md)의 계정·환경·비밀 입력·정리 계약을 적용한다. 정상 세션을 우선 사용한다. 전용 자동 입력에는 `build_ios_input.py --simulator`와 `build_helper.py --simulator-only`로 준비한 별도 runner를 사용하며, `login`이 정확한 Booted UUID를 확인하고 인증·암호화한 loopback 통신으로 입력한다. 기존 실기기 계정의 Simulator 사용 승인이 있으면 원문을 노출하지 않는 `register --from-account`로 Simulator 전용 별칭을 저장한다. 새 계정을 현재 채팅에 제공했다면 표준 입력 등록을 사용한다. 옵션 화면의 환경 선택은 검토된 `environment_navigation`으로 확인한다. 다른 검증된 보호 인증 경로나 필요한 직접 앱 입력도 사용할 수 있다. 일반 UI 테스트·`xcui type`·`idb`·CUA·launch arguments·클립보드에 저장 계정 원문을 넣지 않는다.

직접 입력 동안 캡처·녹화·화면 트리 조회를 멈춘다. 입력 종료·잔류 입력 정리로 조회 재개 조건을 확인한 다음 인증 후 화면·실제 환경을 대조하고 원래 시나리오로 돌아간다. 로그인 입력을 기다리더라도 로그인 없이 가능한 디자인 화면·독립 QA·확보한 근거의 보고는 계속한다. 결과 불명확한 제출을 다른 경로로 반복하지 않는다.

## 원본 캡처·동작 증거

아래 출력 변수는 **검증한 선택 저장소 안의 대상·빌드·체크포인트별 새 파일 경로**다. `SIM_SCREENSHOT`은 `.png`, `SIM_VIDEO`는 `.mov`, `SIM_GIF`는 `.gif`로 정하고 녹화·변환 로그도 같은 작업 폴더에 둔다. 기록할 화면에 비밀 입력이 남지 않았는지 먼저 확인한다.

```bash
xcrun simctl io "$SIMULATOR_UDID" screenshot --type=png "$SIM_SCREENSHOT"
```

PNG의 실제 픽셀 크기·앱 화면·촬영 시점·SHA-256을 확인한다. Simulator 창의 확대율이나 CUA 화면 캡처 크기를 native pixel scale로 추정하지 않는다. 앱이 제공하는 논리 크기와 PNG 크기 등 확인한 근거로 pt·배율을 연결한다.

대표 동작을 녹화할 때는 관리되는 실행 세션에서 다음 명령을 시작하고 **자신이 시작한 녹화의 PID/세션**을 보관한다. 녹화 시작 확인 후 조작하고 그 프로세스에만 SIGINT를 보내며 종료와 영상 파일 확정까지 확인한다. 다른 녹화를 `killall`로 종료하지 않는다.

```bash
xcrun simctl io "$SIMULATOR_UDID" recordVideo --codec=h264 "$SIM_VIDEO" > "$SIM_RECORD_LOG" 2>&1
```

기존 `ffmpeg`가 있으면 확정된 영상에서 표시용 GIF를 만든다. 아래 축소는 리포트 GIF에만 적용하고 디자인 비교 PNG와 원본 영상은 보존한다.

```bash
ffmpeg -n -i "$SIM_VIDEO" -vf "fps=10,scale=640:-1:flags=lanczos" -loop 0 "$SIM_GIF" > "$SIM_GIF_LOG" 2>&1
```

변환 도구가 없거나 녹화할 수 없으면 확보한 PNG·원본 영상으로 리포트를 작성하고 GIF 미확보 이유를 적는다. 정지 이미지 반복으로 조작 GIF를 대신하지 않는다. 캡처·GIF 일부 실패만으로 리포트 생성을 취소하지 않는다.

## 디자인 비교와 보고

[디자인 검수](design-review.md)와 [Markdown 보고](reporting.md)를 그대로 적용한다. 기준 앱과 후보는 가능하면 같은 Simulator·runtime·방향·논리 크기·배율·테마·글꼴·데이터 상태에서 순차 촬영한다. Figma는 정확한 frame을 같은 논리 크기·배율로 export한다. 실기기와 Simulator를 함께 비교하면 대상 종류와 렌더링 조건 차이를 표시하고 실기기 동등성으로 판정하지 않는다.

기존 `scripts/compare_screenshots.py`로 원본 PNG의 25/50/75% 겹침·diff·불일치 mask·수치를 생성한다. 원본 크기나 색·화면 조건이 다르면 자동 resize로 맞추지 않고 재확보한다. UI 조작 도구·`agent-device`·실기기 연결은 이 이미지 비교의 필수 조건이 아니다.

`report.md`와 상대 경로의 `assets/`에 기준·최초 수정 전/최종 수정 후·겹침·차이·대표 GIF 또는 미확보 사유를 담는다. 각 항목의 실행 도구·기대/관찰·판정·빌드와 `Simulator에서 확인`한 범위를 기록한다. 실행하지 못한 항목도 남기고 실제 앱 실행·사용자 관찰·이미지 비교·소스 확인을 구분한다. 카메라·DRM·물리 오디오·실제 APNs/FCM 등 실기기에서 확인해야 하는 항목만 별도 미검증으로 두며 전체 Simulator QA를 차단하지 않는다.

종료 때 이 작업의 녹화·테스트 프로세스를 정리하고 자신이 변경한 설정을 복원한다. Simulator 종료는 소유권과 프로젝트·호스트 지침을 따르며 기존 사용자의 세션·앱 자료·runtime·캐시는 자동 삭제하지 않는다.

명령 형식은 2026-10-27의 Xcode 26.6 `simctl help list/bootstatus/appinfo/get_app_container/io`로 확인했다. 다른 Xcode에서는 필요한 명령의 현재 도움말을 대조하며 이 문서만으로 해당 앱의 실행 성공을 주장하지 않는다.
