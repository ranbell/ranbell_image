/*
 * SFC を実際にコンパイルして（script setup ＋ template をひとつのモジュールに）、
 * **定義されていない識別子を使っていないか**を見る。
 *
 * ビルドは通るのにこれを見逃す —— `mergeRewriteLog` の定義だけ消して呼び出しを
 * 残したとき、Vite は黙って通し、実行時に `refresh()` の catch{} が握り潰した。
 */
import { readFileSync } from 'node:fs'
import { parse, compileScript } from 'vue/compiler-sfc'
import { transform } from 'esbuild'

const KNOWN = new Set([
  // ブラウザ / 標準
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
// 構文であって呼び出しではないもの
const SYNTAX = new Set(['async','import','var','let','const','setup','super','this'])

let bad = 0
for (const file of process.argv.slice(2)) await check(file)
process.exit(bad ? 1 : 0)

async function check(file) {
const src = readFileSync(file, 'utf8')
// テンプレートの書き方（<button> の入れ子など）はここの仕事ではないので黙らせる
const warn = console.warn
console.warn = () => {}
const { descriptor, errors } = parse(src, { filename: file })
if (errors.length) { console.error(`${file}: SFC parse errors`, errors); bad++; return }
// テンプレートの書き方（<button> の入れ子など）はここの仕事ではないので黙らせる
const compiled = compileScript(descriptor, {
  id: 'x', inlineTemplate: true,
  templateOptions: { compilerOptions: { onWarn() {} } },
})

// esbuild に構文を確かめさせ、ついでにコメントを落とさせる。
// （コメントの中の「SSE (…)」「galleries (…)」まで呼び出しに見えてしまうため）
const stripped = (await transform(compiled.content, {
  loader: 'ts', format: 'esm', minifyWhitespace: true,
})).code
console.warn = warn
// 文字列リテラルも落とす —— 日本語の中に「〜（」が入ると呼び出しに見える
// **一度で走査する。** 三種類を順に置換すると、二重引用符の中の
// アポストロフィ（`"it's"`）で数え方がずれて、以降の文字列が残ってしまう。
const code = stripped
  .replace(/`(?:\\.|[^`\\])*`|'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"/g, '""')
  // 正規表現リテラルも落とす —— `/^\s*SAY(?:\s*\(/` の `SAY(` が呼び出しに見える
  .replace(/(^|[=(,:[!&|?{};+\s])\/(?![*/])(?:\\.|\[(?:\\.|[^\]\\])*\]|[^/\\\n])+\/[gimsuy]*/g, '$1/./')

// 宣言された名前を集める（雑でよい —— 目的は「一つも定義が無い名前」を探すこと）
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
// 分割代入・引数・ラベル付きプロパティは雑に拾う
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
// `new Promise((resolve, reject) => …)` のような、その場で作る引数
for (const m of compiled.content.matchAll(/\(\s*([\w$]+(?:\s*,\s*[\w$]+)*)\s*\)\s*=>/g)) {
  for (const n of m[1].split(',')) declared.add(n.trim())
}
for (const m of compiled.content.matchAll(/(?:^|[^\w$.])([\w$]+)\s*=>/g)) declared.add(m[1])
// オブジェクトのメソッド簡記（`{ mounted(el) { … } }` のような指令の定義）
for (const m of compiled.content.matchAll(/[{,]\s*([A-Za-z_$][\w$]*)\s*\([^()]*\)\s*\{/g)) {
  declared.add(m[1])
}

// **呼び出されている名前**だけを見る。プロパティ呼び出し（`a.b(`）は除く。
const called = new Set()
for (const m of code.matchAll(/(^|[^.\w$])([A-Za-z_$][\w$]*)\s*\(/g)) {
  called.add(m[2])
}
const KEYWORDS = new Set(['if','for','while','switch','catch','return','typeof','function',
  'await','new','else','do','try','yield','delete','void','in','of','case'])
// コンパイラが作った名前（`setup`, `t2` のような別名）は報告しない ——
// **書いた人のファイルに実際に出てくる名前だけ**を見る。
const inSource = new RegExp('(?:^|[^\\w$.])' + '(NAME)' + '\\s*\\(')
const missing = [...called].filter(n =>
  !declared.has(n) && !KNOWN.has(n) && !KEYWORDS.has(n) && !SYNTAX.has(n) && !n.startsWith('_')
  && new RegExp(inSource.source.replace('NAME', n.replace(/\$/g, '\\$'))).test(src))
if (missing.length) {
  console.error(`✗ ${file}\n   定義が見つからない呼び出し: ${missing.join(', ')}`)
  bad++
}
}
