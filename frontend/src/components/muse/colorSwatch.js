/*
 * Danbooru colour words → something you can actually look at.
 *
 * Filtering a hundred characters by "black_hair" as a word is reading; doing it
 * by a black dot is glancing. The values are the colours as they read in an
 * illustration rather than as they read in a paint catalogue — anime "grey
 * hair" is closer to silver than to concrete, and "amber eyes" is a warm gold.
 */
const HAIR = {
  black_hair: '#23212a',
  brown_hair: '#6b4a32',
  blonde_hair: '#e0c070',
  grey_hair: '#b8bcc4',
  silver_hair: '#d8dde4',
  white_hair: '#f0f0f2',
  red_hair: '#b8402f',
  orange_hair: '#e08340',
  pink_hair: '#e79ab8',
  purple_hair: '#8f6bb0',
  blue_hair: '#5a7fc4',
  aqua_hair: '#68c0c0',
  green_hair: '#5f9c62',
}

const EYES = {
  black_eyes: '#2a2730',
  brown_eyes: '#7a5334',
  amber_eyes: '#d09a3c',
  grey_eyes: '#a8adb6',
  blue_eyes: '#4f86c8',
  green_eyes: '#57a065',
  purple_eyes: '#9070b8',
  red_eyes: '#c04440',
  pink_eyes: '#e498b4',
  yellow_eyes: '#dcc054',
}

const FALLBACK = '#6b6b75'

export function hairSwatch(tag) { return HAIR[tag] || FALLBACK }
export function eyeSwatch(tag) { return EYES[tag] || FALLBACK }

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
