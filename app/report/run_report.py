"""실행 리포트 HTML 생성.

**외부 리소스를 참조하지 않는다.** CDN·폰트·이미지를 걸면 나중에 그 링크가 죽고,
그러면 반년 전 리포트를 열었을 때 화면이 깨진다. CSS는 인라인이고 스크립트는 없다.

**여기에는 차트를 붙이지 않는다.** 실행 1회 리포트는 "방금 뭐가 나왔나"라 표로 충분하고,
차트 라이브러리를 얹으면 dry-run을 스무 번 돌릴 때마다 `latest.html`이 200KB씩 무거워진다.
차트는 `backtest_report`의 몫이다 — 거기가 여러 날을 늘어놓고 봉 위에 표시를 얹는 화면이고,
그릴 것이 실제로 있는 유일한 자리다 (ARCHITECTURE.md 2.1 / 12.7).
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.formatting import (
    DEFAULT_TIMEZONE,
    format_change,
    format_price,
    format_time,
    timezone_label,
)
from app.pipeline import RunResult

#: 리포트에 실을 최대 신호 수. 넘으면 잘라내고 그 사실을 리포트에 적는다.
MAX_ROWS = 200


@dataclass(frozen=True)
class ReportInput:
    result: RunResult
    signals: list[dict[str, Any]]
    committed: bool
    pipeline_name: str = ""
    user_timezone: str = DEFAULT_TIMEZONE
    """표시용 타임존. 저장은 언제나 UTC다 (규칙 5)."""


def report_path(run_id: str, *, committed: bool, directory: Path | None = None) -> Path:
    """리포트를 쓸 경로.

    dry-run은 `latest.html` **하나를 덮어쓴다** — 전략을 고치며 스무 번 돌려도
    파일이 스무 개 쌓이지 않게. `--commit` 실행만 `run_<id>.html`로 영구히 남는다.
    실제로 나간 판단만 이력으로 보존하면 된다.
    """
    settings = get_settings()
    root = directory or settings.resolve(settings.reports_dir)
    if not committed:
        return root / "latest.html"
    # run_id는 이미 `run_`으로 시작한다. 접두사를 겹쳐 붙이면 run_run_… 이 된다.
    stem = run_id if run_id.startswith("run_") else f"run_{run_id}"
    return root / f"{stem}.html"


def write_run_report(data: ReportInput, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render(data), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- 렌더
def _rank_key(row: dict[str, Any]) -> tuple[float, str]:
    """리포트 정렬 키 — **점수가 아니라 백분위다.**

    ★ 점수로 전역 정렬하면 규칙 17을 리포트에서 되돌리는 꼴이 된다. 12개월 모멘텀의
    스케일이 시장마다 달라서(코인 −60%~+300% vs 국내 −30%~+60%) 한 줄로 세우면
    그 기간에 잘 간 시장이 위를 통째로 차지한다 — 종목을 고른 게 아니라 시장을
    고른 표가 된다.

    **백분위는 시장과 무관하게 같은 뜻이다.** "상위 2.5%"는 코인이든 주식이든
    "제 시장에서 상위 2.5%"라서, 이걸로 세워야 섞인 표가 읽힌다.
    """
    features = row.get("features") or {}
    percentile = features.get("percentile")
    return (
        float(percentile) if isinstance(percentile, int | float) else float("inf"),
        str(row.get("instrument") or ""),
    )


def _render(data: ReportInput) -> str:
    result = data.result
    tz = data.user_timezone
    label = timezone_label(tz)
    ordered = sorted(data.signals, key=_rank_key)
    rows = ordered[:MAX_ROWS]
    truncated = max(0, len(data.signals) - MAX_ROWS)

    banner = (
        ""
        if data.committed
        else _banner(
            "dry-run",
            "signals 미기록 · 봉 미소비. 실제로 남기려면 <code>--commit</code>을 붙이세요.",
        )
    )
    failures = [n for n in result.nodes if str(n.status) == "error"]
    if failures:
        banner += _banner(
            "노드 실패",
            " · ".join(f"{html.escape(n.node_id)}: {html.escape(n.error or '')}" for n in failures),
        )

    return _DOCUMENT.format(
        title=html.escape(f"marketscan · {result.pipeline_id} · {result.run_id}"),
        style=_STYLE,
        heading=html.escape(data.pipeline_name or result.pipeline_id),
        run_id=html.escape(result.run_id),
        mode=html.escape(result.mode),
        status=html.escape(str(result.status)),
        as_of=html.escape(format_time(result.now, tz)),
        tz=html.escape(label),
        committed="예" if data.committed else "아니오 (dry-run)",
        generated=html.escape(format_time(datetime.now(UTC), tz)),
        banner=banner,
        signal_count=len(data.signals),
        signal_table=_signal_table(rows, truncated, tz, label),
        node_table=_node_table(result),
    )


def _signal_table(rows: list[dict[str, Any]], truncated: int, tz: str, label: str) -> str:
    if not rows:
        # 신호 0건은 실패가 아니다 (4.1). 리포트에서도 그렇게 읽혀야 한다.
        return (
            '<p class="empty">신호 0건입니다. '
            "빈 결과는 정상이며 실패와 다릅니다 — 조건에 맞는 종목이 없었거나, "
            "이미 판정한 봉이라 Fresh Bar Gate가 걸러냈습니다.</p>"
        )

    body = "\n".join(
        "<tr>"
        + "".join(
            f"<td>{cell}</td>"
            for cell in (
                _cell(row.get("instrument")),
                # `005930`만으로는 무슨 회사인지 알 수 없다. 소스가 준 이름을 쓴다.
                _cell(row.get("display_name")),
                # 판단한 봉의 종가와 그 봉의 등락률. "지금 시세"가 아니다 —
                # 마감된 봉으로만 판단하므로(4.4) 표시도 그 봉을 따라간다.
                _price_cell(row.get("close"), row.get("change_pct")),
                # 어느 시장 안에서 매긴 순위인가 (규칙 17). 이게 없으면 "1 / 39"의
                # 39가 무엇의 39인지 알 수 없다.
                _cell((row.get("features") or {}).get("rank_pool")),
                _cell(row.get("timeframe")),
                _cell(format_time(row.get("as_of"), tz)),
                _cell((row.get("features") or {}).get("rank")),
                _cell((row.get("features") or {}).get("universe_size")),
                _cell((row.get("features") or {}).get("percentile")),
                _cell((row.get("features") or {}).get("score")),
                _cell(row.get("strategy_id")),
                _cell((row.get("strategy_sha256") or "")[:12]),
            )
        )
        + "</tr>"
        for row in rows
    )
    note = (
        f'<p class="note">상위 {MAX_ROWS}건만 표시했습니다 ({truncated}건 생략).</p>'
        if truncated
        else ""
    )
    # 미국 세션은 KST로 다음 날 새벽에 닫힌다. 값은 맞지만 같은 거래일의 국내
    # 종목보다 하루 뒤로 보여서, 적어 두지 않으면 데이터가 어긋난 것으로 읽힌다.
    note += (
        f'<p class="note">시각은 모두 {label} 기준이며 <strong>봉이 마감한 순간</strong>입니다 '
        f"— 미국장은 한국 시각으로 다음 날 새벽에 닫히므로, 같은 거래일이라도 "
        f"국내·코인보다 날짜가 하루 뒤로 보입니다.</p>"
    )
    return f"""<div class="scroll"><table>
<thead><tr>
<th>종목</th><th>이름</th><th>종가 (등락)</th><th>시장</th><th>봉</th>
<th>봉 마감 ({label})</th>
<th>순위</th><th>유니버스</th><th>백분위</th><th>점수</th><th>전략</th><th>소스 해시</th>
</tr></thead>
<tbody>
{body}
</tbody></table></div>{note}"""


def _node_table(result: RunResult) -> str:
    body = "\n".join(
        f"<tr><td>{html.escape(n.node_id)}</td>"
        f"<td>{html.escape(n.type)}</td>"
        f'<td class="s-{html.escape(str(n.status))}">{html.escape(str(n.status))}</td>'
        f"<td>{n.duration_ms:.0f} ms</td>"
        f"<td>{_logs(n.logs)}</td></tr>"
        for n in result.nodes
    )
    return f"""<div class="scroll"><table>
<thead><tr><th>노드</th><th>type</th><th>상태</th><th>소요</th><th>로그</th></tr></thead>
<tbody>
{body}
</tbody></table></div>"""


def _logs(lines: list[str]) -> str:
    if not lines:
        return "-"
    return "<br>".join(html.escape(line) for line in lines)


def _cell(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, float):
        return html.escape(f"{value:,.6g}")
    return html.escape(str(value))


def _price_cell(close: Any, change: Any) -> str:
    """`239,500 (+2.34%)` — 등락률에만 색을 입힌다.

    **상승이 빨강, 하락이 파랑이다.** 국내 HTS·증권사 관례를 따른다 — 서구
    관례(상승 초록)와 반대라 뒤집으면 순간적으로 정반대로 읽힌다. 가격 자체는
    색을 주지 않는다: 색이 넓게 깔리면 어느 쪽이 신호인지 흐려진다.
    """
    text = format_price(close if isinstance(close, int | float) else None)
    if not text:
        return "-"
    if not isinstance(change, int | float):
        return html.escape(text)
    tone = "up" if change > 0 else "down" if change < 0 else "flat"
    return (
        f"{html.escape(text)} "
        f'<span class="chg {tone}">{html.escape(format_change(change))}</span>'
    )


def _banner(label: str, message: str) -> str:
    return f'<div class="banner"><strong>{html.escape(label)}</strong> {message}</div>'


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
.meta { color:var(--muted); margin:0 0 1.5rem; }
.meta code { color:var(--fg); }
.banner { background:var(--warn-bg); border-left:3px solid var(--warn-line);
          padding:.75rem 1rem; margin:0 0 1rem; border-radius:0 4px 4px 0; }
.scroll { overflow-x:auto; }
table { border-collapse:collapse; width:100%; font-variant-numeric:tabular-nums; }
th, td { text-align:left; padding:.45rem .7rem; border-bottom:1px solid var(--line);
         white-space:nowrap; }
th { background:var(--head); font-weight:600; }
td:last-child { white-space:normal; color:var(--muted); }
.empty, .note { color:var(--muted); }
/* 상승 빨강 · 하락 파랑 — 국내 HTS 관례다. 서구 관례와 반대라 뒤집으면
   순간적으로 정반대로 읽힌다. */
.chg { font-variant-numeric:tabular-nums; }
.chg.up { color:#d32f2f; }
.chg.down { color:#1565c0; }
.chg.flat { color:var(--muted); }
.s-error { color:#d33; font-weight:600; }
.s-skipped { color:var(--muted); }
footer { margin-top:2.5rem; color:var(--muted); font-size:.85rem;
         border-top:1px solid var(--line); padding-top:1rem; }
"""

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
  <code>{run_id}</code> · mode <code>{mode}</code> · 상태 <code>{status}</code><br>
  기준 시각 <code>{as_of}</code> ({tz}) · 기록됨 {committed}
</p>
{banner}
<h2>신호 {signal_count}건</h2>
{signal_table}
<h2>노드 실행</h2>
{node_table}
<footer>
  marketscan · {generated} 생성 · 이 파일은 재생성 가능합니다<br>
  신호의 근거를 더 파려면 <code>marketscan explain &lt;signal_id&gt;</code>
</footer>
</main>
</body>
</html>
"""
