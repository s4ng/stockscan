"""백테스트 리포트 HTML — 캔들 차트 + 조건 충족일 마커 (ARCHITECTURE.md 12.7).

**자기완결적이다.** 차트 라이브러리를 `vendor/`에서 읽어 **인라인**한다. CDN을
걸면 반년 뒤 그 링크가 죽었을 때 화면이 통째로 비고, 그러면 "지난 판단을 되짚는다"는
이 파일의 존재 이유가 사라진다 (2.1).

**상단 배너를 지우지 않는다.** 단일 종목 리플레이는 횡단면 컷을 적용하지 못하므로
(replay.py 참조) 마커는 "조건 충족일"이지 "실제 신호일"이 아니다. 그 한 줄이
없으면 이 리포트는 없는 성과를 본 것처럼 읽힌다.
"""

from __future__ import annotations

import html
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.backtest.replay import ReplayResult
from app.core.config import get_settings
from app.core.formatting import DEFAULT_TIMEZONE, format_price, format_time, timezone_label

#: 표에 실을 최대 행수. 넘으면 잘라내고 그 사실을 적는다 (조용한 절삭 금지).
MAX_ROWS = 200

VENDOR_DIR = Path(__file__).parent / "vendor"
CHART_JS = VENDOR_DIR / "lightweight-charts.standalone.production.js"

#: 마커 색. ★ **캔들 팔레트 밖이어야 한다.**
#:
#: 봉은 상승 빨강·하락 파랑(국내 HTS 관례)을 이미 쓴다. 마커를 그 둘 중 하나로 두면
#: 빨간 봉 아래의 빨간 화살표처럼 **하필 신호가 난 날에 가장 안 보인다** — 돌파일은
#: 대개 상승 마감이라 빨간 봉과 겹치는 것이 예외가 아니라 기본이다.
#: 호박색은 빨강·파랑 어느 쪽과도 겹치지 않고, 흰 배경과 어두운 배경 양쪽에서 읽힌다.
MARKER_COLOR = "#f59e0b"

#: 워밍업 구간(= `--start` 이전) 봉의 색. **판정하지 않은 구간**이라 눈에 띄면 안 된다.
#:
#: 이 구분이 없으면 차트 왼쪽 끝부터 백테스트가 돈 것처럼 보인다 — 실제로는 지표
#: 워밍업용으로 그려 둔 것뿐이고, 그 구간에는 마커가 나올 수 없다.
WARMUP_COLOR = "#9aa0a6"


def report_path(result: ReplayResult, directory: Path | None = None) -> Path:
    """`backtest_krx_005930_20251201_20260804.html`.

    실행마다 남긴다 — `latest.html`처럼 덮어쓰면 파라미터를 바꿔 가며 돌린 결과가
    서로를 지운다. 백테스트는 **비교하려고** 돌리는 것이라 이력이 남아야 한다.
    """
    settings = get_settings()
    root = directory or settings.resolve(settings.reports_dir)
    stem = "_".join(
        [
            "backtest",
            _slug(result.instrument.venue),
            _slug(result.instrument.symbol),
            result.start.strftime("%Y%m%d"),
            result.end.strftime("%Y%m%d"),
        ]
    )
    return root / f"{stem}.html"


def write_backtest_report(
    result: ReplayResult, path: Path, *, user_timezone: str = DEFAULT_TIMEZONE
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render(result, user_timezone), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- 렌더
def _render(result: ReplayResult, tz: str) -> str:
    label = timezone_label(tz)
    # ★ 마커에 글자를 붙이지 않는다. 충족일이 몰리는 구간에서 라벨이 서로 겹쳐
    #   봉을 가리는데, 차트에서 읽어야 하는 것은 "어느 봉인가" 하나다. 뜻은 차트
    #   아래 범례 한 줄로 옮겼다 — 마커마다 반복할 이유가 없다.
    markers = [
        {
            "time": day.session.isoformat(),
            "position": "belowBar",
            "color": MARKER_COLOR,
            "shape": "arrowUp",
        }
        for day in result.signal_days
    ]
    name = result.instrument.display_name or result.instrument.symbol

    return _DOCUMENT.format(
        title=html.escape(f"stockscan 백테스트 · {result.instrument.key}"),
        style=_STYLE.replace("{marker_color}", MARKER_COLOR),
        heading=html.escape(f"{result.instrument.key} · {name}"),
        strategy=html.escape(result.strategy_id),
        sha=html.escape(result.strategy_sha256[:12] or "-"),
        period=f"{result.start.isoformat()} ~ {result.end.isoformat()}",
        judged=len(result.days),
        signals=len(result.signal_days),
        skipped=result.skipped_warmup,
        startup=result.startup_candles,
        params=html.escape(json.dumps(result.params, ensure_ascii=False, sort_keys=True)),
        banner=_banner(result),
        legend=_legend(result),
        chart_js=CHART_JS.read_text(encoding="utf-8"),
        bars=json.dumps(_colored_bars(result)),
        start_line=json.dumps(_start_marker(result), ensure_ascii=False),
        markers=json.dumps(markers, ensure_ascii=False),
        precision=_precision(result),
        min_move=f"{10 ** -_precision(result):.8f}".rstrip("0").rstrip(".") or "1",
        table=_table(result, tz, label),
        tz=html.escape(label),
        generated=html.escape(format_time(datetime.now(UTC), tz)),
    )


def _colored_bars(result: ReplayResult) -> list[dict[str, Any]]:
    """`--start` 이전 봉을 회색으로 낮춘다.

    lightweight-charts는 캔들마다 색을 받으므로 시리즈를 쪼갤 필요가 없다. 색만
    낮추면 "여기부터 판정했다"가 설명 없이 보인다.
    """
    start = result.start.isoformat()
    out: list[dict[str, Any]] = []
    for bar in result.bars:
        if bar["time"] >= start:
            out.append(bar)
            continue
        out.append(
            {
                **bar,
                "color": WARMUP_COLOR,
                "borderColor": WARMUP_COLOR,
                "wickColor": WARMUP_COLOR,
            }
        )
    return out


def _start_marker(result: ReplayResult) -> list[dict[str, Any]]:
    """시작일 위에 세우는 깃발.

    회색/컬러 경계만으로는 **워밍업 봉이 하나도 없을 때**(캐시가 얕거나 상장 직후)
    아무 표시도 남지 않는다. 그때도 "여기가 시작"이 보여야 한다.
    """
    start = result.start.isoformat()
    first = next((b["time"] for b in result.bars if b["time"] >= start), None)
    if first is None:
        return []
    return [
        {
            "time": first,
            "position": "aboveBar",
            "color": "#1565c0",
            "shape": "arrowDown",
            "text": f"백테스트 시작 {result.start.isoformat()}",
        }
    ]


def _legend(result: ReplayResult) -> str:
    """차트 아래 범례. 마커에서 뗀 뜻이 여기 한 번만 적힌다.

    ⚠️ **마커의 뜻을 화면에서 없애는 것이 아니다.** 봉 위의 라벨을 지우는 대신
    같은 문장을 여기에 남긴다 — 표시의 의미가 화면 어디에도 없으면 그때부터
    이 리포트는 "실제 신호"로 읽힌다 (12.7).
    """
    warmup = (
        f'<br><span class="warmup">■</span> 회색 구간은 <strong>워밍업</strong>입니다 — '
        f"{result.start.isoformat()} 이전의 봉은 지표 계산에만 쓰고 판정하지 않았습니다 "
        f"(전략이 {result.startup_candles}봉을 요구합니다)."
        if any(bar["time"] < result.start.isoformat() for bar in result.bars)
        else ""
    )
    return (
        f'<p class="legend"><span class="mark">▲</span> '
        f"조건 충족 {len(result.signal_days)}일 — <strong>실제 신호일이 아닙니다</strong> "
        f"(위 경고 참조). 마커는 그날 마감된 봉 아래에 찍힙니다.{warmup}</p>"
    )


def _banner(result: ReplayResult) -> str:
    """★ 지우지 말 것. 이 리포트가 정직해지는 유일한 장치다."""
    if result.cut_applied:
        return ""
    return (
        '<div class="banner"><strong>⚠️ 마커는 “조건 충족일”이지 “실제 신호일”이 '
        "아닙니다.</strong><br>"
        "전략은 <code>compute</code>(종목별) → <code>rank</code>(횡단면) → "
        "<code>select</code>(컷) 순서인데, 이 리포트는 <strong>이 종목 하나만</strong> "
        "리플레이했습니다. 후보가 1개뿐이라 “상위 N개” 컷은 항상 통과합니다 — "
        "실제 실행에서는 같은 날 조건을 통과한 다른 종목에 밀렸을 수 있습니다. "
        "그날 그 시장에서 통과 종목이 컷보다 적었다면 두 값은 같습니다.<br>"
        "순위·백분위·유니버스 크기는 <strong>표에서 뺐습니다</strong> — 유니버스가 1이라 "
        "언제나 “1 / 1 · 상위 100%”가 되어 정보가 아니라 오해가 됩니다."
        "</div>"
    )


def _precision(result: ReplayResult) -> int:
    """가격 자릿수. 종목마다 스케일이 달라 고정값을 쓰면 코인이 전부 0으로 보인다."""
    last = result.bars[-1]["close"] if result.bars else 0
    if last >= 1000:
        return 0
    if last >= 1:
        return 2
    return 8


def _table(result: ReplayResult, tz: str, label: str) -> str:
    days = result.signal_days
    if not days:
        return (
            '<p class="empty">이 기간에 조건을 만족한 날이 없습니다. '
            "0건은 실패가 아닙니다 — 추세추종이라면 돌파는 상태가 아니라 사건이라 "
            "대부분의 날이 0건입니다. 0건이 이어진다고 파라미터를 낮추면 "
            "그때부터 검증된 표준값이 아니라 내가 고른 값이 됩니다.</p>"
        )

    keys = sorted({k for d in days for k in d.features})
    rows = days[:MAX_ROWS]
    body = "\n".join(
        "<tr>"
        + f"<td>{html.escape(d.session.isoformat())}</td>"
        + f"<td>{html.escape(format_time(d.as_of, tz))}</td>"
        + f"<td>{html.escape(format_price(d.close))}</td>"
        + "".join(f"<td>{_cell(d.features.get(k))}</td>" for k in keys)
        + "</tr>"
        for d in rows
    )
    head = "".join(f"<th>{html.escape(k)}</th>" for k in keys)
    note = (
        f'<p class="note">{MAX_ROWS}건만 표시했습니다 ({len(days) - MAX_ROWS}건 생략).</p>'
        if len(days) > MAX_ROWS
        else ""
    )
    return f"""<div class="scroll"><table>
<thead><tr><th>세션</th><th>봉 마감 ({label})</th><th>종가</th>{head}</tr></thead>
<tbody>
{body}
</tbody></table></div>{note}"""


def _cell(value: object) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, bool):
        return "예" if value else "아니오"
    if isinstance(value, float):
        return html.escape(f"{value:,.6g}")
    return html.escape(str(value))


def _slug(raw: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in raw)


_STYLE = """
:root { color-scheme: light dark; --bg:#fff; --fg:#1a1a1a; --muted:#666;
        --line:#e2e2e2; --head:#f6f6f6; --warn-bg:#fff8e1; --warn-line:#e6c200; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#16181c; --fg:#e8e8e8; --muted:#9aa0a6;
          --line:#2c2f36; --head:#1e2127; --warn-bg:#2a2410; --warn-line:#8a7300; }
}
* { box-sizing: border-box; }
body { margin:0; padding:2rem 1.25rem; background:var(--bg); color:var(--fg);
       font:14px/1.6 ui-sans-serif, system-ui, "Apple SD Gothic Neo", "Malgun Gothic", sans-serif; }
main { max-width: 1100px; margin: 0 auto; }
h1 { font-size:1.4rem; margin:0 0 .25rem; }
h2 { font-size:1rem; margin:2rem 0 .5rem; color:var(--muted);
     text-transform:uppercase; letter-spacing:.05em; }
.meta { color:var(--muted); margin:0 0 1.25rem; }
.meta code { color:var(--fg); }
.banner { background:var(--warn-bg); border-left:3px solid var(--warn-line);
          padding:.75rem 1rem; margin:0 0 1.25rem; border-radius:0 4px 4px 0; }
#chart { width:100%; height:420px; }
.scroll { overflow-x:auto; }
table { border-collapse:collapse; width:100%; font-variant-numeric:tabular-nums; }
th, td { text-align:left; padding:.45rem .7rem; border-bottom:1px solid var(--line);
         white-space:nowrap; }
th { background:var(--head); font-weight:600; }
.empty, .note { color:var(--muted); }
.legend { color:var(--muted); margin:.5rem 0 0; font-size:.9rem; }
/* 마커와 **같은 색**이어야 범례가 범례로 읽힌다. 색을 바꾸면 여기도 함께 바꾼다
   — 두 값이 갈리는 순간 범례가 다른 것을 가리키게 된다. */
.legend .mark { color:{marker_color}; font-weight:700; font-size:1.05em; }
.legend .warmup { color:#9aa0a6; }
footer { margin-top:2.5rem; color:var(--muted); font-size:.85rem;
         border-top:1px solid var(--line); padding-top:1rem; }
"""

#: `{bars}` 등은 `str.format`이 채운다. JS의 중괄호는 `{{}}`로 이스케이프한다.
_DOCUMENT = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{style}</style>
</head>
<body>
<main>
<h1>{heading}</h1>
<p class="meta">
  전략 <code>{strategy}</code> @ <code>{sha}</code> · 기간 <code>{period}</code><br>
  판정 {judged}일 · <strong>조건 충족 {signals}일</strong> ·
  워밍업 부족으로 건너뜀 {skipped}일 (필요 {startup}봉)<br>
  파라미터 <code>{params}</code>
</p>
{banner}
<h2>차트</h2>
<div id="chart"></div>
{legend}
<h2>조건 충족일 {signals}건</h2>
{table}
<footer>
  stockscan · {generated} 생성 · 이 파일은 재생성 가능합니다<br>
  차트: TradingView Lightweight Charts™ (Apache-2.0, <code>app/report/vendor/</code>에 동봉)<br>
  시각은 {tz} 기준이며 <strong>봉이 마감한 순간</strong>입니다.
</footer>
</main>
<script>{chart_js}</script>
<script>
const bars = {bars};
const markers = {markers};
const dark = window.matchMedia('(prefers-color-scheme: dark)').matches;
const chart = LightweightCharts.createChart(document.getElementById('chart'), {{
  layout: {{ background: {{ color: 'transparent' }}, textColor: dark ? '#9aa0a6' : '#666' }},
  grid: {{ vertLines: {{ color: dark ? '#2c2f36' : '#eee' }},
           horzLines: {{ color: dark ? '#2c2f36' : '#eee' }} }},
  rightPriceScale: {{ borderColor: dark ? '#2c2f36' : '#e2e2e2' }},
  timeScale: {{ borderColor: dark ? '#2c2f36' : '#e2e2e2' }},
  autoSize: true,
}});
// 상승 빨강 · 하락 파랑 — 국내 HTS 관례다 (run_report와 같은 규칙).
const series = chart.addCandlestickSeries({{
  upColor: '#d32f2f', downColor: '#1565c0',
  borderUpColor: '#d32f2f', borderDownColor: '#1565c0',
  wickUpColor: '#d32f2f', wickDownColor: '#1565c0',
  priceFormat: {{ type: 'price', precision: {precision}, minMove: {min_move} }},
}});
series.setData(bars);
// 시작 깃발을 먼저 두어 같은 날에 마커가 겹쳐도 위/아래로 갈린다.
series.setMarkers({start_line}.concat(markers));
chart.timeScale().fitContent();
</script>
</body>
</html>
"""
