import type { ChangeEvent } from 'react'
import { SOURCE_LABELS, formatNum, pickText, tr } from '../copy'
import type { CopyKey } from '../copy'
import type { EditableItem, ItemStatus, Locale } from '../types'

const STATUS_KEY: Record<ItemStatus, CopyKey> = {
  matched: 'statusMatched',
  unmatched: 'statusUnmatched',
  estimated: 'statusEstimated',
}

interface ItemCardProps {
  item: EditableItem
  locale: Locale
  onGramsChange: (key: string, grams: number) => void
  onDelete: (key: string) => void
}

export default function ItemCard({
  item,
  locale,
  onGramsChange,
  onDelete,
}: ItemCardProps) {
  const c = tr(locale)
  const name = pickText(item.name, locale)
  const grams = Math.min(1000, Math.max(5, item.grams))

  const handleSlider = (event: ChangeEvent<HTMLInputElement>) => {
    onGramsChange(item.key, Number(event.target.value))
  }

  return (
    <article className={`item-card status-${item.status}`}>
      <header className="item-card__head">
        <div className="item-card__title">
          <h3>{name}</h3>
          {item.matched_id && (
            <span className="item-card__id">
              {c('matchedIdLabel')}: {item.matched_id}
            </span>
          )}
        </div>
        <div className="item-card__head-right">
          <span className={`badge badge--${item.status}`}>
            {c(STATUS_KEY[item.status])}
          </span>
          <button
            type="button"
            className="icon-btn"
            aria-label={c('removeDish')}
            title={c('removeDish')}
            onClick={() => onDelete(item.key)}
          >
            ×
          </button>
        </div>
      </header>

      <div className="item-card__amount">
        <label className="amount-label">
          <span>{c('gramsLabel')}</span>
          <span className="amount-value">
            {formatNum(item.grams)} {c('gUnit')}
          </span>
        </label>
        <input
          type="range"
          min={5}
          max={1000}
          step={5}
          value={grams}
          onChange={handleSlider}
        />
      </div>

      <div className="item-card__macros">
        <div className="macro macro--kcal">
          <span className="macro__value">
            {formatNum(item.kcal)}
            <span className="macro__unit">{c('kcalUnit')}</span>
          </span>
          <span className="macro__label">{c('totalKcal')}</span>
        </div>
        <div className="macro">
          <span className="macro__value">
            {formatNum(item.protein, 1)}
            <span className="macro__unit">g</span>
          </span>
          <span className="macro__label">{c('protein')}</span>
        </div>
        <div className="macro">
          <span className="macro__value">
            {formatNum(item.fat, 1)}
            <span className="macro__unit">g</span>
          </span>
          <span className="macro__label">{c('fat')}</span>
        </div>
        <div className="macro">
          <span className="macro__value">
            {formatNum(item.carb, 1)}
            <span className="macro__unit">g</span>
          </span>
          <span className="macro__label">{c('carb')}</span>
        </div>
      </div>

      <footer className="item-card__foot">
        <span className="confidence">
          {c('confidenceLabel')} {Math.round(item.confidence * 100)}%
        </span>
        {item.source && (
          <span className="item-card__source">
            {c('sourceLabel')}{' '}
            {pickText(SOURCE_LABELS[item.source] ?? null, locale) || item.source}
          </span>
        )}
        {item.note && (
          <span className="item-card__note">{pickText(item.note, locale)}</span>
        )}
      </footer>
    </article>
  )
}
