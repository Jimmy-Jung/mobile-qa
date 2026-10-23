#!/usr/bin/env python3
"""README 그림을 만든다: 도식 2장(SVG) + 그래프 1장(PNG). Author: JunyoungJung · 2026-10-23.

    SKILLS="$HOME/.claude/skills"
    PYTHONPATH="$SKILLS/paper-figures/scripts" python3 docs/figures/build_figures.py --png-dir <검수 PNG 폴더>

SVG와 그래프 PNG는 저장소의 docs/figures/ 아래에 둔다. 검수용 PNG는 --png-dir로 저장소 밖에 쓴다.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from chart_style import (INK_2, NEUTRAL, RED, WIDTH_IN, bar_axis, barh_height, plt, read_csv, save, setup_style,
                         value_label, vline_ref)
from paper_kit import (CHIP, NAVY, REGION, STROKE, WHITE, Diagram, box, brief, build_all, flow, lane, marker,
                       new_diagram, node, note, region, task)

HERE = Path(__file__).resolve().parent
BOX_W, BOX_H, STEP = 400, 62, 95


def loops(out: Path) -> Diagram:
    """사람이 눈으로 보는 루프와 근거가 판정하는 루프를 같은 단계 수로 나란히 놓는다."""
    d = new_diagram(
        "mqa-01-loops", "같은 루프, 다른 판정 근거",
        brief(claim="두 루프의 단계는 같고 판정 근거만 다르다. 눈과 기억 대신 캡처·픽셀 수치가 판정한다.",
              span="AI 구현부터 다시 지시 또는 재촬영까지", flow="위에서 아래, 되돌아감은 파선",
              change="판정 근거가 기억에서 측정값으로 바뀐다", actors="AI · 사람 · 실기기 · 비교",
              caption="사람 검수 루프와 근거 기반 검증 루프"), out)

    manual = ["AI가 요구를 해석해 구현", "빌드·설치", "사람이 화면을 열어 확인",
              "“비슷한데 뭔가 다르다”", "말로 다시 지시"]
    checked = ["AI가 요구를 해석해 구현", "빌드·설치를 근거로 확인", "같은 조건으로 실기기 촬영",
               "기준과 픽셀 비교 · 판정", "승인 범위 안에서 코드 수정"]

    region(d, "left", 40, 50, 560, 560, "지금까지 — 사람이 눈으로 보는 루프")
    region(d, "right", 640, 50, 560, 560, "이 플러그인 — 근거가 판정하는 루프")

    for side, (x0, labels) in enumerate(((120, manual), (720, checked))):
        for i, label in enumerate(labels):
            y = 100 + i * STEP
            weak = side == 0 and i == 3
            strong = side == 1 and i == 3
            node(d, f"n{side}{i}", x0, y, BOX_W, BOX_H, label, size=15, emph=strong,
                 fill=CHIP if weak else WHITE)
            if i:
                flow(d, f"f{side}{i}", x0 + BOX_W / 2, y - STEP + BOX_H, [[0, 0], [0, STEP - BOX_H]])

    # 되돌아감: 왼쪽은 처음으로 돌아가 다시 만들고, 오른쪽은 촬영 단계로 돌아가 다시 잰다.
    flow(d, "loop-left", 120, 511, [[0, 0], [-50, 0], [-50, -380], [0, -380]], style="dashed")
    flow(d, "loop-right", 1120, 511, [[0, 0], [50, 0], [50, -190], [0, -190]], style="dashed")

    note(d, "판정 근거는 기억과 눈. 같은 지적이 반복되고 몇 px 차이는 그대로 지나간다.", 120, 628, size=14)
    note(d, "판정 근거는 캡처·픽셀 수치·판정값. 고친 뒤 같은 조건으로 다시 촬영한다.", 720, 628, size=14)
    return d


def slop_types(out: Path) -> Diagram:
    """세 가지 AI 슬롭이 각각 어떤 근거로 드러나고 하나의 판정으로 모이는지."""
    d = new_diagram(
        "mqa-02-evidence", "슬롭 유형마다 다른 근거",
        brief(claim="세 가지 어긋남은 서로 다른 근거로만 드러나고, 근거 없이는 통과가 아니라 미검증이다.",
              span="구현 결과에서 리포트까지", flow="왼쪽에서 오른쪽으로 수렴",
              change="관찰·겹침·픽셀 수치가 판정으로 모인다", actors="구현 결과 · 검증 · 판정",
              caption="슬롭 유형과 그것을 드러내는 근거"), out)

    types = ["요구를 잘못 이해한 동작\n화면은 열리지만 결과가 다르다",
             "잘못 해석한 UI\n구조·배치가 기준과 다르다",
             "비슷하지만 미묘하게 다른 UI\n간격·폰트·색이 몇 px 어긋난다"]
    evidence = ["시나리오 실행 · 관찰 · 조작 GIF",
                "기준과 25 · 50 · 75% 겹침",
                "차이 이미지 · 변경 픽셀 수 · 차이 영역"]

    region(d, "slop", 40, 60, 380, 440, "AI 구현 결과에 섞이는 것")
    region(d, "ev", 460, 60, 380, 440, "검증이 만드는 근거")
    for i, (left, right) in enumerate(zip(types, evidence)):
        y = 110 + i * 130
        node(d, f"t{i}", 60, y, 340, 90, left, size=14, fill=CHIP)
        node(d, f"e{i}", 480, y, 340, 90, right, size=14)
        flow(d, f"m{i}", 400, y + 45, [[0, 0], [80, 0]])

    node(d, "verdict", 900, 240, 290, 110, "판정\n통과 · 실패 · 미검증", emph=True, size=16)
    node(d, "report", 900, 400, 290, 80, "Markdown 리포트", size=15)
    flow(d, "c0", 820, 155, [[0, 0], [40, 0], [40, 140], [80, 140]])
    flow(d, "c1", 820, 285, [[0, 0], [40, 0], [40, 10], [80, 10]])
    flow(d, "c2", 820, 415, [[0, 0], [40, 0], [40, -120], [80, -120]])
    flow(d, "r", 1045, 350, [[0, 0], [0, 50]])
    note(d, "근거가 없으면 통과가 아니라 미검증으로 남는다.", 900, 500, size=13)
    return d


def case_playback(out: Path) -> Diagram:
    """실기기 로그의 시각 간격을 그대로 옮긴 수정 전후 비교."""
    d = new_diagram(
        "mqa-03-case", "같은 조작, 수정 전과 수정 후",
        brief(claim="같은 기기에서 같은 조작을 하면 수정 전에는 영상 영역이 약 8초간 검고 수정 후에는 계속 보인다.",
              span="제어 센터를 연 시점부터 앱이 다시 Active가 될 때까지", flow="왼쪽에서 오른쪽으로 시간",
              change="재생을 다시 시작한 시점에 영상 레이어가 분리되는지", actors="수정 전 빌드 · 수정 후 빌드",
              caption="사례의 수정 전후 비교"), out)

    for side, (x0, title, fill, text, label, foot) in enumerate((
            (40, "수정 전 빌드", "#1a1a1a", WHITE, "영상 영역\n검게 바뀜", "소리와 재생 시간은 계속 올라감"),
            (640, "수정 후 빌드", "#e6ecf3", "#000000", "영상 영역\n그대로 보임", "같은 조작 3회, 분리 로그 0건"))):
        region(d, f"s{side}", x0, 50, 560, 290, title)
        box(d, f"frame{side}", x0 + 40, 100, 480, 210, "", fill=WHITE, stroke=STROKE, stroke_width=1.2, rounded=False)
        box(d, f"video{side}", x0 + 60, 120, 250, 170, label, fill=fill, stroke=NAVY if side else STROKE,
            text=text, size=14, stroke_width=1.2, rounded=False)
        box(d, f"list{side}", x0 + 325, 120, 175, 170, "강의 목록", fill=REGION, stroke=STROKE, size=13,
            stroke_width=1.2, rounded=False)
        note(d, foot, x0 + 40, 350, size=13)

    marker(d, "resume", 742, "제어 센터에서 일시정지 → 재생", y_text=392, y_from=416, y_to=438, y_rule_to=578)
    for side, (y, title, result, fill, emph) in enumerate((
            (440, "수정 전", "영상 영역 검정 (약 8초)", CHIP, False),
            (530, "수정 후", "영상 계속 표시 (같은 구간)", WHITE, True))):
        yc = lane(d, f"t{side}", y, title)
        task(d, f"pre{side}", 215, yc, 525, "제어 센터 열림 · Inactive (약 11초)")
        task(d, f"post{side}", 745, yc, 400, result, fill=fill, emph=emph)

    note(d, "실기기 로그의 시각 간격을 그대로 옮겼다. 두 빌드의 조작 시점 차이는 0.1~0.3초.", 40, 600, size=13)
    return d


def survey_chart(data: Path, out: Path) -> Path:
    rows = read_csv(data, "ai-slop-survey")
    if not rows:
        return out
    colors = {"bad": RED, "neutral": NEUTRAL}
    fig, ax = plt.subplots(figsize=(WIDTH_IN, barh_height(len(rows), per_row=0.42)))
    ys = list(range(len(rows)))[::-1]
    values = [float(r["value"]) for r in rows]
    ax.barh(ys, values, color=[colors[r["tone"]] for r in rows], height=0.56, zorder=3)
    for yi, value in zip(ys, values):
        value_label(ax, value, yi, f"{value:g}%")
    vline_ref(ax, 50, "응답자 절반", -0.95)
    ax.set_ylim(-1.1, len(rows) - 0.4)
    ax.set_yticks(ys, [r["label"] for r in rows], fontsize=8)
    ax.set_xlim(0, 85)
    ax.set_xlabel("응답자 비율 (%)")
    ax.annotate("출처: Stack Overflow 2025 Developer Survey (응답자 49,000명 이상)",
                (0, -1.0), xytext=(0, -2), textcoords="offset points", fontsize=7, color=INK_2)
    bar_axis(ax)
    return save(fig, out, "mqa-chart-01-ai-slop")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--png-dir", type=Path, required=True, help="검수용 PNG를 쓸 저장소 밖 폴더")
    parser.add_argument("--source-dir", type=Path, help="excalidraw 원본 폴더 (기본: --png-dir 옆)")
    args = parser.parse_args()
    source_dir = args.source_dir or args.png_dir.parent / "excalidraw"

    print(setup_style())
    build_all({"mqa-01-loops": loops, "mqa-02-evidence": slop_types, "mqa-03-case": case_playback},
              source_dir=source_dir, svg_dir=HERE, png_dir=args.png_dir)
    survey_chart(HERE / "data", HERE)


if __name__ == "__main__":
    main()
