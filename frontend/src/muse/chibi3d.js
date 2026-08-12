/**
 * Procedural cute chibi rig driven by poseSketch joints + cameraView.
 * Three.js — no external VRM / no Comfy.
 */
import * as THREE from 'three'
import { buildPoseSketch, figureJoints, cameraView } from './poseSketch.js'

const SKIN = 0xffe4e8
const BLUSH = 0xfb7185
const ACCENT = 0x2dd4bf
const ACCENT_B = 0xfbbf24
const OUTFIT = 0x5eead4
const OUTFIT_B = 0xfcd34d
const EYE = 0x1f2937

function skinMat(color = SKIN) {
  return new THREE.MeshToonMaterial({ color })
}

function limbMesh(color) {
  // Unit-length capsule along Y; placeCapsule scales Y to the joint distance.
  const geo = new THREE.CapsuleGeometry(0.045, 1, 4, 8)
  const mesh = new THREE.Mesh(geo, skinMat(color))
  mesh.castShadow = true
  return mesh
}

/** Map 2D joint (100×140, y-down) → Three world. */
export function jointToWorld(p, { xScale = 0.018, yScale = 0.018, z = 0 } = {}) {
  return new THREE.Vector3(
    (p.x - 50) * xScale,
    (72 - p.y) * yScale,
    z,
  )
}

function placeCapsule(mesh, a, b) {
  const start = a.clone()
  const end = b.clone()
  const dir = new THREE.Vector3().subVectors(end, start)
  const len = dir.length()
  if (len < 1e-4) {
    mesh.visible = false
    return
  }
  mesh.visible = true
  // CapsuleGeometry(radius, length=1) total span ≈ length + 2r ≈ 1.09
  const span = 1 + 2 * 0.045
  mesh.scale.set(1, Math.max(0.08, len) / span, 1)
  mesh.position.copy(start).add(end).multiplyScalar(0.5)
  mesh.quaternion.setFromUnitVectors(
    new THREE.Vector3(0, 1, 0),
    dir.clone().normalize(),
  )
}

function makeChibi(accent = ACCENT, outfit = OUTFIT) {
  const root = new THREE.Group()
  const head = new THREE.Mesh(new THREE.SphereGeometry(0.28, 24, 20), skinMat(SKIN))
  head.castShadow = true
  const hair = new THREE.Mesh(
    new THREE.SphereGeometry(0.29, 20, 16, 0, Math.PI * 2, 0, Math.PI * 0.55),
    new THREE.MeshToonMaterial({ color: accent }),
  )
  hair.position.y = 0.06
  hair.rotation.x = Math.PI
  const ahoge = new THREE.Mesh(
    new THREE.CapsuleGeometry(0.02, 0.14, 3, 6),
    new THREE.MeshToonMaterial({ color: accent }),
  )
  ahoge.position.set(0.05, 0.38, 0.02)
  ahoge.rotation.z = -0.4

  const eyeL = new THREE.Mesh(new THREE.SphereGeometry(0.045, 12, 10), new THREE.MeshToonMaterial({ color: EYE }))
  const eyeR = eyeL.clone()
  eyeL.position.set(-0.09, 0.02, 0.24)
  eyeR.position.set(0.09, 0.02, 0.24)
  const shineL = new THREE.Mesh(new THREE.SphereGeometry(0.015, 8, 8), new THREE.MeshToonMaterial({ color: 0xffffff }))
  const shineR = shineL.clone()
  shineL.position.set(-0.08, 0.04, 0.275)
  shineR.position.set(0.1, 0.04, 0.275)

  const blushL = new THREE.Mesh(
    new THREE.SphereGeometry(0.05, 10, 8),
    new THREE.MeshToonMaterial({ color: BLUSH, transparent: true, opacity: 0.55 }),
  )
  const blushR = blushL.clone()
  blushL.position.set(-0.16, -0.04, 0.2)
  blushR.position.set(0.16, -0.04, 0.2)
  blushL.scale.set(1, 0.55, 0.5)
  blushR.scale.set(1, 0.55, 0.5)

  const mouth = new THREE.Mesh(
    new THREE.TorusGeometry(0.04, 0.01, 6, 10, Math.PI),
    new THREE.MeshToonMaterial({ color: BLUSH }),
  )
  mouth.position.set(0, -0.1, 0.25)
  mouth.rotation.x = Math.PI

  const headGroup = new THREE.Group()
  headGroup.add(head, hair, ahoge, eyeL, eyeR, shineL, shineR, blushL, blushR, mouth)

  const body = new THREE.Mesh(
    new THREE.CapsuleGeometry(0.13, 0.18, 6, 12),
    new THREE.MeshToonMaterial({ color: outfit }),
  )
  body.castShadow = true

  const skirt = new THREE.Mesh(
    new THREE.ConeGeometry(0.2, 0.16, 16, 1, true),
    new THREE.MeshToonMaterial({ color: accent, side: THREE.DoubleSide }),
  )
  skirt.position.y = -0.16
  skirt.rotation.x = Math.PI

  const limbs = {
    lArm: limbMesh(SKIN),
    rArm: limbMesh(SKIN),
    lLeg: limbMesh(SKIN),
    rLeg: limbMesh(SKIN),
  }

  const handL = new THREE.Mesh(new THREE.SphereGeometry(0.05, 10, 10), skinMat(SKIN))
  const handR = handL.clone()
  const footL = new THREE.Mesh(new THREE.SphereGeometry(0.06, 10, 10), new THREE.MeshToonMaterial({ color: accent }))
  const footR = footL.clone()
  footL.scale.set(1.2, 0.6, 1.4)
  footR.scale.set(1.2, 0.6, 1.4)

  root.add(headGroup, body, skirt, limbs.lArm, limbs.rArm, limbs.lLeg, limbs.rLeg, handL, handR, footL, footR)

  return {
    root, headGroup, body, skirt, limbs, handL, handR, footL, footR,
    eyeL, eyeR, mouth, accent,
  }
}

function applyJoints(chibi, joints, opts = {}) {
  const head = jointToWorld(joints.head, opts)
  const neck = jointToWorld(joints.neck, opts)
  const hip = jointToWorld(joints.hip, opts)
  const lElbow = jointToWorld(joints.lElbow, opts)
  const rElbow = jointToWorld(joints.rElbow, opts)
  const lHand = jointToWorld(joints.lHand, opts)
  const rHand = jointToWorld(joints.rHand, opts)
  const lKnee = jointToWorld(joints.lKnee, opts)
  const rKnee = jointToWorld(joints.rKnee, opts)
  const lFoot = jointToWorld(joints.lFoot, opts)
  const rFoot = jointToWorld(joints.rFoot, opts)

  chibi.headGroup.position.copy(head)
  // Face away when from_behind
  chibi.headGroup.rotation.set(0, joints.behind ? Math.PI : (joints.profile ? 0.9 : 0), 0)
  // Gaze pitch
  if (joints.behind) {
    chibi.headGroup.rotation.x = 0
  }

  const mid = neck.clone().add(hip).multiplyScalar(0.5)
  chibi.body.position.copy(mid)
  const torsoDir = new THREE.Vector3().subVectors(neck, hip)
  if (torsoDir.lengthSq() > 1e-6) {
    chibi.body.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), torsoDir.normalize())
  }
  chibi.skirt.position.copy(hip).add(new THREE.Vector3(0, -0.02, 0))

  placeCapsule(chibi.limbs.lArm, neck.clone().lerp(lElbow, 0.15), lHand)
  placeCapsule(chibi.limbs.rArm, neck.clone().lerp(rElbow, 0.15), rHand)
  placeCapsule(chibi.limbs.lLeg, hip.clone().lerp(lKnee, 0.1), lFoot)
  placeCapsule(chibi.limbs.rLeg, hip.clone().lerp(rKnee, 0.1), rFoot)

  chibi.handL.position.copy(lHand)
  chibi.handR.position.copy(rHand)
  chibi.footL.position.copy(lFoot)
  chibi.footR.position.copy(rFoot)

  // Hide face dots when behind — headGroup already rotated
  const faceVisible = !joints.behind
  chibi.eyeL.visible = faceVisible
  chibi.eyeR.visible = faceVisible
  chibi.mouth.visible = faceVisible
}

function placeCamera(camera, view, duo) {
  const pitch = view.pitch
  const side = view.side
  const dist = view.dist
  let distMul = dist === 'close' ? 1.15 : dist === 'upper' ? 1.7 : 2.45
  if (duo) distMul *= 1.15

  let x = 0
  let y = 0.35
  let z = distMul
  if (pitch === 'below') { y = -0.15; z = distMul * 0.95 }
  if (pitch === 'above') { y = 1.55; z = distMul * 0.7 }
  if (side === 'side') { x = distMul * 0.95; z = distMul * 0.35; y = pitch === 'above' ? 1.2 : 0.4 }
  if (side === 'behind') { z = -distMul; y = 0.45 }

  camera.position.set(x, y, z)
  const lookY = dist === 'close' ? 0.55 : 0.25
  camera.lookAt(0, lookY, 0)
  camera.fov = dist === 'close' ? 32 : dist === 'upper' ? 38 : 42
  camera.updateProjectionMatrix()
}

/**
 * Mount a live chibi stage into `container`. Returns { update, dispose }.
 */
export function createChibiStage(container, { duo = false } = {}) {
  const width = () => Math.max(120, container.clientWidth || 280)
  const height = () => Math.max(140, Math.min(320, (container.clientWidth || 280) * 0.72))

  const scene = new THREE.Scene()
  scene.background = new THREE.Color(0x100e14)

  const camera = new THREE.PerspectiveCamera(42, width() / height(), 0.1, 20)
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
  renderer.setSize(width(), height())
  renderer.shadowMap.enabled = true
  container.appendChild(renderer.domElement)
  renderer.domElement.style.display = 'block'
  renderer.domElement.style.width = '100%'
  renderer.domElement.style.borderRadius = '0.75rem'

  const hemi = new THREE.HemisphereLight(0xffe4f0, 0x1a2030, 1.1)
  const key = new THREE.DirectionalLight(0xffffff, 0.85)
  key.position.set(2, 4, 3)
  key.castShadow = true
  scene.add(hemi, key, new THREE.AmbientLight(0xffffff, 0.25))

  const ground = new THREE.Mesh(
    new THREE.CircleGeometry(1.4, 48),
    new THREE.MeshToonMaterial({ color: 0x2a2030 }),
  )
  ground.rotation.x = -Math.PI / 2
  ground.position.y = -0.55
  ground.receiveShadow = true
  scene.add(ground)

  // Soft ring
  const ring = new THREE.Mesh(
    new THREE.RingGeometry(0.55, 0.7, 48),
    new THREE.MeshBasicMaterial({ color: 0xfb7185, transparent: true, opacity: 0.22, side: THREE.DoubleSide }),
  )
  ring.rotation.x = -Math.PI / 2
  ring.position.y = -0.54
  scene.add(ring)

  const chibiA = makeChibi(ACCENT, OUTFIT)
  const chibiB = duo ? makeChibi(ACCENT_B, OUTFIT_B) : null
  const stage = new THREE.Group()
  stage.add(chibiA.root)
  if (chibiB) stage.add(chibiB.root)
  scene.add(stage)

  let raf = 0
  let t0 = performance.now()
  let latest = {
    tags: '', beat: '', beatB: '', frame: '', duo,
  }

  function syncPose() {
    const model = buildPoseSketch(latest.tags, {
      beat: latest.beat,
      beat_b: latest.beatB,
      frame: latest.frame,
      duo: latest.duo,
    })
    if (model.empty) return model

    const view = cameraView(model)
    const jointsA = figureJoints(model, { partner: false, interact: model.interact })
    applyJoints(chibiA, jointsA)

    if (chibiB) {
      const modelB = buildPoseSketch(latest.tags, {
        beat: latest.beatB || latest.beat,
        frame: latest.frame,
        duo: true,
      })
      const jointsB = figureJoints(modelB, { partner: true, interact: model.interact })
      applyJoints(chibiB, jointsB)
      chibiA.root.position.x = -0.38
      chibiB.root.position.x = 0.38
    } else {
      chibiA.root.position.x = 0
    }

    // Gaze pitch on head
    if (!jointsA.behind) {
      if (model.gazePitch === 'looking_up') chibiA.headGroup.rotation.x = -0.25
      else if (model.gazePitch === 'looking_down') chibiA.headGroup.rotation.x = 0.28
      else chibiA.headGroup.rotation.x = 0
    }

    placeCamera(camera, view, Boolean(chibiB))
    return model
  }

  function tick(now) {
    const t = (now - t0) / 1000
    // Cute idle bob
    stage.position.y = Math.sin(t * 2.2) * 0.02
    ring.rotation.z = t * 0.25
    renderer.render(scene, camera)
    raf = requestAnimationFrame(tick)
  }

  function onResize() {
    const w = width()
    const h = height()
    camera.aspect = w / h
    camera.updateProjectionMatrix()
    renderer.setSize(w, h)
  }

  const ro = new ResizeObserver(onResize)
  ro.observe(container)

  syncPose()
  raf = requestAnimationFrame(tick)

  return {
    update(payload) {
      latest = { ...latest, ...payload }
      return syncPose()
    },
    dispose() {
      cancelAnimationFrame(raf)
      ro.disconnect()
      renderer.dispose()
      if (renderer.domElement.parentNode) {
        renderer.domElement.parentNode.removeChild(renderer.domElement)
      }
      scene.traverse((obj) => {
        if (obj.geometry) obj.geometry.dispose()
        if (obj.material) {
          if (Array.isArray(obj.material)) obj.material.forEach((m) => m.dispose())
          else obj.material.dispose()
        }
      })
    },
  }
}
