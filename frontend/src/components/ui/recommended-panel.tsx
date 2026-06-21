import React, { useEffect, useState } from 'react'
import { useStore } from '@nanostores/react'
import { cn, avgToTier, tierColor, tftIconUrl } from '@/lib/utils'
import { postSuggest, getUnitsMap } from '@/lib/api'
import { $activeUnits, $placements, placeUnit, addUnitToRandomHex } from '@/store/boardStore'
import type { SuggestResult } from '@/types'

const BOARD_ROWS = 4
const BOARD_COLS = 7

interface Props {
  units?: string[]
  threshold?: number
  limit?: number
  className?: string
  mode?: 'additions' | 'mutations' | 'comps'
  externalResult?: SuggestResult | null
  externalUnitsMap?: Map<string, any>
  isLoading?: boolean
}

export function RecommendedPanel({
  units: propsUnits,
  threshold,
  limit,
  className,
  mode,
  externalResult,
  externalUnitsMap,
  isLoading,
}: Props) {
  const activeUnits = useStore($activeUnits)
  const units = propsUnits && propsUnits.length > 0 ? propsUnits : activeUnits

  const [result, setResult] = useState<SuggestResult | null>(externalResult || null)
  const [debouncedUnits, setDebouncedUnits] = useState<string[]>(units)
  const [unitsMap, setUnitsMap] = useState<Map<string, any>>(externalUnitsMap || new Map())
  const [loading, setLoading] = useState(isLoading || false)

  // ✅ Write to $placements, not $activeUnits
  const handleAddUnit = (name: string) => {
    const unit = unitsMap?.get(name.toLowerCase())
    if (!unit) return
    addUnitToRandomHex(unit, BOARD_ROWS, BOARD_COLS)
  }

  // ✅ Find the hex key and replace the unit in $placements
  const handleSwapUnit = (unitOutName: string, unitInName: string) => {
    const unitIn = unitsMap?.get(unitInName.toLowerCase())
    if (!unitIn) return

    const placements = $placements.get()
    const key = Object.entries(placements).find(
      ([, u]) => u.name.toLowerCase() === unitOutName.toLowerCase()
    )?.[0]

    if (key) {
      placeUnit(key, unitIn)
    } else {
      // Unit to swap out isn't on board — just add it
      addUnitToRandomHex(unitIn, BOARD_ROWS, BOARD_COLS)
    }
  }

  useEffect(() => {
    const handler = setTimeout(() => setDebouncedUnits(units), 500)
    return () => clearTimeout(handler)
  }, [units])

  useEffect(() => {
    if (externalResult !== undefined) {
      setResult(externalResult)
      if (externalUnitsMap) setUnitsMap(externalUnitsMap)
      setLoading(!!isLoading)
      return
    }

    if (!debouncedUnits || debouncedUnits.length === 0) {
      setResult(null)
      return
    }

    async function fetchData() {
      setLoading(true)
      try {
        const [suggestRes, mapRes] = await Promise.all([
          postSuggest({
            units: debouncedUnits,
            ...(threshold !== undefined && { similarity_threshold: threshold }),
            ...(limit !== undefined && { limit }),
          }),
          getUnitsMap(),
        ])
        setResult(suggestRes)
        setUnitsMap(mapRes)
      } catch (err) {
        console.error('Failed to fetch suggestions:', err)
      } finally {
        setLoading(false)
      }
    }

    fetchData()
  }, [debouncedUnits, threshold, limit, externalResult, externalUnitsMap, isLoading])

  function unitData(name: string) {
    const unit = unitsMap?.get(name.toLowerCase())
    return {
      name,
      icon: unit?.icon ? tftIconUrl(unit.icon) : null,
      cost: unit?.cost ?? 1,
    }
  }

  const deltaColor = (delta: number) =>
    delta < -1.5 ? '#22a55a' : delta < -0.8 ? '#c89b3c' : '#98a0b3'

  if (loading && !result) {
    return (
      <div className={cn('flex w-full flex-col gap-6 animate-pulse', className)}>
        <div className="h-32 w-full rounded-lg bg-muted/20" />
        <div className="h-64 w-full rounded-lg bg-muted/20" />
      </div>
    )
  }

  if (!units.length) {
    return (
      <div className={cn('flex w-full flex-col gap-4 rounded-xl border border-dashed border-border/60 p-8 text-center', className)}>
        <p className="text-xs text-muted-foreground italic">Add units to the board to see suggestions.</p>
      </div>
    )
  }

  if (!result) {
    return (
      <div className={cn('flex w-full flex-col gap-4 rounded-xl border border-dashed border-border/60 p-8 text-center', className)}>
        <p className="text-xs text-muted-foreground italic">No suggestions for this board yet.</p>
      </div>
    )
  }

  const isAll = !mode

  return (
    <div className={cn('flex w-full flex-col gap-6 pb-20', className)}>

      {(isAll || mode === 'comps') && (
        <div className="flex flex-col gap-1.5">
          <p className="text-[10px] uppercase tracking-wider text-[#676e85]">Your board</p>
          <div className="flex flex-wrap gap-1">
            {units.filter(Boolean).map((name, i) => {
              const u = unitData(name)
              return (
                <div key={`${name}-${i}`} className="relative shrink-0" title={u.name}>
                  {u.icon ? (
                    <img src={u.icon} alt={u.name} width={36} height={36}
                      className={cn('h-8 w-8 rounded border-2',
                        u.cost === 1 && 'border-[#808080]/60',
                        u.cost === 2 && 'border-[#22a55a]/60',
                        u.cost === 3 && 'border-[#2f6fd6]/60',
                        u.cost === 4 && 'border-[#b44af0]/60',
                        u.cost === 5 && 'border-[#f0c040]/60',
                      )}
                    />
                  ) : (
                    <div className="flex h-8 w-8 items-center justify-center rounded border border-[#2d3146]/30 bg-[#1a1c2b] text-[8px]">
                      {u.name.slice(0, 2)}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
          <p className="text-[11px] text-[#676e85]">
            Superset avg <span className="font-medium text-[#cdd6e4]">{result.superset_avg != null ? result.superset_avg.toFixed(2) : '—'}</span>
            <span className="mx-1 opacity-40">·</span>
            {result.superset_n} games
            <span className="mx-1 opacity-40">·</span>
            patch {result.patch}
          </p>
        </div>
      )}

      {(isAll || mode === 'comps') && (
        <div className="flex flex-col gap-2">
          <p className="text-[10px] uppercase tracking-wider text-[#676e85]">Suggested comps</p>
          <div className="flex max-h-[380px] flex-col overflow-y-auto divide-y divide-[#232635]/40 rounded-lg border border-[#232635]/50 bg-background/20 scrollbar-thin scrollbar-thumb-muted-foreground/10">
            {result.suggested_comps.map((comp, i) => {
              const tier = avgToTier(comp.exact_avg)
              return (
                <div key={i} className="flex flex-col gap-2 px-3 py-2.5 hover:bg-[#1a1c2b]/40 transition-colors">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="text-[11px] font-black" style={{ color: tierColor(tier) }}>{tier}</span>
                      <span className="text-[11px] text-[#98a0b3]">avg {comp.exact_avg.toFixed(2)} · {comp.exact_n}n</span>
                    </div>
                    <span className="text-[10px] text-[#676e85]">{Math.round(comp.similarity * 100)}% match</span>
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {(() => {
                      // Count units already on the board so duplicate units in a
                      // comp (e.g. 2× Samira) highlight per-instance: the first N
                      // copies render as owned, any copies beyond that as missing.
                      const ownedCount: Record<string, number> = {}
                      for (const boardName of units) {
                        const bk = boardName.toLowerCase()
                        ownedCount[bk] = (ownedCount[bk] ?? 0) + 1
                      }
                      const seenCount: Record<string, number> = {}
                      return comp.units.map((name, j) => {
                      const u = unitData(name)
                      const nk = name.toLowerCase()
                      seenCount[nk] = (seenCount[nk] ?? 0) + 1
                      const isMissing = seenCount[nk] > (ownedCount[nk] ?? 0)
                      return (
                        <div
                          key={j}
                          className={cn('relative shrink-0', isMissing && 'cursor-pointer hover:scale-110 transition-transform')}
                          title={`${u.name}${isMissing ? ' (click to add)' : ''}`}
                          onClick={() => isMissing && handleAddUnit(u.name)}
                        >
                          {u.icon ? (
                            <img src={u.icon} alt={u.name} width={32} height={32}
                              className={cn('h-7 w-7 rounded border-2 transition-opacity',
                                isMissing ? 'opacity-40 grayscale' : 'opacity-100',
                                u.cost === 1 && 'border-[#808080]/60',
                                u.cost === 2 && 'border-[#22a55a]/60',
                                u.cost === 3 && 'border-[#2f6fd6]/60',
                                u.cost === 4 && 'border-[#b44af0]/60',
                                u.cost === 5 && 'border-[#f0c040]/60',
                              )}
                            />
                          ) : (
                            <div className={cn('flex h-7 w-7 items-center justify-center rounded border border-[#2d3146]/30 bg-[#1a1c2b] text-[8px]', isMissing && 'opacity-40')}>
                              {u.name.slice(0, 2)}
                            </div>
                          )}
                          {isMissing && <span className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-[#c89b3c]" />}
                        </div>
                      )
                      })
                    })()}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {(isAll || mode === 'additions') && result.additions.length > 0 && (
        <div className="flex flex-col gap-2">
          <p className="text-[10px] uppercase tracking-wider text-[#676e85]">Add to your board</p>
          <div className="flex max-h-[240px] flex-col overflow-y-auto divide-y divide-[#232635]/40 rounded-lg border border-[#232635]/50 bg-background/20 scrollbar-thin">
            {result.additions.map((add, i) => {
              const u = unitData(add.unit)
              return (
                <div key={i} className="flex items-center gap-3 px-3 py-2 hover:bg-[#1a1c2b]/60 transition-colors cursor-pointer group"
                  onClick={() => handleAddUnit(add.unit)}
                >
                  {u.icon ? (
                    <img src={u.icon} alt={u.name} width={32} height={32}
                      className={cn('h-7 w-7 shrink-0 rounded border-2 group-hover:border-primary/50',
                        u.cost === 1 && 'border-[#808080]/60',
                        u.cost === 2 && 'border-[#22a55a]/60',
                        u.cost === 3 && 'border-[#2f6fd6]/60',
                        u.cost === 4 && 'border-[#b44af0]/60',
                        u.cost === 5 && 'border-[#f0c040]/60',
                      )}
                    />
                  ) : (
                    <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded border border-[#2d3146]/30 bg-[#1a1c2b] text-[8px]">
                      {u.name.slice(0, 2)}
                    </div>
                  )}
                  <span className="flex-1 text-xs text-[#cdd6e4] group-hover:text-white">{add.unit}</span>
                  <span className="font-mono text-xs" style={{ color: deltaColor(add.delta) }}>{add.delta.toFixed(2)}</span>
                  <span className="w-12 text-right font-mono text-[11px] text-[#676e85]">avg {add.avg.toFixed(2)}</span>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {(isAll || mode === 'mutations') && result.mutations.length > 0 && (
        <div className="flex flex-col gap-2">
          <p className="text-[10px] uppercase tracking-wider text-[#676e85]">Swap suggestions</p>
          <div className="flex max-h-[240px] flex-col overflow-y-auto divide-y divide-[#232635]/40 rounded-lg border border-[#232635]/50 bg-background/20 scrollbar-thin">
            {result.mutations.map((mut, i) => {
              const uOut = unitData(mut.unit_out)
              const uIn = unitData(mut.unit_in)
              return (
                <div key={i} className="flex items-center gap-2 px-3 py-2 hover:bg-[#1a1c2b]/60 transition-colors cursor-pointer group"
                  onClick={() => handleSwapUnit(mut.unit_out, mut.unit_in)}
                >
                  {uOut.icon ? (
                    <img src={uOut.icon} alt={uOut.name} width={28} height={28}
                      className={cn('h-6 w-6 shrink-0 rounded border-2 grayscale opacity-50',
                        uOut.cost === 1 && 'border-[#808080]/60',
                        uOut.cost === 2 && 'border-[#22a55a]/60',
                        uOut.cost === 3 && 'border-[#2f6fd6]/60',
                        uOut.cost === 4 && 'border-[#b44af0]/60',
                        uOut.cost === 5 && 'border-[#f0c040]/60',
                      )}
                    />
                  ) : (
                    <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded border border-[#2d3146]/30 bg-[#1a1c2b] text-[7px] opacity-50">
                      {uOut.name.slice(0, 2)}
                    </div>
                  )}
                  <svg className="shrink-0 text-[#676e85]" width="12" height="12" viewBox="0 0 12 12" fill="none">
                    <path d="M2 6h8M7 3l3 3-3 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                  </svg>
                  {uIn.icon ? (
                    <img src={uIn.icon} alt={uIn.name} width={28} height={28}
                      className={cn('h-6 w-6 shrink-0 rounded border-2 group-hover:border-primary/50',
                        uIn.cost === 1 && 'border-[#808080]/60',
                        uIn.cost === 2 && 'border-[#22a55a]/60',
                        uIn.cost === 3 && 'border-[#2f6fd6]/60',
                        uIn.cost === 4 && 'border-[#b44af0]/60',
                        uIn.cost === 5 && 'border-[#f0c040]/60',
                      )}
                    />
                  ) : (
                    <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded border border-[#2d3146]/30 bg-[#1a1c2b] text-[7px]">
                      {uIn.name.slice(0, 2)}
                    </div>
                  )}
                  <span className="flex-1 text-[11px] text-[#98a0b3]">
                    <span className="text-[#676e85] line-through">{mut.unit_out}</span>
                    <span className="mx-1">→</span>
                    <span className="text-[#cdd6e4]">{mut.unit_in}</span>
                  </span>
                  <span className="font-mono text-xs" style={{ color: deltaColor(mut.delta) }}>{mut.delta.toFixed(2)}</span>
                  <span className="w-12 text-right font-mono text-[11px] text-[#676e85]">avg {mut.avg.toFixed(2)}</span>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Empty-state fallbacks so a tab never renders blank */}
      {mode === 'additions' && result.additions.length === 0 && (
        <p className="text-xs text-muted-foreground italic">No unit additions suggested for this board.</p>
      )}
      {mode === 'mutations' && result.mutations.length === 0 && (
        <p className="text-xs text-muted-foreground italic">No swaps suggested for this board.</p>
      )}
    </div>
  )
}