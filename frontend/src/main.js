import { createApp } from 'vue'
import './style.css'
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

initToken().then(() => {
  createApp(App).use(i18n).mount('#app')
})
