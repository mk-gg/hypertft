import React, { useState, useEffect } from 'react'
import { useStore } from '@nanostores/react'
import type { TFTUnit } from '@/types'
import { tftIconUrl, cn } from '@/lib/utils'
import { $placements, placeUnit, removeUnit, swapUnit, addUnitToRandomHex } from '@/store/boardStore'
import { BOARD_ROWS, BOARD_COLS } from '@/consts'

interface BoardProps {
  rows?: number
  cols?: number
  /** Hex circumradius in SVG units. */
  size?: number
  /** Gap between adjacent hexes in SVG units. */
  spacing?: number
  className?: string
}

/**
 * Interactive hex-grid board. Units are placed by click or drag from
 * the champion pool (via `tft-drag-start` / `tft-unit-add-random`
 * window events) and stored in the shared placements store.
 */
export function Board({ rows = BOARD_ROWS, cols = BOARD_COLS, size = 40, spacing = 2, className }: BoardProps) {
  const placements = useStore($placements)
  const [activeDragUnit, setActiveDragUnit] = useState<TFTUnit | null>(null)
  const [hoveredHexKey, setHoveredHexKey] = useState<string | null>(null)

  useEffect(() => {
    const handleDragStartGlobal = (e: Event) =>
      setActiveDragUnit((e as CustomEvent<TFTUnit>).detail)
    const handleDragEndGlobal = () => {
      setActiveDragUnit(null)
      setHoveredHexKey(null)
    }
    const handleRandomAdd = (e: Event) => {
      const unit = (e as CustomEvent<TFTUnit>).detail
      if (unit) addUnitToRandomHex(unit, rows, cols)
    }

    window.addEventListener('tft-drag-start', handleDragStartGlobal)
    window.addEventListener('dragend', handleDragEndGlobal)
    window.addEventListener('tft-unit-add-random', handleRandomAdd)

    return () => {
      window.removeEventListener('tft-drag-start', handleDragStartGlobal)
      window.removeEventListener('dragend', handleDragEndGlobal)
      window.removeEventListener('tft-unit-add-random', handleRandomAdd)
    }
  }, [rows, cols])

  const width = Math.sqrt(3) * size
  const height = 2 * size
  const xOffset = width
  const yOffset = height * 0.75
  const boardWidth = (cols * xOffset) + (xOffset / 2)
  const boardHeight = (rows * yOffset) + (height * 0.25)
  const drawSize = size - spacing

  const getHexPoints = (cx: number, cy: number) => {
    const points = []
    for (let i = 0; i < 6; i++) {
      const angle_deg = 60 * i - 90
      const angle_rad = (Math.PI / 180) * angle_deg
      const x = cx + drawSize * Math.cos(angle_rad)
      const y = cy + drawSize * Math.sin(angle_rad)
      points.push(`${x.toFixed(2)},${y.toFixed(2)}`)
    }
    return points.join(' ')
  }

  const handleHexClick = (key: string) => {
    if (placements[key]) removeUnit(key)  // only remove if occupied
  }

  const onDragOver = (e: React.DragEvent, key: string) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
    if (hoveredHexKey !== key) setHoveredHexKey(key)
  }

  const onDrop = (e: React.DragEvent, targetKey: string) => {
    e.preventDefault()
    const unitData = e.dataTransfer.getData('tft-unit')
    const sourceKey = e.dataTransfer.getData('source-key')
    if (!unitData) return

    const draggedUnit = JSON.parse(unitData) as TFTUnit
    setHoveredHexKey(null)
    setActiveDragUnit(null)

    if (sourceKey === targetKey) return

    if (sourceKey) {
      // Dragging from one hex to another — swap or move via store
      swapUnit(sourceKey, targetKey)
    } else {
      // Dragging from the champion pool — place new unit
      placeUnit(targetKey, draggedUnit)
    }
  }

  return (
    <div className={cn('w-full h-full flex items-center justify-center p-4', className)}>
      <svg
        viewBox={`0 0 ${boardWidth} ${boardHeight}`}
        className="block w-full h-auto overflow-visible"
        xmlns="http://www.w3.org/2000/svg"
      >
        {Array.from({ length: rows }).map((_, row) =>
          Array.from({ length: cols }).map((_, col) => {
            const indent = row % 2 === 1 ? xOffset / 2 : 0
            const cx = (width / 2) + col * xOffset + indent
            const cy = size + row * yOffset
            const key = `${row}-${col}`
            const unit = placements[key]

            return (
              <g
                key={key}
                onClick={() => handleHexClick(key)}
                onDragOver={(e) => onDragOver(e, key)}
                onDragLeave={() => setHoveredHexKey(null)}
                onDrop={(e) => onDrop(e, key)}
                // React's SVG typings omit `draggable`, but browsers honor it
                // on SVG elements — pass it via spread to satisfy the checker.
                {...{ draggable: !!unit }}
                onDragStart={(e) => {
                  if (!unit) return
                  e.dataTransfer.setData('tft-unit', JSON.stringify(unit))
                  e.dataTransfer.setData('source-key', key)
                  e.dataTransfer.effectAllowed = 'move'
                  setActiveDragUnit(unit)
                  window.dispatchEvent(new CustomEvent('tft-drag-start', { detail: unit }))
                }}
                onDragEnd={(e) => {
                  // Dropped outside the board — remove from store
                  if (e.dataTransfer.dropEffect === 'none') {
                    removeUnit(key)
                  }
                  setActiveDragUnit(null)
                  setHoveredHexKey(null)
                }}
                className="cursor-pointer group"
              >
                <polygon
                  points={getHexPoints(cx, cy)}
                  className={cn(
                    'transition-all duration-200 fill-muted/50 stroke-border stroke-[1.5px]',
                    'group-hover:fill-accent group-hover:fill-opacity-100',
                    unit && 'fill-accent/20 stroke-primary/50',
                    hoveredHexKey === key && 'fill-primary/30 stroke-primary stroke-2',
                  )}
                />
                {unit && (
                  <foreignObject
                    x={cx - (Math.sqrt(3) * drawSize) / 2}
                    y={cy - drawSize}
                    width={Math.sqrt(3) * drawSize}
                    height={2 * drawSize}
                    className="pointer-events-auto"
                  >
                    <div className="flex h-full w-full items-center justify-center">
                      <img
                        src={tftIconUrl(unit.icon)}
                        alt={unit.name}
                        className="h-full w-full object-cover animate-in zoom-in-50 duration-200 shadow-lg"
                        style={{ clipPath: 'polygon(50% 0%, 100% 25%, 100% 75%, 50% 100%, 0% 75%, 0% 25%)' }}
                      />
                    </div>
                  </foreignObject>
                )}

                {/* Ghost preview while dragging */}
                {!unit && hoveredHexKey === key && activeDragUnit && (
                  <foreignObject
                    x={cx - (Math.sqrt(3) * drawSize) / 2}
                    y={cy - drawSize}
                    width={Math.sqrt(3) * drawSize}
                    height={2 * drawSize}
                    className="pointer-events-none opacity-40"
                  >
                    <div className="flex h-full w-full items-center justify-center">
                      <img
                        src={tftIconUrl(activeDragUnit.icon)}
                        alt={activeDragUnit.name}
                        className="h-full w-full object-cover grayscale"
                        style={{ clipPath: 'polygon(50% 0%, 100% 25%, 100% 75%, 50% 100%, 0% 75%, 0% 25%)' }}
                      />
                    </div>
                  </foreignObject>
                )}
              </g>
            )
          })
        )}
      </svg>
    </div>
  )
}
