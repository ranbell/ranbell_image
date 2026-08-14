import { ref } from 'vue'

const FAV_KEY = 'muse.favoriteCharacterIds'
const favorites = ref(load())

function load() {
  try {
    const raw = JSON.parse(localStorage.getItem(FAV_KEY) || '[]')
    return Array.isArray(raw) ? raw.map(String).filter(Boolean) : []
  } catch {
    return []
  }
}

function persist() {
  localStorage.setItem(FAV_KEY, JSON.stringify(favorites.value))
}

function reload() {
  favorites.value = load()
}

function isFavorite(id) {
  return favorites.value.includes(String(id || ''))
}

function toggleFavorite(id) {
  const key = String(id || '')
  if (!key) return
  const i = favorites.value.indexOf(key)
  if (i >= 0) favorites.value.splice(i, 1)
  else favorites.value.unshift(key)
  persist()
}

if (typeof window !== 'undefined') {
  window.addEventListener('storage', (e) => {
    if (e.key === FAV_KEY) reload()
  })
}

export function useMuseFavorites() {
  return { favorites, isFavorite, toggleFavorite, reload }
}
