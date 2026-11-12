// Small shared pieces of the LoRA comparison sheet screens. The stacking rules
// mirror backend/app/lora/sheet_compare.py (the PNG uses that one); these are
// for display, so near enough is fine.

export function fileStem(path) {
  const name = (path || '').slice((path || '').lastIndexOf('/') + 1)
  const dot = name.lastIndexOf('.')
  return dot > 0 ? name.slice(0, dot) : name
}

export function shortModel(path, limit = 30) {
  const s = fileStem(path)
  return s.length <= limit ? s : s.slice(0, limit - 1) + '…'
}

export function rowLabel(row) { return row.label || fileStem(row.lora) }

export function colLabel(t, col) {
  if (!col.trigger_on) return t('loraSheet.noTriggerCol', { s: col.strength })
  return col.strength === 0 ? t('loraSheet.baseCol') : String(col.strength)
}

const colId = col => `${Math.round(Number(col.strength) * 1e4) / 1e4}|${col.trigger_on ? 1 : 0}`

// Every column any of the sheets has: by strength, the no-trigger ones last.
export function mergedColumns(sheets) {
  const seen = new Map()
  for (const s of sheets) {
    for (const c of s.columns_spec || []) {
      if (!seen.has(colId(c))) seen.set(colId(c), { strength: c.strength, trigger_on: c.trigger_on })
    }
  }
  return [...seen.values()].sort((a, b) =>
    (a.trigger_on === b.trigger_on ? 0 : a.trigger_on ? -1 : 1) || a.strength - b.strength)
}

// The sheet's render keys on the merged columns; '' where it has no such column.
export function alignedGrid(sheet, cols) {
  const own = (sheet.columns_spec || []).map(colId)
  return (sheet.grid || []).map(keys => {
    const byCol = new Map(own.map((id, i) => [id, keys[i]]))
    return cols.map(c => byCol.get(colId(c)) || '')
  })
}

const FIELDS = [
  ['unet', 'model', s => s.models?.unet, v => shortModel(v)],
  ['clip', 'te', s => s.models?.clip, v => shortModel(v)],
  ['vae', 'vae', s => s.models?.vae, v => shortModel(v)],
  ['seed', 'seed', s => s.seed, String],
  ['size', 'size', s => `${s.width}×${s.height}`, String],
  ['steps', 'steps', s => s.steps, String],
  ['cfg', 'cfg', s => s.cfg, String],
  ['sampling', 'sampler', s => `${s.sampler}/${s.scheduler}`, String],
  ['prompt', 'prompt', s => s.prompt, v => (v.length > 60 ? v.slice(0, 59) + '…' : v)],
]

// { common: ['model …', 'seed 42'], per: [['seed 1'], ['seed 2']], prompt }
export function diffFields(sheets) {
  const common = []
  const per = sheets.map(() => [])
  let prompt = ''
  for (const [field, label, get, show] of FIELDS) {
    const values = sheets.map(get)
    if (values.every(v => v === values[0])) {
      if (field === 'prompt') prompt = values[0] || ''
      else common.push(`${label} ${show(values[0] ?? '')}`)
      continue
    }
    values.forEach((v, i) => per[i].push(`${label} ${show(v ?? '')}`))
  }
  return { common, per, prompt }
}

export async function api(path, opts = {}) {
  const resp = await fetch(path, {
    headers: opts.body ? { 'Content-Type': 'application/json' } : undefined,
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  })
  if (!resp.ok) {
    let detail = ''
    try { detail = (await resp.json()).detail } catch {}
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail || resp.status))
  }
  return resp.json()
}
