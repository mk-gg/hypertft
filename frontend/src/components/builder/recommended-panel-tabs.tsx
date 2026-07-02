import React, { useState, useEffect } from 'react'
import { useStore } from '@nanostores/react'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { RecommendedPanel } from './recommended-panel'
import { $activeUnits } from '@/store/boardStore'
import { postSuggest, getUnitsMap } from '@/lib/api'
import type { SuggestResult, TFTUnit } from '@/types'

const TAB_MODES = ['comps', 'additions', 'mutations'] as const
type TabMode = (typeof TAB_MODES)[number]

const TAB_LABELS: Record<TabMode, string> = {
  comps: 'Comps',
  additions: 'Add',
  mutations: 'Swap',
}

/**
 * Tabbed container for the suggestion panels. Owns the data: fetches
 * suggestions (debounced) whenever the board changes and passes the
 * shared result down to one RecommendedPanel per tab.
 */
export const RecommendedPanelTabs: React.FC = () => {
  const activeUnits = useStore($activeUnits)
  const [result, setResult] = useState<SuggestResult | null>(null)
  const [unitsMap, setUnitsMap] = useState<Map<string, TFTUnit>>(new Map())
  const [loading, setLoading] = useState(false)
  const [activeTab, setActiveTab] = useState<TabMode>('comps')
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    setMounted(true)
  }, [])

  // Fetch suggestions whenever the board changes, debounced
  useEffect(() => {
    if (!activeUnits || activeUnits.length === 0) {
      setResult(null)
      return
    }

    const fetchData = async () => {
      setLoading(true)
      try {
        const [suggestRes, mapRes] = await Promise.all([
          postSuggest({ units: activeUnits }),
          getUnitsMap(),
        ])
        setResult(suggestRes)
        setUnitsMap(mapRes)
      } catch (err) {
        console.error('Failed to fetch suggestions:', err)
        setResult(null)
      } finally {
        setLoading(false)
      }
    }

    const handler = setTimeout(fetchData, 500)
    return () => clearTimeout(handler)
  }, [activeUnits])

  const hasContent: Record<TabMode, boolean> = {
    comps: (result?.suggested_comps?.length ?? 0) > 0,
    additions: (result?.additions?.length ?? 0) > 0,
    mutations: (result?.mutations?.length ?? 0) > 0,
  }

  // Auto-switch away from a tab that becomes empty
  useEffect(() => {
    if (!mounted || hasContent[activeTab]) return
    const fallback = TAB_MODES.find((m) => hasContent[m])
    if (fallback) setActiveTab(fallback)
  }, [hasContent.comps, hasContent.additions, hasContent.mutations])

  return (
    <Tabs
      value={activeTab}
      onValueChange={(v) => setActiveTab(v as TabMode)}
      className="flex h-full w-full min-h-0 flex-col"
    >
      <TabsList className="grid w-full grid-cols-3">
        {TAB_MODES.map((tabMode) => (
          <TabsTrigger key={tabMode} value={tabMode} disabled={mounted && !hasContent[tabMode]}>
            {TAB_LABELS[tabMode]}
          </TabsTrigger>
        ))}
      </TabsList>

      {TAB_MODES.map((tabMode) => (
        <TabsContent key={tabMode} value={tabMode} className="flex-1 overflow-y-auto p-4">
          <RecommendedPanel
            mode={tabMode}
            result={result}
            unitsMap={unitsMap}
            isLoading={loading}
          />
        </TabsContent>
      ))}
    </Tabs>
  )
}
