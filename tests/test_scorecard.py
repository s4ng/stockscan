"""성적표가 **자기 가정을 밝히는가** (ARCHITECTURE.md 4.8).

⚠️ **2026-08-07에 채점 대상이 고정됐다.** 한때 `[샀다/안 샀다]` 응답으로 산 것과
무시한 것을 갈라 비교했지만, 그 비교는 응답을 빠짐없이 해야만 성립했다 — 산 것만
답하고 무시한 것은 넘기면 **자기가 고른 분할**이 되어 결론이 아첨하는 쪽으로 기운다.
지금은 낸 신호를 **전부 샀다고 가정**한다.

그래서 지켜야 할 것이 하나 늘었다. **가정을 매번 적는다.** 안 적으면 사람은 이
숫자를 자기 계좌 수익률로 읽는데, 실제로는 산 적 없는 종목까지 들어 있는 값이다.
그 오독이 정확히 이 프로젝트가 막으려는 "자신감 기계"다.
"""

from __future__ import annotations

from app import scorecard as sc


def _card(**kwargs) -> sc.Scorecard:
    card = sc.Scorecard(strategy="trend_breakout_55", signals=12, evaluated=9, **kwargs)
    card.horizons = [sc.Horizon(bars=20, count=9, median=0.02, hit_rate=0.55)]
    return card


def test_the_scorecard_states_that_it_assumes_every_signal_was_bought():
    text = sc.render(_card())

    assert "전부 샀다고 가정" in text
    assert "12건" in text


def test_the_override_comparison_is_gone_and_does_not_come_back():
    """걷어낸 비교가 되살아나면 여기서 잡힌다.

    되살리려면 **응답을 빠짐없이 받을 방법**이 먼저 있어야 한다. 없이 되살리면
    편향된 분할로 "재량에 값이 있다/없다"를 판정하게 된다.
    """
    text = sc.render(_card())

    assert "무시한 것" not in text
    assert "미응답" not in text
    assert not hasattr(sc, "Override")


def test_an_empty_scorecard_does_not_claim_an_assumption_it_did_not_make():
    """신호가 0건이면 채점할 것도 가정할 것도 없다 — 빈 결과와 미구현은 다르다 (12.3)."""
    text = sc.render(sc.Scorecard(strategy="trend_breakout_55"))

    assert "신호가 없습니다" in text
    assert "가정" not in text
