import { createApp } from 'vue'
import './style.css'
import './styles/night-archive.css'
import App from './App.vue'
import { i18n } from './i18n.js'
import { initToken, getToken } from './apiToken.js'

const _origFetch = window.fetch.bind(window)
window.fetch = function (input, init = {}) {
  let pathname = ''
  if (typeof input === 'string') pathname = input
  else if (input instanceof URL) pathname = input.pathname
  else if (input instanceof Request) pathname = new URL(input.url, location.origin).pathname

  if (pathname.startsWith('/api')) {
    init = { ...init, headers: { ...init.headers, 'X-API-Token': getToken() } }
  }
  return _origFetch(input, init).then(res => {
    if (res.status === 401) window.dispatchEvent(new CustomEvent('api-unauthorized'))
    return res
  })
}

// The screens are separate files fetched when first opened. A tab left open across
// a redeploy asks for names the server no longer has: reload once to pick up the
// new ones rather than leave a button that does nothing.
const RELOADED_FOR_NEW_BUILD = 'ranbell.reloadedForNewBuild'
window.addEventListener('vite:preloadError', (event) => {
  try {
    if (sessionStorage.getItem(RELOADED_FOR_NEW_BUILD)) return  // already tried; let it fail
    sessionStorage.setItem(RELOADED_FOR_NEW_BUILD, '1')
  } catch { return }
  event.preventDefault()
  location.reload()
})

initToken().then(() => {
  createApp(App).use(i18n).mount('#app')
  try { sessionStorage.removeItem(RELOADED_FOR_NEW_BUILD) } catch { /* private window */ }
})
