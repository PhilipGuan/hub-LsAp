import { useEffect, useRef, useState } from 'react'
import type { ChangeEvent } from 'react'
import { analyze, recompute } from './api'
import { formatNum, pickText, tr } from './copy'
import ItemCard from './components/ItemCard'
import type {
  AnalyzeResponse,
  EditableItem,
  Locale,
  RecomputeItem,
} from './types'

const MIN_GRAMS = 5
const MAX_GRAMS = 1000
const DEBOUNCE_MS = 400

let uid = 0
function nextKey(): string {
  uid += 1
  return `item-${uid}`
}

function toEditable(items: AnalyzeResponse['items']): EditableItem[] {
  return items.map((item) => ({ ...item, key: nextKey() }))
}

function toRecomputeItem(item: EditableItem): RecomputeItem {
  if (item.matched_id) {
    return { id: item.matched_id, grams: item.grams }
  }
  return { name_zh: item.name.zh, name_en: item.name.en, grams: item.grams }
}

function describeError(error: unknown): string {
  if (error instanceof Error) return error.message
  return String(error)
}

function clampGrams(value: number): number {
  return Math.min(MAX_GRAMS, Math.max(MIN_GRAMS, value))
}

export default function App() {
  const [locale, setLocale] = useState<Locale>('zh-CN')
  const [mode, setMode] = useState<'meal' | 'label'>('meal')
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<string | null>(null)
  const [text, setText] = useState('')
  const [items, setItems] = useState<EditableItem[]>([])
  const [result, setResult] = useState<AnalyzeResponse | null>(null)
  const [analyzing, setAnalyzing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [draftName, setDraftName] = useState('')
  const [draftGrams, setDraftGrams] = useState(100)

  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const previewRef = useRef<string | null>(null)
  const c = tr(locale)

  useEffect(() => {
    return () => {
      if (timer.current) clearTimeout(timer.current)
      if (previewRef.current) URL.revokeObjectURL(previewRef.current)
    }
  }, [])

  const applyRecompute = (response: AnalyzeResponse) => {
    setResult(response)
    setItems((prev) =>
      prev.map((item, index) => {
        const updated = response.items[index]
        if (!updated) return item
        return {
          ...item,
          name: updated.name,
          matched_id: updated.matched_id,
          kcal: updated.kcal,
          protein: updated.protein,
          fat: updated.fat,
          carb: updated.carb,
          confidence: updated.confidence,
          status: updated.status,
          note: updated.note,
        }
      }),
    )
  }

  const scheduleRecompute = (nextItems: EditableItem[]) => {
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => {
      recompute({ locale, items: nextItems.map(toRecomputeItem) })
        .then(applyRecompute)
        .catch((err) => setError(describeError(err)))
    }, DEBOUNCE_MS)
  }

  const selectFile = (selected: File | null) => {
    if (previewRef.current) {
      URL.revokeObjectURL(previewRef.current)
      previewRef.current = null
    }
    setFile(selected)
    if (selected) {
      const url = URL.createObjectURL(selected)
      previewRef.current = url
      setPreview(url)
    } else {
      setPreview(null)
    }
  }

  const handleFile = (event: ChangeEvent<HTMLInputElement>) => {
    selectFile(event.target.files?.[0] ?? null)
  }

  const handleGrams = (key: string, grams: number) => {
    const next = items.map((item) =>
      item.key === key ? { ...item, grams } : item,
    )
    setItems(next)
    scheduleRecompute(next)
  }

  const handleDelete = (key: string) => {
    const next = items.filter((item) => item.key !== key)
    setItems(next)
    scheduleRecompute(next)
  }

  const handleAdd = () => {
    const name = draftName.trim()
    if (!name) return
    const item: EditableItem = {
      key: nextKey(),
      name: { zh: name, en: name },
      matched_id: null,
      grams: clampGrams(draftGrams || MIN_GRAMS),
      kcal: 0,
      protein: 0,
      fat: 0,
      carb: 0,
      confidence: 0,
      status: 'estimated',
      note: null,
    }
    const next = [...items, item]
    setItems(next)
    setDraftName('')
    setDraftGrams(100)
    scheduleRecompute(next)
  }

  const handleAnalyze = async () => {
    if (!file && !text.trim()) {
      setError(c('needInput'))
      return
    }
    setAnalyzing(true)
    setError(null)
    try {
      const form = new FormData()
      if (file) form.append('image', file)
      if (text.trim()) form.append('text', text.trim())
      form.append('mode', mode)
      form.append('locale', locale)
      const response = await analyze(form)
      setResult(response)
      setItems(toEditable(response.items))
    } catch (err) {
      setError(describeError(err))
    } finally {
      setAnalyzing(false)
    }
  }

  return (
    <div className="app">
      <header className="app-header">
        <div className="app-header__top">
          <h1 className="app-title">{c('appTitle')}</h1>
          <div className="locale-switch" role="group" aria-label="Language">
            <button
              type="button"
              className={locale === 'zh-CN' ? 'is-active' : ''}
              onClick={() => setLocale('zh-CN')}
            >
              {c('localeZh')}
            </button>
            <button
              type="button"
              className={locale === 'en' ? 'is-active' : ''}
              onClick={() => setLocale('en')}
            >
              {c('localeEn')}
            </button>
          </div>
        </div>
        <p className="app-subtitle">{c('appSubtitle')}</p>
      </header>

      <section className="panel">
        <div className="mode-switch" role="group" aria-label={c('modeMeal')}>
          <button
            type="button"
            className={mode === 'meal' ? 'is-active' : ''}
            onClick={() => setMode('meal')}
          >
            {c('modeMeal')}
          </button>
          <button
            type="button"
            className={mode === 'label' ? 'is-active' : ''}
            onClick={() => setMode('label')}
          >
            {c('modeLabel')}
          </button>
        </div>

        <label className="upload-box">
          <input
            type="file"
            accept="image/*"
            capture="environment"
            onChange={handleFile}
            hidden
          />
          <span className="upload-box__cta">
            {file ? c('changeImage') : c('uploadCta')}
          </span>
          <span className="upload-box__hint">{c('uploadHint')}</span>
        </label>

        {preview && (
          <div className="preview">
            <img src={preview} alt="" />
            <button
              type="button"
              className="preview__remove"
              onClick={() => selectFile(null)}
            >
              {c('removeImage')}
            </button>
          </div>
        )}

        <label className="field">
          <span className="field__label">{c('textLabel')}</span>
          <textarea
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder={c('textPlaceholder')}
            rows={2}
          />
        </label>

        <button
          type="button"
          className="btn btn--primary"
          onClick={handleAnalyze}
          disabled={analyzing}
        >
          {analyzing ? c('analyzing') : c('analyzeButton')}
        </button>
      </section>

      {error && (
        <div className="banner banner--error" role="alert">
          <div>
            <strong>{c('errorTitle')}</strong>
            <p>{error}</p>
          </div>
          <button
            type="button"
            className="btn btn--ghost"
            onClick={handleAnalyze}
            disabled={analyzing}
          >
            {c('retry')}
          </button>
        </div>
      )}

      {result && (
        <section className="results">
          <div className="totals">
            <div className="totals__kcal">
              <span className="totals__value">
                {formatNum(result.totals.kcal)}
              </span>
              <span className="totals__unit">{c('kcalUnit')}</span>
            </div>
            <span className="totals__caption">{c('totalKcal')}</span>
            <div className="totals__macros">
              <div className="macro">
                <span className="macro__value">
                  {formatNum(result.totals.protein, 1)}
                  <span className="macro__unit">g</span>
                </span>
                <span className="macro__label">{c('protein')}</span>
              </div>
              <div className="macro">
                <span className="macro__value">
                  {formatNum(result.totals.fat, 1)}
                  <span className="macro__unit">g</span>
                </span>
                <span className="macro__label">{c('fat')}</span>
              </div>
              <div className="macro">
                <span className="macro__value">
                  {formatNum(result.totals.carb, 1)}
                  <span className="macro__unit">g</span>
                </span>
                <span className="macro__label">{c('carb')}</span>
              </div>
            </div>
          </div>

          {result.warnings.length > 0 && (
            <div className="banner banner--warn">
              <div>
                <strong>{c('warningsTitle')}</strong>
                <ul>
                  {result.warnings.map((warning, index) => (
                    <li key={index}>{pickText(warning, locale)}</li>
                  ))}
                </ul>
              </div>
            </div>
          )}

          <h2 className="section-title">{c('perDish')}</h2>

          {items.length === 0 ? (
            <p className="empty">{c('emptyItems')}</p>
          ) : (
            <div className="cards">
              {items.map((item) => (
                <ItemCard
                  key={item.key}
                  item={item}
                  locale={locale}
                  onGramsChange={handleGrams}
                  onDelete={handleDelete}
                />
              ))}
            </div>
          )}

          <div className="add-dish">
            <h3 className="add-dish__title">{c('addDishTitle')}</h3>
            <div className="add-dish__row">
              <input
                type="text"
                value={draftName}
                onChange={(event) => setDraftName(event.target.value)}
                placeholder={c('namePlaceholder')}
              />
              <div className="add-dish__grams">
                <input
                  type="number"
                  min={MIN_GRAMS}
                  max={MAX_GRAMS}
                  value={draftGrams}
                  onChange={(event) => setDraftGrams(Number(event.target.value))}
                />
                <span>{c('gUnit')}</span>
              </div>
            </div>
            <button
              type="button"
              className="btn btn--ghost"
              onClick={handleAdd}
              disabled={!draftName.trim()}
            >
              {c('addButton')}
            </button>
          </div>
        </section>
      )}
    </div>
  )
}
