/*
 * Compiles the SFCs for real (script setup plus template into one module) and looks
 * for **identifiers that are used without being defined**.
 *
 * A build passes and misses this — when `mergeRewriteLog`'s definition alone was
 * deleted and the call left behind, Vite passed silently and at runtime the catch{}
 * in `refresh()` swallowed it.
 */
import { readFileSync } from 'node:fs'
import { parse, compileScript } from 'vue/compiler-sfc'
import { transform } from 'esbuild'

const KNOWN = new Set([
  // Browser and standard library
  'window','document','console','localStorage','sessionStorage','navigator','location',
  'setTimeout','clearTimeout','setInterval','clearInterval','fetch','EventSource','URL',
  'Math','JSON','Date','Number','String','Boolean','Object','Array','Map','Set','Promise',
  'RegExp','Error','isNaN','parseInt','parseFloat','encodeURIComponent','decodeURIComponent',
  'undefined','NaN','Infinity','globalThis','structuredClone','AbortController','FormData',
  'Blob','File','Image','requestAnimationFrame','performance','process','arguments',
  'URLSearchParams','ResizeObserver','IntersectionObserver','MutationObserver','Event',
  'CustomEvent','History','Audio','Worker','WebSocket','TextEncoder','TextDecoder',
  'queueMicrotask','atob','btoa','alert','confirm','prompt','getComputedStyle','matchMedia',
])
// Syntax, not calls
const SYNTAX = new Set(['async','import','var','let','const','setup','super','this'])

// **With no arguments it checked nothing** — and that is how the release steps
// called it. Given none, it checks every SFC in the app.
async function everySfc() {
  const { readdir } = await import('node:fs/promises')
  const roots = ['src', 'src/components', 'src/components/muse']
  const files = []
  for (const dir of roots) {
    let entries = []
    try { entries = await readdir(dir, { withFileTypes: true }) } catch { continue }
    for (const e of entries) {
      if (e.isFile() && e.name.endsWith('.vue')) files.push(`${dir}/${e.name}`)
    }
  }
  return files.sort()
}

let bad = 0
const given = process.argv.slice(2)
const targets = given.length ? given : await everySfc()
if (!targets.length) {
  console.error('✗ 検査する .vue が見つからない（frontend/ で実行すること）')
  process.exit(1)
}
for (const file of targets) await check(file)
if (!bad) console.log(`✓ ${targets.length} ファイル、定義の無い呼び出しは無し`)
process.exit(bad ? 1 : 0)

async function check(file) {
const src = readFileSync(file, 'utf8')
// How the template is written (nested <button>s and so on) is not this tool's job,
// so it is silenced
const warn = console.warn
console.warn = () => {}
const { descriptor, errors } = parse(src, { filename: file })
if (errors.length) { console.error(`${file}: SFC parse errors`, errors); bad++; return }
// How the template is written (nested <button>s and so on) is not this tool's job,
// so it is silenced
const compiled = compileScript(descriptor, {
  id: 'x', inlineTemplate: true,
  templateOptions: { compilerOptions: { onWarn() {} } },
})

// Let esbuild check the syntax and strip the comments while it is there.
// (Otherwise "SSE (…)" or "galleries (…)" inside a comment looks like a call.)
const stripped = (await transform(compiled.content, {
  loader: 'ts', format: 'esm', minifyWhitespace: true,
})).code
console.warn = warn
// String literals are stripped too — a 「…（」 inside Japanese text looks like a call
// **Scanned in one pass.** Replacing the three kinds one after another lets an
// apostrophe inside double quotes (`"it's"`) throw the counting off, and the strings
// after it survive.
const code = stripped
  .replace(/`(?:\\.|[^`\\])*`|'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"/g, '""')
  // Regex literals are stripped too — the `SAY(` in `/^\s*SAY(?:\s*\(/` looks like a call
  .replace(/(^|[=(,:[!&|?{};+\s])\/(?![*/])(?:\\.|\[(?:\\.|[^\]\\])*\]|[^/\\\n])+\/[gimsuy]*/g, '$1/./')

// Collect the declared names (roughly is fine — the aim is to find names with no
// definition anywhere)
const declared = new Set()
const decl = /(?:^|\s)(?:const|let|var|function|class)\s+([A-Za-z_$][\w$]*)/g
for (const m of compiled.content.matchAll(decl)) declared.add(m[1])
// import { a, b as c } from '…' / import d from '…'
for (const m of compiled.content.matchAll(/import\s+([\s\S]*?)\s+from\s+['"]/g)) {
  for (const part of m[1].replace(/[{}]/g, ' ').split(',')) {
    const name = part.trim().split(/\s+as\s+/).pop()?.trim()
    if (name && /^[A-Za-z_$][\w$]*$/.test(name)) declared.add(name)
  }
}
// Destructuring, arguments and labelled properties are picked up loosely
for (const m of compiled.content.matchAll(/(?:function\s*[\w$]*\s*|=>\s*)?\(([^()]*)\)\s*(?:=>|\{)/g)) {
  for (const part of m[1].split(',')) {
    const name = part.trim().split(/[=:]/)[0].replace(/[{}[\].]/g, '').trim()
    if (name && /^[A-Za-z_$][\w$]*$/.test(name)) declared.add(name)
  }
}
for (const m of compiled.content.matchAll(/(?:const|let|var)\s*[{[]([^}\]]*)[}\]]/g)) {
  for (const part of m[1].split(',')) {
    const name = part.trim().split(/[=:]/).pop().trim()
    if (name && /^[A-Za-z_$][\w$]*$/.test(name)) declared.add(name)
  }
}
for (const m of compiled.content.matchAll(/(?:for\s*\(\s*(?:const|let|var)\s+([\w$]+))/g)) declared.add(m[1])
for (const m of compiled.content.matchAll(/catch\s*\(\s*([\w$]+)/g)) declared.add(m[1])
// Arguments created on the spot, as in `new Promise((resolve, reject) => …)`
for (const m of compiled.content.matchAll(/\(\s*([\w$]+(?:\s*,\s*[\w$]+)*)\s*\)\s*=>/g)) {
  for (const n of m[1].split(',')) declared.add(n.trim())
}
for (const m of compiled.content.matchAll(/(?:^|[^\w$.])([\w$]+)\s*=>/g)) declared.add(m[1])
// Object method shorthand (a directive definition such as `{ mounted(el) { … } }`)
for (const m of compiled.content.matchAll(/[{,]\s*([A-Za-z_$][\w$]*)\s*\([^()]*\)\s*\{/g)) {
  declared.add(m[1])
}

// Only **names that are called** are looked at. Property calls (`a.b(`) are excluded.
const called = new Set()
for (const m of code.matchAll(/(^|[^.\w$])([A-Za-z_$][\w$]*)\s*\(/g)) {
  called.add(m[2])
}
const KEYWORDS = new Set(['if','for','while','switch','catch','return','typeof','function',
  'await','new','else','do','try','yield','delete','void','in','of','case'])
// Names the compiler made (aliases such as `setup` or `t2`) are not reported —
// **only names that actually appear in the author's own file** are looked at.
const inSource = new RegExp('(?:^|[^\\w$.])' + '(NAME)' + '\\s*\\(')
const missing = [...called].filter(n =>
  !declared.has(n) && !KNOWN.has(n) && !KEYWORDS.has(n) && !SYNTAX.has(n) && !n.startsWith('_')
  && new RegExp(inSource.source.replace('NAME', n.replace(/\$/g, '\\$'))).test(src))
if (missing.length) {
  console.error(`✗ ${file}\n   定義が見つからない呼び出し: ${missing.join(', ')}`)
  bad++
}

// **Names the template uses that setup does not have.** A call in the script is
// `foo(`, but the same name in the template compiles to `_ctx.foo` — a property
// access, which the pass above skips on purpose. That is exactly where
// `retryImageLoad` and `searchTag` hid: written into an `@error` / `@search-tag`
// handler, defined nowhere, and silent until the event fired. Anything reaching
// through `_ctx` in a `<script setup>` component is either a global property
// (`$t`, `$i18n` — they start with `$`) or a name that does not exist.
const viaCtx = [...new Set([...compiled.content.matchAll(/_ctx\.([A-Za-z_$][\w$]*)/g)]
  .map(m => m[1]))]
  .filter(n => !n.startsWith('$') && !declared.has(n) && !KNOWN.has(n))
if (viaCtx.length) {
  console.error(`✗ ${file}\n   テンプレートが使っていて setup に無い名前: ${viaCtx.join(', ')}`)
  bad++
}
}
