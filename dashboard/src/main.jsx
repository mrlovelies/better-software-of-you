import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { AuthProvider } from './hooks/useAuth.jsx'
import { PendingProvider } from './hooks/usePendingCounts.jsx'
import Layout from './components/Layout.jsx'
import Overview from './pages/Overview.jsx'
import Signals from './pages/Signals.jsx'
import Competitive from './pages/Competitive.jsx'
import Forecasts from './pages/Forecasts.jsx'
import Builds from './pages/Builds.jsx'
import Login from './pages/Login.jsx'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route element={<PendingProvider><Layout /></PendingProvider>}>
            <Route path="/" element={<Overview />} />
            <Route path="/signals" element={<Signals />} />
            <Route path="/competitive" element={<Competitive />} />
            <Route path="/forecasts" element={<Forecasts />} />
            <Route path="/builds" element={<Builds />} />
          </Route>
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  </React.StrictMode>
)
