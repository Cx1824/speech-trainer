import { Routes, Route, Navigate } from 'react-router-dom'
import AppLayout from './components/Layout/AppLayout'
import Home from './pages/Home'
import Interview from './pages/Interview'
import Report from './pages/Report'
import Settings from './pages/Settings'
import QuestionBank from './pages/QuestionBank'

export default function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route path="/" element={<Home />} />
        <Route path="/interview" element={<Interview />} />
        <Route path="/report/:id" element={<Report />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/question-bank" element={<QuestionBank />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
