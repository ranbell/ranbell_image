/*
 * Danbooru colour words → something you can actually look at.
 *
 * Filtering a hundred characters by "black_hair" as a word is reading; doing it
 * by a black dot is glancing. The values are the colours as they read in an
 * illustration rather than as they read in a paint catalogue — anime "grey
 * hair" is closer to silver than to concrete, and "amber eyes" is a warm gold.
 *
 * Two things are asked of a colour word, and they want different answers:
 *
 *   `hairSwatch` / `eyeSwatch` — her own shade, for the dot on her card. Every
 *   word gets one, including the modified ones: the roster says `chestnut_hair`
 *   and `pale_blue_eyes`, and a table of plain colour names answered eleven of
 *   the thirty with the same fallback grey. Eleven identical grey dots is not a
 *   colour you can glance at.
 *
 *   `colorFamily` — the group, for the filter row. Twenty-two hair dots for
 *   thirty characters is not a filter either; sixteen of them selected exactly
 *   one person, and `brown` / `dark_brown` / `chestnut` were three dots a
 *   person cannot tell apart and would never mean separately. Clicking brown
 *   should find every brown-haired character.
 */

// Base colours. A modified word (`dark_`, `light_`, `pale_`) is shaded from
// these rather than needing its own entry.
const HAIR = {
  black: '#23212a',
  brown: '#6b4a32',
  chestnut: '#7d4a35',
  blonde: '#e0c070',
  grey: '#b8bcc4',
  silver: '#d8dde4',
  white: '#f0f0f2',
  red: '#b8402f',
  orange: '#e08340',
  pink: '#e79ab8',
  purple: '#8f6bb0',
  blue: '#5a7fc4',
  navy: '#33436e',
  aqua: '#68c0c0',
  teal: '#3f9a9a',
  green: '#5f9c62',
}

const EYES = {
  black: '#2a2730',
  brown: '#7a5334',
  hazel: '#94743e',
  amber: '#d09a3c',
  grey: '#a8adb6',
  silver: '#c6ccd4',
  blue: '#4f86c8',
  navy: '#33436e',
  green: '#57a065',
  aqua: '#4fb3b3',
  teal: '#3f9a9a',
  purple: '#9070b8',
  violet: '#a06fd0',
  red: '#c04440',
  pink: '#e498b4',
  yellow: '#dcc054',
  orange: '#d5813c',
}

// Shades that read as the same colour to someone scanning a filter row. The
// exact word keeps its own dot on her card; only the grouping collapses.
const FAMILY_OF = {
  chestnut: 'brown',
  hazel: 'brown',
  navy: 'blue',
  silver: 'grey',
  violet: 'purple',
  teal: 'aqua',
}

// `dark_blue_hair` → 0.62× the blue; `light_pink_hair` → most of the way to
// white. Written as multipliers so a word nobody has thought of still lands
// near the right colour instead of on the fallback.
const SHADE = { dark: -0.38, deep: -0.38, light: 0.45, pale: 0.55, bright: 0.15 }

const FALLBACK = '#6b6b75'

/** `light_brown_hair` → `['light', 'brown']`; `black_hair` → `[null, 'black']`. */
function parts(tag, noun) {
  const word = String(tag || '').trim().toLowerCase()
    .replace(new RegExp(`_${noun}$`), '')
  const [head, ...rest] = word.split('_')
  if (rest.length && head in SHADE) return [head, rest.join('_')]
  return [null, word]
}

function shade(hex, amount) {
  const n = parseInt(hex.slice(1), 16)
  const mix = (c) => {
    const towards = amount < 0 ? 0 : 255
    return Math.round(c + (towards - c) * Math.abs(amount))
  }
  const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map(mix)
  return `#${((r << 16) | (g << 8) | b).toString(16).padStart(6, '0')}`
}

function swatch(table, tag, noun) {
  const [modifier, base] = parts(tag, noun)
  const hex = table[base]
  if (!hex) return FALLBACK
  return modifier ? shade(hex, SHADE[modifier]) : hex
}

export function hairSwatch(tag) { return swatch(HAIR, tag, 'hair') }
export function eyeSwatch(tag) { return swatch(EYES, tag, 'eyes') }

/** True when the word resolved to a real colour rather than the fallback. */
export function hasSwatch(tag, kind = 'hair') {
  const [, base] = parts(tag, kind === 'eyes' ? 'eyes' : 'hair')
  return base in (kind === 'eyes' ? EYES : HAIR)
}

/**
 * The group a colour word belongs to, for filtering: `dark_brown_hair`,
 * `chestnut_hair` and `brown_hair` are all `brown`. Unknown words keep
 * themselves, so a colour nobody anticipated is still its own filter rather
 * than silently joining another.
 */
export function colorFamily(tag, kind = 'hair') {
  const [, base] = parts(tag, kind === 'eyes' ? 'eyes' : 'hair')
  return FAMILY_OF[base] || base || ''
}

/** The dot for a whole family — the unmodified base colour. */
export function familySwatch(family, kind = 'hair') {
  const table = kind === 'eyes' ? EYES : HAIR
  return table[family] || FALLBACK
}

/** The word without its noun: `black_hair` → `black`. */
export function colorWord(tag) {
  return String(tag || '').replace(/_(hair|eyes)$/, '').replace(/_/g, ' ')
}

/*
 * A character's own palette is free text ("wine", "deep green", "brass"), so it
 * cannot be looked up. CSS knows most of the plain ones; the rest get a guess
 * from whichever known word they contain, and anything left over is skipped
 * rather than shown as a wrong colour.
 */
const PALETTE_HINTS = {
  wine: '#722f37', brass: '#b5a642', charcoal: '#36454f', cream: '#f5eddb',
  indigo: '#4b0082', ivory: '#fffff0', navy: '#1b2a4a', rust: '#b7410e',
  slate: '#708090', sand: '#c2b280', moss: '#8a9a5b', plum: '#8e4585',
  amber: '#ffbf00', copper: '#b87333', denim: '#3b5a80', mustard: '#e1ad01',
  sage: '#9caf88', terracotta: '#e2725b', mint: '#98ff98', peach: '#ffe5b4',
  lavender: '#e6e6fa', burgundy: '#800020', teal: '#008080', ochre: '#cc7722',
}

export function paletteSwatch(word) {
  const name = String(word || '').trim().toLowerCase()
  if (!name) return ''
  if (PALETTE_HINTS[name]) return PALETTE_HINTS[name]
  for (const [key, hex] of Object.entries(PALETTE_HINTS)) {
    if (name.includes(key)) return hex
  }
  // "deep green", "pale blue" — the last word is usually the colour itself.
  const last = name.split(/\s+/).pop()
  return CSS.supports?.('color', last) ? last : ''
}
