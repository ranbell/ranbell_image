<template>
  <div class="cr-overlay" @click.self="$emit('close')">
    <div class="cr-panel">

      <!-- ── header ── -->
      <header class="cr-header">
        <span class="cr-title">CONTROL ROOM</span>
        <span class="cr-master-badge" :class="`cr-master--${masterStatus.toLowerCase()}`">
          <span class="cr-lamp" :class="masterLampClass" />
          SYS {{ masterStatus }}
        </span>
        <span class="cr-sse-indicator" :class="sseConnected ? 'cr-sse--ok' : 'cr-sse--lost'">
          <span class="cr-sse-dot" />
          {{ sseConnected ? 'LIVE' : 'LOST' }}
        </span>
        <span class="cr-clock">{{ clock }}</span>
        <button class="cr-close" @click="$emit('close')" aria-label="Close">✕</button>
      </header>

      <!-- ── annunciator row (system status) ── -->
      <div class="cr-annunciator">
        <div
          v-for="sys in SYSTEMS"
          :key="sys.key"
          class="cr-ann-tile"
          :class="`cr-ann--${systemStatus[sys.key] || 'standby'}`"
          :title="systemStatus[sys.key] === 'guard' ? t('controlRoom.gpuGuardTooltip') : undefined"
        >
          <span class="cr-lamp" :class="`cr-lamp--${systemStatus[sys.key] || 'standby'}`" />
          <span class="cr-ann-name">{{ sys.label }}</span>
          <span class="cr-ann-state">{{ systemStatus[sys.key] === 'guard' ? '⛨ GPU GUARD' : (systemStatus[sys.key] || 'standby').toUpperCase() }}</span>
          <button
            v-if="sys.lane"
            class="cr-ann-ctrl-btn"
            :class="{ 'is-paused': laneStates[sys.lane]?.paused, 'is-guard': isGuardPaused(sys.lane) }"
            :disabled="isGuardPaused(sys.lane)"
            @click.stop="toggleLanePause(sys.lane)"
            :title="isGuardPaused(sys.lane) ? t('controlRoom.gpuGuardTooltip') : laneStates[sys.lane]?.paused ? t('controlRoom.resumeLane') : t('controlRoom.pauseLane')"
          >{{ isGuardPaused(sys.lane) ? '⛨' : laneStates[sys.lane]?.paused ? '▶' : '⏸' }}</button>
        </div>
      </div>

      <!-- ── P&ID process flow diagram: Gen → Tag → Embed → Eval pipeline ── -->
      <div class="cr-pid">

        <!-- GPU interlock rail: shown while the spooler holds lanes to protect the GPU -->
        <div v-if="guardActive" class="cr-pid-interlock-rail">
          <span class="cr-pid-interlock-label">⛨ GPU INTERLOCK · {{ guardSourceLabel }}</span>
        </div>

        <!-- JOBS IN source terminus (all queued jobs waiting to enter the pipeline) -->
        <div class="cr-pid-terminus">
          <div class="cr-pid-terminus-label">JOBS IN</div>
          <div class="cr-pid-terminus-count" :class="{ 'cr-pid-terminus-count--active': waitedJobs.length > 0 }">
            {{ waitedJobs.length }}
          </div>
        </div>

        <template v-for="(unit, i) in pidUnits" :key="unit.key">
          <!-- pipe into this unit -->
          <div class="cr-pid-pipe" :class="{ 'cr-pid-pipe--xfer': i > 0 }">
            <div class="cr-pid-pipe-line" :class="pipeFlowing(i) ? 'cr-pid-pipe--flowing' : 'cr-pid-pipe--idle'" />
            <span v-if="i > 0" class="cr-pid-pipe-arrow">▶</span>
          </div>

          <div class="cr-pid-unit-wrap">
            <!-- interlock taps: ● on the active trigger lane, shield (⛨) on guarded lanes -->
            <span v-if="guardActive && unit.trigger && unit.job" class="cr-pid-tap cr-pid-tap--source">●</span>
            <span v-else-if="unit.status === 'guard'" class="cr-pid-tap cr-pid-tap--shield">⛨</span>

            <div class="cr-pid-unit" :class="[`cr-pid-unit--${unit.key}`, `cr-pid-state--${unit.status || 'standby'}`]">
              <div class="cr-pid-unit-header">
                <span class="cr-lamp" :class="`cr-lamp--${unit.status || 'standby'}`" />
                <span class="cr-pid-unit-name">{{ unit.name }}</span>
                <span v-if="unit.cpuOnly" class="cr-pid-cpu-chip" :title="t('controlRoom.cpuLaneTooltip')">CPU</span>
                <span class="cr-pid-unit-state">{{ unit.status === 'guard' ? '⛨ GUARD' : (unit.status || 'standby').toUpperCase() }}</span>
                <button
                  class="cr-pid-valve-btn"
                  :class="{ 'is-paused': laneStates[unit.lane]?.paused, 'is-guard': isGuardPaused(unit.lane) }"
                  :disabled="isGuardPaused(unit.lane)"
                  @click="toggleLanePause(unit.lane)"
                  :title="isGuardPaused(unit.lane) ? t('controlRoom.gpuGuardTooltip') : laneStates[unit.lane]?.paused ? t('controlRoom.resumeLane') : t('controlRoom.pauseLane')"
                >{{ isGuardPaused(unit.lane) ? '⛨' : laneStates[unit.lane]?.paused ? '▶' : '⏸' }}</button>
              </div>
              <div class="cr-pid-unit-body">
                <div class="cr-pid-tank" :title="`${unit.depth} queued`">
                  <div class="cr-pid-tank-fill" :class="`cr-pid-tank-fill--${unit.key}`"
                    :style="{ height: `${Math.min(100, unit.depth * 20)}%` }" />
                  <span class="cr-pid-tank-label">{{ unit.depth }}</span>
                </div>
                <div class="cr-pid-reactor">
                  <div class="cr-pid-reactor-track">
                    <div class="cr-pid-reactor-fill" :class="`cr-pid-reactor-fill--${unit.key}`"
                      :style="{ transform: `scaleX(${unit.job ? (unit.job.progress ?? 0) : 0})` }" />
                  </div>
                  <span class="cr-pid-reactor-label" :class="{ 'cr-pid-reactor-label--guard': !unit.job && unit.status === 'guard' }">
                    {{ unit.job
                      ? `${Math.round((unit.job.progress ?? 0) * 100)}%`
                      : unit.status === 'guard' ? `HELD BY ${guardSourceLabel}` : '—' }}
                  </span>
                </div>
              </div>
              <div class="cr-pid-resbar">
                <template v-if="unit.resource?.kind === 'local'">
                  <span class="cr-pid-resbar-name">{{ unit.resource.name }}</span>
                  <div class="cr-pid-resbar-gauge">
                    <div class="cr-pid-resbar-fill" :class="`cr-pid-resbar-fill--${unit.key}`"
                      :style="{ width: `${Math.round((unit.metric === 'gpu' ? unit.resource.gpu_util_pct : unit.resource.cpu_pct) ?? 0)}%` }" />
                  </div>
                  <span class="cr-pid-resbar-val">
                    {{ unit.metric === 'gpu' ? 'GPU' : 'CPU' }}
                    {{ Math.round((unit.metric === 'gpu' ? unit.resource.gpu_util_pct : unit.resource.cpu_pct) ?? 0) }}%
                  </span>
                  <span class="cr-pid-resbar-vram" v-if="unit.metric === 'gpu' && unit.resource.vram_total_gb">
                    {{ Math.round(unit.resource.vram_used_gb ?? 0) }}/{{ unit.resource.vram_total_gb }}G
                  </span>
                  <span class="cr-pid-resbar-vram" v-else-if="unit.metric === 'cpu' && unit.resource.ram_total_gb">
                    {{ Math.round(unit.resource.ram_used_gb ?? 0) }}/{{ unit.resource.ram_total_gb }}G
                  </span>
                </template>
                <template v-else-if="unit.resource">
                  <span class="cr-lamp cr-lamp--xs"
                    :class="unit.resource.reachable ? 'cr-lamp--nominal' : (unit.resource.last_checked == null ? 'cr-lamp--starting' : 'cr-lamp--fault')" />
                  <span class="cr-pid-resbar-name">{{ unit.resource.name }}</span>
                  <span class="cr-pid-resbar-slots" v-if="unit.resource.concurrency" :title="t('controlRoom.slotsTooltip')">×{{ unit.resource.concurrency }}</span>
                  <span class="cr-pid-resbar-latency" v-if="unit.resource.reachable && unit.resource.latency_ms != null">~{{ Math.round(unit.resource.latency_ms) }}ms</span>
                  <span class="cr-pid-resbar-starting" v-else-if="!unit.resource.reachable && unit.resource.last_checked == null">STARTING</span>
                  <span class="cr-pid-resbar-fault" v-else-if="!unit.resource.reachable">UNREACHABLE</span>
                </template>
              </div>
            </div>
          </div>
        </template>

        <!-- pipe: EVAL → VECTOR STORE -->
        <div class="cr-pid-pipe">
          <div class="cr-pid-pipe-line"
            :class="evalActiveJob ? 'cr-pid-pipe--flowing' : 'cr-pid-pipe--idle'" />
        </div>

        <!-- VECTOR STORE drain terminus -->
        <div class="cr-pid-terminus">
          <div class="cr-pid-terminus-label">VECTOR<br>STORE</div>
          <span class="cr-lamp"
            :class="systemStatus.vectorStore === 'fault'
              ? 'cr-lamp--fault'
              : systemStatus.vectorStore === 'nominal' || systemStatus.vectorStore === 'active'
              ? 'cr-lamp--nominal'
              : 'cr-lamp--standby'" />
        </div>

      </div>

      <div class="cr-body">

        <!-- ── left: resources ── -->
        <section class="cr-section cr-section--resources">
          <div class="cr-section-label">RESOURCES</div>
          <div class="cr-resources-scroll">

            <div v-for="res in localResources" :key="res.name" class="cr-resource-local">
              <div class="cr-resource-header">
                <span class="cr-lamp cr-lamp--nominal" />
                <span class="cr-resource-name">{{ res.name }}</span>
              </div>

              <template v-if="res.gpu_util_pct != null">
                <div class="cr-gauge-row">
                  <span class="cr-gauge-label">GPU</span>
                  <div class="cr-gauge-track">
                    <div class="cr-gauge-fill" :class="tempClass(res.temp_c, 75, 85)" :style="{ transform: `scaleX(${res.gpu_util_pct / 100})` }" />
                  </div>
                  <span class="cr-gauge-val cr-gauge-pct">{{ res.gpu_util_pct }}%</span>
                  <span class="cr-temp-val" :class="tempClass(res.temp_c, 75, 85)">
                    {{ res.temp_c != null ? `${res.temp_c}°C` : '' }}
                  </span>
                </div>
                <div class="cr-gauge-row" v-if="res.vram_total_gb">
                  <span class="cr-gauge-label">VRAM</span>
                  <div class="cr-gauge-track">
                    <div class="cr-gauge-fill" :class="ratioClass(res.vram_used_gb, res.vram_total_gb, 0.75, 0.9)" :style="{ transform: `scaleX(${Math.min(1, res.vram_used_gb / res.vram_total_gb)})` }" />
                  </div>
                  <span class="cr-gauge-val">{{ res.vram_used_gb }}/{{ res.vram_total_gb }}GB</span>
                </div>
              </template>
              <div v-else class="cr-gauge-unavailable">GPU —</div>

              <template v-if="res.cpu_pct != null">
                <div class="cr-gauge-row">
                  <span class="cr-gauge-label">CPU</span>
                  <div class="cr-gauge-track">
                    <div class="cr-gauge-fill" :class="ratioClass(res.cpu_pct, 100, 70, 90)" :style="{ transform: `scaleX(${res.cpu_pct / 100})` }" />
                  </div>
                  <span class="cr-gauge-val cr-gauge-pct">{{ res.cpu_pct }}%</span>
                  <span class="cr-temp-val" :class="tempClass(res.cpu_temp_c, 80, 90)">
                    {{ res.cpu_temp_c != null ? `${res.cpu_temp_c}°C` : '' }}
                  </span>
                </div>
                <div class="cr-gauge-row" v-if="res.ram_total_gb">
                  <span class="cr-gauge-label">RAM</span>
                  <div class="cr-gauge-track">
                    <div class="cr-gauge-fill" :class="ratioClass(res.ram_used_gb, res.ram_total_gb, 0.75, 0.9)" :style="{ transform: `scaleX(${Math.min(1, res.ram_used_gb / res.ram_total_gb)})` }" />
                  </div>
                  <span class="cr-gauge-val">{{ res.ram_used_gb }}/{{ res.ram_total_gb }}GB</span>
                </div>
              </template>
              <div v-else class="cr-gauge-unavailable">CPU —</div>
            </div>

            <div v-for="res in remoteResources" :key="res.name" class="cr-resource-remote">
              <div class="cr-resource-header">
                <span class="cr-lamp" :class="res.reachable ? 'cr-lamp--nominal' : (res.last_checked == null ? 'cr-lamp--starting' : 'cr-lamp--fault')" />
                <span class="cr-resource-name">{{ resourceLabel(res.name) }}</span>
                <span class="cr-remote-slots" v-if="res.concurrency" :title="t('controlRoom.slotsTooltip')">×{{ res.concurrency }}</span>
                <span class="cr-remote-version" v-if="res.version">v{{ res.version }}</span>
                <span class="cr-remote-latency" v-if="res.reachable && res.latency_ms != null">
                  ~{{ Math.round(res.latency_ms) }}ms
                </span>
                <span class="cr-remote-down" v-else-if="!res.reachable && res.last_checked != null">FAULT</span>
                <span class="cr-remote-starting" v-else-if="!res.reachable">STARTING</span>
              </div>
              <div class="cr-remote-endpoint" v-if="res.endpoint">
                {{ res.endpoint }}
                <span v-if="res.last_ok" class="cr-remote-lastok">last ok {{ relativeTime(res.last_ok) }}</span>
              </div>
            </div>

            <div class="cr-queue-row">
              <span class="cr-queue-label">QUEUE</span>
              <span class="cr-queue-val" :class="{ 'cr-caution-text': pendingCount >= 3 }">
                {{ pendingCount }} pending
              </span>
            </div>

            <div class="cr-totalizer">
              <span class="cr-totalizer-glyph">⊹</span>
              {{ totalProcessed.toLocaleString() }} synthesized
            </div>

            <template v-if="disks?.length">
              <div class="cr-section-sublabel">DISK</div>
              <div v-for="d in disks" :key="d.name" class="cr-disk-row">
                <div class="cr-disk-header">
                  <span class="cr-disk-name">{{ d.name }}</span>
                  <span class="cr-disk-val">{{ d.free_gb }}G / {{ d.total_gb }}G</span>
                </div>
                <div class="cr-gauge-track cr-gauge-track--disk">
                  <div class="cr-gauge-fill"
                    :class="d.used_pct >= diskFaultPct ? 'cr-gauge--fault' : d.used_pct >= diskCautionPct ? 'cr-gauge--caution' : 'cr-gauge--nominal'"
                    :style="{ transform: `scaleX(${d.used_pct / 100})` }" />
                </div>
              </div>
            </template>

          </div>
        </section>

        <!-- ── center: active jobs + waited queue ── -->
        <section class="cr-section cr-section--jobs">
          <div class="cr-section-label">ACTIVE JOBS</div>

          <div class="cr-job-list">
            <div v-if="activeJobs.length === 0" class="cr-job-empty">— no active jobs —</div>
            <div
              v-for="job in activeJobs"
              :key="job.id"
              class="cr-job-entry"
              :class="[`cr-job--${job.state}`, job.held ? 'cr-job--held' : '']"
            >
              <div class="cr-job-row1">
                <span class="cr-job-id-dim">{{ job.id }}</span>
                <span class="cr-job-title">{{ job.title }}</span>
                <div class="cr-job-actions">
                  <!-- RUNNING: Pause -->
                  <button
                    v-if="job.state === 'running'"
                    :class="['cr-job-action-btn', pausePending.has(job.id) ? 'cr-job-action-btn--pause-pending' : '']"
                    @click="pauseJob(job.id)"
                    :title="t('controlRoom.pause')"
                  >⏸</button>
                  <!-- PAUSED (individual): Resume -->
                  <button
                    v-if="job.state === 'paused' && !laneStates[job.lane]?.paused"
                    class="cr-job-action-btn cr-job-action-btn--resume"
                    @click="resumeJob(job.id)"
                    :title="t('controlRoom.resume')"
                  >▶</button>
                  <!-- Cancel. 待ち行列のものも止められる（`spooler.cancel`
                       は QUEUED を扱えるのに、ボタンだけ出ていなかった）。 -->
                  <button
                    v-if="['running', 'paused', 'queued'].includes(job.state)"
                    class="cr-cancel-btn"
                    @click="$emit('cancel', job.id)"
                    :title="t('controlRoom.cancel')"
                  >✕</button>
                  <!-- Retry (failed) -->
                  <button
                    v-else-if="job.state === 'failed'"
                    class="cr-retry-btn"
                    @click="$emit('retry', job.id)"
                  >retry</button>
                  <!-- 失敗したものを片付ける。止めるものはもう無いので、
                       これは取り消しではなく片付け（サーバ側で dismiss）。 -->
                  <button
                    v-if="['failed', 'cancelled', 'succeeded'].includes(job.state)"
                    class="cr-cancel-btn"
                    @click="$emit('cancel', job.id)"
                    :title="t('controlRoom.dismiss')"
                  >✕</button>
                  <span v-else-if="job.state === 'cancelling'" class="cr-job-status">cancelling…</span>
                </div>
              </div>

              <div class="cr-job-row2" v-if="job.state === 'running'">
                <ProgressBar
                  class="cr-job-progress"
                  :progress="job.progress || 0"
                  :progress-text="job.progress_indeterminate ? '…' : (job.progress_text || null)"
                  :indeterminate="job.progress_indeterminate"
                  :eta="job.progress_indeterminate ? null : (job.eta_seconds ?? null)"
                />
                <span class="cr-job-elapsed">{{ formatElapsed(job) }}</span>
              </div>
              <div class="cr-job-row2" v-else-if="job.state === 'paused'">
                <span v-if="isGuardPaused(job.lane)" class="cr-job-badge cr-job-badge--guard" :title="t('controlRoom.gpuGuardTooltip')">
                  ⛨ GPU Guard
                </span>
                <span v-else class="cr-job-badge cr-job-badge--paused">
                  {{ laneStates[job.lane]?.paused ? '⏸ Lane Paused' : '⏸ Paused' }}
                </span>
                <span class="cr-job-elapsed">{{ formatElapsed(job) }}</span>
              </div>
              <div class="cr-job-row2 cr-job-row2--error" v-else-if="job.state === 'failed' && job.error">
                <span class="cr-job-error" :title="job.error">{{ truncate(job.error, 60) }}</span>
              </div>
              <div v-if="job.meta?.sha256s?.length" class="cr-job-meta">
                <div class="cr-job-thumbs">
                  <img
                    v-for="sha in job.meta.sha256s.slice(0, 4)" :key="sha"
                    :src="`/api/thumbnails/${sha}.webp`"
                    class="cr-job-thumb"
                    @error="onThumbnailError($event)"
                  />
                </div>
                <span v-if="job.meta.positive_preview" class="cr-job-prompt-preview" :title="job.meta.positive_preview">
                  {{ job.meta.positive_preview.slice(0, 80) }}{{ job.meta.positive_preview.length > 80 ? '…' : '' }}
                </span>
              </div>
            </div>
          </div>

          <div v-if="failedCount > 1" class="cr-bulk-bar">
            <button class="cr-bulk-btn cr-bulk-btn--retry" @click="$emit('retry-all-failed')">retry all {{ failedCount }}</button>
          </div>

          <!-- ── waited queue (ISA-101 style) ── -->
          <div class="cr-waited-block">
            <div class="cr-waited-header">
              <span class="cr-waited-label">WAITED</span>
              <span class="cr-waited-count" :class="{ 'cr-waited-count--caution': waitedJobs.length >= 3 }">
                {{ waitedJobs.length }}
              </span>
              <button
                v-if="waitedJobs.length > 1"
                class="cr-bulk-btn cr-bulk-btn--cancel cr-waited-cancel-all"
                @click="$emit('cancel-all-queued')"
              >cancel all</button>
            </div>
            <div class="cr-waited-list">
              <div v-if="waitedJobs.length === 0" class="cr-waited-empty">— queue empty —</div>
              <div
                v-for="(job, i) in waitedJobs"
                :key="job.id"
                class="cr-waited-row"
                :class="{ 'cr-waited-row--held': job.held }"
              >
                <span class="cr-waited-pos">{{ String(i + 1).padStart(2, ' ') }}</span>
                <span class="cr-waited-lane" :data-lane="job.lane">{{ laneCode(job.lane) }}</span>
                <span class="cr-waited-title" :title="job.title">{{ job.title }}</span>
                <span v-if="job.held" class="cr-waited-tag cr-waited-tag--held" :title="t('controlRoom.heldTooltip')">HELD</span>
                <span v-else-if="job.priority > 0" class="cr-waited-tag cr-waited-tag--pri">P{{ job.priority }}</span>
                <div class="cr-waited-actions">
                  <template v-if="!job.held">
                    <button
                      class="cr-waited-btn"
                      :disabled="queuedIdsByLane[job.lane]?.[0] === job.id"
                      @click="reorderJob(job.id, 1)"
                      :title="t('controlRoom.priorityUp')"
                    >↑</button>
                    <button
                      class="cr-waited-btn"
                      :disabled="queuedIdsByLane[job.lane]?.at(-1) === job.id"
                      @click="reorderJob(job.id, -1)"
                      :title="t('controlRoom.priorityDown')"
                    >↓</button>
                  </template>
                  <button
                    class="cr-cancel-btn cr-cancel-btn--sm"
                    @click="$emit('cancel', job.id)"
                    :title="t('controlRoom.cancel')"
                  >✕</button>
                </div>
              </div>
            </div>
          </div>

          <div class="cr-throughput">
            <span class="cr-throughput-label">THROUGHPUT</span>
            <span class="cr-sparkline">{{ sparkline }}</span>
            <span class="cr-throughput-val">{{ throughput }}/min</span>
          </div>
        </section>

        <!-- ── right: event log ── -->
        <section class="cr-section cr-section--log">
          <div class="cr-section-header-row">
            <span class="cr-section-label">EVENT LOG</span>
            <span class="cr-log-count">{{ filteredLog.length }}/200</span>
            <span class="cr-log-filters">
              <button
                class="cr-filter-btn"
                :class="{ active: logFilter === 'all' }"
                @click="logFilter = 'all'"
              >ALL</button>
              <button
                class="cr-filter-btn"
                :class="{ active: logFilter === 'done' }"
                @click="logFilter = 'done'"
              >DONE</button>
              <button
                class="cr-filter-btn"
                :class="{ active: logFilter === 'fault' }"
                @click="logFilter = 'fault'"
              >FAULT</button>
            </span>
          </div>

          <div class="cr-log-scroll" ref="logScrollRef">
            <div v-if="filteredLog.length === 0" class="cr-log-empty">— no events —</div>
            <div
              v-for="(entry, i) in filteredLog"
              :key="i"
              class="cr-log-entry"
              :class="[`cr-log--${entry.level}`, { 'cr-log--clickable': !!entry.jobId }]"
              @click="entry.jobId && $emit('reopen-refine', entry.jobId)"
            >
              <span class="cr-log-icon">{{ entry.level === 'done' ? '✓' : entry.level === 'fault' ? '✗' : '·' }}</span>
              <span class="cr-log-time">{{ entry.text.slice(0, 8) }}</span>
              <span class="cr-log-msg">{{ entry.text.slice(10) }}</span>
              <span v-if="entry.sha256s?.length" class="cr-log-thumbs">
                <img
                  v-for="sha in entry.sha256s.slice(0, 4)" :key="sha"
                  :src="`/api/thumbnails/${sha}.webp`"
                  class="cr-log-thumb"
                  @error="onThumbnailError($event)"
                />
              </span>
            </div>
          </div>
        </section>

      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useControlRoom } from '../composables/useControlRoom.js'
import ProgressBar from './ProgressBar.vue'

const { t } = useI18n()

const props = defineProps({
  jobsMap:      { type: Object,  required: true },
  controlRoom:  { type: Object,  required: true },
  sseConnected: { type: Boolean, default: true },
  disks:          { type: Array,   default: () => [] },
  diskCautionPct: { type: Number,  default: 75 },
  diskFaultPct:   { type: Number,  default: 90 },
})

const emit = defineEmits(['close', 'retry', 'cancel', 'retry-all-failed', 'cancel-all-queued', 'reopen-refine'])

const {
  SYSTEMS,
  systemStatus,
  masterStatus,
  activeJobs,
  waitedJobs,
  eventLog,
  throughput,
  sparkline,
  totalProcessed,
  localResources,
  remoteResources,
  laneStates,
  guardActive,
  guardSourceLabel,
  promptActiveJob,
  promptQueueDepth,
  genActiveJob,
  genQueueDepth,
  tagActiveJob,
  tagQueueDepth,
  embedActiveJob,
  embedQueueDepth,
  evalActiveJob,
  evalQueueDepth,
  promptResource,
  genResource,
  tagResource,
  embedResource,
  evalResource,
} = props.controlRoom

function isGuardPaused(lane) {
  const ls = laneStates.value[lane]
  return !!(ls?.paused && ls.pause_reason === 'auto')
}

async function toggleLanePause(lane) {
  const paused = laneStates.value[lane]?.paused
  try {
    await fetch(`/api/jobs/lanes/${lane}/${paused ? 'resume' : 'pause'}`, { method: 'POST' })
  } catch (e) {
    console.error('Lane control error:', e)
  }
}

// ── P&ID unit descriptors (flow order: PROMPT → GEN → TAG → EMBED → EVAL) ───
// metric picks which local gauge to show; cpuOnly marks GPU-independent lanes;
// trigger marks priority lanes that can hold the GPU interlock
const pidUnits = computed(() => [
  { key: 'prompt', name: 'PROMPT',    lane: 'prompt',  status: systemStatus.value.promptEngine, job: promptActiveJob.value, depth: promptQueueDepth.value, resource: promptResource.value, metric: 'gpu', trigger: true },
  { key: 'gen',   name: 'GENERATION', lane: 'gen',     status: systemStatus.value.generation, job: genActiveJob.value,   depth: genQueueDepth.value,   resource: genResource.value,   metric: 'gpu', trigger: true },
  { key: 'tag',   name: 'TAGGING',    lane: 'tagging', status: systemStatus.value.tagging,    job: tagActiveJob.value,   depth: tagQueueDepth.value,   resource: tagResource.value,   metric: 'cpu', cpuOnly: true },
  { key: 'embed', name: 'EMBEDDING',  lane: 'embed',   status: systemStatus.value.embedding,  job: embedActiveJob.value, depth: embedQueueDepth.value, resource: embedResource.value, metric: 'gpu' },
  { key: 'eval',  name: 'ALIGNMENT',  lane: 'eval',    status: systemStatus.value.alignment,  job: evalActiveJob.value,  depth: evalQueueDepth.value,  resource: evalResource.value,  metric: 'cpu' },
])

function pipeFlowing(i) {
  const units = pidUnits.value
  if (i === 0) return units[0].depth > 0
  return !!(units[i - 1].job || units[i].depth > 0)
}

// track jobs where pause was requested but state is still running (for blink control)
const pausePending = ref(new Set())

// snapshot of elapsed seconds at pause time (to freeze the timer display)
const pausedElapsed = ref({})

watch(activeJobs, (jobs) => {
  const now = Math.floor(Date.now() / 1000)

  // pausePending: clear when job transitions out of running
  if (pausePending.value.size) {
    const changed = jobs.filter(j => pausePending.value.has(j.id) && j.state !== 'running')
    if (changed.length) {
      const next = new Set(pausePending.value)
      changed.forEach(j => next.delete(j.id))
      pausePending.value = next
    }
  }

  // pausedElapsed: freeze elapsed seconds at the moment of pause, clear on resume
  const nextElapsed = { ...pausedElapsed.value }
  let changed = false
  for (const job of jobs) {
    if (job.state === 'paused' && !(job.id in nextElapsed)) {
      nextElapsed[job.id] = job.started_at ? now - job.started_at : 0
      changed = true
    } else if (job.state !== 'paused' && job.id in nextElapsed) {
      delete nextElapsed[job.id]
      changed = true
    }
  }
  if (changed) pausedElapsed.value = nextElapsed
})

async function pauseJob(jobId) {
  pausePending.value = new Set([...pausePending.value, jobId])
  try { await fetch(`/api/jobs/${jobId}/pause`, { method: 'POST' }) }
  catch (e) {
    pausePending.value = new Set([...pausePending.value].filter(id => id !== jobId))
    console.error('Pause job error:', e)
  }
}

async function resumeJob(jobId) {
  try { await fetch(`/api/jobs/${jobId}/resume`, { method: 'POST' }) }
  catch (e) { console.error('Resume job error:', e) }
}

async function reorderJob(jobId, direction) {
  try {
    await fetch(`/api/jobs/${jobId}/reorder`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ direction }),
    })
  } catch (e) { console.error('Reorder job error:', e) }
}

const MASTER_LAMP = {
  STARTING: 'cr-lamp--starting',
  STANDBY:  'cr-lamp--standby',
  RUNNING:  'cr-lamp--active',
  CAUTION:  'cr-lamp--caution',
  FAULT:    'cr-lamp--fault',
}
const masterLampClass = computed(() => MASTER_LAMP[masterStatus.value] ?? 'cr-lamp--standby')

// queued jobId list per lane (descending priority, matches waitedJobs sort order)
const queuedIdsByLane = computed(() => {
  const map = {}
  for (const job of waitedJobs.value) {
    if (!job.held) {
      if (!map[job.lane]) map[job.lane] = []
      map[job.lane].push(job.id)
    }
  }
  return map
})

const RESOURCE_LABELS = {
  'remote-qdrant':  'Qdrant',
  'remote-ollama':  'Ollama',
  'remote-comfyui': 'ComfyUI',
  'local-gpu0':     'GPU 0',
}
function resourceLabel(name) {
  return RESOURCE_LABELS[name] ?? name
}

// ── clock + elapsed time tick ────────────────────────────────────────────────

const clock = ref('')
const elapsedTick = ref(0)

function updateClock() {
  const now = new Date()
  clock.value = [now.getHours(), now.getMinutes(), now.getSeconds()]
    .map(n => String(n).padStart(2, '0'))
    .join(':')
  elapsedTick.value++
}
updateClock()
const _clockTimer = setInterval(updateClock, 1000)
onUnmounted(() => clearInterval(_clockTimer))

// ── log filter ────────────────────────────────────────────────────────────────

const logFilter = ref('all')
const logScrollRef = ref(null)

const filteredLog = computed(() => {
  if (logFilter.value === 'fault') return eventLog.value.filter(e => e.level === 'fault')
  if (logFilter.value === 'done')  return eventLog.value.filter(e => e.level === 'done')
  return eventLog.value
})

// ── queue counts ──────────────────────────────────────────────────────────────

const pendingCount = computed(() => waitedJobs.value.length)

// ── bulk operations ───────────────────────────────────────────────────────────

const failedCount = computed(() => activeJobs.value.filter(j => j.state === 'failed').length)

// ── utilities ────────────────────────────────────────────────────────────────

const _LANE_CODE = { gen: 'GEN', tagging: 'TAG', embed: 'EMB', eval: 'ALN', prompt: 'PE', sync: 'SYN' }
function laneCode(lane) {
  return _LANE_CODE[lane] ?? (lane ?? '').slice(0, 3).toUpperCase()
}

function onThumbnailError(event) {
  const img = event.target
  const retries = parseInt(img.dataset.retries || '0')
  if (retries >= 3) return
  img.dataset.retries = retries + 1
  const src = img.src
  setTimeout(() => { img.src = ''; img.src = src }, 600 * (retries + 1))
}

function truncate(str, n) {
  if (!str) return ''
  return str.length > n ? str.slice(0, n) + '…' : str
}

function formatElapsed(job) {
  void elapsedTick.value
  if (!job.started_at) return ''
  const sec = job.id in pausedElapsed.value
    ? pausedElapsed.value[job.id]
    : Math.floor(Date.now() / 1000 - job.started_at)
  if (sec < 60) return `${sec}s`
  const m = Math.floor(sec / 60), r = sec % 60
  return r > 0 ? `${m}m ${r}s` : `${m}m`
}

function relativeTime(epoch) {
  const sec = Math.floor(Date.now() / 1000 - epoch)
  if (sec < 60)   return `${sec}s ago`
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`
  return `${Math.floor(sec / 3600)}h ago`
}

function tempClass(val, caution, fault) {
  if (val == null) return ''
  if (val >= fault)   return 'cr-gauge--fault'
  if (val >= caution) return 'cr-gauge--caution'
  return 'cr-gauge--nominal'
}

function ratioClass(used, total, caution, fault) {
  if (!total) return 'cr-gauge--nominal'
  const r = used / total
  if (r >= fault)   return 'cr-gauge--fault'
  if (r >= caution) return 'cr-gauge--caution'
  return 'cr-gauge--nominal'
}
</script>

<style scoped>
/* ── CSS variables ──────────────────────────────────────────────────────────── */
.cr-overlay {
  --cr-bg:            #0d0d12;
  --cr-bg-section:    #111118;
  --cr-border:        rgba(130, 80, 220, 0.25);
  --cr-border-inner:  rgba(130, 80, 220, 0.12);
  --cr-text:          #c8c8d4;
  --cr-text-dim:      #6a6a7a;
  --cr-text-label:    #9070c0;
  --cr-mono:          'JetBrains Mono', 'Fira Code', 'Consolas', monospace;

  --cr-nominal:       #3d6b50;
  --cr-nominal-glow:  rgba(61, 107, 80, 0.4);
  --cr-active:        #4a7c5a;
  --cr-active-glow:   rgba(74, 124, 90, 0.5);
  --cr-caution:       #b8860b;
  --cr-caution-glow:  rgba(184, 134, 11, 0.5);
  --cr-fault:         #cc3333;
  --cr-fault-glow:    rgba(204, 51, 51, 0.6);
  --cr-standby:       #2a2a3a;
  --cr-guard:         #45c5e0;
  --cr-guard-glow:    rgba(69, 197, 224, 0.5);

  /* centralized font sizes */
  --cr-font-body:  14px;
  --cr-font-small: 12px;
  --cr-font-label: 12px;
}

/* ── overlay ────────────────────────────────────────────────────────────────── */
.cr-overlay {
  position: fixed;
  inset: 0;
  z-index: var(--z-system);
  background: rgba(0, 0, 0, 0.6);
  display: flex;
  flex-direction: column;
  align-items: stretch;
}

.cr-panel {
  background: var(--cr-bg);
  border-bottom: 1px solid var(--cr-border);
  box-shadow: 0 4px 40px rgba(0, 0, 0, 0.8);
  display: flex;
  flex-direction: column;
  height: clamp(400px, 82vh, 960px);
}

/* ── header ──────────────────────────────────────────────────────────────────── */
.cr-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 18px;
  border-bottom: 1px solid var(--cr-border);
  background: #0a0a10;
  font-family: var(--cr-mono);
  font-size: var(--cr-font-body);
  flex-shrink: 0;
}

.cr-title {
  color: var(--cr-text-label);
  font-weight: 700;
  letter-spacing: 0.18em;
  margin-right: auto;
  font-size: var(--cr-font-body);
}

.cr-master-badge {
  display: flex;
  align-items: center;
  gap: 7px;
  font-weight: 700;
  font-size: var(--cr-font-body);
  letter-spacing: 0.08em;
  padding: 4px 12px;
  border-radius: 3px;
  border: 1px solid;
}
.cr-master--standby {
  color: var(--cr-text-dim);
  border-color: rgba(100, 100, 130, 0.35);
  background: rgba(100, 100, 130, 0.06);
}
.cr-master--starting {
  color: #4aa8cc;
  border-color: rgba(74, 168, 204, 0.45);
  background: rgba(74, 168, 204, 0.08);
}
.cr-master--running {
  color: var(--cr-active);
  border-color: rgba(74, 124, 90, 0.45);
  background: rgba(74, 124, 90, 0.09);
}
.cr-master--nominal {
  color: var(--cr-active);
  border-color: rgba(74, 124, 90, 0.45);
  background: rgba(74, 124, 90, 0.09);
}
.cr-master--caution {
  color: var(--cr-caution);
  border-color: rgba(184, 134, 11, 0.45);
  background: rgba(184, 134, 11, 0.09);
}
.cr-master--fault {
  color: var(--cr-fault);
  border-color: rgba(204, 51, 51, 0.45);
  background: rgba(204, 51, 51, 0.09);
}

.cr-sse-indicator {
  display: flex;
  align-items: center;
  gap: 6px;
  font-family: var(--cr-mono);
  font-size: var(--cr-font-small);
  font-weight: 700;
  letter-spacing: 0.1em;
}
.cr-sse-indicator.cr-sse--ok   { color: var(--cr-nominal); }
.cr-sse-indicator.cr-sse--lost { color: var(--cr-fault); }

.cr-sse-dot {
  display: inline-block;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
}
.cr-sse--ok .cr-sse-dot {
  background: var(--cr-nominal);
  box-shadow: 0 0 4px var(--cr-nominal-glow);
  animation: cr-pulse 3s ease-in-out infinite;
  will-change: opacity;
}
.cr-sse--lost .cr-sse-dot {
  background: var(--cr-fault);
  box-shadow: 0 0 6px var(--cr-fault-glow);
  animation: cr-pulse 0.6s ease-in-out infinite;
  will-change: opacity;
}

.cr-clock {
  color: var(--cr-text-dim);
  font-size: var(--cr-font-body);
  min-width: 6ch;
  letter-spacing: 0.05em;
}

.cr-close {
  background: none;
  border: none;
  color: var(--cr-text-dim);
  cursor: pointer;
  font-size: 16px;
  padding: 0 4px;
  line-height: 1;
}
.cr-close:hover { color: var(--cr-text); }

/* ── annunciator row ─────────────────────────────────────────────────────────── */
.cr-annunciator {
  display: flex;
  align-items: stretch;
  border-bottom: 1px solid var(--cr-border);
  background: #090910;
  flex-shrink: 0;
}

.cr-ann-tile {
  flex: 1 1 0;
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 10px 16px;
  border-right: 1px solid var(--cr-border-inner);
  font-family: var(--cr-mono);
  min-width: 0;
}
.cr-ann-tile:last-child { border-right: none; }

.cr-ann-tile .cr-lamp {
  width: 10px;
  height: 10px;
  flex-shrink: 0;
}

.cr-ann-name {
  font-size: var(--cr-font-small);
  color: var(--cr-text);
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cr-ann-state {
  font-size: var(--cr-font-small);
  font-weight: 700;
  letter-spacing: 0.06em;
  white-space: nowrap;
}

.cr-ann--nominal .cr-ann-state { color: #4a8060; }
.cr-ann--active  .cr-ann-state { color: var(--cr-active); }
.cr-ann--caution .cr-ann-state { color: var(--cr-caution); }
.cr-ann--fault                 { background: rgba(204, 51, 51, 0.06); }
.cr-ann--fault   .cr-ann-state { color: var(--cr-fault); }
.cr-ann--standby .cr-ann-state { color: var(--cr-text-dim); }

/* ── body (3-column grid) ───────────────────────────────────────────────────── */
.cr-body {
  display: grid;
  grid-template-columns: 380px 1fr 300px;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

/* ── section common ──────────────────────────────────────────────────────────── */
.cr-section {
  padding: 12px 16px;
  border: 1px solid var(--cr-border-inner);
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-height: 0;
  overflow: hidden;
}

.cr-section-label {
  font-family: var(--cr-mono);
  font-size: var(--cr-font-label);
  letter-spacing: 0.18em;
  color: var(--cr-text-label);
  font-weight: 700;
  flex-shrink: 0;
}

/* ── lamp ────────────────────────────────────────────────────────────────────── */
.cr-lamp {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.cr-lamp--nominal {
  background: var(--cr-nominal);
  box-shadow: 0 0 4px var(--cr-nominal-glow);
}

@keyframes cr-pulse {
  0%, 100% { opacity: 1; }
  50%       { opacity: 0.35; }
}

.cr-lamp--active,
.cr-lamp--caution {
  animation: cr-pulse var(--cr-pulse-duration, 2s) ease-in-out infinite;
  will-change: opacity;
}

.cr-lamp--active {
  background: var(--cr-active);
  box-shadow: 0 0 6px var(--cr-active-glow);
  --cr-pulse-duration: 2s;
}
.cr-lamp--caution {
  background: var(--cr-caution);
  box-shadow: 0 0 6px var(--cr-caution-glow);
  --cr-pulse-duration: 1s;
}
.cr-lamp--fault {
  background: var(--cr-fault);
  box-shadow: 0 0 8px var(--cr-fault-glow);
}
.cr-lamp--standby {
  background: var(--cr-standby);
}
.cr-lamp--starting {
  background: #4aa8cc;
  box-shadow: 0 0 6px rgba(74, 168, 204, 0.5);
  animation: cr-pulse 3s ease-in-out infinite;
  will-change: opacity;
}
.cr-lamp--paused {
  background: #6655aa;
  box-shadow: 0 0 5px rgba(102, 85, 170, 0.5);
  animation: cr-pulse 1.8s ease-in-out infinite;
  will-change: opacity;
}
.cr-ann--paused .cr-ann-state {
  color: #9988cc;
}
.cr-lamp--guard {
  background: var(--cr-guard);
  box-shadow: 0 0 6px var(--cr-guard-glow);
  animation: cr-pulse 2.6s ease-in-out infinite;
  will-change: opacity;
}
.cr-ann--guard { background: rgba(69, 197, 224, 0.05); }
.cr-ann--guard .cr-ann-state { color: var(--cr-guard); }

/* ── annunciator: lane pause/resume buttons ──────────────────────────────── */
.cr-ann-ctrl-btn {
  background: none;
  border: 1px solid rgba(100, 100, 150, 0.3);
  color: var(--cr-text-dim, #666);
  border-radius: 2px;
  padding: 1px 5px;
  font-size: 9px;
  cursor: pointer;
  line-height: 1;
  margin-left: auto;
  flex-shrink: 0;
  font-family: inherit;
  transition: background 0.15s, color 0.15s;
}
.cr-ann-ctrl-btn:hover {
  background: rgba(100, 100, 150, 0.15);
  color: var(--cr-text, #ccc);
}
.cr-ann-ctrl-btn.is-paused {
  border-color: rgba(102, 85, 170, 0.5);
  color: #9988cc;
}
.cr-ann-ctrl-btn.is-paused:hover {
  background: rgba(102, 85, 170, 0.15);
}
/* guard = spooler-managed: not operable, shown as a shield */
.cr-ann-ctrl-btn.is-guard,
.cr-pid-valve-btn.is-guard {
  border-color: rgba(69, 197, 224, 0.55);
  color: var(--cr-guard);
  cursor: default;
  pointer-events: none;
}

/* ── resources ───────────────────────────────────────────────────────────────── */
.cr-section--resources {
  grid-column: 1;
}

.cr-resources-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.cr-resource-local,
.cr-resource-remote {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--cr-border-inner);
}

.cr-resource-header {
  display: flex;
  align-items: center;
  gap: 7px;
  font-family: var(--cr-mono);
}

.cr-resource-name {
  color: var(--cr-text);
  font-size: var(--cr-font-body);
  font-weight: 600;
  letter-spacing: 0.04em;
}

.cr-remote-latency {
  color: var(--cr-text-dim);
  font-size: var(--cr-font-small);
  margin-left: auto;
  font-family: var(--cr-mono);
}

.cr-remote-down {
  color: var(--cr-fault);
  font-size: var(--cr-font-small);
  letter-spacing: 0.1em;
  margin-left: auto;
  font-family: var(--cr-mono);
  font-weight: 700;
}

.cr-remote-starting {
  color: #4aa8cc;
  font-size: var(--cr-font-small);
  letter-spacing: 0.1em;
  margin-left: auto;
  font-family: var(--cr-mono);
  font-weight: 700;
}

.cr-remote-version {
  color: var(--cr-text-dim);
  font-size: var(--cr-font-small);
  font-family: var(--cr-mono);
  opacity: 0.6;
}

.cr-remote-slots {
  color: var(--cr-text-dim);
  font-size: 10px;
  font-family: var(--cr-mono);
  font-variant-numeric: tabular-nums;
  border: 1px solid rgba(100, 100, 130, 0.35);
  border-radius: 2px;
  padding: 0 4px;
  line-height: 1.5;
  flex-shrink: 0;
}

.cr-remote-endpoint {
  font-family: var(--cr-mono);
  font-size: var(--cr-font-small);
  color: var(--cr-text-dim);
  padding-left: 15px;
  display: flex;
  gap: 8px;
}

.cr-remote-lastok {
  color: #444;
}

.cr-gauge-unavailable {
  font-family: var(--cr-mono);
  font-size: var(--cr-font-small);
  color: var(--cr-text-dim);
  padding-left: 15px;
}

.cr-gauge-row {
  display: flex;
  align-items: center;
  gap: 7px;
  padding-left: 15px;
  font-family: var(--cr-mono);
}

.cr-gauge-label {
  color: var(--cr-text-dim);
  min-width: 40px;
  font-size: var(--cr-font-small);
}

.cr-gauge-track {
  flex: 1;
  height: 8px;
  background: rgba(255, 255, 255, 0.07);
  border-radius: 2px;
  /* overflow:hidden removed — fill stays within scaleX(0-1) bounds */
}

.cr-gauge-fill {
  height: 100%;
  width: 100%;
  border-radius: 2px;
  transform-origin: left center;
  will-change: transform;
}

.cr-gauge--nominal { background: var(--cr-nominal); }
.cr-gauge--caution { background: var(--cr-caution); }
.cr-gauge--fault   { background: var(--cr-fault); }

.cr-section-sublabel {
  font-family: var(--cr-mono);
  font-size: 10px;
  color: var(--cr-text-label);
  letter-spacing: 0.12em;
  font-weight: 700;
  margin-top: 8px;
  margin-bottom: 3px;
}

.cr-disk-row {
  margin-bottom: 4px;
}

.cr-disk-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  font-family: var(--cr-mono);
  font-size: 11px;
  color: var(--cr-text-dim);
  margin-bottom: 2px;
  gap: 4px;
}

.cr-disk-name {
  color: var(--cr-text);
  font-size: 11px;
  flex-shrink: 0;
}

.cr-disk-val {
  font-size: 10px;
  text-align: right;
  white-space: nowrap;
}

.cr-gauge-track--disk {
  height: 5px;
}

.cr-gauge-val {
  color: var(--cr-text-dim);
  min-width: 7ch;
  text-align: right;
  font-size: var(--cr-font-small);
  font-family: var(--cr-mono);
}

.cr-gauge-pct {
  min-width: 4ch;
}

.cr-temp-val {
  font-family: var(--cr-mono);
  font-size: var(--cr-font-small);
  min-width: 5ch;
  text-align: right;
  color: var(--cr-text-dim);
}

.cr-temp-val.cr-gauge--caution { color: var(--cr-caution); }
.cr-temp-val.cr-gauge--fault   { color: var(--cr-fault); }

.cr-queue-row {
  display: flex;
  align-items: center;
  gap: 10px;
  font-family: var(--cr-mono);
  font-size: var(--cr-font-body);
  margin-top: 2px;
}

.cr-queue-label {
  color: var(--cr-text-label);
  font-size: var(--cr-font-label);
  letter-spacing: 0.12em;
  font-weight: 700;
}

.cr-queue-val {
  color: var(--cr-text);
}

.cr-caution-text {
  color: var(--cr-caution);
  font-weight: 700;
}

.cr-totalizer {
  font-family: var(--cr-mono);
  font-size: var(--cr-font-body);
  color: var(--cr-text-dim);
}

.cr-totalizer-glyph {
  color: var(--cr-nominal);
  margin-right: 5px;
}

/* ── job list ────────────────────────────────────────────────────────────────── */
.cr-section--jobs {
  grid-column: 2;
}

.cr-job-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.cr-job-empty {
  font-family: var(--cr-mono);
  font-size: var(--cr-font-body);
  color: var(--cr-text-dim);
  padding: 10px 0;
}

.cr-job-entry {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-family: var(--cr-mono);
  font-size: var(--cr-font-body);
  padding: 6px 8px;
  border-radius: 3px;
  border-left: 2px solid transparent;
}

.cr-job--running   { border-left-color: var(--cr-active); }
.cr-job--queued    { border-left-color: var(--cr-standby); }
.cr-job--failed    { border-left-color: var(--cr-fault); background: rgba(204, 51, 51, 0.07); }
.cr-job--cancelling { border-left-color: var(--cr-caution); }

.cr-job-row1 {
  display: flex;
  align-items: center;
  gap: 8px;
}

.cr-job-id-dim {
  color: var(--cr-text-dim);
  font-size: var(--cr-font-small);
  flex-shrink: 0;
  opacity: 0.7;
}

.cr-job-title {
  color: var(--cr-text);
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--cr-font-body);
}

.cr-job-row2 {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-left: 4px;
}

.cr-job-row2--error {
  padding-left: 6px;
}

.cr-job-progress {
  flex: 1;
}

.cr-job-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 4px;
  padding-left: 6px;
}

.cr-job-thumbs {
  display: flex;
  gap: 2px;
  flex-shrink: 0;
}

.cr-job-thumb {
  width: 22px;
  height: 22px;
  object-fit: cover;
  border-radius: 2px;
  opacity: 0.7;
}

.cr-job-prompt-preview {
  font-size: 10px;
  color: var(--cr-text-dim);
  flex: 1;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-family: var(--cr-mono);
}

.cr-job-elapsed {
  color: var(--cr-text-dim);
  font-size: var(--cr-font-small);
  flex-shrink: 0;
  margin-left: auto;
  min-width: 6ch;
  text-align: right;
}

.cr-job-status {
  color: var(--cr-text-dim);
  font-size: var(--cr-font-small);
}

.cr-job-error {
  flex: 1;
  color: var(--cr-fault);
  font-size: var(--cr-font-small);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cr-cancel-btn {
  background: none;
  border: 1px solid var(--cr-text-dim);
  color: var(--cr-text-dim);
  font-family: var(--cr-mono);
  font-size: var(--cr-font-small);
  padding: 2px 10px;
  cursor: pointer;
  border-radius: 2px;
  white-space: nowrap;
  flex-shrink: 0;
}
.cr-cancel-btn:hover {
  border-color: var(--cr-fault);
  color: var(--cr-fault);
}

.cr-retry-btn {
  background: none;
  border: 1px solid var(--cr-fault);
  color: var(--cr-fault);
  font-family: var(--cr-mono);
  font-size: var(--cr-font-small);
  padding: 2px 10px;
  cursor: pointer;
  border-radius: 2px;
  white-space: nowrap;
  flex-shrink: 0;
}
.cr-retry-btn:hover {
  background: rgba(204, 51, 51, 0.15);
}

.cr-job-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.cr-job-action-btn {
  background: none;
  border: 1px solid var(--cr-text-dim);
  color: var(--cr-text-dim);
  font-size: 11px;
  padding: 1px 6px;
  cursor: pointer;
  border-radius: 2px;
  white-space: nowrap;
  line-height: 1.6;
}
.cr-job-action-btn:hover {
  border-color: var(--cr-text);
  color: var(--cr-text);
}
.cr-job-action-btn:disabled {
  opacity: 0.25;
  cursor: default;
  pointer-events: none;
}
.cr-job-action-btn--resume {
  border-color: #4caf50;
  color: #4caf50;
}
.cr-job-action-btn--resume:hover {
  background: rgba(76, 175, 80, 0.15);
}
.cr-job-action-btn--pause-pending {
  border-color: #e8a84a;
  color: #e8a84a;
  animation: cr-pulse 0.7s ease-in-out infinite;
  cursor: default;
  pointer-events: none;
}

.cr-job-badge {
  font-size: var(--cr-font-small);
  padding: 1px 7px;
  border-radius: 2px;
  white-space: nowrap;
  flex-shrink: 0;
}
.cr-job-badge--held {
  background: rgba(130, 60, 200, 0.25);
  color: #b98fdf;
  border: 1px solid rgba(130, 60, 200, 0.5);
}
.cr-job-badge--paused {
  background: rgba(230, 140, 40, 0.22);
  color: #e8a84a;
  border: 1px solid rgba(230, 140, 40, 0.5);
}
.cr-job-badge--guard {
  background: rgba(69, 197, 224, 0.12);
  color: var(--cr-guard);
  border: 1px solid rgba(69, 197, 224, 0.45);
}

/* ── bulk action bar ─────────────────────────────────────────────────────────── */
.cr-bulk-bar {
  display: flex;
  gap: 8px;
  padding-top: 6px;
  border-top: 1px solid var(--cr-border-inner);
  flex-shrink: 0;
}
/* ── waited queue (ISA-101 DCS style) ────────────────────────────────────────── */
.cr-waited-block {
  flex-shrink: 0;
  border-top: 1px solid var(--cr-border-inner);
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.cr-waited-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 0 4px;
  flex-shrink: 0;
}

.cr-waited-label {
  font-family: var(--cr-mono);
  font-size: var(--cr-font-label);
  letter-spacing: 0.18em;
  color: var(--cr-text-label);
  font-weight: 700;
}

.cr-waited-count {
  font-family: var(--cr-mono);
  font-size: var(--cr-font-label);
  color: var(--cr-text-dim);
  min-width: 2ch;
  font-weight: 600;
}
.cr-waited-count--caution {
  color: var(--cr-caution);
}

.cr-waited-cancel-all {
  margin-left: auto;
  padding: 1px 8px;
  font-size: 10px;
}

.cr-waited-list {
  display: flex;
  flex-direction: column;
  max-height: 160px;
  overflow-y: auto;
}

.cr-waited-empty {
  font-family: var(--cr-mono);
  font-size: var(--cr-font-small);
  color: var(--cr-text-dim);
  opacity: 0.4;
  padding: 3px 0;
}

.cr-waited-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 3px 2px;
  font-family: var(--cr-mono);
  font-size: 11px;
  border-left: 2px solid transparent;
  border-bottom: 1px solid rgba(130, 80, 220, 0.06);
}
.cr-waited-row:last-child { border-bottom: none; }
.cr-waited-row:hover { background: rgba(255, 255, 255, 0.025); }
.cr-waited-row--held { border-left-color: #6655aa; opacity: 0.7; }

.cr-waited-pos {
  color: var(--cr-text-dim);
  font-size: 10px;
  min-width: 2ch;
  text-align: right;
  opacity: 0.5;
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
}

.cr-waited-lane {
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.06em;
  padding: 1px 5px;
  border-radius: 1px;
  flex-shrink: 0;
  /* default standby */
  background: rgba(100, 100, 130, 0.18);
  color: #8888aa;
  border: 1px solid rgba(100, 100, 130, 0.3);
}
/* per-lane color coding (ISA-101: use subdued tints, not saturated) */
.cr-waited-lane[data-lane="gen"]     { background: rgba(74, 124, 90, 0.18);  color: #6aaa80; border-color: rgba(74, 124, 90, 0.35); }
.cr-waited-lane[data-lane="tagging"] { background: rgba(170, 125, 65, 0.18); color: #c09a60; border-color: rgba(170, 125, 65, 0.35); }
.cr-waited-lane[data-lane="embed"]  { background: rgba(74, 110, 180, 0.18); color: #7090cc; border-color: rgba(74, 110, 180, 0.35); }
.cr-waited-lane[data-lane="eval"]   { background: rgba(160, 100, 200, 0.18); color: #aa80cc; border-color: rgba(160, 100, 200, 0.35); }
.cr-waited-lane[data-lane="prompt"] { background: rgba(184, 134, 11, 0.15); color: #b89040; border-color: rgba(184, 134, 11, 0.3); }
.cr-waited-lane[data-lane="sync"]   { background: rgba(80, 140, 160, 0.18); color: #60aacc; border-color: rgba(80, 140, 160, 0.35); }

.cr-waited-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--cr-text-dim);
  font-size: 11px;
}

.cr-waited-tag {
  font-size: 9px;
  letter-spacing: 0.06em;
  padding: 1px 5px;
  border-radius: 1px;
  flex-shrink: 0;
  font-weight: 700;
}
.cr-waited-tag--held {
  background: rgba(102, 85, 170, 0.2);
  color: #9988cc;
  border: 1px solid rgba(102, 85, 170, 0.4);
}
.cr-waited-tag--pri {
  background: rgba(184, 134, 11, 0.15);
  color: #b89040;
  border: 1px solid rgba(184, 134, 11, 0.3);
}

.cr-waited-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
}

.cr-waited-btn {
  background: none;
  border: 1px solid rgba(100, 100, 130, 0.25);
  color: var(--cr-text-dim);
  font-size: 9px;
  padding: 0 4px;
  line-height: 1.7;
  cursor: pointer;
  border-radius: 1px;
  opacity: 0.6;
}
.cr-waited-btn:hover { opacity: 1; background: rgba(255, 255, 255, 0.05); }
.cr-waited-btn:disabled { opacity: 0.15; cursor: default; pointer-events: none; }

.cr-cancel-btn--sm {
  font-size: 9px;
  padding: 0px 5px;
  line-height: 1.7;
  opacity: 0.5;
}
.cr-cancel-btn--sm:hover { opacity: 1; }

.cr-waited-list::-webkit-scrollbar { width: 3px; }
.cr-waited-list::-webkit-scrollbar-track { background: transparent; }
.cr-waited-list::-webkit-scrollbar-thumb { background: var(--cr-border); border-radius: 2px; }

.cr-bulk-btn {
  background: none;
  font-family: var(--cr-mono);
  font-size: var(--cr-font-small);
  padding: 3px 10px;
  cursor: pointer;
  border-radius: 2px;
  white-space: nowrap;
}

.cr-bulk-btn--retry {
  border: 1px solid var(--cr-fault);
  color: var(--cr-fault);
}
.cr-bulk-btn--retry:hover {
  background: rgba(204, 51, 51, 0.12);
}

.cr-bulk-btn--cancel {
  border: 1px solid var(--cr-text-dim);
  color: var(--cr-text-dim);
}
.cr-bulk-btn--cancel:hover {
  border-color: var(--cr-caution);
  color: var(--cr-caution);
}

/* ── throughput ──────────────────────────────────────────────────────────────── */
.cr-throughput {
  display: flex;
  align-items: center;
  gap: 8px;
  font-family: var(--cr-mono);
  font-size: var(--cr-font-small);
  padding-top: 6px;
  border-top: 1px solid var(--cr-border-inner);
  color: var(--cr-text-dim);
  flex-shrink: 0;
}

.cr-throughput-label {
  color: var(--cr-text-label);
  letter-spacing: 0.12em;
  font-size: var(--cr-font-label);
  font-weight: 700;
}

.cr-sparkline {
  letter-spacing: 1px;
  color: var(--cr-nominal);
}

.cr-throughput-val {
  margin-left: auto;
  font-size: var(--cr-font-body);
  color: var(--cr-text);
  font-weight: 600;
}

/* ── event log ───────────────────────────────────────────────────────────────── */
.cr-section--log {
  grid-column: 3;
  overflow: hidden;
}

.cr-section-header-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.cr-log-count {
  font-family: var(--cr-mono);
  font-size: var(--cr-font-small);
  color: var(--cr-text-dim);
  opacity: 0.6;
}

.cr-log-filters {
  display: flex;
  gap: 4px;
  margin-left: auto;
}

.cr-filter-btn {
  background: none;
  border: 1px solid var(--cr-border-inner);
  color: var(--cr-text-dim);
  font-family: var(--cr-mono);
  font-size: var(--cr-font-small);
  padding: 2px 8px;
  cursor: pointer;
  border-radius: 2px;
  letter-spacing: 0.08em;
}
.cr-filter-btn.active,
.cr-filter-btn:hover {
  border-color: var(--cr-text-label);
  color: var(--cr-text-label);
}

.cr-log-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.cr-log-empty {
  font-family: var(--cr-mono);
  font-size: var(--cr-font-body);
  color: var(--cr-text-dim);
  padding: 6px 0;
}

.cr-log-entry {
  display: flex;
  align-items: baseline;
  gap: 7px;
  font-family: var(--cr-mono);
  font-size: var(--cr-font-body);
  padding: 3px 2px;
  line-height: 1.45;
}

.cr-log-icon {
  flex-shrink: 0;
  width: 1.1em;
  text-align: center;
  font-size: var(--cr-font-small);
}

.cr-log-time {
  color: var(--cr-text-dim);
  font-size: var(--cr-font-small);
  flex-shrink: 0;
  min-width: 8ch;
  opacity: 0.8;
}

.cr-log-msg {
  flex: 1;
  word-break: break-word;
  white-space: normal;
}

.cr-log--info  .cr-log-icon { color: var(--cr-text-dim); }
.cr-log--done  .cr-log-icon { color: #4a8060; }
.cr-log--fault .cr-log-icon { color: var(--cr-fault); }
.cr-log--info  .cr-log-msg  { color: var(--cr-text-dim); }
.cr-log--done  .cr-log-msg  { color: #5a9070; }
.cr-log--fault .cr-log-msg  { color: #e05050; }

.cr-log--clickable { cursor: pointer; }
.cr-log--clickable:hover { background: rgba(130, 80, 220, 0.08); border-radius: 2px; }

.cr-log-thumbs {
  display: flex;
  gap: 2px;
  flex-shrink: 0;
  margin-left: auto;
}

.cr-log-thumb {
  width: 18px;
  height: 18px;
  object-fit: cover;
  border-radius: 2px;
  opacity: 0.75;
}

/* ── scrollbar ───────────────────────────────────────────────────────────────── */
.cr-log-scroll::-webkit-scrollbar,
.cr-job-list::-webkit-scrollbar,
.cr-resources-scroll::-webkit-scrollbar { width: 4px; }
.cr-log-scroll::-webkit-scrollbar-track,
.cr-job-list::-webkit-scrollbar-track,
.cr-resources-scroll::-webkit-scrollbar-track { background: transparent; }
.cr-log-scroll::-webkit-scrollbar-thumb,
.cr-job-list::-webkit-scrollbar-thumb,
.cr-resources-scroll::-webkit-scrollbar-thumb {
  background: var(--cr-border);
  border-radius: 2px;
}

/* ── P&ID process flow diagram ──────────────────────────────────────────────── */
.cr-pid {
  position: relative;
  display: flex;
  align-items: center;
  padding: 0 20px;
  height: 112px;
  background: #090910;
  border-bottom: 1px solid var(--cr-border);
  flex-shrink: 0;
  overflow: hidden;
  font-family: var(--cr-mono);
  gap: 0;
}

/* ── GPU interlock rail (dashed bus across the top while guard is active) ──── */
.cr-pid-interlock-rail {
  position: absolute;
  top: 4px;
  left: 20px;
  right: 20px;
  height: 0;
  border-top: 1px dashed rgba(69, 197, 224, 0.55);
  display: flex;
  justify-content: center;
  pointer-events: none;
  z-index: 1;
}

.cr-pid-interlock-label {
  position: relative;
  top: -7px;
  background: #090910;
  padding: 0 8px;
  font-size: 8px;
  font-weight: 700;
  letter-spacing: 0.14em;
  color: var(--cr-guard);
  line-height: 1.4;
  white-space: nowrap;
}

/* wrapper so interlock taps can sit above the unit (unit itself clips overflow) */
.cr-pid-unit-wrap {
  position: relative;
  flex-shrink: 0;
}

.cr-pid-tap {
  position: absolute;
  top: -10px;
  left: 50%;
  transform: translateX(-50%);
  font-size: 9px;
  line-height: 1;
  z-index: 2;
  pointer-events: none;
  color: var(--cr-guard);
  text-shadow: 0 0 4px var(--cr-guard-glow);
}
.cr-pid-tap--shield { animation: cr-pulse 2.6s ease-in-out infinite; }

/* terminus boxes (JOBS IN / VECTOR STORE) */
.cr-pid-terminus {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 5px;
  width: 72px;
  height: 82px;
  border: 1px solid rgba(130, 80, 220, 0.22);
  border-radius: 2px;
  background: rgba(130, 80, 220, 0.05);
  flex-shrink: 0;
}

.cr-pid-terminus-label {
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.1em;
  color: var(--cr-text-label);
  text-align: center;
  line-height: 1.3;
}

.cr-pid-terminus-count {
  font-size: 15px;
  font-weight: 700;
  color: var(--cr-text-dim);
  font-variant-numeric: tabular-nums;
  min-width: 2ch;
  text-align: center;
  line-height: 1;
}
.cr-pid-terminus-count--active { color: var(--cr-active); }

/* pipe segments */
.cr-pid-pipe {
  flex: 1 0 16px;
  max-width: 56px;
  height: 82px;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
}

.cr-pid-pipe--xfer { max-width: 48px; }

.cr-pid-pipe-line {
  width: 100%;
  height: 3px;
  border-radius: 1px;
  transition: background 0.5s ease, box-shadow 0.5s ease;
}

.cr-pid-pipe--flowing {
  background: linear-gradient(90deg, var(--cr-nominal) 0%, #5ab070 100%);
  box-shadow: 0 0 5px rgba(61, 107, 80, 0.5);
}

.cr-pid-pipe--idle { background: rgba(130, 80, 220, 0.12); }

.cr-pid-pipe-arrow {
  position: absolute;
  font-size: 9px;
  color: var(--cr-text-dim);
  opacity: 0.55;
  pointer-events: none;
  line-height: 1;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -55%);
  margin-top: 1px;
}

/* processing unit boxes */
.cr-pid-unit {
  display: flex;
  flex-direction: column;
  gap: 0;
  width: 164px;
  height: 90px;
  border: 1px solid rgba(130, 80, 220, 0.2);
  border-radius: 2px;
  background: #0c0c14;
  flex-shrink: 0;
  overflow: hidden;
  transition: border-color 0.3s, background 0.3s;
}

/* lane base tints */
.cr-pid-unit--gen    { border-color: rgba(74, 124, 90, 0.3); background: rgba(74, 124, 90, 0.05); }
.cr-pid-unit--embed  { border-color: rgba(74, 110, 180, 0.3); background: rgba(74, 110, 180, 0.05); }
.cr-pid-unit--tag    { border-color: rgba(170, 125, 65, 0.3); background: rgba(170, 125, 65, 0.05); }
.cr-pid-unit--prompt { border-color: rgba(80, 140, 160, 0.3); background: rgba(80, 140, 160, 0.05); }

.cr-pid-unit--prompt.cr-pid-state--active,
.cr-pid-unit--prompt.cr-pid-state--nominal { border-color: rgba(80, 140, 160, 0.55); background: rgba(80, 140, 160, 0.09); }
.cr-pid-unit--prompt.cr-pid-state--caution { border-color: rgba(184, 134, 11, 0.5); background: rgba(184, 134, 11, 0.07); }
.cr-pid-unit--prompt.cr-pid-state--fault   { border-color: rgba(204, 51, 51, 0.5); background: rgba(204, 51, 51, 0.07); }
.cr-pid-unit--prompt.cr-pid-state--paused  { border-color: rgba(102, 85, 170, 0.45); background: rgba(102, 85, 170, 0.06); }

.cr-pid-unit--tag.cr-pid-state--active,
.cr-pid-unit--tag.cr-pid-state--nominal { border-color: rgba(170, 125, 65, 0.55); background: rgba(170, 125, 65, 0.09); }
.cr-pid-unit--tag.cr-pid-state--caution { border-color: rgba(184, 134, 11, 0.5); background: rgba(184, 134, 11, 0.07); }
.cr-pid-unit--tag.cr-pid-state--fault   { border-color: rgba(204, 51, 51, 0.5); background: rgba(204, 51, 51, 0.07); }
.cr-pid-unit--tag.cr-pid-state--paused  { border-color: rgba(102, 85, 170, 0.45); background: rgba(102, 85, 170, 0.06); }

/* GPU guard state overrides the lane tint on any unit */
.cr-pid-unit.cr-pid-state--guard {
  border-color: rgba(69, 197, 224, 0.5);
  background: rgba(69, 197, 224, 0.06);
}

/* ISA-101 state intensity modifiers */
.cr-pid-unit--gen.cr-pid-state--active,
.cr-pid-unit--gen.cr-pid-state--nominal  { border-color: rgba(74, 124, 90, 0.55); background: rgba(74, 124, 90, 0.09); }
.cr-pid-unit--gen.cr-pid-state--caution  { border-color: rgba(184, 134, 11, 0.5); background: rgba(184, 134, 11, 0.07); }
.cr-pid-unit--gen.cr-pid-state--fault    { border-color: rgba(204, 51, 51, 0.5); background: rgba(204, 51, 51, 0.07); }
.cr-pid-unit--gen.cr-pid-state--paused   { border-color: rgba(102, 85, 170, 0.45); background: rgba(102, 85, 170, 0.06); }

.cr-pid-unit--embed.cr-pid-state--active,
.cr-pid-unit--embed.cr-pid-state--nominal { border-color: rgba(74, 110, 180, 0.55); background: rgba(74, 110, 180, 0.09); }
.cr-pid-unit--embed.cr-pid-state--caution { border-color: rgba(184, 134, 11, 0.5); background: rgba(184, 134, 11, 0.07); }
.cr-pid-unit--embed.cr-pid-state--fault   { border-color: rgba(204, 51, 51, 0.5); background: rgba(204, 51, 51, 0.07); }
.cr-pid-unit--embed.cr-pid-state--paused  { border-color: rgba(102, 85, 170, 0.45); background: rgba(102, 85, 170, 0.06); }

.cr-pid-unit-header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 7px 4px;
  background: rgba(0,0,0,0.2);
  border-bottom: 1px solid rgba(130, 80, 220, 0.1);
  flex-shrink: 0;
}

.cr-pid-unit-header .cr-lamp { width: 8px; height: 8px; flex-shrink: 0; }

.cr-pid-unit-name {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.1em;
  color: var(--cr-text);
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.cr-pid-unit-state {
  font-size: 9px;
  letter-spacing: 0.05em;
  color: var(--cr-text-dim);
  flex-shrink: 0;
}

.cr-pid-state--active  .cr-pid-unit-state { color: var(--cr-active); }
.cr-pid-state--nominal .cr-pid-unit-state { color: #4a8060; }
.cr-pid-state--caution .cr-pid-unit-state { color: var(--cr-caution); }
.cr-pid-state--fault   .cr-pid-unit-state { color: var(--cr-fault); }
.cr-pid-state--paused  .cr-pid-unit-state { color: #8877bb; }
.cr-pid-state--guard   .cr-pid-unit-state { color: var(--cr-guard); font-weight: 700; }

/* CPU-only lane chip (TAGGING keeps running during GPU guard) */
.cr-pid-cpu-chip {
  font-size: 8px;
  font-weight: 700;
  letter-spacing: 0.08em;
  padding: 0 4px;
  line-height: 1.5;
  border: 1px solid rgba(170, 125, 65, 0.4);
  border-radius: 1px;
  color: #c09a60;
  flex-shrink: 0;
}

/* valve button */
.cr-pid-valve-btn {
  background: none;
  border: 1px solid rgba(100, 100, 150, 0.3);
  color: var(--cr-text-dim);
  border-radius: 2px;
  padding: 1px 5px;
  font-size: 9px;
  cursor: pointer;
  line-height: 1.4;
  flex-shrink: 0;
  font-family: inherit;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}
.cr-pid-valve-btn:hover { background: rgba(100, 100, 150, 0.15); color: var(--cr-text); }
.cr-pid-valve-btn.is-paused { border-color: rgba(102, 85, 170, 0.55); color: #9988cc; }
.cr-pid-valve-btn.is-paused:hover { background: rgba(102, 85, 170, 0.15); }

/* unit body */
.cr-pid-unit-body {
  display: flex;
  align-items: stretch;
  gap: 7px;
  padding: 6px 8px 5px;
  flex: 1;
  min-height: 0;
}

/* vertical tank (queue depth level indicator) */
.cr-pid-tank {
  width: 14px;
  flex-shrink: 0;
  border: 1px solid rgba(130, 80, 220, 0.15);
  border-radius: 1px;
  background: rgba(0,0,0,0.35);
  position: relative;
  overflow: hidden;
  display: flex;
  align-items: flex-end;
  justify-content: center;
}

.cr-pid-tank-fill {
  position: absolute;
  bottom: 0;
  left: 0;
  width: 100%;
  border-radius: 1px;
  transition: height 0.5s ease;
}

.cr-pid-tank-fill--prompt { background: rgba(80, 140, 160, 0.65); }
.cr-pid-tank-fill--gen   { background: rgba(74, 124, 90, 0.65); }
.cr-pid-tank-fill--tag   { background: rgba(170, 125, 65, 0.65); }
.cr-pid-tank-fill--embed { background: rgba(74, 110, 180, 0.65); }
.cr-pid-tank-fill--eval  { background: rgba(160, 100, 200, 0.65); }

.cr-pid-tank-label {
  font-size: 8px;
  color: var(--cr-text-dim);
  position: relative;
  z-index: 1;
  line-height: 1;
  padding-bottom: 2px;
  font-variant-numeric: tabular-nums;
}

/* horizontal reactor bar (active job progress) */
.cr-pid-reactor {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 4px;
  min-width: 0;
}

.cr-pid-reactor-track {
  height: 9px;
  background: rgba(255,255,255,0.05);
  border: 1px solid rgba(130, 80, 220, 0.12);
  border-radius: 1px;
  overflow: hidden;
}

.cr-pid-reactor-fill {
  height: 100%;
  width: 100%;
  border-radius: 1px;
  transform-origin: left center;
  transform: scaleX(0);
  will-change: transform;
  transition: transform 0.4s ease;
}

.cr-pid-reactor-fill--prompt { background: linear-gradient(90deg, #3a7080 0%, #60aacc 100%); }
.cr-pid-reactor-fill--gen   { background: linear-gradient(90deg, var(--cr-nominal) 0%, #6aaa80 100%); }
.cr-pid-reactor-fill--tag   { background: linear-gradient(90deg, #8a6a30 0%, #c09a60 100%); }
.cr-pid-reactor-fill--embed { background: linear-gradient(90deg, #3a5ea8 0%, #7090cc 100%); }
.cr-pid-reactor-fill--eval  { background: linear-gradient(90deg, #7040a0 0%, #aa80cc 100%); }

.cr-pid-reactor-label {
  font-size: 10px;
  color: var(--cr-text-dim);
  font-variant-numeric: tabular-nums;
  text-align: right;
  line-height: 1;
}

.cr-pid-reactor-label--guard {
  font-size: 8px;
  letter-spacing: 0.06em;
  color: var(--cr-guard);
  white-space: nowrap;
}

/* eval unit lane tint */
.cr-pid-unit--eval  { border-color: rgba(160, 100, 200, 0.3); background: rgba(160, 100, 200, 0.05); }
.cr-pid-unit--eval.cr-pid-state--active,
.cr-pid-unit--eval.cr-pid-state--nominal { border-color: rgba(160, 100, 200, 0.55); background: rgba(160, 100, 200, 0.09); }
.cr-pid-unit--eval.cr-pid-state--caution { border-color: rgba(184, 134, 11, 0.5); background: rgba(184, 134, 11, 0.07); }
.cr-pid-unit--eval.cr-pid-state--fault   { border-color: rgba(204, 51, 51, 0.5); background: rgba(204, 51, 51, 0.07); }
.cr-pid-unit--eval.cr-pid-state--paused  { border-color: rgba(102, 85, 170, 0.45); background: rgba(102, 85, 170, 0.06); }

/* resource bar footer */
.cr-pid-resbar {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 3px 7px 3px;
  border-top: 1px solid rgba(130, 80, 220, 0.1);
  background: rgba(0,0,0,0.18);
  flex-shrink: 0;
  min-height: 18px;
  overflow: hidden;
}

.cr-pid-resbar-name {
  font-size: 8px;
  color: var(--cr-text-dim);
  letter-spacing: 0.04em;
  flex-shrink: 0;
  font-family: var(--cr-mono);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 70px;
}

.cr-pid-resbar-gauge {
  flex: 1;
  height: 4px;
  background: rgba(255,255,255,0.06);
  border-radius: 1px;
  overflow: hidden;
  min-width: 20px;
}

.cr-pid-resbar-fill {
  height: 100%;
  border-radius: 1px;
  transition: width 0.5s ease;
}
.cr-pid-resbar-fill--prompt { background: rgba(80, 140, 160, 0.75); }
.cr-pid-resbar-fill--gen   { background: rgba(74, 124, 90, 0.75); }
.cr-pid-resbar-fill--tag   { background: rgba(170, 125, 65, 0.75); }
.cr-pid-resbar-fill--embed { background: rgba(74, 110, 180, 0.75); }
.cr-pid-resbar-fill--eval  { background: rgba(160, 100, 200, 0.75); }

.cr-pid-resbar-val {
  font-size: 8px;
  color: var(--cr-text-dim);
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
  font-family: var(--cr-mono);
  white-space: nowrap;
}

.cr-pid-resbar-vram {
  font-size: 8px;
  color: var(--cr-text-dim);
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
  font-family: var(--cr-mono);
  opacity: 0.6;
  white-space: nowrap;
}

.cr-pid-resbar-latency {
  font-size: 8px;
  color: var(--cr-active);
  font-variant-numeric: tabular-nums;
  font-family: var(--cr-mono);
  flex-shrink: 0;
}

.cr-pid-resbar-fault {
  font-size: 8px;
  color: var(--cr-fault);
  letter-spacing: 0.05em;
  font-family: var(--cr-mono);
  flex-shrink: 0;
}

.cr-pid-resbar-starting {
  font-size: 8px;
  color: #4aa8cc;
  letter-spacing: 0.05em;
  font-family: var(--cr-mono);
  flex-shrink: 0;
}

.cr-pid-resbar-slots {
  font-size: 8px;
  color: var(--cr-text-dim);
  border: 1px solid rgba(100, 100, 130, 0.35);
  border-radius: 1px;
  padding: 0 3px;
  font-family: var(--cr-mono);
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
}

.cr-lamp--xs { width: 6px !important; height: 6px !important; flex-shrink: 0; }
</style>
