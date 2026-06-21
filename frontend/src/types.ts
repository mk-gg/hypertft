export type Site = {
  title: string
  description: string
  href: string
  author: string
  locale: string
}

export type SocialLink = {
  href: string
  label: string
  icon?: string
}

export type IconMap = {
  [key: string]: string
}

export type PatchMeta = {
  patch: string
  set_number: number
  total_matches: number
  total_participants: number
  total_comps: number
  last_updated: string
  regions: string[]
  available_patches:  string[]
}

export type TFTUnit = {
  id: string
  name: string
  cost: number
  traits: string[]
  icon: string
}

export type TFTTrait = {
  id: string
  name: string
  icon: string
}

export type UnitsData = {
  set: number
  patch: string
  units: TFTUnit[]
  traits: TFTTrait[]
  items: unknown[]
}

export type TFTItem = {
  id: string
  name: string
  icon: string
}

export type PatchData = {
  latest: string
  patches: string[]
}

export type CompRequest = {
  units: string[]
  similarity_threshold?: number
}

export type CompAddition = {
  unit: string
  avg: number
  n: number
}

export type CompResult = {
  units: string[]
  exact: { avg: number; n: number }
  superset: { avg: number; n: number }
  mutations: unknown[]
  additions: CompAddition[]
}

export type TopComp = {
  units: string[]
  exact_avg: number
  exact_n: number
  super_avg: number
  super_n: number
}

export type TopCompsResult = {
  patch: string
  total: number
  comps: TopComp[]
}

export type SuggestRequest = {
  patch?: string
  units: string[]
  similarity_threshold?: number
  limit?: number
}

export type SuggestedComp = {
  units: string[]
  missing: string[]
  exact_avg: number
  exact_n: number
  similarity: number
}

export type CompMutation = {
  unit_out: string
  unit_in: string
  avg: number
  delta: number
  n: number
}

export type CompAdditionSuggest = {
  unit: string
  avg: number
  delta: number
  n: number
}

export type ItemStat = {
  item: string
  avg: number
  delta: number
  n: number
}

export type UnitItemStats = {
  unit: string
  items: ItemStat[]
}

export type SuggestResult = {
  board: string[]
  patch: string
  threshold_used: number
  superset_avg: number | null  // null when the board has no superset match (super_n === 0)
  superset_n: number
  suggested_comps: SuggestedComp[]
  additions: CompAdditionSuggest[]
  mutations: CompMutation[]
  exact_items: UnitItemStats[]   
}