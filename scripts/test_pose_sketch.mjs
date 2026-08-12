#!/usr/bin/env node
/** Pose + avatar camera contract tests (no WebGL / no VRM file load). */
import { buildPoseSketch, hintsFromProse } from '../frontend/src/muse/poseSketch.js'
import { placeAvatarCamera } from '../frontend/src/muse/avatar3d.js'
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
  const cam = new THREE.PerspectiveCamera()
  placeAvatarCamera(cam, p)
  assert(cam.position.y < 0.3, `low cam y=${cam.position.y}`)
  assert(cam.position.x > 0.8, `side cam x=${cam.position.x}`)
  assert(Math.abs(cam.position.z) < 0.8, `profile cam z=${cam.position.z}`)
}

{
  const h = hintsFromProse('空を見上げて、寄りで')
  assert(h.gazePitch === 'looking_up', 'looking up')
  assert(h.cameraDistance === 'close', 'close')
}

console.log('avatar pose contract ok')
