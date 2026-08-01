import { PlaceholderPage } from './PlaceholderPage'

export function AlertChannelsPage() {
  return (
    <PlaceholderPage
      title="알림 채널"
      phase="Phase 1 예정"
      description={
        '신호가 도착할 곳을 등록합니다. 등록한 채널은 파이프라인의 Alert 노드에서 ' +
        '이름으로 골라 쓰며, 토큰은 노드가 아니라 여기에만 저장됩니다.'
      }
      items={[
        {
          name: '텔레그램',
          detail: '봇 토큰 + chat_id. 개인 채팅·그룹·채널 모두 지원 예정',
          accent: 'bg-accent-input',
        },
        {
          name: '슬랙',
          detail: 'Incoming Webhook URL 또는 봇 토큰 + 채널',
          accent: 'bg-accent-logic',
        },
        {
          name: '디스코드',
          detail: 'Webhook URL',
          accent: 'bg-accent-action',
        },
        {
          name: '중복 알림 방지',
          detail:
            '같은 봉에서 같은 신호가 두 번 나가지 않도록 dedup_key로 막습니다. ' +
            '종목별 재알림 금지 기간(Cooldown)도 여기서 설정합니다',
          accent: 'bg-accent-trigger',
        },
      ]}
      note={
        '토큰은 DAG JSON에 저장되지 않습니다. 파이프라인을 내보내도 자격 증명은 함께 나가지 ' +
        '않으며, 노드는 채널 ID만 참조합니다.'
      }
    />
  )
}
