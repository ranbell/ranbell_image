#!/usr/bin/env node
/** Pose + shot-camera / set-overview contract tests (no WebGL / no VRM file load). */
import { buildPoseSketch, hintsFromProse } from '../frontend/src/muse/poseSketch.js'
import {
  placeAvatarCamera,
  placeSetOverviewCamera,
  shotCameraWorld,
} from '../frontend/src/muse/avatar3d.js'
import * as THREE from '../frontend/node_modules/three/build/three.module.js'

function assert(cond, msg) {
  if (!cond) throw new Error(msg)
}

{
  const p = buildPoseSketch(
    'crouching, squatting, from_side, profile, from_below, low_angle',
    { beat: 'しゃがんで', frame: '横からローアングル' },
  )
  assert(p.posture === 'squatting' || p.posture === 'crouching', `posture ${p.posture}`)
  assert(p.cameraSide === 'side', `side ${p.cameraSide}`)
  assert(p.cameraPitch === 'below', `pitch ${p.cameraPitch}`)
  assert(p.gazePitch === 'looking_up', `gaze ${p.gazePitch}`)

  // Shot camera (what Comfy should frame) stays low + side profile
  const shot = shotCameraWorld(p)
  assert(shot.position.y < 0.35, `low shot y=${shot.position.y}`)
  assert(shot.position.x > 0.8, `side shot x=${shot.position.x}`)
  assert(Math.abs(shot.position.z) < 0.9, `profile shot z=${shot.position.z}`)

  // Viewport is set overview: elevated, pulls back so full body + cam fit
  const overview = new THREE.PerspectiveCamera()
  placeSetOverviewCamera(overview, p, shot)
  assert(overview.position.y > 1.2, `overview elev y=${overview.position.y}`)
  assert(overview.position.length() > 2.5, `overview distance ${overview.position.length()}`)

  // Deprecated wrapper still places overview (not the low shot cam)
  const legacy = new THREE.PerspectiveCamera()
  placeAvatarCamera(legacy, p)
  assert(legacy.position.y > 1.2, `legacy overview y=${legacy.position.y}`)
}

{
  const h = hintsFromProse('空を見上げて、寄りで')
  assert(h.gazePitch === 'looking_up', 'looking up')
  assert(h.cameraDistance === 'close', 'close')
}

console.log('avatar pose contract ok')
