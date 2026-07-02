import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}


export function tftIconUrl(iconPath: string): string {
  const relative = iconPath
    .toLowerCase()
    .replace(/\.tex$/, '.png')
  return `https://raw.communitydragon.org/latest/game/${relative}`
}
export function avgToTier(avg: number): string {
  if (avg <= 4.25) return 'S'
  if (avg <= 4.50) return 'A'
  if (avg <= 4.75) return 'B'
  if (avg <= 5.0) return 'C'
  return 'D'
}

export function tierColor(tier: string): string {
  const colors: Record<string, string> = {
    'S': '#f0c040',
    'A':  '#e89a2a',
    'B':  '#7ec86e',
    'C':  '#5b9fd6',
    'D':  '#98a0b3',
  }
  return colors[tier] ?? '#98a0b3'
}

// Single source of truth for the per-cost unit border color (1→5).
// Full literal class strings so Tailwind's scanner keeps them (no dynamic
// interpolation — `border-[${hex}]` would get purged from the build).
const COST_BORDER_CLASS: Record<number, string> = {
  1: 'border-[#808080]/60',
  2: 'border-[#22a55a]/60',
  3: 'border-[#2f6fd6]/60',
  4: 'border-[#b44af0]/60',
  5: 'border-[#f0c040]/60',
}

export function costBorderClass(cost: number): string {
  return COST_BORDER_CLASS[cost] ?? COST_BORDER_CLASS[1]
}

/**
 * Color for an avg-placement delta: green for a strong improvement,
 * gold for a moderate one, gray otherwise.
 */
export function deltaColor(delta: number): string {
  return delta < -1.5 ? '#22a55a' : delta < -0.8 ? '#c89b3c' : '#98a0b3'
}