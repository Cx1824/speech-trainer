import { Layout, Menu } from 'antd'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'

const { Header, Content } = Layout

const items = [
  { key: '/', label: '首页' },
  { key: '/interview', label: '面试训练' },
  { key: '/question-bank', label: '题库' },
  { key: '/settings', label: '设置' },
]

export default function AppLayout() {
  const nav = useNavigate()
  const loc = useLocation()
  const activeKey = '/' + loc.pathname.split('/')[1]

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{ display: 'flex', alignItems: 'center', background: '#fff', borderBottom: '1px solid var(--color-border)' }}>
        <div style={{ fontWeight: 500, marginRight: 32, color: 'var(--color-primary)' }}>表达能力训练</div>
        <Menu
          mode="horizontal"
          selectedKeys={[activeKey]}
          items={items}
          style={{ flex: 1, minWidth: 0, borderBottom: 'none' }}
          onClick={({ key }) => nav(key)}
        />
      </Header>
      <Content style={{ padding: 24 }}>
        <Outlet />
      </Content>
    </Layout>
  )
}
