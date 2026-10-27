---
name: setup
description: Mobile QA 플러그인 설치 후 최초 사용 시 내장 또는 외장 저장소의 기존 산출물 폴더를 선택하고 로컬 준비 상태를 확인한다. 저장 폴더를 명시적으로 변경할 때도 사용한다.
metadata:
  author: JunyoungJung
  created: "2026-10-22"
  updated: "2026-10-27"
---

# Mobile QA 설정

플러그인 루트의 `scripts/setup.py`와 `scripts/doctor.py`를 사용한다. 설치 위치는 호스트가 제공한 스킬 경로에서 두 단계 위로 확인하고 홈의 특정 plugin cache 경로를 추측하지 않는다. 설정·계정·기기 식별자·빌드 산출물을 플러그인 폴더에 쓰지 않는다.

1. 읽기 전용 설명·설계만 요청받았으면 설정 파일을 쓰지 않는다. 실제 최초 설정 또는 첫 QA·빌드 준비라면 `python3 -B scripts/setup.py`를 실행한다. 설정이 없을 때만 macOS의 폴더 선택 창을 열며, 사용자가 내장 또는 외장 저장소의 원하는 **기존 폴더**를 선택한다. 프로젝트·호스트의 추가 저장 정책도 함께 따른다. 이 최초 선택을 에이전트가 임의의 볼륨·경로로 대체하지 않는다.
2. 도구는 실제 볼륨·마운트·볼륨 UUID·쓰기 가능 여부, 선택 폴더 경계와 심볼릭 링크 없는 경로를 검사하고 `~/Library/Application Support/mobile-qa/config.json`에 비공개 권한으로 저장한다. 설정이 있으면 검증 후 재사용하며 매번 폴더를 다시 묻지 않는다. 취소·연결 해제·UUID 불일치·쓰기 거부는 원인을 알리고 해당 준비를 중단한다. 마운트 지점처럼 보이는 폴더를 만들거나 선택 폴더 밖의 다른 경로로 자동 우회하지 않는다.
3. 사용자가 저장 위치 변경을 요청했거나 정확한 경로를 이미 제공했다면 `python3 -B scripts/setup.py --storage-dir <확인한 저장 폴더>`로 명시 변경한다. 자동 재선택·캐시 이동·이전 산출물 삭제는 하지 않는다. 비밀값 없는 분리 설정이 필요한 검증에서는 `--config` 또는 `MOBILE_QA_CONFIG`를 사용하며 시스템 `HOME`을 바꾸지 않는다.
4. 실기기는 `python3 -B scripts/doctor.py`, iOS Simulator는 `python3 -B scripts/doctor.py --simulator`로 요청 범위의 release·의존 도구·저장소를 읽기 전용으로 점검한다. Simulator 모드는 `simctl`·이미지 비교 준비를 확인하며 `agent-device`·실기기 서명·native 계정 helper를 요구하지 않는다. UI 조작·앱 실행·로그인 성공은 별도 확인한다. 누락 항목은 사용자가 승인한 준비 범위와 대조하고 필요한 작업만 수행한다. 도구 설치·업데이트·서명·native 빌드를 설정 성공에 포함해 보고하지 않는다. `agent-device` 버전을 임의로 올리거나 원문 자격증명을 조회하지 않는다.
5. 결과에는 저장소 선택·재사용 여부, 저장소 검증, 부족한 준비와 미검증 범위를 간단히 남긴다. 이어지는 [모바일 QA](../mobile-device-qa/SKILL.md) 또는 [계정 작업](../test-accounts/SKILL.md)에 준비 정보를 인계한다.

기존 `~/.config/agent-test-accounts/` 계정과 Library의 프로젝트별 QA 전용 저장소는 유지한다. 새 설정은 raw 로그·캡처·영상·빌드·캐시의 저장 위치만 정하며 계정·지식을 공개 패키지에 복사하지 않는다. 설정 파일이 있어도 매 실행의 볼륨 검증을 생략하지 않는다.

Android Emulator의 준비는 [도구 선택 기준](../mobile-device-qa/references/tool-selection.md)에 따라 Android CLI·ADB·기존 프로젝트 테스트를 확인한다. iOS Simulator 전용 doctor나 실기기용 `agent-device`를 필수로 요구하지 않는다. Maestro·Patrol은 필요한 역할이 있을 때만 준비한다.
