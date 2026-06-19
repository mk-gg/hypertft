import React, { useEffect, useState } from 'react'
import { useStore } from '@nanostores/react'
import { $activeUnits } from '@/store/boardStore'
import { postSuggest, getUnitsMap, getItemsMap } from '@/lib/api'
import { tftIconUrl, cn } from '@/lib/utils'
import type { SuggestResult, UnitItemStats } from '@/types'

type Mode = 'exact' | 'super'

const deltaColor = (delta: number) =>
  delta < -1.5 ? '#22a55a' : delta < -0.8 ? '#c89b3c' : '#98a0b3'

export function ItemsPanel() {
  const activeUnits = useStore($activeUnits)
  const [result, setResult] = useState<SuggestResult | null>(null)
  const [unitsMap, setUnitsMap] = useState<Map<string, any>>(new Map())
  const [itemsMap, setItemsMap] = useState<Map<string, any>>(new Map())
  const [loading, setLoading] = useState(false)
  const [mode, setMode] = useState<Mode>('exact')

  useEffect(() => {
    if (!activeUnits.length) {
      setResult(null)
      return
    }

    const handler = setTimeout(async () => {
      setLoading(true)
      try {
        const [suggestRes, uMap, iMap] = await Promise.all([
          postSuggest({ units: activeUnits, similarity_threshold: 0, limit: 6 }),
          getUnitsMap(),
          getItemsMap(),
        ])
        setResult(suggestRes)
        setUnitsMap(uMap)
        setItemsMap(iMap)
      } catch (err) {
        console.error('ItemsPanel fetch error:', err)
      } finally {
        setLoading(false)
      }
    }, 500)

    return () => clearTimeout(handler)
  }, [activeUnits])

  function unitIcon(name: string) {
    const unit = unitsMap.get(name.toLowerCase())
    return unit?.icon ? tftIconUrl(unit.icon) : null
  }

  function itemIcon(itemId: string) {
    const item = itemsMap.get(itemId.toLowerCase())
    return item?.icon ? tftIconUrl(item.icon) : null
  }

  function itemName(itemId: string) {
    const item = itemsMap.get(itemId.toLowerCase())
    return item?.name ?? itemId.replace(/^TFT\w+_Item_/, '').replace(/_/g, ' ')
  }

  function unitCost(name: string) {
    return unitsMap.get(name.toLowerCase())?.cost ?? 1
  }

  const COST_BORDER: Record<number, string> = {
    1: 'border-[#808080]/60',
    2: 'border-[#22a55a]/60',
    3: 'border-[#2f6fd6]/60',
    4: 'border-[#b44af0]/60',
    5: 'border-[#f0c040]/60',
  }

  if (!activeUnits.length) {
    return (
      <div className="flex flex-col gap-4 rounded-xl border border-dashed border-border/60 p-8 text-center">
        <p className="text-xs italic text-muted-foreground">Add units to the board to see item recommendations.</p>
      </div>
    )
  }

  if (loading && !result) {
    return (
      <div className="flex flex-col gap-3 animate-pulse">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="h-24 w-full rounded-lg bg-muted/20" />
        ))}
      </div>
    )
  }

  if (!result) return null

  const data: UnitItemStats[] = mode === 'exact' ? result.exact_items : result.super_items

  return (
    <div className="flex flex-col gap-4 pb-10">

      {/* Mode toggle */}
      <div className="flex items-center gap-1 self-start rounded-lg border border-[#232635]/50 bg-background/20 p-1">
        {(['exact', 'super'] as Mode[]).map((m) => (
          <button
            key={m}
            onClick={() => setMode(m)}
            className={cn(
              'rounded-md px-3 py-1 text-[11px] font-medium transition-all',
              mode === m
                ? 'bg-[#c89b3c] text-[#1a1a1a]'
                : 'text-[#676e85] hover:text-[#cdd6e4] hover:bg-[#232635]/30',
            )}
          >
            {m === 'exact' ? 'Exact' : 'Superset'}
          </button>
        ))}
      </div>

      {/* Per-unit item rows */}
      <div className="flex flex-col gap-3">
        {data.map((unitStat) => {
          const icon = unitIcon(unitStat.unit)
          const cost = unitCost(unitStat.unit)
          return (
            <div key={unitStat.unit} className="flex flex-col overflow-hidden rounded-lg border border-[#232635]/50 bg-background/20">

              {/* Unit header */}
              <div className="flex items-center gap-2 border-b border-[#232635]/40 bg-[#1a1c2b]/40 px-3 py-2">
                {icon ? (
                  <img
                    src={icon}
                    alt={unitStat.unit}
                    width={24}
                    height={24}
                    className={cn('h-6 w-6 rounded border-2', COST_BORDER[cost])}
                  />
                ) : (
                  <div className={cn('flex h-6 w-6 items-center justify-center rounded border-2 bg-[#1a1c2b] text-[8px]', COST_BORDER[cost])}>
                    {unitStat.unit.slice(0, 2)}
                  </div>
                )}
                <span className="text-xs font-medium text-[#cdd6e4]">{unitStat.unit}</span>
              </div>

              {/* Item rows */}
              <div className="flex overflow-x-auto gap-2 p-2 scrollbar-thin scrollbar-thumb-muted-foreground/10">
                {unitStat.items.map((item, j) => {
                  const iIcon = itemIcon(item.item)
                  const iName = itemName(item.item)
                  return (
                    <div key={j} className="flex flex-col items-center flex-shrink-0 w-20 p-1 rounded-md hover:bg-[#1a1c2b]/40 transition-colors">
                      {iIcon ? (
                        <img
                          src={iIcon}
                          alt={iName}
                          width={32}
                          height={32}
                          title={iName}
                          className="h-8 w-8 shrink-0 rounded-sm border border-[#232635]/50"
                        />
                      ) : (
                        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-sm border border-[#232635]/50 bg-[#1a1c2b] text-[9px] text-[#676e85]">
                          {iName.slice(0, 2)}
                        </div>
                      )}
                      <span className="mt-1 text-[10px] text-[#98a0b3] text-center truncate w-full">{iName}</span>
                      <span className="font-mono text-[11px]" style={{ color: deltaColor(item.delta) }}>
                        {item.delta.toFixed(2)}
                      </span>
                      <span className="font-mono text-[9px] text-[#676e85]">
                        {item.avg.toFixed(2)}
                      </span>
                    </div>
                  )
                })}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}