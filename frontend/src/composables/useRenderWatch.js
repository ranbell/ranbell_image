import { onUnmounted, ref } from 'vue'

/*
 * Keep re-reading something until the renders you asked for have landed.
 *
 * Queuing a character portrait returns a job id and nothing else; the picture
 * attaches itself to the preset minutes later, and the screen that asked for it
 * had no way to find out. You pressed "draw", nothing happened, and the new
 * candidate only appeared if you happened to close the panel and open it again.
 *
 * There is no per-character event to subscribe to — the job stream carries jobs,
 * not presets — so this polls, but only while something is actually outstanding
 * and never past its own deadline. A bulk of two hundred gets a long window; one
 * portrait gets a short one.
 */
export function useRenderWatch(refresh, { every = 4000 } = {}) {
  const watching = ref(false)
  let timer = null
  let until = 0

  function stop() {
    if (timer) clearInterval(timer)
    timer = null
    watching.value = false
  }

  /** Watch for `seconds`; calling again extends rather than restarts. */
  function watch(seconds) {
    until = Math.max(until, Date.now() + seconds * 1000)
    watching.value = true
    if (timer) return
    timer = setInterval(async () => {
      if (Date.now() > until) { stop(); return }
      try { await refresh() } catch { /* a failed poll is not worth a toast */ }
    }, every)
  }

  onUnmounted(stop)
  return { watch, stop, watching }
}
