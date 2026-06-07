import { createI18n } from 'vue-i18n'
import ja from './locales/ja.json'
import en from './locales/en.json'

const saved = localStorage.getItem('locale')

export const i18n = createI18n({
  legacy: false,
  locale: saved || 'en',
  fallbackLocale: 'en',
  messages: { ja, en },
})
