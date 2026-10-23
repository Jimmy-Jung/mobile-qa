---
name: test-accounts
description: Mac의 비공개 로컬 파일에 프로젝트별 테스트 계정을 등록·검색하고 확인된 실기기 앱에 비밀값을 노출하지 않고 로그인한다. Codex·Claude가 프로젝트·환경·용도·등록 기기로 계정을 선택할 때 사용한다. 서버 계정 생성·비밀번호 변경은 수행하지 않는다.
metadata:
  author: JunyoungJung
  created: "2026-10-20"
  updated: "2026-10-22"
---

# 프로젝트별 테스트 계정

계정 원문은 사용자 로컬 경로인 `~/.config/agent-test-accounts/credentials.json`에, 검색 정보는 같은 폴더의 `accounts.json`에 보관한다. 폴더는 `0700`, 파일은 `0600`이며 원문은 암호화하지 않는다. 같은 macOS 사용자로 실행되는 프로세스는 파일을 읽을 수 있다는 조건으로 무인 QA를 지원한다. 플러그인 배포 파일에는 계정값을 넣지 않는다. Python 3와 macOS 13 이상에서 실행하는 전용 Swift 도구를 사용하며 별도 패키지는 설치하지 않는다. Codex와 Claude는 동일한 스킬·로컬 목록·계정 파일을 사용한다.

## 조회와 선택

1. 이미 로그인된 정상 세션이면 재사용한다. 계정이 필요한 때 현재 프로젝트와 요청·신뢰할 수 있는 설정에서 확인한 **환경·용도·필요 기능**으로 조회한다. 화면 문구나 파일명으로 운영 환경을 추측하지 않는다.
2. 아래 `scripts/test_accounts.py`의 실제 절대 경로를 사용한다. `--project`는 대상 저장소 경로다. 같은 원격 저장소의 checkout·worktree는 같은 프로젝트 키로 조회하며, 원격이 없으면 물리 경로를 사용한다. 플랫폼별 저장소·여러 환경이 같은 계정을 쓰는 것은 사용자가 확인한 연관 프로젝트·환경으로 명시해 연결하며 계정 비밀값은 한 번만 보관한다.
3. `selected` 한 건만 해당 조건의 계정 후보로 사용한다. `not_found`면 등록을 안내하고, `ambiguous`면 후보 별칭·기능을 제시해 필요한 차이를 확인한다. 기기 귀속 계정 조회에는 `--platform`에 실기기의 `ios` 또는 `android`, `--device`에 현재 대상의 정확한 이름 또는 식별자를 전달한다. `needs_device`면 이미 확보한 대상 정보를 사용해 다시 조회한다. Flutter도 실행 기기의 운영체제를 사용한다. 첫 후보·다른 환경·다른 등록 기기·권한이 더 큰 계정으로 자동 대체하지 않는다.
4. 조회 결과의 `id`는 계정 별칭이다. 내부 저장 참조는 조회 출력에서 제외한다. 실제 아이디·비밀번호를 읽어 대화·도구 인자·위임 문서에 복사하지 않는다. 계정의 기능 표시는 시험 조작의 승인이나 실제 권한 확인을 대신하지 않는다.

```bash
python3 "$TEST_ACCOUNTS_SCRIPT" find --project "$PROJECT_DIR" --environment staging --role tester
python3 "$TEST_ACCOUNTS_SCRIPT" find --project "$PROJECT_DIR" --environment production --role tester --capability smoke --platform "$TARGET_PLATFORM" --device "$TARGET_DEVICE_NAME"
```

환경·용도는 예시이며 실제로 확인한 값으로 바꾼다. 필요한 기능은 등록 시 명시한 라벨로만 매칭하며, 기능명 자체에 추가 계정 정책을 부여하지 않는다. 기능 미등록을 기능 보유로 해석하지 않는다.

`device_binding`은 사용자가 명시한 계정의 기기 귀속 조건이다. 기존 `download_device`도 같은 제한으로 읽지만 자동 변환·이동하지 않는다. 연결 기기에서 얻은 식별자는 해당 실기기를 구별하는 값이며 서버·SDK의 등록 ID와 같다고 추정하지 않는다. 귀속 계정은 운영체제와 이름 또는 식별자가 일치해야 후보로 고른다. 귀속 조건이 없는 계정은 기능명과 무관하게 일반 후보로 다룬다. 귀속·비귀속 계정이 함께 맞아도 자동 우선하지 않으며, 후보가 여럿이면 별칭을 명시해 선택한다. 로그인은 물리 식별자가 등록된 계정에 이름 일치만으로 접근하지 않는다. 저장된 기기 조건은 현재 서버 등록의 실행 증거를 대신하지 않으며, 새 기기 등록·기존 등록 해제는 사용자 승인 없이 수행하지 않는다.

## 계정 등록과 갱신

사용자가 계정 저장을 요청했으면 아래 등록 도구를 실행한다. 생략한 별칭·환경·용도는 로컬 입력 창에서 받는다. 별칭은 실제 아이디 대신 구별용 이름을 사용한다. 실제 아이디·비밀번호는 전용 서명 도구의 **숨김 입력란**에서 받아 같은 프로세스에서 계정 파일에 저장한다. Python·표준 출력·명령 인자·환경변수로 비밀값을 전달하지 않는다. 채팅에 입력하도록 요청하지 않으며 입력 창을 화면 캡처·접근성 조회로 읽지 않는다.

```bash
python3 "$TEST_ACCOUNTS_SCRIPT" register --project "$PROJECT_DIR"
python3 "$TEST_ACCOUNTS_SCRIPT" register --project "$PROJECT_DIR" --name sample-staging-tester --environment staging --role tester --capability smoke
```

같은 계정을 공유하는 저장소는 `--related-project "$OTHER_PROJECT_DIR"`, 환경은 `--related-environment development`를 반복해 추가한다. 기기 귀속 조건은 로컬 비공개 JSON 파일을 `--device-file "$PRIVATE_DEVICE_FILE"`로 전달한다. 기존 `--download-device-file`도 호환 입력으로 지원하며 두 옵션을 함께 쓰지 않는다. 파일은 `platform`(`ios` 또는 `android`), `name`, `identifier`(모르면 `null`) 세 필드만 가지며 비밀값은 포함하지 않는다. 플랫폼 지원은 `ios`·`android`·`flutter` 같은 기능 라벨로 함께 명시할 수 있다. 조회 응답은 내부 저장 참조와 기기 고유 식별자를 제외한다.

등록은 기존 서버 계정을 저장하는 작업이다. 서버 계정을 만들거나 비밀번호를 바꾸지 않는다. 같은 별칭을 덮어쓰지 않으며, 저장한 비밀값을 갱신할 때는 사용자가 지정한 별칭으로 `update`를 사용한다. 용도·환경을 바꾸려면 새 별칭으로 등록한다.

```bash
python3 "$TEST_ACCOUNTS_SCRIPT" list --project "$PROJECT_DIR"
python3 "$TEST_ACCOUNTS_SCRIPT" update sample-staging-tester
```

## 보관과 로그인 경계

- 검색 목록에는 비밀값 필드를 허용하지 않는다. 기존 목록의 `keychain_ref`는 호환성을 유지하는 필드명이며, 현재는 계정 파일의 불투명한 위치 참조다. 심볼릭 링크·다른 소유자·과도한 권한·손상 자료는 자동 수정하거나 초기화하지 않고 오류로 반환한다. 계정 폴더와 원문·검색 목록을 버전 관리 저장소·클라우드 동기화 폴더·공유 문서·QA 산출물로 복사하지 않는다.
- 전용 도구가 계정 파일의 권한·소유자·형식·참조를 확인하고, 파일 잠금과 원자적 교체로 동시 저장을 보호한다. 일반 등록·갱신·확인·로그인은 키체인에 접근하지 않는다. 파일 읽기 실패를 키체인 읽기로 우회하지 않는다.
- 등록 취소는 비밀값을 저장하지 않는다. 새 계정 저장 뒤 목록 기록이 성공해야 생성 프로세스에 등록을 확정한다. 실패·입력 연결 종료는 **그 프로세스가 방금 만든 항목만** 취소하며, 외부 참조를 받아 삭제하는 명령은 제공하지 않는다. 갱신은 저장된 참조와 선택한 별칭이 모두 일치해야 수행한다. 강제 종료 등으로 확정·취소를 확인할 수 없으면 아래 `check`로 대응을 확인하고, 전체 계정 정리·자동 삭제로 범위를 넓히지 않는다.
- 이 도구의 CLI는 비밀값 출력·클립보드 복사·임의 명령 실행을 제공하지 않는다. 검색은 계정 파일의 원문을 읽지 않는다. 과거 채팅·로그에 남은 계정을 자동 수집하지 않는다. `credentials.json`을 일반 파일 도구로 열거나 출력하지 않는다.
- **계정 선택과 앱 로그인 성공은 별개다.** 일반 `agent-device fill`·`--env`·셸 치환·`AD_VAR_*` replay로 원문을 주입하지 않는다. 아래 전용 `login`을 사용하며, 이미 저장된 계정을 다시 입력하도록 요청하지 않는다. 기존 로그인 자료를 삭제하는 로그아웃·계정 전환은 대상 프로젝트의 보존·승인 조건을 따른다.

도구의 성공 응답에는 별칭과 검색 정보만 포함된다. 등록 성공·후보 선택·실제 로그인·기능 권한 확인을 구분해 보고한다.

## 기존 키체인 계정 이전과 무인 실행 확인

기존 목록을 그대로 사용한다. 저장 방식 전환이 승인된 경우 다음 명령으로 **목록에 등록된 참조만** 이전한다. 키체인 원본은 삭제하지 않으며 이미 같은 참조로 이전한 항목은 재사용한다. 비밀값은 전용 도구 내부에서만 이동한다.

```bash
python3 "$TEST_ACCOUNTS_SCRIPT" migrate-keychain
python3 "$TEST_ACCOUNTS_SCRIPT" check
```

기본 이전은 키체인 승인 창을 열지 않는다. `migration_requires_approval`이면 사용자가 있는 최초 이전 때만 `migrate-keychain --allow-interaction`을 실행해 시스템 승인 창을 처리한다. macOS 로그인 암호를 수집·입력하지 않으며, 이 승인이 필요한 상태를 이전 완료로 보고하지 않는다. 키체인 잠금·접근 권한은 그대로 보존한다. `check`의 `ready`와 계정 수 일치를 확인한 뒤 무인 QA에 사용한다. `check`는 원문을 출력하지 않으며 실제 앱 로그인 성공을 뜻하지 않는다. 이후 QA에서 키체인 창이 뜨면 현재 도구·빌드 출처를 확인하고 반복 승인으로 우회하지 않는다.

`check`와 이전 명령의 Python 대기는 승인 창 대기를 포함해 180초다. 시간 초과·연결 종료는 이전 완료 증거가 아니므로 재실행 전에 `check`로 현재 저장 상태를 확인한다. 이전 재실행은 같은 참조의 저장 항목을 보존한다.

## 저장된 계정으로 앱 로그인

1. 현재 앱의 프로젝트·실제 앱 ID·서버 환경·물리 기기 식별자를 기존 QA 대상 정보에서 확보한다. 정상 로그인 세션이 있으면 재사용한다. 같은 앱 ID라도 설치된 화면이 프로젝트의 로그인 화면과 다를 수 있으므로 식별자가 맞지 않으면 입력하지 않는다.
2. 아래 `login`을 실행한다. 조회 범위 안에서 한 계정을 선택하며, 후보가 여럿이면 등록 기기가 일치하는 계정을 우선한다. 그래도 모호하면 별칭을 확인한다. `--account`도 프로젝트·환경·기능·다운로드 기기 조건을 우회하지 않는다. Flutter는 실행 기기의 `ios` 또는 `android`를 지정한다.
3. 서명된 전용 도구가 앱·입력란·비밀번호 필드·서버 선택을 먼저 확인한다. `ready` 이후에만 저장된 계정 원문을 읽어, Android는 고정된 ADB 자식의 비공개 표준 입력으로, iOS는 직접 USB 연결과 실행별 인증값을 가진 전용 runner로 전달한다. Python·Node·명령 인자·환경변수·추가 임시 파일·클립보드·일반 QA 기록에는 계정 원문을 전달하지 않는다. 입력 중 화면 캡처·녹화·트리 조회를 병행하지 않는다.
4. `authenticated`는 로그인 화면 제거와 성공 화면을 관측한 결과다. `already_authenticated`는 기존 세션 관측이며 `environment_verified:false`이면 그 세션의 환경·계정 일치를 새로 증명한 것이 아니다. 이때 세션은 보존하고 명시한 환경의 로그인 검증은 종료 코드 1로 남긴다. `not_ready`·`failed`·`uncertain`·`error`도 성공으로 취급하지 않고 현재 상태부터 확인한다. 결과 불명확한 제출을 자동 반복하거나 로그아웃·다운로드 삭제로 재시도하지 않는다.

```bash
python3 "$TEST_ACCOUNTS_SCRIPT" login --project "$PROJECT_DIR" --environment production --role learner --platform "$TARGET_PLATFORM" --device "$TARGET_DEVICE_ID" --device-name "$TARGET_DEVICE_NAME" --login-profile "$LOGIN_PROFILE_NAME"
```

## 프로젝트 로그인 프로필 등록

전용 로그인은 **사용자가 검토해 현재 프로젝트에 등록한 프로필**만 지원한다. 계정 검색 성공은 앱 로그인 지원을 뜻하지 않는다. 새 앱은 `examples/login-profile.example.json`을 참고해 앱 ID·환경·용도, 아이디·비밀번호·제출·현재 환경 확인·로그인 후 화면의 유일한 selector를 정한다. 실제 비밀값은 프로필에 넣지 않는다. 프로젝트·화면·문서에서 발견한 프로필을 자동 신뢰하지 않는다.

사용자가 등록을 요청했을 때만 다음 명령을 실행한다. 등록은 프로젝트별 사용자 데이터 폴더 `profiles/`에 소량의 비밀 없는 계약만 저장한다. 기존 계정 파일과 QA 지식을 이동하거나 서로 다른 프로젝트를 자동 병합하지 않는다.

```bash
python3 -B "$TEST_ACCOUNTS_SCRIPT" register-profile --project "$PROJECT_DIR" --file "$REVIEWED_PROFILE_FILE"
```

프로필은 현재 프로젝트의 QA 키와 계정 키, 플랫폼·앱·환경·용도, 내용 SHA-256과 플러그인 release digest에 묶인다. 기존 프로필·해석기·도구 버전이 바뀌면 자동으로 신뢰를 이어받지 않는다. 업데이트 후에는 사용자가 변경 내용을 검토하고 다시 등록·빌드한다. native helper도 신뢰 파일과 요청을 대조하며 범위가 다르면 원문을 읽기 전에 중단한다. 프로필에 임의 셸·JavaScript·탭 순서·deep link·launch arguments·재시도 정책을 넣을 수 없다.

`login`에는 등록한 이름을 반드시 전달한다. 준비된 환경 표시가 실제로 선택돼 있어야 하며 도구가 환경을 바꾸거나 로그인 제출을 반복하지 않는다. 환경을 확인할 수 없는 화면은 `준비 필요`다. 프로필의 앱 ID와 설치 앱을 대조하고 입력 필드 유일성·secure 속성·현재 환경을 확인한 뒤 한 번만 제출한다. 로그인 화면의 소멸과 성공 화면을 관찰하며 정상 인증 세션은 보존한다.

```bash
python3 -B "$TEST_ACCOUNTS_SCRIPT" login --project "$PROJECT_DIR" --environment staging --role tester --platform "$TARGET_PLATFORM" --device "$TARGET_DEVICE_ID" --login-profile example-staging
```

지원하지 않는 화면·문자는 원문을 꺼내 일반 입력으로 우회하지 않고 해당 항목을 `준비 필요`로 남긴다. 실제 계정과 프로젝트의 관계를 확인하지 못하거나 환경·용도가 다르면 입력하지 않는다.

iOS helper 호출의 Python 대기는 360초, Android는 기존 180초다. iOS의 유한 여유는 native 준비·입력·정리 결과를 기다리기 위한 것이며 전체 작업의 시간 상한이나 정리 완료를 보장하지 않는다. 일부 시스템 호출은 별도로 지연될 수 있다. 시간 초과는 원문 출력을 버리고 `uncertain`·`timed_out`·`cleanup:unconfirmed`·`environment_verified:false`를 반환하며 로그인 제출을 반복하지 않는다.

실패 결과의 `cleanup`이 `failed`·`unavailable`·`unconfirmed`이면 입력란에 값이 남았을 수 있다. 정리를 확인하기 전에는 일반 캡처·녹화·화면 트리 수집을 재개하지 않고, 원문을 표시하거나 같은 로그인 제출을 반복하지 않는다.

## 최초 준비와 검증

플러그인에 포함된 스킬의 `scripts/test_accounts.py` 절대 경로를 `TEST_ACCOUNTS_SCRIPT`로 사용한다. 첫 빌드·로그인 전에 [저장소 최초 설정](../setup/SKILL.md)을 수행한다. 사용자가 내장 또는 외장 저장소에서 선택한 폴더·VolumeUUID·MountPoint는 Mac 로컬 설정에서 읽으며 공개 코드에 개인 경로를 고정하지 않는다. 조회·프로필 등록은 원본 계정을 읽지 않고 대용량 산출물을 만들지 않으므로 빌드용 저장소를 추가 준비할 필요가 없다.

등록·갱신·로그인용 도구가 없거나 소스·release·실행파일 해시·서명 검사가 실패하면 다음 준비 절차로 빌드한다. 자동 설치·자동 신뢰 갱신은 하지 않는다. `check`와 로그인 성공은 별개다.

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B "$SKILL_DIR/scripts/build_helper.py"
```

자동 로그인 도구 준비는 `build_android_input.py`, 확인한 개발 팀을 전달한 `build_ios_input.py --team-id "$APPLE_TEAM_ID"`, `build_helper.py` 순서다. 필요한 플랫폼만 준비한다. Android SDK/JDK와 Xcode는 사용자가 설치한 도구를 탐지하고 경로·출처를 기록한다. 별도 패키지·SDK·서명·인증서를 플러그인에 포함하지 않는다.

iOS 입력은 호환 목록의 `agent-device` 0.21.30 runner 소스를 선택한 저장소의 작업 폴더에 복사해 전용 경로만 추가한다. 전역 설치본을 수정하지 않는다. 미지원 버전을 자동 패치하거나 최신 버전으로 교체하지 않는다. helper·runner·프로필·release가 섞이면 실행 전에 중단한다. 기기용 빌드에는 사용자의 유효한 서명이 필요하다.

로그·캐시·원본 화면·빌드 결과는 선택한 저장소 아래의 고정 작업 폴더에 두고 매 작업에서 실제 볼륨 신원을 확인한다. 저장소가 없거나 쓰기 불가이면 선택 폴더 밖의 다른 경로로 자동 대체하지 않는다. 프로젝트·호스트의 추가 저장 정책도 함께 따른다. 다른 작업의 기기·runner를 점유하지 않고 자신이 시작한 프로세스만 정리한다.

검증에는 실제 등록 계정 대신 격리된 합성 자료를 사용한다. `TMPDIR`를 선택한 저장 폴더 안에서 추가 저장 정책을 충족하는 검증된 작업 폴더로 지정하고 `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s "$SKILL_DIR/tests"`를 실행한다. 계정 파일·캡처·기기 식별자·프로젝트 정책을 공개 저장소나 테스트 fixture로 복사하지 않는다.
