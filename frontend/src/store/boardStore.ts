import { atom, computed } from 'nanostores'
import type { TFTUnit } from '@/types'
import { BOARD_ROWS, BOARD_COLS } from '@/consts'

/** Hex key (`"row-col"`) → unit placed on that hex. */
export const $placements = atom<Record<string, TFTUnit>>({})

/** Names of all units currently on the board. */
export const $activeUnits = computed($placements, (p) =>
  Object.values(p).map((u) => u.name)
)

export function placeUnit(key: string, unit: TFTUnit) {
  $placements.set({ ...$placements.get(), [key]: unit })
}

export function removeUnit(key: string) {
  const next = { ...$placements.get() }
  delete next[key]
  $placements.set(next)
}

/** Move a unit between hexes, swapping if the target is occupied. */
export function swapUnit(fromKey: string, toKey: string) {
  const next = { ...$placements.get() }
  const temp = next[toKey]
  next[toKey] = next[fromKey]
  if (temp) next[fromKey] = temp
  else delete next[fromKey]
  $placements.set(next)
}

/** Place a unit on a random empty hex; no-op when the board is full. */
export function addUnitToRandomHex(unit: TFTUnit, rows = BOARD_ROWS, cols = BOARD_COLS) {
  const current = $placements.get()
  const allKeys = Array.from({ length: rows }, (_, r) =>
    Array.from({ length: cols }, (_, c) => `${r}-${c}`)
  ).flat()
  const empty = allKeys.filter((k) => !current[k])
  if (!empty.length) return
  const key = empty[Math.floor(Math.random() * empty.length)]
  $placements.set({ ...current, [key]: unit })
}

/**
 * Look up a unit by display name and place it on a random empty hex.
 * Suggestion payloads reference units by name only, so callers resolve
 * them through the units map from the API.
 */
export function addUnitByName(name: string, unitsMap: Map<string, TFTUnit>) {
  const unit = unitsMap.get(name.toLowerCase())
  if (unit) addUnitToRandomHex(unit)
}

/**
 * Replace a unit on the board (matched by name) with another.
 * Falls back to placing the incoming unit on a random empty hex
 * when the outgoing unit isn't on the board.
 */
export function swapUnitByName(
  unitOutName: string,
  unitInName: string,
  unitsMap: Map<string, TFTUnit>,
) {
  const unitIn = unitsMap.get(unitInName.toLowerCase())
  if (!unitIn) return

  const key = Object.entries($placements.get()).find(
    ([, u]) => u.name.toLowerCase() === unitOutName.toLowerCase()
  )?.[0]

  if (key) placeUnit(key, unitIn)
  else addUnitToRandomHex(unitIn)
}
