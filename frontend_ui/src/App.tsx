import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { AppLayout } from './components/AppLayout'
import { OverviewPage } from './pages/OverviewPage'
import { EntitiesPage } from './pages/EntitiesPage'
import { GraphPage } from './pages/GraphPage'
import { ScenariosPage } from './pages/ScenariosPage'
import { QueryPage } from './pages/QueryPage'
import { SupplyChainMapPage } from './pages/SupplyChainMapPage'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppLayout />}>
          <Route index element={<OverviewPage />} />
          <Route path="entities" element={<EntitiesPage />} />
          <Route path="map" element={<SupplyChainMapPage />} />
          <Route path="graph" element={<GraphPage />} />
          <Route path="scenarios" element={<ScenariosPage />} />
          <Route path="query" element={<QueryPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
