import { PlaceholderPage } from './PlaceholderPage'

export function ConnectionsPage() {
  return (
    <PlaceholderPage
      title="연결"
      phase="Phase 2 예정"
      description={
        '시세·주문·AI 소스의 API 키를 등록하고, 시장과 봉 단위별로 어떤 소스를 먼저 쓸지 ' +
        '정합니다. 소스는 캔버스의 노드가 아니라 여기서 관리하므로, 소스를 바꿔도 ' +
        '파이프라인은 그대로 둘 수 있습니다.'
      }
      items={[
        {
          name: '암호화폐 — 업비트 · 바이낸스',
          detail: 'CCXT 기반. 시세와 주문을 모두 제공',
          accent: 'bg-accent-indicator',
        },
        {
          name: '주식 (일봉) — PyKRX · yfinance · FinanceDataReader',
          detail: '인증이 필요 없는 내장 소스. 백테스트용 과거 이력을 담당',
          accent: 'bg-accent-input',
        },
        {
          name: '주식 (분봉) — 한국투자증권 · Alpaca',
          detail: '계좌·API 키 필요. 실시간 신호용이며 최근 봉만 조회합니다',
          accent: 'bg-accent-logic',
        },
        {
          name: '증권사 주문 — Toss · 한국투자증권',
          detail: '실주문은 Phase 5입니다. 그 전까지는 시세 전용으로만 씁니다',
          accent: 'bg-accent-action',
        },
        {
          name: 'AI — Anthropic · OpenAI',
          detail: 'LLM Decision 노드용. 프롬프트·응답은 캐시되어 재실행이 무료입니다',
          accent: 'bg-accent-trigger',
        },
        {
          name: '라우팅 표',
          detail:
            '(시장 × 봉 단위)마다 소스 우선순위를 정합니다. 앞 소스가 실패하면 다음으로 ' +
            '폴백하므로 무료 소스가 막혀도 파이프라인이 멈추지 않습니다',
          accent: 'bg-outline',
        },
      ]}
      note={
        'API 키는 암호화해 저장되며 DAG JSON에는 연결 ID만 남습니다. ' +
        '거래소 키는 가능하면 읽기 전용과 거래용을 분리하고, 출금 권한은 부여하지 마세요.'
      }
    />
  )
}
