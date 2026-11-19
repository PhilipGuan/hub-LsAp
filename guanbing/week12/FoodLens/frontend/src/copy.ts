// 集中定义所有静态 UI 文案（中英双语）。
import type { Locale, LocalizedText } from './types'

export const COPY = {
  appTitle: { zh: 'FoodLens 食物热量识别', en: 'FoodLens Calorie Lens' },
  appSubtitle: {
    zh: '拍照识别一餐，逐道估算热量与三大营养素',
    en: 'Photograph a meal to estimate calories and macros per dish',
  },

  localeZh: { zh: '中文', en: '中文' },
  localeEn: { zh: 'English', en: 'English' },

  modeMeal: { zh: '拍一餐', en: 'Meal' },
  modeLabel: { zh: '营养标签', en: 'Nutrition label' },

  uploadCta: { zh: '拍照 / 上传图片', en: 'Take photo / Upload image' },
  uploadHint: {
    zh: '支持手机相机拍摄，或从相册选择',
    en: 'Use your camera or pick from your gallery',
  },
  changeImage: { zh: '更换图片', en: 'Change image' },
  removeImage: { zh: '移除', en: 'Remove' },

  textLabel: { zh: '补充描述（可选）', en: 'Notes (optional)' },
  textPlaceholder: {
    zh: '例如：红烧肉、米饭、清炒时蔬',
    en: 'e.g. braised pork, rice, stir-fried greens',
  },
  needInput: {
    zh: '请拍照、上传图片或输入描述',
    en: 'Add a photo, upload an image, or enter a description',
  },

  analyzeButton: { zh: '分析', en: 'Analyze' },
  analyzing: { zh: '分析中…', en: 'Analyzing…' },

  errorTitle: { zh: '出错了', en: 'Something went wrong' },
  retry: { zh: '重试', en: 'Retry' },

  totalKcal: { zh: '总热量', en: 'Total calories' },
  kcalUnit: { zh: '千卡', en: 'kcal' },
  protein: { zh: '蛋白质', en: 'Protein' },
  fat: { zh: '脂肪', en: 'Fat' },
  carb: { zh: '碳水化合物', en: 'Carbs' },
  gUnit: { zh: '克', en: 'g' },

  perDish: { zh: '每道菜', en: 'Dishes' },
  emptyItems: { zh: '未识别到任何菜品', en: 'No dishes recognized' },
  gramsLabel: { zh: '克数', en: 'Amount' },
  confidenceLabel: { zh: '置信度', en: 'Confidence' },
  sourceLabel: { zh: '数据来源', en: 'Source' },
  matchedIdLabel: { zh: '库条目', en: 'Library entry' },
  statusMatched: { zh: '已匹配', en: 'Matched' },
  statusUnmatched: { zh: '未收录', en: 'Not in database' },
  statusEstimated: { zh: '估算', en: 'Estimated' },
  removeDish: { zh: '删除该菜品', en: 'Remove dish' },

  warningsTitle: { zh: '提示', en: 'Warnings' },

  addDishTitle: { zh: '添加菜品', en: 'Add a dish' },
  namePlaceholder: { zh: '菜品名称', en: 'Dish name' },
  addButton: { zh: '添加', en: 'Add' },
} as const

export type CopyKey = keyof typeof COPY

/** 营养库来源标签，键与后端 FoodRecord.source 取值一致。 */
export const SOURCE_LABELS: Record<string, LocalizedText> = {
  seed: { zh: '自建库', en: 'Built-in' },
  manual: { zh: '自建库', en: 'Built-in' },
  usda: { zh: 'USDA', en: 'USDA' },
  china_fct: { zh: '中国食物成分表', en: 'China FCT' },
}

/** 返回一个绑定到指定语言的取词函数。 */
export function tr(locale: Locale): (key: CopyKey) => string {
  const lang: 'zh' | 'en' = locale === 'en' ? 'en' : 'zh'
  return (key: CopyKey) => COPY[key][lang]
}

/** 从接口返回的 { zh, en } 文本中取出对应语言。 */
export function pickText(text: LocalizedText | null | undefined, locale: Locale): string {
  if (!text) return ''
  return locale === 'en' ? text.en : text.zh
}

/** 数值格式化（固定小数位，非法值回退为 0）。 */
export function formatNum(value: number, digits = 0): string {
  if (!Number.isFinite(value)) return (0).toFixed(digits)
  return value.toFixed(digits)
}
