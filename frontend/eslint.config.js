// Bug-finding rules only. Layout and attribute order are left to the author —
// a rule that only reflows markup would bury the findings that matter.
// scripts/check_undefined.mjs stays: it compiles the SFC templates and finds names
// used there without a definition, which ESLint does not see.
import js from '@eslint/js'
import pluginVue from 'eslint-plugin-vue'
import globals from 'globals'

export default [
  { ignores: ['dist/**', 'node_modules/**', 'public/**'] },
  js.configs.recommended,
  ...pluginVue.configs['flat/essential'],
  {
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: {
        ...globals.browser,
        __APP_VERSION__: 'readonly',
      },
    },
    rules: {
      // `const { a, b, ...rest } = x` is how a field is left out of a request body.
      'no-unused-vars': ['error', {
        argsIgnorePattern: '^_', varsIgnorePattern: '^_', caughtErrors: 'none', ignoreRestSiblings: true,
      }],
      'no-empty': ['error', { allowEmptyCatch: true }],
      // Full-width spaces line up Japanese text on purpose; still caught in code.
      'no-irregular-whitespace': 'off',
      'vue/no-irregular-whitespace': ['error', {
        skipStrings: true, skipTemplates: true, skipComments: true, skipHTMLTextContents: true,
      }],
      // Single-word names are the app's own components (Gallery, Settings…), not HTML clashes.
      'vue/multi-word-component-names': 'off',
    },
  },
  {
    files: ['*.config.js', 'scripts/**'],
    languageOptions: { globals: { ...globals.node } },
  },
]
