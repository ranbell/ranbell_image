import { ref, computed } from 'vue'

export const INVERSION_AXIS_IDS = ['visual', 'time_weather', 'emotion', 'clothing', 'hair', 'style', 'location', 'narrative', 'action', 'parts']

// ── Singleton state (module scope — survives component unmount) ─────────────
const inspireTab              = ref('serendipity')
const inspireLoading          = ref(false)
const inspireResults          = ref([])
const inspireMorphTimeline    = ref([])
const inspireError            = ref('')
const inspireSlots            = ref([])
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
const inversionUserInjectPrompt = ref('')
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
  inversionUserInjectPrompt.value = ''
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

function setActiveReader(reader) {
  _activeReader = reader
}

export function useInspireSession() {
  return {
    inspireTab,
    inspireLoading,
    inspireResults,
    inspireMorphTimeline,
    inspireError,
    inspireSlots,
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
    inversionUserInjectPrompt,
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
    inspireResultSelection,
    toggleInspireResultSelection,
    isRunning,
    hasSession,
    resetSession,
    setActiveReader,
  }
}
