import { atom, computed } from 'nanostores'
import type { TFTUnit } from '@/types'

// Key → unit placements on the board
export const $placements = atom<Record<string, TFTUnit>>({})

// Derived unit names list — what RecommendedPanelTabs already reads
export const $activeUnits = computed($placements, (p) =>
  Object.values(p).map((u) => u.name)
)

// Actions
export function placeUnit(key: string, unit: TFTUnit) {
  $placements.set({ ...$placements.get(), [key]: unit })
}

export function removeUnit(key: string) {
  const next = { ...$placements.get() }
  delete next[key]
  $placements.set(next)
}

export function swapUnit(fromKey: string, toKey: string) {
  const next = { ...$placements.get() }
  const temp = next[toKey]
  next[toKey] = next[fromKey]
  if (temp) next[fromKey] = temp
  else delete next[fromKey]
  $placements.set(next)
}

export function addUnitToRandomHex(unit: TFTUnit, rows: number, cols: number) {
  const current = $placements.get()
  const allKeys = Array.from({ length: rows }, (_, r) =>
    Array.from({ length: cols }, (_, c) => `${r}-${c}`)
  ).flat()
  const empty = allKeys.filter((k) => !current[k])
  if (!empty.length) return
  const key = empty[Math.floor(Math.random() * empty.length)]
  $placements.set({ ...current, [key]: unit })
}