import { Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { lazy, Suspense } from 'react'
import AppLayout from './components/Layout/AppLayout'

const Home = lazy(() => import('./pages/Home'))
const Interview = lazy(() => import('./pages/Interview'))
const Report = lazy(() => import('./pages/Report'))
const Settings = lazy(() => import('./pages/Settings'))
const QuestionBank = lazy(() => import('./pages/QuestionBank'))

export default function App() {
  return (
    <Suspense fallback={<div style={{ padding: 48, textAlign: 'center' }}>正在加载页面…</div>}>
      <Routes>
        <Route element={<AppLayout />}>
          <Route path="/" element={<Home />} />
          <Route path="/training" element={<Interview />} />
          <Route path="/interview" element={<LegacyTrainingRedirect />} />
          <Route path="/report/:id" element={<Report />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/question-bank" element={<QuestionBank />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </Suspense>
  )
}

function LegacyTrainingRedirect() {
  const { search } = useLocation()
  return <Navigate to={`/training${search}`} replace />
}
