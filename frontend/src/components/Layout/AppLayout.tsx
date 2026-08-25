import { Layout, Menu } from 'antd'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'

const { Header, Content } = Layout

const items = [
  { key: '/', label: '首页' },
  { key: '/training', label: '开始训练' },
  { key: '/question-bank', label: '题库' },
  { key: '/settings', label: '设置' },
]

export default function AppLayout() {
  const nav = useNavigate()
  const loc = useLocation()
  const activeKey = '/' + loc.pathname.split('/')[1]

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header className="app-shell-header">
        <button type="button" className="app-shell-brand" onClick={() => nav('/')}>
          <span className="app-shell-brand-mark" aria-hidden="true" />
          <span className="app-shell-brand-copy">表达能力训练器</span>
        </button>
        <Menu
          className="app-shell-menu"
          mode="horizontal"
          selectedKeys={[activeKey]}
          items={items}
          onClick={({ key }) => nav(key)}
        />
      </Header>
      <Content className="app-content" style={{ padding: 24 }}>
        <Outlet />
      </Content>
    </Layout>
  )
}
