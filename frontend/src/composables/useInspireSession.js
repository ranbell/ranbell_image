import { ref, computed } from 'vue'

export const INVERSION_AXIS_IDS = ['visual', 'time_weather', 'emotion', 'clothing', 'hair', 'style', 'location', 'narrative', 'action', 'parts']

// ── Singleton state (module scope — survives component unmount) ─────────────
const inspireTab              = ref('serendipity')
const inspireLoading          = ref(false)
const inspireResults          = ref([])
const inspireMorphTimeline    = ref([])
const inspireError            = ref('')
const inspireSlots            = ref([])
const inspireSlotsDirty       = ref(false)
const arithmeticRoles         = ref({})
const morphSlotA              = ref('')
const morphSlotB              = ref('')
const inspireAnomalyTags      = ref([])
const inspireInversionTags    = ref([])
const inspireInversionNegativeTags = ref([])
const inspireInversionStory   = ref('')
const inversionChangeTargets  = ref(INVERSION_AXIS_IDS.slice())
const inversionStage          = ref(0)
const inversionStageLabel     = ref('')
const inversionFixedTags      = ref([])
const inversionVolatileTags   = ref([])
const inversionNewTags        = ref([])
const inversionNeutralizerTags = ref([])
const inversionAtmosphereTags = ref([])
const inversionUserSections = ref({ character: '', background: '', props: '', action: '' })
const inversionLang           = ref('en')
const inversionStrength       = ref(1.0)
const inspireInversionTagsNl  = ref('')
const inversionPromptView     = ref('both')
const inversionRemovedTags    = ref([])
const inversionFixedTagsGrouped    = ref({})
const inversionVolatileTagsGrouped = ref({})
const inversionNewTagsGrouped      = ref({})
const inversionLlmClassification   = ref({})
const inversionStep2RawResult      = ref({})
const inversionSubjectTags     = ref([])
const inversionHairTags        = ref([])
const inversionClothingTags    = ref([])
const inversionAccessoryTags   = ref([])
const inversionPoseTags        = ref([])
const inversionExpressionTags  = ref([])
const inversionBackgroundTags  = ref([])
const inversionObjectTags      = ref([])
const inversionLightingTags    = ref([])
const brainstormLoading       = ref(false)
const brainstormText          = ref('')
const brainstormStreaming      = ref('')
const inspireRightView        = ref('results')
const inversionStoryStreaming  = ref('')
const discoverContextRoles    = ref({})
const groupedSearchQuery      = ref('')
const groupedBy               = ref('model_name')
const inspireGroupedResults   = ref([])
const blendWeights            = ref({})
const outlierMode             = ref('antipode')
const textSearchQuery         = ref('')

// Emotion search
const emotionSearchDimension  = ref('melancholy')
const emotionSearchMinScore   = ref(0.5)
const emotionSearchResults    = ref([])
const emotionSearchLoading    = ref(false)

const inversionJobId = ref(null)
const brainstormJobId = ref(null)
const inspireResultSelection = ref(new Set())

// ── Active SSE stream (cancel on reset to avoid orphaned writes) ───────────
let _activeReader = null

// ── Derived ────────────────────────────────────────────────────────────────
const isRunning = computed(() => inspireLoading.value || brainstormLoading.value)

const hasSession = computed(() =>
  isRunning.value ||
  inspireResults.value.length > 0 ||
  inspireMorphTimeline.value.length > 0 ||
  inspireGroupedResults.value.length > 0 ||
  !!brainstormText.value ||
  !!inspireInversionStory.value
)

// ── Actions ────────────────────────────────────────────────────────────────
function resetSession(initialSlots = []) {
  if (_activeReader) {
    _activeReader.cancel().catch(() => {})
    _activeReader = null
  }
  if (inversionJobId.value) {
    fetch(`/api/jobs/${inversionJobId.value}/cancel`, { method: 'POST' }).catch(() => {})
    inversionJobId.value = null
  }
  if (brainstormJobId.value) {
    fetch(`/api/jobs/${brainstormJobId.value}/cancel`, { method: 'POST' }).catch(() => {})
    brainstormJobId.value = null
  }
  const shas = initialSlots.slice(0, 6)
  inspireTab.value              = 'serendipity'
  inspireLoading.value          = false
  inspireResults.value          = []
  inspireMorphTimeline.value    = []
  inspireError.value            = ''
  inspireSlots.value            = shas
  inspireSlotsDirty.value       = false
  inspireAnomalyTags.value      = []
  inspireInversionTags.value    = []
  inspireInversionNegativeTags.value = []
  inspireInversionStory.value   = ''
  inversionChangeTargets.value  = INVERSION_AXIS_IDS.slice()
  inversionStage.value          = 0
  inversionStageLabel.value     = ''
  inversionFixedTags.value      = []
  inversionVolatileTags.value   = []
  inversionNewTags.value        = []
  inversionNeutralizerTags.value = []
  inversionAtmosphereTags.value = []
  inversionUserSections.value = { character: '', background: '', props: '', action: '' }
  inversionLang.value           = 'en'
  inversionStrength.value       = 1.0
  inspireInversionTagsNl.value  = ''
  inversionPromptView.value     = 'both'
  inversionRemovedTags.value    = []
  inversionFixedTagsGrouped.value    = {}
  inversionVolatileTagsGrouped.value = {}
  inversionNewTagsGrouped.value      = {}
  inversionLlmClassification.value   = {}
  inversionStep2RawResult.value      = {}
  inversionSubjectTags.value     = []
  inversionHairTags.value        = []
  inversionClothingTags.value    = []
  inversionAccessoryTags.value   = []
  inversionPoseTags.value        = []
  inversionExpressionTags.value  = []
  inversionBackgroundTags.value  = []
  inversionObjectTags.value      = []
  inversionLightingTags.value    = []
  inversionStoryStreaming.value  = ''
  brainstormLoading.value       = false
  brainstormText.value          = ''
  brainstormStreaming.value      = ''
  inspireRightView.value        = 'results'
  discoverContextRoles.value    = {}
  groupedSearchQuery.value      = ''
  groupedBy.value               = 'model_name'
  inspireGroupedResults.value   = []
  outlierMode.value             = 'antipode'
  textSearchQuery.value         = ''
  emotionSearchResults.value    = []
  emotionSearchLoading.value    = false
  inspireResultSelection.value  = new Set()
  const roles = {}
  const weights = {}
  shas.forEach((s, i) => {
    roles[s]   = i < 2 ? 'add' : 'sub'
    weights[s] = 0.5
  })
  arithmeticRoles.value = roles
  blendWeights.value    = weights
  morphSlotA.value      = shas[0] || ''
  morphSlotB.value      = shas[1] || ''
}

function toggleInspireResultSelection(sha256) {
  const s = new Set(inspireResultSelection.value)
  if (s.has(sha256)) s.delete(sha256)
  else s.add(sha256)
  inspireResultSelection.value = s
}

function addToInspireSlots(sha256) {
  if (inspireSlots.value.includes(sha256)) return 'duplicate'
  if (inspireSlots.value.length >= 6) return 'full'
  inspireSlots.value = [...inspireSlots.value, sha256]
  inspireSlotsDirty.value = true
  return 'ok'
}

function removeFromInspireSlots(sha256) {
  inspireSlots.value = inspireSlots.value.filter(s => s !== sha256)
  inspireSlotsDirty.value = true
  if (morphSlotA.value === sha256) morphSlotA.value = inspireSlots.value[0] || ''
  if (morphSlotB.value === sha256) morphSlotB.value = inspireSlots.value.find(s => s !== morphSlotA.value) || ''
}

function setActiveReader(reader) {
  _activeReader = reader
}

async function runEmotionSearch(token) {
  emotionSearchLoading.value = true
  emotionSearchResults.value = []
  try {
    const r = await fetch('/api/ai/emotion-search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-API-Token': token },
      body: JSON.stringify({
        emotion: emotionSearchDimension.value,
        min_score: emotionSearchMinScore.value,
        limit: 50,
      }),
    })
    if (!r.ok) throw new Error(await r.text())
    const data = await r.json()
    emotionSearchResults.value = data.results || []
  } catch (e) {
    console.error('Emotion search failed:', e)
  } finally {
    emotionSearchLoading.value = false
  }
}

export function useInspireSession() {
  return {
    inspireTab,
    inspireLoading,
    inspireResults,
    inspireMorphTimeline,
    inspireError,
    inspireSlots,
    inspireSlotsDirty,
    arithmeticRoles,
    morphSlotA,
    morphSlotB,
    inspireAnomalyTags,
    inspireInversionTags,
    inspireInversionNegativeTags,
    inspireInversionStory,
    inversionChangeTargets,
    inversionStage,
    inversionStageLabel,
    inversionFixedTags,
    inversionVolatileTags,
    inversionNewTags,
    inversionNeutralizerTags,
    inversionAtmosphereTags,
    inversionUserSections,
    inversionLang,
    inversionStrength,
    inspireInversionTagsNl,
    inversionPromptView,
    inversionRemovedTags,
    inversionFixedTagsGrouped,
    inversionVolatileTagsGrouped,
    inversionNewTagsGrouped,
    inversionLlmClassification,
    inversionStep2RawResult,
    inversionSubjectTags,
    inversionHairTags,
    inversionClothingTags,
    inversionAccessoryTags,
    inversionPoseTags,
    inversionExpressionTags,
    inversionBackgroundTags,
    inversionObjectTags,
    inversionLightingTags,
    inversionJobId,
    brainstormJobId,
    brainstormLoading,
    brainstormText,
    brainstormStreaming,
    inspireRightView,
    inversionStoryStreaming,
    discoverContextRoles,
    groupedSearchQuery,
    groupedBy,
    inspireGroupedResults,
    blendWeights,
    outlierMode,
    textSearchQuery,
    emotionSearchDimension,
    emotionSearchMinScore,
    emotionSearchResults,
    emotionSearchLoading,
    inspireResultSelection,
    toggleInspireResultSelection,
    addToInspireSlots,
    removeFromInspireSlots,
    isRunning,
    hasSession,
    resetSession,
    setActiveReader,
    runEmotionSearch,
  }
}
