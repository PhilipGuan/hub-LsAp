// 与后端 API 契约（冻结）严格对应的类型定义。

export type Locale = 'zh-CN' | 'en'

export type AnalysisMode = 'meal' | 'label'

export interface LocalizedText {
  zh: string
  en: string
}

export type ItemStatus = 'matched' | 'unmatched' | 'estimated'

export interface AnalyzedItem {
  name: LocalizedText
  matched_id: string | null
  grams: number
  kcal: number
  protein: number
  fat: number
  carb: number
  confidence: number
  status: ItemStatus
  note: LocalizedText | null
  /** 命中的库条目来源（seed / manual / usda / china_fct），未命中为 null */
  source?: string | null
}

export interface Totals {
  kcal: number
  protein: number
  fat: number
  carb: number
}

export interface AnalyzeResponse {
  mode: AnalysisMode
  items: AnalyzedItem[]
  totals: Totals
  warnings: LocalizedText[]
}

export interface RecomputeItem {
  id?: string | null
  name_zh?: string | null
  name_en?: string | null
  grams: number
}

export interface RecomputeRequest {
  locale: Locale
  items: RecomputeItem[]
}

export interface HealthResponse {
  provider: string
  status: string
  nutrition_count: number
}

/** 前端本地可编辑条目：在 API 条目基础上附加稳定的本地 key。 */
export interface EditableItem extends AnalyzedItem {
  key: string
}
