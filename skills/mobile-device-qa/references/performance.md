# 실기기 성능 계측

Author: JunyoungJung · Created: 2026-09-09

Updated: 2026-09-10

성능 검증을 요청했을 때만 사용한다. 준비된 도구·앱·테스트 타깃을 재사용하며, 환경 설치·새 빌드·배포가 필요하면 어떤 준비가 필요한지 구분한다. 이 문서를 읽거나 trace 파일을 만드는 것만으로 성능 검증이 완료되지는 않는다.

## 측정 계약과 도구 선택

먼저 대상 프로젝트의 배포 타깃·실기기 OS·렌더링 경로를 확인한다. 같은 플랫폼에서도 UI 애니메이션, Flutter 프레임, 영상 디코딩·표시는 서로 다른 계측 대상이다.

| 목적 | 도구와 지표 |
|---|---|
| iOS UI 끊김 확인 | Instruments Animation Hitches 또는 XCTest 애니메이션 지표: hitch 수·시간·비율, 프레임 수 |
| iOS 끊김 원인 분석 | Time Profiler의 CPU 호출 스택, 필요한 경우 System Trace의 스케줄링·대기 |
| Android View·Compose UI 회귀 | Macrobenchmark `FrameTimingMetric`: `frameDurationCpuMs`, 지원되는 기기의 `frameOverrunMs` |
| Android 끊김 원인 분석 | Perfetto system trace와 지원되는 대상의 FrameTimeline |
| Flutter UI·raster 성능 | 실기기의 profile 빌드와 Flutter DevTools Performance 타임라인 |
| 초기화·첫 화면 지연 | 앱 또는 SDK의 의미 있는 시작·완료 지점에 대응하는 signpost/trace와 이벤트 시간 |

검증 전에 동작·시작 화면·측정 시작/종료 이벤트·기준 빌드·비교 빌드를 정한다. 기기, OS, 빌드 모드, 콘텐츠, 네트워크, 캐시 상태, 화면 주사율 조건, 전원·온도 상태를 기록하고 비교 시 맞춘다. cold 초기화와 warm 반복을 섞지 않는다.

녹화·스크린샷 폴링·전체 화면 트리 조회를 측정 구간에서 끈다. 장면 확인용 녹화는 별도 실행으로 남긴다. 자동화 연결·LLM 대기·화면 준비 시간은 앱의 성능 측정 구간 밖에 둔다. XCTest/Macrobenchmark가 조작을 담당하는 동안 agent-device로 같은 기기를 동시에 조작하지 않는다. 별도 테스트 runner와 충돌하는 자신의 UI 세션은 실행 전에 정리하며, 연결 해제만으로 기기의 runner가 종료됐다고 간주하지 않는다.

한 비교 묶음에서는 같은 UI 제어 방식·동작 시퀀스를 유지한다. 세션 인계·복구가 발생한 실행은 제외 이유와 함께 따로 남긴다. CLI batch 시간과 도구가 보고하는 실행 시간은 자동화 지연 지표이며 앱의 초기화·hitch·영상 프레임 지표를 대신하지 않는다. 계측만을 위해 UI 제어 runner를 추가로 시작하지 말고, 준비된 xctrace/플랫폼 계측 도구를 직접 사용한다.

모든 예시의 `PERF_OUT`은 **선택한 저장 폴더 안에서 추가 저장 정책을 충족하는 검증된 작업별 산출물 디렉터리**이고, 미리 생성돼 있어야 한다. `RUN_ID`는 보존할 결과의 실행 식별자다. 기기·프로세스·테스트 식별자도 실제 조회한 값만 사용한다. 샘플 시간과 반복 횟수는 재현 구간·표본 변동에 맞춰 결정하고 기록하며, 공통 고정값을 임의로 적용하지 않는다.

## iOS: Instruments와 xctrace

### 프레임 지연 수집

1. 측정할 앱을 실제 기기에서 실행하고 프로세스를 확인한다. 성능 비교에는 Release에 맞춘 설정을 사용하고 디버거·coverage·sanitizer 등 측정을 교란하는 진단 설정을 제외한다. 설정 변경이 필요한 경우 프로젝트의 기존 성능 테스트 구성을 사용한다.
2. Instruments에서 해당 **실기기와 앱 프로세스**를 선택하고 `Animation Hitches`로 기록한다. 수집이 시작된 뒤 정한 동작을 실행하고 종료한다. 조작 전후의 긴 유휴 구간을 결과 분모에 넣지 않는다.
3. hitch가 발생한 구간과 표시 지연 시간을 확인한다. CPU 원인을 찾을 때 같은 시나리오를 `Time Profiler`로 재현해 해당 구간의 메인 스레드와 큰 self time 호출을 조사한다. CPU 사용량만으로 프레임 정상 여부를 판정하지 않는다.

CLI로 기록할 때는 먼저 설치된 Xcode의 `xcrun xctrace help record`와 `xcrun xctrace list templates`에서 필요한 옵션·템플릿을 한 번 확인한다. 아래 명령은 실행 중인 앱을 대상으로 하며 `TRACE_WINDOW`에는 재현 동작에 근거한 단위 포함 기간을 넣는다.

```bash
xcrun xctrace record \
  --template 'Animation Hitches' \
  --device "$DEVICE_UDID" \
  --attach "$APP_PID" \
  --time-limit "$TRACE_WINDOW" \
  --output "$PERF_OUT/ios-$RUN_ID.trace" \
  > "$PERF_OUT/ios-$RUN_ID.log" 2>&1
```

이 명령은 수집 중 실행 상태를 유지한다. 제어 도구가 지원하는 실행 세션에서 시작해 수집 시작을 확인하고, 그 사이에 재현 동작을 수행한 뒤 종료 상태를 확인한다. 기록이 끝난 후에 동작을 실행하면 그 동작은 측정되지 않는다. 프로세스 attach가 불가능하면 빈 trace를 성공으로 처리하지 않는다.

기존 agent-device 세션에서 지원되는 버전이라면 아래 시작·종료 명령을 대신 사용할 수 있다. **두 명령 사이에 재현 동작을 실행**하며, xctrace 경로와 중복 수집하지 않는다.

```bash
agent-device perf trace start --session "$QA_SESSION" --kind xctrace \
  --template 'Animation Hitches' --out "$PERF_OUT/ios-$RUN_ID.trace"
# 수집 시작 확인 → 측정할 동작 실행 → 동작 완료 확인
agent-device perf trace stop --session "$QA_SESSION" --kind xctrace \
  --out "$PERF_OUT/ios-$RUN_ID.trace"
```

trace는 Instruments에서 열어 분석하거나, `xcrun xctrace export --input "$TRACE_PATH" --toc` 결과를 선택한 저장 폴더 안의 경로로 저장한 뒤 필요한 테이블만 추출한다. schema 이름·PID·run 번호는 실제 trace에서 확인한다. 파일 존재뿐 아니라 대상 프로세스·측정 구간·프레임 표본을 확인하고, 원문 trace/XML 전체를 대화에 출력하지 않는다.

### XCTest로 반복 회귀 검증

준비된 **성능 UI 테스트 타깃**에서 `XCTOSSignpostMetric.scrollDraggingMetric`, `scrollDecelerationMetric`, `navigationTransitionMetric` 등 실제 동작에 맞는 지표를 선택한다. 일반 reducer 단위 테스트를 UI 성능 테스트로 취급하지 않는다. 사용하는 지표의 SDK availability를 대상 프로젝트에서 확인한다.

- 테스트는 `import XCTest`를 사용한다. UIKit이 제공하는 구간 지표를 우선 쓰고, 커스텀 애니메이션은 해당 구간을 나타내는 animation signpost가 실제로 발생하는지 확인한다. 단순 시간 interval만 기록한 결과에 애니메이션 지표가 있다고 가정하지 않는다.
- `XCTestCase.measure(metrics:options:block:)`에서 대상 동작만 측정한다. 시작 화면을 반복마다 복원하고, `XCTMeasureOptions.invocationOptions = [.manuallyStop]`과 `stopMeasuring()`을 사용해 복원 동작을 측정 구간에서 뺀다. 반대 방향 스와이프가 원래 위치를 정확히 복원한다고 가정하지 않는다.
- 실행 전 agent-device 등 다른 UI 제어 runner의 간섭을 해소한다. 자신의 세션만 정리하고 다른 작업의 세션을 종료하지 않는다.
- Xcode의 성능 결과·xcresult에서 hitch 시간·수·비율과 실제 반복 결과를 읽고 baseline과 비교한다. hitch 비율의 단위는 보통 `ms/s`이며 Android의 percentile 시간과 같은 값으로 취급하지 않는다. [Apple의 측정 절차](https://developer.apple.com/videos/play/wwdc2020/10077/)

실기기용 테스트 바이너리와 `.xctestrun`이 이미 준비된 경우의 실행 예시다. `PERF_TEST`는 실제 타깃/클래스/메서드 선택자다. 빌드가 필요하면 기존 빌드 절차와 프로젝트·호스트의 캐시 저장 정책을 별도 적용한다.

```bash
xcodebuild test-without-building \
  -xctestrun "$XCTESTRUN" \
  -destination "platform=iOS,id=$DEVICE_UDID" \
  -only-testing:"$PERF_TEST" \
  -resultBundlePath "$PERF_OUT/ios-$RUN_ID.xcresult" \
  > "$PERF_OUT/ios-test-$RUN_ID.log" 2>&1
```

## Android: FrameTimingMetric과 Perfetto

### Macrobenchmark 구성과 실행

기존 Macrobenchmark 모듈·테스트를 확인한다. 실제 기기, non-debuggable/profileable 대상 앱, 테스트 APK, 필요한 ProfileInstaller 및 프로젝트에 맞는 AndroidX Benchmark 구성이 준비돼 있어야 한다. 새 모듈·의존성·APK가 필요하면 준비 작업으로 구분한다. 실행 검사를 억제해 debug 빌드나 에뮬레이터 수치를 정식 비교값으로 만들지 않는다. [Macrobenchmark 구성·실행](https://developer.android.com/topic/performance/benchmarking/macrobenchmark-overview)

1. `MacrobenchmarkRule.measureRepeated`의 `metrics`에 `FrameTimingMetric()`을 넣는다. 테스트에서는 `androidx.benchmark.macro.FrameTimingMetric`과 `androidx.benchmark.macro.junit4.MacrobenchmarkRule`을 사용한다.
2. `setupBlock`에서 대상 화면과 데이터를 준비하고, `measureBlock`에서는 UI Automator로 실제 측정할 전환·스크롤만 실행한다. 매 반복의 시작 상태가 같아야 한다.
3. `CompilationMode`, Baseline Profile 적용 여부, `StartupMode`와 반복 횟수를 기준 실행과 비교 실행에서 일치시킨다. 기준 결과 JSON과 iteration별 system trace를 보존한다.
4. 기존 프로젝트 실행 구성을 실제 기기 하나로 제한해 테스트한다. Gradle로 실행하면 사용자 `GRADLE_USER_HOME`뿐 아니라 프로젝트 build·project cache와 JVM 임시 경로도 선택한 저장 폴더와 프로젝트·호스트 정책을 따르는지 먼저 확인한다. `connectedCheck`가 연결된 여러 기기를 모두 실행하도록 방치하지 않는다.

대상 앱과 테스트 APK가 이미 설치된 경우에는 기기를 명시해 instrumentation을 실행할 수 있다. `BENCHMARK_TEST`는 실제 테스트 클래스 또는 `클래스#메서드`, `TEST_RUNNER_COMPONENT`는 설치된 테스트의 `패키지/runner` 값이다.

```bash
adb -s "$DEVICE_SERIAL" shell am instrument -w \
  -e class "$BENCHMARK_TEST" "$TEST_RUNNER_COMPONENT" \
  > "$PERF_OUT/android-test-$RUN_ID.log" 2>&1
```

테스트 로그의 instrumentation 결과와 실행된 테스트 수를 확인한다. `adb` 종료 코드만으로 통과시키지 않는다. 프로젝트 runner/Gradle 플러그인이 제공하는 결과 수집 경로를 사용하거나, 기기에 기록된 실제 JSON·trace 경로를 확인해 `adb -s "$DEVICE_SERIAL" pull`로 `PERF_OUT` 아래에 복사한다. 내부 저장소 접근이 불가능하면 root 등으로 우회하지 말고 수집 제약을 보고한다.

### 지표 해석

- `frameDurationCpuMs`: UI thread와 RenderThread의 CPU 프레임 생성 시간이다. GPU·합성·영상 디코더 전체 시간으로 해석하지 않는다.
- `frameOverrunMs`: 프레임이 주어진 표시 시한을 얼마나 넘겼는지 나타내며 양수는 시한 초과다. **Android 12 / API 31 이상**에서 제공된다. 이전 기기에서는 지원되는 CPU 프레임 지표·trace만 보고하고 overrun은 미지원으로 남긴다.
- P50과 P95/P99 등 제공되는 분포를 같이 본다. 중앙값만 개선되고 느린 프레임 구간은 악화될 수 있다. 수집되지 않은 항목은 0으로 채우지 않는다. [지표 정의](https://developer.android.com/reference/androidx/benchmark/macro/FrameTimingMetric)

### Perfetto로 원인 확인

Macrobenchmark가 만든 iteration trace를 우선 재사용한다. 단발 재현이 필요하고 설치된 agent-device가 지원하면 다음과 같이 수집한다.

```bash
agent-device perf trace start --session "$QA_SESSION" --kind perfetto \
  --out "$PERF_OUT/android-$RUN_ID.perfetto-trace"
# 수집 시작 확인 → 측정할 동작 실행 → 동작 완료 확인
agent-device perf trace stop --session "$QA_SESSION" --kind perfetto \
  --out "$PERF_OUT/android-$RUN_ID.perfetto-trace"
```

해당 명령이 없으면 준비된 Android Studio의 System Trace 또는 기기 System Tracing으로 같은 구간을 수집한다. 결과는 선택한 저장 폴더 안의 경로로 가져와 Android Studio/Perfetto UI에서 로컬 파일로 분석한다. [시스템 추적](https://developer.android.com/topic/performance/tracing/on-device)

지원되는 기기에서 Expected/Actual FrameTimeline, 해당 앱의 UI thread·RenderThread, CPU 스케줄링·GPU·SurfaceFlinger 이벤트를 같은 구간에서 연결해 본다. 늦은 프레임이 앱 작업·GPU 대기·합성 중 어디에서 발생했는지 trace 근거로 설명한다. FrameTimeline은 API 31 이상이며 공식 문서에는 SurfaceView 지원 제한이 있으므로, 트랙이 없다고 영상 프레임이 정상이라고 판단하지 않는다. [FrameTimeline과 제한](https://perfetto.dev/docs/data-sources/frametimeline)

## Flutter와 영상 플레이어

Flutter 위젯은 준비된 실기기 **profile 빌드**에서 DevTools Performance의 UI·raster 타임라인을 확인한다. Android View/RenderThread 지표와 Flutter UI/raster 지표를 동일하게 취급하지 않는다. 프레임 표본이 실제 어느 렌더링 경로를 반영하는지 확인한다. debug 모드 결과나 고정된 60fps 기준을 다른 빌드·주사율 조건에 그대로 적용하지 않는다. [Flutter 성능 계측](https://docs.flutter.dev/perf/ui-performance)

영상 SDK가 별도 surface에 표시하는 영상은 UI 버튼·오버레이의 프레임 지표만으로 디코딩·프레임 드롭·표시 간격을 검증할 수 없다. SDK가 공개하는 dropped/rendered frame·buffering 이벤트, 필요한 미디어/합성 trace 등 실제 확보 가능한 근거를 사용한다. 확보하지 못한 영상 지표는 `미검증`으로 남긴다. 이는 별도 버퍼를 합성하는 렌더링 구조에 따른 적용 범위 구분이다. [Android SurfaceView 구조](https://source.android.com/docs/core/graphics/arch-sv-glsv)

초기화 지연은 요청 수신 → SDK 준비 → 첫 프레임 표시처럼 **실제로 관찰 가능한 구간**을 각각 기록한다. SDK prepared 이벤트가 첫 프레임 표시를 뜻한다고 가정하지 않는다. 브릿지 비용을 비교할 때는 동일 기기의 동등한 빌드 모드·콘텐츠·캐시·네트워크 조건에서 같은 시작/종료 의미로 재현한다. CLI 왕복 시간은 앱의 초기화 시간으로 사용하지 않는다.

## 비교와 판정

- baseline과 변경 빌드는 동일 측정 도구·버전·시나리오·단위로 비교한다. iOS hitch ratio와 Android overrun, `agent-device perf frames`의 요약값과 Macrobenchmark의 percentile을 직접 등치하지 않는다.
- 반복 횟수·수집 시간·유효 프레임 수를 보고한다. 단 한 번의 최고값이나 다른 화면에서 얻은 표본으로 개선을 주장하지 않는다. 순서·발열·캐시 효과가 의심되면 조건을 맞춰 재측정하고 기존 표본도 보존한다.
- 프로젝트의 성능 예산과 반복 측정의 변동폭을 기준으로 판정한다. 근거 없는 공통 합격선이나 임의의 회귀율을 넣지 않는다. 기준값이 없으면 우선 측정 결과를 기준 후보로 보고하며, 기준 없이 성능 통과를 선언하지 않는다.
- `통과/실패`는 합의된 기준과 유효 표본이 있을 때만 사용한다. 표본·필수 지표가 없으면 `미검증`, 도구·권한·빌드가 미준비면 `준비 필요`다. 기준이 없는 유효 측정에는 수치는 제시하되 통과 판정은 미검증으로 구분한다.
- 실패한 자동화 단계나 복구·앱 재실행이 측정 구간에 섞이면 해당 실행을 정상 비교 표본에 합치지 않는다. 실패 이력과 제외 이유를 남긴다.

성능 보고에는 `시나리오/기기·OS/빌드/조건/측정 구간/도구·버전/지표·단위/표본 수/기준값/변경값/절대 차이/판정 근거/trace·JSON·xcresult 경로`를 남긴다. 기준값이 0이면 상대 변화율을 계산하지 않는다. 기능 통과, 시각 검토, 성능 판정은 각자 확인된 근거를 표시한다.

문서 기준: 2026-09-09. agent-device 성능 명령의 설치 버전별 계약은 [공식 Commands](https://oss.callstack.com/agent-device/docs/commands#performance-diagnostics)와 관련 `help`에서 확인한다. 이 가이드의 정적 검증을 실제 기기 계측 완료로 표현하지 않는다.
