const STORAGE_KEY = 'api_token'
const DEFAULT_TOKEN = 'RANBELL_IMAGE_API_TOKEN'

// Capture native fetch before main.js wraps it (ES module top-level runs first).
const _nativeFetch = fetch

let _token = null

export async function initToken() {
  const stored = sessionStorage.getItem(STORAGE_KEY)
  if (stored) {
    _token = stored
    _syncCookie(_token)
    return
  }
  try {
    const resp = await _nativeFetch('/api/token')
    if (resp.ok) {
      const { token } = await resp.json()
      _token = token
      sessionStorage.setItem(STORAGE_KEY, token)
      _syncCookie(token)
      return
    }
  } catch {}
  _token = DEFAULT_TOKEN
  _syncCookie(_token)   // clear stale cookie even in fallback
}

export function getToken() {
  return _token ?? sessionStorage.getItem(STORAGE_KEY) ?? DEFAULT_TOKEN
}

export function saveAndSyncToken(token) {
  _token = token
  sessionStorage.setItem(STORAGE_KEY, token)
  _syncCookie(token)
}

function _syncCookie(token) {
  document.cookie = `api_token=${encodeURIComponent(token)}; SameSite=Strict; Path=/`
}

export function syncCookieFromStorage() {
  const token = getToken()
  _syncCookie(token)
  return token
}
