---
name: test-accounts
description: 프로젝트별 테스트 계정을 로컬 파일에 저장·조회·갱신한다. 사용자가 알려 준 계정을 저장하거나 AI가 저장된 아이디·비밀번호를 꺼내 앱·브라우저에 로그인할 때 사용한다.
metadata:
  author: JunyoungJung
  created: "2026-10-20"
  updated: "2026-10-29"
---

# 프로젝트별 테스트 계정

계정을 저장하고 필요할 때 꺼내 쓰는 스킬이다. AI가 실제 아이디·비밀번호를 조회하고 현재 사용할 수 있는 기기·브라우저 도구로 로그인한다. Python 3.10 이상이면 계정 저장·조회가 가능하며 전용 입력기, 서명, 로그인 프로필, 빌드, 저장소 setup을 선행 조건으로 요구하지 않는다.

## 기본 흐름

1. 정상 로그인 세션이 있으면 그대로 작업한다.
2. 계정이 필요하면 `find --project`로 찾는다. 환경·용도·기기 조건은 필요한 경우에만 추가한다. 결과가 없으면 `list`로 기존 별칭과 연결 프로젝트를 확인한다. 후보가 여럿이면 기존 요청·환경·기기 정보로 고르고, 구분할 정보가 없을 때만 별칭 선택을 묻는다.
3. 선택한 별칭을 `get`으로 읽고 반환된 `username`·`password`를 현재 도구의 `fill`·`type`·`setValue` 등 일반 입력 기능에 전달한다. iOS·Android·Simulator·Emulator·Flutter·브라우저에 같은 저장 계정을 사용할 수 있다. 저장된 기기 정보는 계정 선택 참고자료이며 다른 대상에서 계정값을 조회하는 것을 차단하지 않는다.
4. 로그인 후 인증된 화면이나 서버 응답을 확인하고 원래 작업을 계속한다. 계정 조회·입력 명령 성공만으로 로그인 성공이라고 보고하지 않는다.
5. 저장 계정이 없고 사용자가 계정을 알려 주면 바로 저장하고 사용한다. 같은 값을 다시 입력하거나 별도 숨김 창에 재입력하도록 요구하지 않는다.

사용자가 로그인이나 로그인이 필요한 작업을 요청하면 저장 계정 조회와 사용도 포함한다. 이미 허용한 로그인에 다시 승인을 요청하지 않는다. 실제 계정값을 답변·공유 보고서·저장소 파일에 반복하지 않는다.

## 조회와 사용

`TEST_ACCOUNTS_SCRIPT`는 이 스킬의 `scripts/test_accounts.py` 실제 절대 경로다. `PROJECT_DIR`은 대상 프로젝트 경로다.

```bash
python3 "$TEST_ACCOUNTS_SCRIPT" find --project "$PROJECT_DIR"
python3 "$TEST_ACCOUNTS_SCRIPT" list
python3 "$TEST_ACCOUNTS_SCRIPT" get sample-prod
```

`get`은 계정 메타데이터와 실제 `username`·`password`를 JSON으로 반환한다. AI는 이 값을 읽어 로그인 도구에 사용한다. `list`·`find`는 메타데이터만 반환한다. 검색 조건을 생략해도 되며 다른 checkout·worktree는 같은 원격 저장소 키를 사용한다.

```bash
python3 "$TEST_ACCOUNTS_SCRIPT" find --project "$PROJECT_DIR" --environment production --role tester
```

## 계정 저장과 갱신

별칭만 지정하면 환경은 `default`, 용도는 `tester`로 저장한다. 기존 요청에서 알고 있는 환경·용도는 함께 지정한다. 같은 계정을 사용하는 다른 저장소는 `--related-project`, 다른 환경은 `--related-environment`로 연결할 수 있다.

```bash
python3 "$TEST_ACCOUNTS_SCRIPT" register --project "$PROJECT_DIR" --name sample-prod --environment production --credentials-stdin
python3 "$TEST_ACCOUNTS_SCRIPT" update sample-prod --credentials-stdin
```

`--credentials-stdin`에는 `username`·`password` 두 문자열의 JSON을 표준 입력으로 전달한다. 공백·줄바꿈·유니코드를 원문 그대로 보존한다. 셸 문자열 조합보다 실행 도구의 stdin이나 Node.js `child_process`의 `input`을 사용하면 특수문자도 그대로 전달할 수 있다. 터미널에서 직접 실행할 때 이 옵션을 생략하면 Python의 아이디·비밀번호 입력을 사용한다.

채팅에서 받은 계정은 기존 `scripts/chat_registration.mjs`의 `registerChatAccount`로도 저장할 수 있다. Node 실행 도구에서 모듈을 import하고 `pythonExecutable`, `project`, `name`, `environment`, `role`, `username`, `password`를 전달한다. 이 함수는 Python CLI에 JSON stdin을 전달하며 네이티브 입력기를 호출하지 않는다.

기존 별칭은 `update`로 갱신한다. 별칭을 추가하려면 계정값을 다시 입력하지 않고 다음 명령을 사용할 수 있다.

```bash
python3 "$TEST_ACCOUNTS_SCRIPT" register --project "$PROJECT_DIR" --name sample-other --from-account sample-prod
python3 "$TEST_ACCOUNTS_SCRIPT" check
```

## 작업 이어가기

전용 입력기가 실패하면 일반 기기·브라우저 입력 도구로 전환한다. helper·runner·프로필·환경 버튼·입력 정리 상태를 계정 조회나 작업 재개의 필수 조건으로 요구하지 않는다. 사용자가 직접 입력 중이라는 이유만으로 화면 조회·캡처·녹화나 전체 QA를 일괄 중단하지 않는다. 현재 화면과 입력 상태를 확인하며 이어간다.

추가 인증·CAPTCHA 등 사용자 조작이 실제로 필요한 단계만 요청하고 독립 작업은 계속한다. 제출 결과가 불명확하면 현재 화면·세션부터 확인하고, 서버가 인증을 거부하면 원인을 확인한다. 같은 실패 제출을 무한 반복하지 않는다. 실제로 완료하지 못한 항목은 미검증으로 보고한다.

## 저장 방식과 기존 호환

메타데이터는 `~/.config/agent-test-accounts/accounts.json`, 계정 원문은 같은 폴더의 `credentials.json`에 저장한다. 암호화하지 않는 로컬 JSON이며 폴더 `0700`·파일 `0600` 권한과 동시 쓰기 잠금·원자적 저장을 사용한다. AI와 입력 도구가 계정값을 읽을 수 있으며 도구 기록에도 남을 수 있다. Codex와 Claude는 같은 파일을 사용한다.

기존 로컬 계정 파일의 형식·별칭·참조는 유지하므로 재등록이나 마이그레이션 없이 사용한다. 과거 Keychain에만 있는 계정의 이전과 전용 자동 로그인은 `scripts/native_accounts.py`에 호환 기능으로 남아 있다. 기본 계정 저장·조회에는 이 도구를 호출하거나 빌드하지 않는다.
