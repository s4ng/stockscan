"""정적 HTML 리포트 (ARCHITECTURE.md 2.1 / 7장).

**서빙하지 않고 `reports/`에 파일로 떨어뜨린다.** 파일로 남아야 나중에 비교할 수
있고, 그러려면 상주 서버가 필요 없다.

단일 실행(`run`)의 산출물은 stdout과 이 HTML **둘뿐이다** — 텔레그램 같은 외부
전송은 상주 실행(`serve`)의 몫으로 미뤘다 (11장 4b).
"""

from app.report.run_report import report_path, write_run_report

__all__ = ["report_path", "write_run_report"]
