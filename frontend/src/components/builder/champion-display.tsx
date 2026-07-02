import { Button } from '@/components/ui/button'
import { cn, tftIconUrl } from '@/lib/utils'
import type { TFTUnit } from '@/types'
import React, { useMemo, useState } from 'react'

interface ChampionDisplayProps {
  units: TFTUnit[]
  className?: string
}

const COST_COLORS: Record<number, string> = {
  1: 'border-slate-400',
  2: 'border-emerald-500',
  3: 'border-blue-500',
  4: 'border-purple-500',
  5: 'border-amber-400',
}

/**
 * Searchable, cost-filterable champion pool. Champions are added to
 * the board by click (random empty hex) or by dragging onto a hex.
 */
export function ChampionDisplay({ units, className }: ChampionDisplayProps) {
  const [search, setSearch] = useState('')
  const [selectedCost, setSelectedCost] = useState<number | null>(null)

  const filteredUnits = useMemo(() => {
    return units
      .filter((unit) => {
        const matchesName = unit.name.toLowerCase().includes(search.toLowerCase())
        const matchesCost = selectedCost === null || unit.cost === selectedCost
        return matchesName && matchesCost
      })
      .sort((a, b) => {
        // Special non-champion units (Golem, Training Dummy, Mini Black Hole)
        // have no traits — sort them after all real champions.
        const aSpecial = a.traits.length === 0
        const bSpecial = b.traits.length === 0
        if (aSpecial !== bSpecial) return aSpecial ? 1 : -1
        return a.cost - b.cost || a.name.localeCompare(b.name)
      })
  }, [units, search, selectedCost])

  const handleUnitClick = (unit: TFTUnit) => {
    window.dispatchEvent(new CustomEvent('tft-unit-add-random', { detail: unit }))
  }

  const handleDragStart = (e: React.DragEvent, unit: TFTUnit) => {
    e.dataTransfer.setData('tft-unit', JSON.stringify(unit))
    e.dataTransfer.effectAllowed = 'move'
    window.dispatchEvent(new CustomEvent('tft-drag-start', { detail: unit }))
  }

  return (
    <div className={cn('flex flex-col gap-4 p-4', className)}>
      {/* Filters Area */}
      <div className="flex flex-col sm:flex-row gap-3 items-center justify-between">
        <input
          type="text"
          placeholder="Search champions..."
          className="w-full sm:max-w-xs h-9 rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <div className="flex gap-1">
          {[1, 2, 3, 4, 5].map((cost) => (
            <Button
              key={cost}
              variant={selectedCost === cost ? 'default' : 'outline'}
              size="sm"
              className={cn('h-8 w-8 p-0', selectedCost === cost && COST_COLORS[cost])}
              onClick={() => setSelectedCost(selectedCost === cost ? null : cost)}
            >
              {cost}
            </Button>
          ))}
          {selectedCost !== null && (
            <Button variant="ghost" size="sm" className="h-8 px-2 text-xs" onClick={() => setSelectedCost(null)}>
              Reset
            </Button>
          )}
        </div>
      </div>

      {/* Champions Grid */}
      <div className="grid grid-cols-4 sm:grid-cols-6 md:grid-cols-8 lg:grid-cols-10 gap-3">
        {filteredUnits.length > 0 ? (
          filteredUnits.map((unit) => (
            <div key={unit.id} className="group flex flex-col items-center text-center text-[10px] leading-tight">
              <button
                onClick={() => handleUnitClick(unit)}
                draggable
                onDragStart={(e) => handleDragStart(e, unit)}
                className={cn(
                  'relative size-12 overflow-hidden rounded-md border-2 bg-muted shadow-sm ring-offset-background transition-all hover:scale-110 active:scale-95 cursor-grab active:cursor-grabbing outline-none',
                  COST_COLORS[unit.cost] || 'border-border'
                )}
              >
                <img
                  src={tftIconUrl(unit.icon)}
                  alt={unit.name}
                  className="h-full w-full object-cover grayscale-[20%] group-hover:grayscale-0"
                  loading="lazy"
                />
              </button>
              <span className="mt-1 truncate w-full text-foreground/80">{unit.name}</span>
            </div>
          ))
        ) : (
          <p className="col-span-full text-center text-muted-foreground py-8">No champions match your filters.</p>
        )}
      </div>
    </div>
  )
}
