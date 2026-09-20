#!/usr/bin/env node
/** Pose + shot-camera / set-overview contract tests (no WebGL / no VRM file load). */
import { buildPoseSketch, hintsFromProse } from '../frontend/src/muse/poseSketch.js'
import {
  placeAvatarCamera,
  placeSetOverviewCamera,
  shotCameraWorld,
  resolveDuoCollision,
  AVATAR_COLLISION_RADIUS,
} from '../frontend/src/muse/avatar3d.js'
import { clampBoneRotation } from '../frontend/src/muse/vrmIk.js'
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

{
  const { buildPoseCoachMessage, poseModelToTags, cameraEnumsFromShotPosition } =
    await import('../frontend/src/muse/poseCoach.js')
  const model = {
    posture: 'squatting',
    arms: 'arms_at_sides',
    gazePitch: 'looking_up',
    cameraPitch: 'below',
    cameraSide: 'side',
    cameraDistance: 'full',
  }
  const msg = buildPoseCoachMessage(model)
  assert(msg.includes('ポーズコーチング'), 'coach header')
  assert(msg.includes('こうしてね'), 'coach ask')
  assert(msg.includes('しゃがん'), 'posture ja')
  const tags = poseModelToTags(model)
  assert(tags.includes('from_side'), `tags ${tags}`)
  assert(tags.includes('from_below') || tags.includes('low_angle'), `tags pitch ${tags}`)
  const enums = cameraEnumsFromShotPosition(new THREE.Vector3(2.0, 0.1, 0.5), { crouch: true })
  assert(enums.cameraSide === 'side', `infer side ${enums.cameraSide}`)
  assert(enums.cameraPitch === 'below', `infer pitch ${enums.cameraPitch}`)
}

{
  // boneOverrides is additive: no override → byte-identical coach output.
  const { buildPoseCoachMessage, poseModelToTags, summarizeBoneOverrides } =
    await import('../frontend/src/muse/poseCoach.js')
  const base = {
    posture: 'sitting',
    arms: 'crossed_arms',
    gazePitch: 'looking_ahead',
    cameraPitch: 'eye',
    cameraSide: 'front',
    cameraDistance: 'full',
  }
  const msgBefore = buildPoseCoachMessage(base)
  const tagsBefore = poseModelToTags(base)
  assert(buildPoseCoachMessage({ ...base, boneOverrides: undefined }) === msgBefore, 'no-override message unchanged')
  assert(poseModelToTags({ ...base, boneOverrides: undefined }) === tagsBefore, 'no-override tags unchanged')

  const empty = summarizeBoneOverrides(null)
  assert(empty.bodyBits.length === 0 && empty.tags.length === 0, 'summarize null is empty')

  const torso = { ...base, boneOverrides: { chest: { x: 0.3, y: 0, z: 0 } } }
  const msgTorso = buildPoseCoachMessage(torso)
  const tagsTorso = poseModelToTags(torso)
  assert(msgTorso.includes('捻って'), `torso override should mention twist: ${msgTorso}`)
  assert(tagsTorso.includes('torso_twist'), `torso override tag: ${tagsTorso}`)
  assert(!msgTorso.includes('指'), 'torso override should not mention fingers')

  const finger = { ...base, boneOverrides: { leftThumbProximal: { x: 0.5, y: 0, z: 0 } } }
  const msgFinger = buildPoseCoachMessage(finger)
  const tagsFinger = poseModelToTags(finger)
  assert(msgFinger.includes('指'), `finger override should mention fingers: ${msgFinger}`)
  assert(tagsFinger.includes('finger_gesture'), `finger override tag: ${tagsFinger}`)
  assert(!tagsFinger.includes('torso_twist'), 'finger override should not tag torso_twist')

  const head = { ...base, boneOverrides: { neck: { x: 0.1, y: 0.2, z: 0 } } }
  assert(poseModelToTags(head).includes('head_tilt'), 'neck override tags head_tilt')
}

{
  // Joint-limit clamp: in range passes through, out of range clamps, unknown bone is a no-op.
  const inRange = clampBoneRotation('neck', { x: 0.1, y: 0.1, z: 0 })
  assert(inRange.x === 0.1 && inRange.y === 0.1, `in-range unclamped: ${JSON.stringify(inRange)}`)

  const outOfRange = clampBoneRotation('neck', { x: 5, y: -5, z: 5 })
  assert(outOfRange.x <= 0.6 && outOfRange.x >= -0.6, `neck x clamped: ${outOfRange.x}`)
  assert(outOfRange.y <= 0.8 && outOfRange.y >= -0.8, `neck y clamped: ${outOfRange.y}`)

  const finger = clampBoneRotation('rightThumbProximal', { x: 3, y: 3, z: 3 })
  assert(finger.x <= 1.4, `finger proximal x clamped: ${finger.x}`)
  assert(finger.y <= 0.3, `finger proximal y clamped: ${finger.y}`)

  const unknown = clampBoneRotation('leftUpperArm', { x: 3, y: -3, z: 2 })
  assert(unknown.x === 3 && unknown.y === -3 && unknown.z === 2, `unknown bone is no-op: ${JSON.stringify(unknown)}`)
}

{
  // Duo body-drag collision: too-close points get pushed out to exactly minDist,
  // sliding along the direction from the other character rather than snapping back.
  const minDist = AVATAR_COLLISION_RADIUS * 2
  const far = resolveDuoCollision(2, 0, -1, 0, minDist)
  assert(far.x === 2 && far.z === 0, `far apart untouched: ${JSON.stringify(far)}`)

  const overlapping = resolveDuoCollision(0.1, 0, 0, 0, minDist)
  const dist = Math.hypot(overlapping.x - 0, overlapping.z - 0)
  assert(Math.abs(dist - minDist) < 1e-6, `pushed out to minDist: ${dist} vs ${minDist}`)

  const closeButOk = resolveDuoCollision(minDist + 0.01, 0, 0, 0, minDist)
  assert(closeButOk.x === minDist + 0.01, 'just-outside minDist untouched')
}

console.log('avatar pose contract ok')
