import type {
    PatchData,
    PatchMeta,
    UnitsData,
    CompRequest,
    CompResult,
    TopCompsResult,
    TFTUnit,
    SuggestRequest,
    SuggestResult,
    TFTTrait,
    TFTItem,
} from '@/types'

// Prefixed with PUBLIC_ so Astro inlines it into the client bundle too —
// this module is imported by both .astro (server) and .tsx (client island) code.
const BASE_URL = import.meta.env.PUBLIC_TFT_API_URL ?? 'http://localhost:8000'

async function apiFetch<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      accept: 'application/json',
      ...init?.headers,
    },
  })
  if (!res.ok) {
    const errorBody = await res.json().catch(() => ({}))
    const detail = errorBody.detail ? JSON.stringify(errorBody.detail) : res.statusText
    throw new Error(`[TFT API] ${init?.method ?? 'GET'} ${path} → ${res.status}: ${detail}`)
  }
  return res.json() as Promise<T>
}

export function getPatches(): Promise<PatchData> {
  return apiFetch<PatchData>('/comp/patches')
}

export function getMeta(): Promise<PatchMeta> {
  return apiFetch<PatchMeta>('/meta')
}

export function getUnits(): Promise<UnitsData> {
  return apiFetch<UnitsData>('/meta/units')
}

export function getTopComps(patch: string, minN = 1): Promise<TopCompsResult> {
  return apiFetch<TopCompsResult>(
    `/comp/top?patch=${encodeURIComponent(patch)}&min_n=${minN}`,
  )
}

export function postComp(body: CompRequest): Promise<CompResult> {
  return apiFetch<CompResult>('/comp', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

let _unitsCache: UnitsData | null = null

export async function getUnitsMap(): Promise<Map<string, TFTUnit>> {
  if (!_unitsCache) _unitsCache = await getUnits()
  const map = new Map<string, TFTUnit>()
  for (const unit of _unitsCache.units) {
    // key by lowercase name for easy lookup from comp unit strings
    map.set(unit.name.toLowerCase(), unit)
  }
  return map
}

export async function getTraitsMap(): Promise<Map<string, TFTTrait>> {
  if (!_unitsCache) _unitsCache = await getUnits()
  const map = new Map<string, TFTTrait>()
  for (const trait of _unitsCache.traits) {
    map.set(trait.name.toLowerCase(), trait)
    map.set(trait.id.toLowerCase(), trait)   // also index by id
  }
  return map
}

export async function getItemsMap(): Promise<Map<string, TFTItem>> {
  if (!_unitsCache) _unitsCache = await getUnits()
  const map = new Map<string, TFTItem>()
  for (const item of _unitsCache.items) {
    map.set(item.name.toLowerCase(), item)
    map.set(item.id.toLowerCase(), item)
  }
  return map
}

export function postSuggest(body: SuggestRequest): Promise<SuggestResult> {
  const payload: Record<string, unknown> = {
    units: body.units,
  }
  if (body.similarity_threshold !== undefined) {
    payload.similarity_threshold = body.similarity_threshold
  }
  if (body.limit !== undefined) {
    payload.limit = body.limit
  }

  return apiFetch<SuggestResult>('/comp/suggest', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}