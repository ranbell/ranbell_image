/**
 * Cute procedural 3D chibi — closer to the PVC-figure look.
 * Driven by poseSketch tags (posture / side / pitch / distance).
 * Three.js only — no VRM / no Comfy.
 */
import * as THREE from 'three'
import { buildPoseSketch } from './poseSketch.js'

const SKIN = 0xffe4e6
const BLUSH = 0xff8fab
const TEAL = 0x2ec4b6
const TEAL_DEEP = 0x1a9e94
const TEAL_SOFT = 0x7ee8de
const SHIRT = 0xb8f3ee
const WHITE = 0xfff8fb
const EYE_TEAL = 0x1f9e96
const EYE_DARK = 0x143d3a

function toonGradient() {
  // 4-step cel ramp for MeshToonMaterial
  const data = new Uint8Array([
    95, 95, 95, 255,
    150, 150, 150, 255,
    205, 205, 205, 255,
    255, 255, 255, 255,
  ])
  const tex = new THREE.DataTexture(data, 4, 1, THREE.RGBAFormat)
  tex.needsUpdate = true
  tex.magFilter = THREE.NearestFilter
  tex.minFilter = THREE.NearestFilter
  return tex
}

const GRAD = toonGradient()

function toon(color, opts = {}) {
  return new THREE.MeshToonMaterial({
    color,
    gradientMap: GRAD,
    ...opts,
  })
}

function v(x, y, z) { return new THREE.Vector3(x, y, z) }

/** Bone-ish limb: capsule between local points, updated each pose. */
function makeLimb(radius, color) {
  const mesh = new THREE.Mesh(new THREE.CapsuleGeometry(radius, 1, 5, 10), toon(color))
  mesh.castShadow = true
  return mesh
}

function setLimb(mesh, a, b, radius = 0.045) {
  const dir = v().subVectors(b, a)
  const len = dir.length()
  if (len < 1e-4) { mesh.visible = false; return }
  mesh.visible = true
  const span = 1 + 2 * radius
  const geoR = mesh.geometry.parameters?.radius ?? radius
  mesh.scale.set(radius / geoR, Math.max(0.05, len) / span, radius / geoR)
  mesh.position.copy(a).add(b).multiplyScalar(0.5)
  mesh.quaternion.setFromUnitVectors(v(0, 1, 0), dir.normalize())
}

/**
 * Build one cute uniform chibi (big head, teal bob, school-ish outfit, chunky shoes).
 */
export function makeChibi({ accent = TEAL, shirt = SHIRT } = {}) {
  const root = new THREE.Group()

  // —— Head ——
  const headG = new THREE.Group()
  const head = new THREE.Mesh(new THREE.SphereGeometry(0.34, 32, 24), toon(SKIN))
  head.castShadow = true
  // Hair bob (upper hemisphere + side volume)
  const hairMat = toon(accent)
  const hairCap = new THREE.Mesh(new THREE.SphereGeometry(0.355, 28, 20), hairMat)
  hairCap.scale.set(1.02, 0.92, 1.05)
  hairCap.position.y = 0.04
  const bang = new THREE.Mesh(new THREE.SphereGeometry(0.22, 16, 12), hairMat)
  bang.scale.set(1.15, 0.55, 0.55)
  bang.position.set(0, 0.08, 0.28)
  const sideL = new THREE.Mesh(new THREE.SphereGeometry(0.14, 14, 12), hairMat)
  const sideR = sideL.clone()
  sideL.position.set(-0.28, -0.02, 0.05)
  sideR.position.set(0.28, -0.02, 0.05)
  const ahoge = new THREE.Mesh(new THREE.CapsuleGeometry(0.025, 0.18, 4, 8), hairMat)
  ahoge.position.set(0.06, 0.48, 0.02)
  ahoge.rotation.z = -0.55

  // Eyes — teal, cute, matching the reference look
  const eyeWhite = (x) => {
    const g = new THREE.Group()
    const w = new THREE.Mesh(new THREE.SphereGeometry(0.07, 14, 12), toon(WHITE))
    w.scale.set(0.85, 1.05, 0.55)
    const iris = new THREE.Mesh(new THREE.SphereGeometry(0.045, 12, 10), toon(EYE_TEAL))
    iris.position.z = 0.035
    const pupil = new THREE.Mesh(new THREE.SphereGeometry(0.022, 10, 8), toon(EYE_DARK))
    pupil.position.z = 0.055
    const shine = new THREE.Mesh(new THREE.SphereGeometry(0.016, 8, 8), toon(WHITE))
    shine.position.set(0.015, 0.02, 0.07)
    g.add(w, iris, pupil, shine)
    g.position.set(x, 0.02, 0.29)
    return g
  }
  const eyeL = eyeWhite(-0.1)
  const eyeR = eyeWhite(0.1)

  const blushL = new THREE.Mesh(
    new THREE.SphereGeometry(0.055, 10, 8),
    toon(BLUSH, { transparent: true, opacity: 0.5 }),
  )
  const blushR = blushL.clone()
  blushL.position.set(-0.2, -0.06, 0.24)
  blushR.position.set(0.2, -0.06, 0.24)
  blushL.scale.set(1.1, 0.5, 0.45)
  blushR.scale.set(1.1, 0.5, 0.45)

  const nose = new THREE.Mesh(new THREE.SphereGeometry(0.015, 8, 6), toon(0xffc6d0))
  nose.position.set(0, -0.04, 0.33)
  const mouth = new THREE.Mesh(
    new THREE.TorusGeometry(0.035, 0.008, 6, 12, Math.PI),
    toon(BLUSH),
  )
  mouth.position.set(0, -0.12, 0.3)
  mouth.rotation.x = Math.PI

  headG.add(head, hairCap, bang, sideL, sideR, ahoge, eyeL, eyeR, blushL, blushR, nose, mouth)
  headG.position.y = 0.55

  // —— Torso / uniform ——
  const torsoG = new THREE.Group()
  const torso = new THREE.Mesh(new THREE.CapsuleGeometry(0.14, 0.16, 6, 14), toon(shirt))
  torso.castShadow = true
  const collar = new THREE.Mesh(new THREE.TorusGeometry(0.12, 0.03, 8, 16, Math.PI * 1.3), toon(WHITE))
  collar.position.set(0, 0.16, 0.02)
  collar.rotation.x = Math.PI / 2
  const tie = new THREE.Mesh(new THREE.ConeGeometry(0.04, 0.12, 8), toon(accent))
  tie.position.set(0, 0.02, 0.13)
  tie.rotation.x = Math.PI
  // Pleated skirt approximation: ring of thin boxes
  const skirt = new THREE.Group()
  const pleatMat = toon(accent)
  for (let i = 0; i < 10; i++) {
    const a = (i / 10) * Math.PI * 2
    const p = new THREE.Mesh(new THREE.BoxGeometry(0.06, 0.14, 0.02), pleatMat)
    p.position.set(Math.sin(a) * 0.15, -0.2, Math.cos(a) * 0.15)
    p.lookAt(0, -0.2, 0)
    skirt.add(p)
  }
  const skirtFill = new THREE.Mesh(
    new THREE.CylinderGeometry(0.17, 0.2, 0.12, 16, 1, true),
    toon(TEAL_DEEP, { side: THREE.DoubleSide }),
  )
  skirtFill.position.y = -0.2
  torsoG.add(torso, collar, tie, skirt, skirtFill)
  torsoG.position.y = 0.05

  // —— Limbs ——
  const armL = makeLimb(0.045, SKIN)
  const armR = makeLimb(0.045, SKIN)
  const sleeveL = makeLimb(0.055, shirt)
  const sleeveR = makeLimb(0.055, shirt)
  const legL = makeLimb(0.05, SKIN)
  const legR = makeLimb(0.05, SKIN)
  const sockL = makeLimb(0.055, WHITE)
  const sockR = makeLimb(0.055, WHITE)

  const handL = new THREE.Mesh(new THREE.SphereGeometry(0.055, 12, 10), toon(SKIN))
  const handR = handL.clone()

  function makeShoe() {
    const g = new THREE.Group()
    const sole = new THREE.Mesh(new THREE.BoxGeometry(0.16, 0.06, 0.28), toon(WHITE))
    sole.position.y = -0.02
    const upper = new THREE.Mesh(new THREE.CapsuleGeometry(0.07, 0.08, 4, 10), toon(accent))
    upper.rotation.z = Math.PI / 2
    upper.position.set(0, 0.04, 0.02)
    g.add(sole, upper)
    g.castShadow = true
    return g
  }
  const shoeL = makeShoe()
  const shoeR = makeShoe()

  // Stripe on socks (decal spheres)
  const stripe = (y) => {
    const s = new THREE.Mesh(new THREE.TorusGeometry(0.056, 0.01, 6, 16), toon(accent))
    s.rotation.x = Math.PI / 2
    s.position.y = y
    return s
  }

  root.add(
    headG, torsoG,
    armL, armR, sleeveL, sleeveR, handL, handR,
    legL, legR, sockL, sockR, shoeL, shoeR,
  )

  return {
    root, headG, torsoG,
    armL, armR, sleeveL, sleeveR, handL, handR,
    legL, legR, sockL, sockR, shoeL, shoeR,
    eyeL, eyeR, accent,
    _stripeL1: stripe(0.08), _stripeL2: stripe(0.02),
    _stripeR1: stripe(0.08), _stripeR2: stripe(0.02),
  }
}

/**
 * Native 3D pose targets (world-ish local to character facing +Z).
 * Returns anchor points for head / torso / limbs / shoes.
 */
export function poseAnchors(model) {
  const posture = model.posture || 'standing'
  const side = model.cameraSide || 'front'
  const gaze = model.gazePitch || 'looking_ahead'
  const arms = model.arms || 'arms_at_sides'

  // Default standing
  let head = v(0, 0.55, 0)
  let neck = v(0, 0.28, 0)
  let hip = v(0, -0.05, 0)
  let lShoulder = v(-0.16, 0.22, 0)
  let rShoulder = v(0.16, 0.22, 0)
  let lHand = v(-0.28, -0.02, 0.05)
  let rHand = v(0.28, -0.02, 0.05)
  let lKnee = v(-0.1, -0.35, 0.02)
  let rKnee = v(0.1, -0.35, 0.02)
  let lFoot = v(-0.1, -0.62, 0.04)
  let rFoot = v(0.1, -0.62, 0.04)
  let rootY = 0

  if (posture === 'sitting') {
    hip = v(0, -0.2, 0)
    lKnee = v(-0.18, -0.22, 0.2)
    rKnee = v(0.18, -0.22, 0.2)
    lFoot = v(-0.18, -0.45, 0.28)
    rFoot = v(0.18, -0.45, 0.28)
    head = v(0, 0.42, 0)
    neck = v(0, 0.18, 0)
    rootY = -0.05
  } else if (posture === 'squatting' || posture === 'crouching' || posture === 'kneeling') {
    // Deep crouch — the hero pose from the reference
    hip = v(0, -0.28, 0.02)
    lKnee = v(-0.12, -0.32, 0.28)
    rKnee = v(0.12, -0.32, 0.28)
    lFoot = v(-0.14, -0.55, 0.08)
    rFoot = v(0.14, -0.55, 0.08)
    head = v(0, 0.28, 0.06)
    neck = v(0, 0.05, 0.04)
    lShoulder = v(-0.16, 0.0, 0.04)
    rShoulder = v(0.16, 0.0, 0.04)
    lHand = v(-0.22, -0.28, 0.22)
    rHand = v(0.22, -0.28, 0.22)
    rootY = -0.02
  } else if (posture === 'lying') {
    head = v(-0.45, -0.15, 0)
    neck = v(-0.2, -0.15, 0)
    hip = v(0.25, -0.18, 0)
    lHand = v(-0.1, 0.05, 0.15)
    rHand = v(-0.1, -0.35, 0.15)
    lFoot = v(0.55, -0.05, 0.1)
    rFoot = v(0.55, -0.3, 0.1)
    lKnee = v(0.4, -0.08, 0.08)
    rKnee = v(0.4, -0.28, 0.08)
  } else if (posture === 'jumping') {
    rootY = 0.25
    lFoot = v(-0.12, -0.5, -0.05)
    rFoot = v(0.12, -0.5, 0.05)
  }

  if (arms === 'arms_up' || arms === 'arms_behind_head') {
    lHand = v(-0.28, 0.45, 0.05)
    rHand = v(0.28, 0.45, 0.05)
  } else if (arms === 'spread_arms' || arms === 'outstretched_arms') {
    lHand = v(-0.5, 0.15, 0)
    rHand = v(0.5, 0.15, 0)
  }

  // Gaze tips the head
  if (gaze === 'looking_up') {
    head = head.clone().add(v(0, 0.04, 0.03))
  } else if (gaze === 'looking_down') {
    head = head.clone().add(v(0, -0.02, -0.02))
  }

  // Side/behind are handled by the camera, not by spinning the character away
  // from a useful silhouette (yawing -90 + side-cam was reading as rear-three-quarter).
  let yaw = 0
  if (side === 'behind') yaw = Math.PI

  return {
    head, neck, hip, lShoulder, rShoulder, lHand, rHand,
    lKnee, rKnee, lFoot, rFoot, rootY, yaw, gaze, side,
  }
}

function applyPose(chibi, anchors) {
  const {
    head, neck, hip, lShoulder, rShoulder, lHand, rHand,
    lKnee, rKnee, lFoot, rFoot, rootY, yaw, gaze, side,
  } = anchors

  chibi.root.rotation.y = yaw
  chibi.root.position.y = rootY

  chibi.headG.position.copy(head)
  chibi.headG.rotation.set(0, 0, 0)
  if (gaze === 'looking_up') chibi.headG.rotation.x = -0.35
  else if (gaze === 'looking_down') chibi.headG.rotation.x = 0.3
  // Profile: hide far eye slightly
  if (side === 'side') {
    chibi.eyeL.visible = false
    chibi.eyeR.visible = true
  } else if (side === 'behind') {
    chibi.eyeL.visible = false
    chibi.eyeR.visible = false
  } else {
    chibi.eyeL.visible = true
    chibi.eyeR.visible = true
  }

  const mid = neck.clone().add(hip).multiplyScalar(0.5)
  chibi.torsoG.position.copy(mid)
  const torsoDir = v().subVectors(neck, hip)
  if (torsoDir.lengthSq() > 1e-6) {
    chibi.torsoG.quaternion.setFromUnitVectors(v(0, 1, 0), torsoDir.normalize())
  } else {
    chibi.torsoG.quaternion.identity()
  }

  setLimb(chibi.sleeveL, lShoulder, lHand.clone().lerp(lShoulder, 0.35), 0.055)
  setLimb(chibi.sleeveR, rShoulder, rHand.clone().lerp(rShoulder, 0.35), 0.055)
  setLimb(chibi.armL, lShoulder.clone().lerp(lHand, 0.25), lHand, 0.042)
  setLimb(chibi.armR, rShoulder.clone().lerp(rHand, 0.25), rHand, 0.042)
  chibi.handL.position.copy(lHand)
  chibi.handR.position.copy(rHand)

  setLimb(chibi.legL, hip.clone().add(v(-0.07, 0, 0)), lFoot, 0.05)
  setLimb(chibi.legR, hip.clone().add(v(0.07, 0, 0)), rFoot, 0.05)
  // Socks: lower half of legs
  setLimb(chibi.sockL, lKnee.clone().lerp(lFoot, 0.15), lFoot.clone().add(v(0, 0.06, 0)), 0.055)
  setLimb(chibi.sockR, rKnee.clone().lerp(rFoot, 0.15), rFoot.clone().add(v(0, 0.06, 0)), 0.055)

  chibi.shoeL.position.copy(lFoot)
  chibi.shoeR.position.copy(rFoot)
  chibi.shoeL.rotation.set(0, 0, 0)
  chibi.shoeR.rotation.set(0, 0, 0)
}

/** Dramatic camera from poseSketch camera fields. */
export function placeChibiCamera(camera, model, { duo = false } = {}) {
  const pitch = model.cameraPitch || 'eye'
  const side = model.cameraSide || 'front'
  const dist = model.cameraDistance || 'full'
  const posture = model.posture || 'standing'

  let x = 0
  let y = 0.35
  let z = 2.4
  let lookY = 0.25
  let fov = 40

  if (dist === 'close') { z = 1.25; fov = 30; lookY = 0.45 }
  else if (dist === 'upper') { z = 1.7; fov = 36; lookY = 0.35 }

  if (pitch === 'below') {
    // Worm's-eye — hero of crouch reference
    y = posture === 'squatting' || posture === 'crouching' ? -0.45 : -0.2
    z = dist === 'close' ? 1.35 : 1.85
    lookY = posture === 'squatting' || posture === 'crouching' ? 0.15 : 0.4
    fov = 38
  } else if (pitch === 'above') {
    y = 1.8
    z = 1.6
    lookY = 0.05
  }

  if (side === 'side') {
    // True profile: camera on +X, character still faces +Z
    x = pitch === 'below' ? 1.7 : 2.2
    z = pitch === 'below' ? 0.15 : 0.25
    if (pitch === 'below' && (posture === 'squatting' || posture === 'crouching')) {
      x = 1.55
      y = -0.5
      z = 0.2
      lookY = 0.08
      fov = 35
    }
  } else if (side === 'behind') {
    z = -Math.abs(z)
    x = 0.15
  }

  if (duo) {
    x *= 1.1
    z *= 1.15
    fov += 4
  }

  camera.position.set(x, y, z)
  camera.lookAt(0, lookY, 0)
  camera.fov = fov
  camera.updateProjectionMatrix()
}

/**
 * Mount live stage. Returns { update, dispose, renderer, scene }.
 */
export function createChibiStage(container, { duo = false } = {}) {
  const width = () => Math.max(160, container.clientWidth || 320)
  const height = () => Math.max(180, Math.min(380, (container.clientWidth || 320) * 0.85))

  const scene = new THREE.Scene()
  scene.background = new THREE.Color(0x2a2a2e) // charcoal like the reference

  const camera = new THREE.PerspectiveCamera(40, width() / height(), 0.05, 30)
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
  renderer.setSize(width(), height())
  renderer.shadowMap.enabled = true
  renderer.shadowMap.type = THREE.PCFSoftShadowMap
  container.appendChild(renderer.domElement)
  Object.assign(renderer.domElement.style, {
    display: 'block', width: '100%', borderRadius: '0.75rem',
  })

  // Soft studio lights
  scene.add(new THREE.HemisphereLight(0xffe8f0, 0x1a1a22, 0.85))
  const key = new THREE.DirectionalLight(0xffffff, 1.05)
  key.position.set(3, 5, 4)
  key.castShadow = true
  key.shadow.mapSize.set(1024, 1024)
  const fill = new THREE.DirectionalLight(0xa5f3fc, 0.35)
  fill.position.set(-3, 2, -1)
  const rim = new THREE.DirectionalLight(0xffc1d5, 0.4)
  rim.position.set(-2, 3, -4)
  scene.add(key, fill, rim, new THREE.AmbientLight(0xffffff, 0.2))

  const ground = new THREE.Mesh(
    new THREE.CircleGeometry(1.6, 64),
    toon(0x3a3a40),
  )
  ground.rotation.x = -Math.PI / 2
  ground.position.y = -0.62
  ground.receiveShadow = true
  scene.add(ground)

  const chibiA = makeChibi()
  const chibiB = duo ? makeChibi({ accent: 0xfbbf24, shirt: 0xfde68a }) : null
  const stage = new THREE.Group()
  stage.add(chibiA.root)
  if (chibiB) {
    stage.add(chibiB.root)
    chibiA.root.position.x = -0.42
    chibiB.root.position.x = 0.42
  }
  scene.add(stage)

  let latest = { tags: '', beat: '', beatB: '', frame: '', duo }
  let raf = 0
  const t0 = performance.now()

  function sync() {
    const model = buildPoseSketch(latest.tags, {
      beat: latest.beat,
      beat_b: latest.beatB,
      frame: latest.frame,
      duo: latest.duo,
    })
    if (model.empty) return model
    const anchors = poseAnchors(model)
    applyPose(chibiA, anchors)
    if (chibiB) {
      const modelB = buildPoseSketch(latest.tags, {
        beat: latest.beatB || latest.beat,
        frame: latest.frame,
        duo: true,
      })
      applyPose(chibiB, poseAnchors(modelB))
    }
    placeChibiCamera(camera, model, { duo: Boolean(chibiB) })
    return model
  }

  function tick(now) {
    const t = (now - t0) / 1000
    stage.position.y = Math.sin(t * 1.8) * 0.012
    renderer.render(scene, camera)
    raf = requestAnimationFrame(tick)
  }

  const ro = new ResizeObserver(() => {
    const w = width()
    const h = height()
    camera.aspect = w / h
    camera.updateProjectionMatrix()
    renderer.setSize(w, h)
  })
  ro.observe(container)

  sync()
  raf = requestAnimationFrame(tick)

  return {
    update(payload) {
      latest = { ...latest, ...payload }
      return sync()
    },
    dispose() {
      cancelAnimationFrame(raf)
      ro.disconnect()
      renderer.dispose()
      renderer.domElement.remove()
      scene.traverse((obj) => {
        if (obj.geometry) obj.geometry.dispose()
        if (obj.material) {
          const mats = Array.isArray(obj.material) ? obj.material : [obj.material]
          mats.forEach((m) => {
            if (m !== GRAD && m.gradientMap !== GRAD) m.dispose()
            else if (m.gradientMap === GRAD) {
              // keep shared gradient; dispose material shell only
              m.gradientMap = null
              m.dispose()
            } else m.dispose()
          })
        }
      })
    },
    renderer,
    scene,
    camera,
  }
}

// Keep old helper export for tests
export function jointToWorld(p, { xScale = 0.018, yScale = 0.018, z = 0 } = {}) {
  return new THREE.Vector3((p.x - 50) * xScale, (72 - p.y) * yScale, z)
}
