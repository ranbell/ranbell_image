/**
 * The spooler's task name, in the reader's language.
 *
 * `job.title` is the identifier a job was submitted under (`generate_actress_diary`,
 * `muse_board`). The Control Room printed it raw, so the console read as source
 * code. The names live in `jobTitle.*` in both locales.
 *
 * Some titles carry a variable tail — `comfy_generate (API_Anima_v3.json)`,
 * `invoke.spirit/水`, `character_board:signature`. The stem is translated and the
 * tail is kept as it is, because that part is a filename or a name and is not
 * ours to translate.
 *
 * An unknown title is printed as it stands. A new job must never come out blank.
 */
const SPLITTERS = [' (', '/', ':']

export function jobLabel(title, { t, te }) {
  const raw = String(title ?? '').trim()
  if (!raw) return ''
  const key = (name) => `jobTitle.${name}`
  if (te(key(raw))) return t(key(raw))
  for (const sep of SPLITTERS) {
    const at = raw.indexOf(sep)
    if (at <= 0) continue
    const stem = raw.slice(0, at)
    if (!te(key(stem))) continue
    const tail = raw.slice(at + sep.length).replace(/\)$/, '').trim()
    return tail ? `${t(key(stem))} (${tail})` : t(key(stem))
  }
  return raw
}
