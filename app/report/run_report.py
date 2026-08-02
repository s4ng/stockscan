"""실행 리포트 HTML 생성.

**외부 리소스를 참조하지 않는다.** CDN·폰트·이미지를 걸면 나중에 그 링크가 죽고,
그러면 반년 전 리포트를 열었을 때 화면이 깨진다. CSS는 인라인이고 스크립트는 없다.

⚠️ 차트는 아직 없다. 백테스트 리포트(Phase 3)에서 uPlot을 vendoring할 때 붙인다 —
지금 넣으면 그릴 것이 신호 3건뿐이다.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.engine.runner import RunResult

#: 리포트에 실을 최대 신호 수. 넘으면 잘라내고 그 사실을 리포트에 적는다.
MAX_ROWS = 200


@dataclass(frozen=True)
class ReportInput:
    result: RunResult
    signals: list[dict[str, Any]]
    committed: bool
    pipeline_name: str = ""


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
def _render(data: ReportInput) -> str:
    result = data.result
    rows = data.signals[:MAX_ROWS]
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
        as_of=html.escape(result.now),
        committed="예" if data.committed else "아니오 (dry-run)",
        generated=datetime.now(UTC).isoformat(timespec="seconds"),
        banner=banner,
        signal_count=len(data.signals),
        signal_table=_signal_table(rows, truncated),
        node_table=_node_table(result),
    )


def _signal_table(rows: list[dict[str, Any]], truncated: int) -> str:
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
            f"<td>{_cell(value)}</td>"
            for value in (
                row.get("instrument"),
                row.get("timeframe"),
                row.get("as_of"),
                (row.get("features") or {}).get("rank"),
                (row.get("features") or {}).get("universe_size"),
                (row.get("features") or {}).get("percentile"),
                (row.get("features") or {}).get("score"),
                row.get("strategy_id"),
                (row.get("strategy_sha256") or "")[:12],
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
    return f"""<div class="scroll"><table>
<thead><tr>
<th>종목</th><th>봉</th><th>as_of</th><th>순위</th><th>유니버스</th>
<th>백분위</th><th>점수</th><th>전략</th><th>소스 해시</th>
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
    if value is None:
        return "-"
    if isinstance(value, float):
        return html.escape(f"{value:,.6g}")
    return html.escape(str(value))


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
  기준 시각 <code>{as_of}</code> · 기록됨 {committed}
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
