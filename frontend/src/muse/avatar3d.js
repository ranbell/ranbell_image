/**
 * Normal-proportion VRM avatar preview driven by poseSketch tags.
 * Replaces the procedural chibi for conversation pose/camera checks.
 */
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { VRMLoaderPlugin, VRMUtils, VRMHumanBoneName } from '@pixiv/three-vrm'
import { buildPoseSketch } from './poseSketch.js'
import { cameraEnumsFromShotPosition } from './poseCoach.js'
import { solveLimbIk } from './vrmIk.js'

export const DEFAULT_VRM_URL = '/models/pose_avatar.vrm'

const Bone = VRMHumanBoneName

function bone(vrm, name) {
  return vrm?.humanoid?.getNormalizedBoneNode(name) || null
}

function setEuler(node, x = 0, y = 0, z = 0) {
  if (!node) return
  node.rotation.set(x, y, z)
}

/**
 * Apply a resting / tagged pose onto a VRM humanoid (normalized bones).
 */
export function applyVrmPose(vrm, model) {
  if (!vrm?.humanoid) return
  vrm.humanoid.resetNormalizedPose()

  const posture = model.posture || 'standing'
  const arms = model.arms || 'arms_at_sides'
  const gaze = model.gazePitch || 'looking_ahead'
  const side = model.cameraSide || 'front'
  const crouch = posture === 'squatting' || posture === 'crouching' || posture === 'kneeling'

  const hips = bone(vrm, Bone.Hips)
  const spine = bone(vrm, Bone.Spine)
  const chest = bone(vrm, Bone.Chest)
  const neck = bone(vrm, Bone.Neck)
  const head = bone(vrm, Bone.Head)
  const lUpperLeg = bone(vrm, Bone.LeftUpperLeg)
  const rUpperLeg = bone(vrm, Bone.RightUpperLeg)
  const lLowerLeg = bone(vrm, Bone.LeftLowerLeg)
  const rLowerLeg = bone(vrm, Bone.RightLowerLeg)
  const lFoot = bone(vrm, Bone.LeftFoot)
  const rFoot = bone(vrm, Bone.RightFoot)
  const lUpperArm = bone(vrm, Bone.LeftUpperArm)
  const rUpperArm = bone(vrm, Bone.RightUpperArm)
  const lLowerArm = bone(vrm, Bone.LeftLowerArm)
  const rLowerArm = bone(vrm, Bone.RightLowerArm)

  // Keep feet on the floor — crouch is bone-only, don't sink the rig.
  // (VRM instance has no userData; we stash groundY on the scene graph.)
  const groundY = Number.isFinite(vrm.scene?.userData?.groundY) ? vrm.scene.userData.groundY : 0
  vrm.scene.position.y = posture === 'jumping' ? groundY + 0.2 : groundY

  // Default: arms down (avoid T-pose)
  setEuler(lUpperArm, 0.15, 0.05, 1.2)
  setEuler(rUpperArm, 0.15, -0.05, -1.2)
  setEuler(lLowerArm, 0.2, 0, 0.1)
  setEuler(rLowerArm, 0.2, 0, -0.1)

  if (posture === 'standing' || posture === 'walking' || posture === 'running') {
    if (posture === 'walking' || posture === 'running') {
      const swing = posture === 'running' ? 0.55 : 0.3
      setEuler(lUpperLeg, -swing, 0, 0)
      setEuler(rUpperLeg, swing, 0, 0)
      setEuler(lLowerLeg, swing * 0.8, 0, 0)
      setEuler(rLowerLeg, swing * 0.4, 0, 0)
    }
  } else if (posture === 'sitting') {
    setEuler(spine, 0.1, 0, 0)
    setEuler(lUpperLeg, -Math.PI / 2, 0.08, 0)
    setEuler(rUpperLeg, -Math.PI / 2, -0.08, 0)
    setEuler(lLowerLeg, Math.PI / 2, 0, 0)
    setEuler(rLowerLeg, Math.PI / 2, 0, 0)
    setEuler(lFoot, 0.2, 0, 0)
    setEuler(rFoot, 0.2, 0, 0)
  } else if (crouch) {
    setEuler(spine, 0.22, 0, 0)
    setEuler(chest, 0.1, 0, 0)
    setEuler(lUpperLeg, -1.45, 0.15, 0.1)
    setEuler(rUpperLeg, -1.45, -0.15, -0.1)
    setEuler(lLowerLeg, 2.15, 0, 0)
    setEuler(rLowerLeg, 2.15, 0, 0)
    setEuler(lFoot, 0.4, 0, 0)
    setEuler(rFoot, 0.4, 0, 0)
    // Hands toward knees / ground
    setEuler(lUpperArm, 0.55, 0.25, 0.85)
    setEuler(rUpperArm, 0.55, -0.25, -0.85)
    setEuler(lLowerArm, 0.35, 0.1, 0.15)
    setEuler(rLowerArm, 0.35, -0.1, -0.15)
  } else if (posture === 'lying') {
    // Soft supine bone pose (scene may also be rotated by duo layouts)
    setEuler(spine, 0.05, 0, 0)
    setEuler(lUpperLeg, -0.15, 0.08, 0)
    setEuler(rUpperLeg, -0.1, -0.08, 0)
    setEuler(lLowerLeg, 0.25, 0, 0)
    setEuler(rLowerLeg, 0.2, 0, 0)
    setEuler(lUpperArm, 0.2, 0.3, 0.9)
    setEuler(rUpperArm, 0.2, -0.3, -0.9)
  } else if (posture === 'jumping') {
    setEuler(lUpperLeg, -0.4, 0, 0)
    setEuler(rUpperLeg, 0.35, 0, 0)
    setEuler(lLowerLeg, 0.6, 0, 0)
  }

  if (arms === 'arms_up' || arms === 'arms_behind_head') {
    setEuler(lUpperArm, -2.4, 0.2, 0.4)
    setEuler(rUpperArm, -2.4, -0.2, -0.4)
    if (arms === 'arms_behind_head') {
      setEuler(lLowerArm, -1.2, 0, 0)
      setEuler(rLowerArm, -1.2, 0, 0)
    }
  } else if (arms === 'crossed_arms') {
    setEuler(lUpperArm, -0.8, 0.6, 0.5)
    setEuler(rUpperArm, -0.8, -0.6, -0.5)
    setEuler(lLowerArm, -1.3, 0.4, 0)
    setEuler(rLowerArm, -1.3, -0.4, 0)
  } else if (arms === 'spread_arms' || arms === 'outstretched_arms') {
    setEuler(lUpperArm, 0, 0, 1.55)
    setEuler(rUpperArm, 0, 0, -1.55)
  }

  // Gaze
  const headX = gaze === 'looking_up' ? -0.35 : gaze === 'looking_down' ? 0.35 : 0
  const neckX = gaze === 'looking_up' ? -0.2 : gaze === 'looking_down' ? 0.12 : 0
  const headY = side === 'side' ? -0.4 : 0
  setEuler(neck, neckX, 0, 0)
  setEuler(head, headX, headY, 0)

  vrm.humanoid.update()
  vrm.update(0)
}

/**
 * Where the *shot* camera sits on set (what we're framing for Comfy).
 * Returned as world position + lookAt + fov — not the viewport camera.
 */
export function shotCameraWorld(model, { duo = false } = {}) {
  const pitch = model.cameraPitch || 'eye'
  const side = model.cameraSide || 'front'
  const dist = model.cameraDistance || 'full'
  const posture = model.posture || 'standing'
  const crouch = posture === 'squatting' || posture === 'crouching' || posture === 'kneeling'

  const subjectY = crouch ? 0.55 : posture === 'sitting' ? 0.7 : 1.05
  let x = 0
  let y = subjectY
  let z = 2.4
  let fov = 35

  if (dist === 'close') { z = 1.0; y = crouch ? 0.7 : 1.4; fov = 28 }
  else if (dist === 'upper') { z = 1.6; y = crouch ? 0.65 : 1.25; fov = 32 }
  else { z = 2.6; fov = 34 } // full body shot distance

  if (pitch === 'below') {
    y = crouch ? 0.12 : 0.28
    z = dist === 'close' ? 1.35 : dist === 'upper' ? 1.9 : 2.5
    fov = 36
  } else if (pitch === 'above') {
    y = crouch ? 1.8 : 2.4
    z = dist === 'close' ? 1.2 : 2.0
  }

  if (side === 'side') {
    x = pitch === 'below' ? 2.2 : 2.6
    z = pitch === 'below' ? 0.45 : 0.55
    if (pitch === 'below' && crouch) {
      x = 2.0
      y = 0.1
      z = 0.5
    }
  } else if (side === 'behind') {
    z = -Math.abs(z)
    x = 0.15
  }

  if (duo) {
    x *= 1.1
    z *= 1.15
    fov += 3
  }

  return {
    position: new THREE.Vector3(x, y, z),
    lookAt: new THREE.Vector3(0, subjectY * 0.85, 0),
    fov,
    subjectY,
  }
}

/** @deprecated use shotCameraWorld + placeSetOverviewCamera */
export function placeAvatarCamera(camera, model, opts = {}) {
  placeSetOverviewCamera(camera, model, shotCameraWorld(model, opts), opts)
}

/**
 * Viewport camera: always a set overview that keeps full body + shot camera in frame.
 */
export function placeSetOverviewCamera(viewCam, model, shot, { duo = false } = {}) {
  const crouch = ['squatting', 'crouching', 'kneeling'].includes(model.posture)
  const subject = new THREE.Vector3(0, crouch ? 0.55 : 0.9, 0)
  const shotPos = shot.position.clone()

  // Midpoint bias toward the subject so she stays large, but shot cam stays visible.
  const focus = subject.clone().lerp(shotPos, 0.28)

  // Overview opposite-ish the shot camera, elevated — "撮影現場" diagram angle.
  const away = subject.clone().sub(shotPos)
  away.y = 0
  if (away.lengthSq() < 1e-4) away.set(0, 0, 1)
  away.normalize()
  const side = new THREE.Vector3().crossVectors(away, new THREE.Vector3(0, 1, 0))
  if (side.lengthSq() < 1e-6) side.set(1, 0, 0)
  else side.normalize()

  const span = subject.distanceTo(shotPos)
  const back = 3.4 + span * 0.35 + (duo ? 0.7 : 0)
  const elev = 1.9 + Math.min(1.0, span * 0.2)
  const overview = focus.clone()
    .addScaledVector(away, back)
    .add(new THREE.Vector3(0, elev, 0))
    .addScaledVector(side, 1.35)

  viewCam.position.copy(overview)
  viewCam.lookAt(focus)
  // Fit both subject and shot camera with a little padding
  const toSub = subject.distanceTo(overview)
  const toShot = shotPos.distanceTo(overview)
  const half = Math.max(toSub, toShot, 2.2) * 0.55
  const distFocus = focus.distanceTo(overview)
  const fitFov = THREE.MathUtils.radToDeg(2 * Math.atan(half / Math.max(0.5, distFocus))) + 8
  viewCam.fov = THREE.MathUtils.clamp(fitFov, 38, 55)
  viewCam.near = 0.05
  viewCam.far = 60
  viewCam.updateProjectionMatrix()
}

/** Physical camera body + frustum for the set. */
export function createShotCameraGizmo() {
  const root = new THREE.Group()
  root.name = 'shotCameraGizmo'

  const bodyMat = new THREE.MeshStandardMaterial({
    color: 0x1e293b, metalness: 0.4, roughness: 0.35,
  })
  const accentMat = new THREE.MeshStandardMaterial({
    color: 0x38bdf8, metalness: 0.2, roughness: 0.4, emissive: 0x0ea5e9, emissiveIntensity: 0.25,
  })

  const body = new THREE.Mesh(new THREE.BoxGeometry(0.22, 0.16, 0.28), bodyMat)
  const lens = new THREE.Mesh(new THREE.CylinderGeometry(0.07, 0.09, 0.18, 20), accentMat)
  lens.rotation.x = Math.PI / 2
  // After lookAt, object -Z faces the subject — put lens / frustum on -Z.
  lens.position.z = -0.2
  const grip = new THREE.Mesh(new THREE.BoxGeometry(0.08, 0.2, 0.1), bodyMat)
  grip.position.set(0.16, -0.05, 0)
  const hot = new THREE.Mesh(new THREE.SphereGeometry(0.03, 10, 8), accentMat)
  hot.position.set(0, 0.1, 0.05)

  // Frustum: tip toward camera body, open toward subject (local -Z)
  const frustum = new THREE.Mesh(
    new THREE.ConeGeometry(0.55, 1.2, 4, 1, true),
    new THREE.MeshBasicMaterial({
      color: 0x38bdf8, transparent: true, opacity: 0.14,
      side: THREE.DoubleSide, depthWrite: false,
    }),
  )
  frustum.rotation.x = Math.PI / 2
  frustum.position.z = -0.85
  const frustumEdge = new THREE.LineSegments(
    new THREE.EdgesGeometry(new THREE.ConeGeometry(0.55, 1.2, 4, 1, true)),
    new THREE.LineBasicMaterial({ color: 0x7dd3fc, transparent: true, opacity: 0.7 }),
  )
  frustumEdge.rotation.x = Math.PI / 2
  frustumEdge.position.z = -0.85

  const peg = new THREE.Mesh(
    new THREE.CylinderGeometry(0.015, 0.015, 0.35, 8),
    accentMat,
  )
  peg.position.y = 0.28

  root.add(body, lens, grip, hot, frustum, frustumEdge, peg)

  return {
    root,
    /** Aim gizmo: Three lookAt points local -Z at subject. */
    place(shot) {
      root.position.copy(shot.position)
      root.lookAt(shot.lookAt)
      const dist = shot.position.distanceTo(shot.lookAt)
      const fl = Math.min(2.2, Math.max(0.8, dist * 0.55))
      frustum.scale.set(1, fl / 1.2, 1)
      frustum.position.z = -(fl * 0.5 + 0.25)
      frustumEdge.scale.copy(frustum.scale)
      frustumEdge.position.copy(frustum.position)
    },
  }
}

/** Simple studio floor marks + soft backdrop — reads as a set, not a close-up. */
function createStudioSet() {
  const group = new THREE.Group()
  const floor = new THREE.Mesh(
    new THREE.CircleGeometry(4.5, 72),
    new THREE.MeshStandardMaterial({ color: 0x333338, roughness: 0.9, metalness: 0.05 }),
  )
  floor.rotation.x = -Math.PI / 2
  floor.receiveShadow = true

  const grid = new THREE.GridHelper(8, 16, 0x52525b, 0x3f3f46)
  grid.position.y = 0.002

  // Subject mark
  const mark = new THREE.Mesh(
    new THREE.RingGeometry(0.28, 0.34, 48),
    new THREE.MeshBasicMaterial({ color: 0xf472b6, transparent: true, opacity: 0.55, side: THREE.DoubleSide }),
  )
  mark.rotation.x = -Math.PI / 2
  mark.position.y = 0.01

  // Soft backdrop
  const wall = new THREE.Mesh(
    new THREE.PlaneGeometry(6, 3.2),
    new THREE.MeshStandardMaterial({ color: 0x27272a, roughness: 1, metalness: 0 }),
  )
  wall.position.set(0, 1.5, -2.4)

  // Light stands (decorative)
  const standMat = new THREE.MeshStandardMaterial({ color: 0x71717a, metalness: 0.5, roughness: 0.4 })
  function stand(x, z) {
    const g = new THREE.Group()
    const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.03, 2.2, 8), standMat)
    pole.position.y = 1.1
    const head = new THREE.Mesh(
      new THREE.BoxGeometry(0.35, 0.25, 0.12),
      new THREE.MeshStandardMaterial({ color: 0xfef3c7, emissive: 0xfbbf24, emissiveIntensity: 0.35 }),
    )
    head.position.set(0, 2.15, 0.05)
    g.add(pole, head)
    g.position.set(x, 0, z)
    return g
  }

  group.add(floor, grid, mark, wall, stand(-2.4, 1.2), stand(2.4, 1.0))
  return group
}

async function loadVrm(url) {
  const loader = new GLTFLoader()
  loader.register((parser) => new VRMLoaderPlugin(parser))
  const gltf = await loader.loadAsync(url)
  const vrm = gltf.userData.vrm
  if (!vrm) throw new Error('VRM missing in glTF userData')
  try { VRMUtils.removeUnnecessaryVertices(gltf.scene) } catch { /* optional */ }
  try { VRMUtils.combineSkeletons(gltf.scene) } catch { /* optional */ }
  try { VRMUtils.combineMorphs(vrm) } catch { /* optional */ }
  try { VRMUtils.rotateVRM0(vrm) } catch { /* optional */ }
  vrm.scene.traverse((obj) => {
    obj.frustumCulled = false
    if (obj.isMesh) {
      obj.castShadow = false
      obj.receiveShadow = false
    }
  })
  // Plant feet on the studio floor (store offset on scene — VRM has no userData)
  const box = new THREE.Box3().setFromObject(vrm.scene)
  if (Number.isFinite(box.min.y)) vrm.scene.position.y -= box.min.y
  if (!vrm.scene.userData) vrm.scene.userData = {}
  vrm.scene.userData.groundY = vrm.scene.position.y
  vrm.scene.userData.baseRotY = vrm.scene.rotation.y
  return vrm
}

/**
 * Mount a VRM stage into `container`.
 * Viewport = set overview (full body + shot camera). Shot camera = tag-driven.
 */
export async function createAvatarStage(container, {
  duo = false,
  modelUrl = DEFAULT_VRM_URL,
} = {}) {
  const width = () => Math.max(160, container.clientWidth || 320)
  const height = () => Math.max(220, Math.min(460, (container.clientWidth || 320) * 1.05))

  const scene = new THREE.Scene()
  scene.background = new THREE.Color(0x1c1c1f)

  const viewCam = new THREE.PerspectiveCamera(42, width() / height(), 0.05, 60)
  const renderer = new THREE.WebGLRenderer({
    antialias: false,
    alpha: false,
    powerPreference: 'low-power',
    failIfMajorPerformanceCaveat: false,
  })
  renderer.setPixelRatio(1)
  renderer.setSize(width(), height())
  // Soft shadows + high DPR thrash software WebGL; keep preview light.
  renderer.shadowMap.enabled = false
  container.appendChild(renderer.domElement)
  Object.assign(renderer.domElement.style, {
    display: 'block', width: '100%', borderRadius: '0.75rem',
    touchAction: 'none', userSelect: 'none',
  })
  renderer.domElement.addEventListener('contextmenu', (e) => e.preventDefault())

  scene.add(new THREE.HemisphereLight(0xfff0f5, 0x1a1a22, 0.95))
  const key = new THREE.DirectionalLight(0xffffff, 1.0)
  key.position.set(3, 5, 4)
  const fill = new THREE.DirectionalLight(0xa5f3fc, 0.35)
  fill.position.set(-3, 2.2, -1.2)
  const rim = new THREE.DirectionalLight(0xffc1d5, 0.3)
  rim.position.set(-1.2, 2.5, -3.2)
  scene.add(key, fill, rim, new THREE.AmbientLight(0xffffff, 0.28))

  scene.add(createStudioSet())
  const shotGizmo = createShotCameraGizmo()
  scene.add(shotGizmo.root)

  const status = document.createElement('div')
  status.textContent = 'モデル読込中…'
  Object.assign(status.style, {
    position: 'absolute', left: '12px', bottom: '10px',
    fontSize: '10px', color: 'rgba(186,230,253,0.9)', pointerEvents: 'none',
  })
  container.style.position = container.style.position || 'relative'
  container.appendChild(status)

  let vrmA
  let vrmB = null
  try {
    vrmA = await loadVrm(modelUrl)
    scene.add(vrmA.scene)
    if (duo) {
      vrmB = await loadVrm(modelUrl)
      vrmB.scene.position.x = 0.55
      vrmA.scene.position.x = -0.55
      scene.add(vrmB.scene)
    }
  } catch (err) {
    status.textContent = `読込失敗: ${err?.message || err}`
    status.style.color = 'rgba(252,165,165,0.95)'
    renderer.dispose()
    throw err
  }

  status.remove()

  // Set badge
  const badge = document.createElement('div')
  badge.textContent = '撮影現場プレビュー · 全身＋ショットカメラ'
  Object.assign(badge.style, {
    position: 'absolute', left: '10px', top: '8px',
    fontSize: '10px', color: 'rgba(253,224,71,0.95)',
    background: 'rgba(0,0,0,0.35)', padding: '3px 8px', borderRadius: '999px',
    pointerEvents: 'none',
  })
  container.appendChild(badge)

  let latest = { tags: '', beat: '', beatB: '', frame: '', duo }
  let raf = 0
  const clock = new THREE.Clock()

  /** @type {null | ReturnType<typeof buildPoseSketch>} */
  let coachModel = null
  let coachMode = false
  let coachSubject = 'a' // 'a' | 'b'
  let customLimbs = false
  let shotOverride = null // THREE.Vector3 | null
  /** Half-gap between duo subjects (world X). */
  let duoSpacing = 0.55
  /**
   * When true, skip auto duo layout — user dragged / snapped characters into place.
   * Closer/Farther or resetPlacement clears this.
   */
  let freePlacement = false
  /** @type {'overview' | 'shot'} */
  let viewMode = 'overview'
  let dragKind = null // 'shot' | 'ik' | 'body' | 'yaw' | null
  let dragIk = null // effector entry
  /** @type {any | null} */
  let dragBodyVrm = null
  let lastPointer = { x: 0, y: 0 }
  const dragPlane = new THREE.Plane()
  const dragHit = new THREE.Vector3()
  const viewDir = new THREE.Vector3()
  const groundPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0)
  const _tmpV = new THREE.Vector3()
  const _tmpV2 = new THREE.Vector3()
  const _tmpE = new THREE.Euler()
  let overviewSnapshot = null // { position, target } to restore when leaving shot

  const orbit = new OrbitControls(viewCam, renderer.domElement)
  orbit.enableDamping = true
  orbit.dampingFactor = 0.12
  orbit.enablePan = true
  orbit.screenSpacePanning = true
  orbit.rotateSpeed = 0.85
  orbit.panSpeed = 0.7
  orbit.zoomSpeed = 0.9
  orbit.maxPolarAngle = Math.PI * 0.495
  orbit.minDistance = 1.2
  orbit.maxDistance = 12
  orbit.enabled = false
  // Left = rotate, middle/right = pan (IK uses left on handles)
  orbit.mouseButtons = {
    LEFT: THREE.MOUSE.ROTATE,
    MIDDLE: THREE.MOUSE.DOLLY,
    RIGHT: THREE.MOUSE.PAN,
  }
  orbit.touches = {
    ONE: THREE.TOUCH.ROTATE,
    TWO: THREE.TOUCH.DOLLY_PAN,
  }

  const handleRoot = new THREE.Group()
  handleRoot.visible = false
  scene.add(handleRoot)

  /** Floor rings under each avatar for body move / yaw (duo coach). */
  const placeRoot = new THREE.Group()
  placeRoot.visible = false
  scene.add(placeRoot)
  /** @type {{ group: THREE.Group, ring: THREE.Mesh, yaw: THREE.Mesh, yawHit: THREE.Mesh, hit: THREE.Mesh, vrm: any, who: 'a'|'b' }[]} */
  const placeHandles = []

  const IK_SPECS = [
    { id: 'leftHand', bones: [Bone.LeftUpperArm, Bone.LeftLowerArm], tip: Bone.LeftHand, color: 0x38bdf8, size: 0.05, hit: 0.12, side: 'left', kind: 'arm' },
    { id: 'rightHand', bones: [Bone.RightUpperArm, Bone.RightLowerArm], tip: Bone.RightHand, color: 0x38bdf8, size: 0.05, hit: 0.12, side: 'right', kind: 'arm' },
    { id: 'leftFoot', bones: [Bone.LeftUpperLeg, Bone.LeftLowerLeg], tip: Bone.LeftFoot, color: 0xf472b6, size: 0.055, hit: 0.13, side: 'left', kind: 'leg' },
    { id: 'rightFoot', bones: [Bone.RightUpperLeg, Bone.RightLowerLeg], tip: Bone.RightFoot, color: 0xf472b6, size: 0.055, hit: 0.13, side: 'right', kind: 'leg' },
  ]

  /** @type {{ mesh: THREE.Mesh, hit: THREE.Mesh, id: string, vrm: any, bones: any[], tip: any, side: string, kind: string, baseColor: number }[]} */
  const ikEffectors = []
  let hoverIk = null

  function coachVrm() {
    return coachSubject === 'b' && vrmB ? vrmB : vrmA
  }

  function getSceneYaw(vrm) {
    if (!vrm?.scene) return 0
    _tmpE.setFromQuaternion(vrm.scene.quaternion, 'YXZ')
    return _tmpE.y
  }

  function setSceneUpright(vrm) {
    if (!vrm?.scene) return
    const yaw = getSceneYaw(vrm)
    const gy = Number.isFinite(vrm.scene.userData?.groundY) ? vrm.scene.userData.groundY : 0
    const base = Number.isFinite(vrm.scene.userData?.baseRotY) ? vrm.scene.userData.baseRotY : 0
    vrm.scene.rotation.order = 'YXZ'
    vrm.scene.rotation.set(0, Number.isFinite(yaw) ? yaw : base, 0)
    vrm.scene.position.y = gy
    vrm.scene.userData.sceneLie = false
  }

  function setSceneLie(vrm, yaw = null) {
    if (!vrm?.scene) return
    const y = yaw == null ? getSceneYaw(vrm) : yaw
    const gy = Number.isFinite(vrm.scene.userData?.groundY) ? vrm.scene.userData.groundY : 0
    vrm.scene.rotation.order = 'YXZ'
    vrm.scene.rotation.set(Math.PI / 2, y, 0)
    vrm.scene.position.y = gy + 0.1
    vrm.scene.userData.sceneLie = true
  }

  function worldBonePos(vrm, name, out = _tmpV) {
    const n = bone(vrm, name)
    if (!n) return null
    n.updateWorldMatrix(true, false)
    return n.getWorldPosition(out)
  }

  function applyDuoSpacing() {
    if (vrmB) {
      const g = THREE.MathUtils.clamp(duoSpacing, 0.22, 1.35)
      duoSpacing = g
      vrmA.scene.position.x = -g
      vrmB.scene.position.x = g
      vrmA.scene.position.z = 0
      vrmB.scene.position.z = 0
      setSceneUpright(vrmA)
      setSceneUpright(vrmB)
      // Restore upright base yaw
      const baseA = Number.isFinite(vrmA.scene.userData?.baseRotY) ? vrmA.scene.userData.baseRotY : 0
      const baseB = Number.isFinite(vrmB.scene.userData?.baseRotY) ? vrmB.scene.userData.baseRotY : 0
      vrmA.scene.rotation.set(0, baseA, 0)
      vrmB.scene.rotation.set(0, baseB, 0)
    } else {
      vrmA.scene.position.x = 0
      vrmA.scene.position.z = 0
      setSceneUpright(vrmA)
    }
  }

  function resetSceneOrient(vrm) {
    if (!vrm?.scene) return
    const baseY = Number.isFinite(vrm.scene.userData?.baseRotY) ? vrm.scene.userData.baseRotY : 0
    vrm.scene.rotation.order = 'YXZ'
    vrm.scene.rotation.set(0, baseY, 0)
    const gy = Number.isFinite(vrm.scene.userData?.groundY) ? vrm.scene.userData.groundY : 0
    vrm.scene.position.y = gy
    vrm.scene.position.z = 0
    vrm.scene.userData.sceneLie = false
  }

  /**
   * Bone-aware assist: sit A, lie B, place B's head on A's lap.
   * Leaves freePlacement on so the user can nudge afterward.
   */
  function doSnapHeadToLap() {
    if (!vrmB) return false
    freePlacement = true

    const giver = vrmA
    const recv = vrmB

    if (coachModel) {
      coachModel = {
        ...coachModel,
        interact: 'lap_pillow',
        posture: 'sitting',
        gazePitch: coachModel.gazePitch || 'looking_down',
        empty: false,
      }
    }

    applyVrmPose(giver, {
      posture: 'sitting',
      arms: 'arms_at_sides',
      gazePitch: 'looking_down',
      cameraPitch: 'eye',
      cameraSide: 'side',
      cameraDistance: 'upper',
      empty: false,
      active: ['sitting'],
    })
    applyVrmPose(recv, {
      posture: 'lying',
      arms: 'arms_at_sides',
      gazePitch: 'looking_up',
      cameraPitch: 'eye',
      cameraSide: 'side',
      cameraDistance: 'upper',
      empty: false,
      active: ['lying'],
    })

    setSceneUpright(giver)
    const baseA = Number.isFinite(giver.scene.userData?.baseRotY) ? giver.scene.userData.baseRotY : 0
    giver.scene.rotation.set(0, baseA, 0)
    giver.scene.updateMatrixWorld(true)

    const lap = new THREE.Vector3()
    const lThigh = worldBonePos(giver, Bone.LeftUpperLeg, _tmpV)
    const rThigh = worldBonePos(giver, Bone.RightUpperLeg, _tmpV2)
    if (lThigh && rThigh) lap.copy(lThigh).add(rThigh).multiplyScalar(0.5)
    else {
      const hips = worldBonePos(giver, Bone.Hips, _tmpV)
      if (hips) lap.copy(hips)
      else lap.set(giver.scene.position.x, 0.45, giver.scene.position.z)
    }
    lap.y += 0.05

    const hipsG = worldBonePos(giver, Bone.Hips, new THREE.Vector3()) || giver.scene.position.clone()
    // Prefer yaw that points receiver's head toward giver and feet outward
    let bestYaw = getSceneYaw(recv)
    let bestScore = Infinity
    for (let i = 0; i < 24; i++) {
      const yaw = (i / 24) * Math.PI * 2
      setSceneLie(recv, yaw)
      recv.scene.updateMatrixWorld(true)
      const head = worldBonePos(recv, Bone.Head, _tmpV)
      if (!head) continue
      const offset = head.clone().sub(recv.scene.position)
      recv.scene.position.x = lap.x - offset.x
      recv.scene.position.z = lap.z - offset.z
      recv.scene.updateMatrixWorld(true)
      const head2 = worldBonePos(recv, Bone.Head, _tmpV)
      const hipsR = worldBonePos(recv, Bone.Hips, _tmpV2)
      if (!head2 || !hipsR) continue
      const headErr = head2.distanceTo(lap)
      // Prefer hips farther from giver hips than head (head on lap, body out)
      const headToG = head2.distanceTo(hipsG)
      const hipsToG = hipsR.distanceTo(hipsG)
      const score = headErr * 2.2 + Math.max(0, headToG - hipsToG) * 1.5
      if (score < bestScore) {
        bestScore = score
        bestYaw = yaw
      }
    }

    setSceneLie(recv, bestYaw)
    recv.scene.updateMatrixWorld(true)
    {
      const head = worldBonePos(recv, Bone.Head, _tmpV)
      if (head) {
        const offset = head.clone().sub(recv.scene.position)
        recv.scene.position.x = lap.x - offset.x
        recv.scene.position.z = lap.z - offset.z
      }
    }
    giver.scene.updateMatrixWorld(true)
    recv.scene.updateMatrixWorld(true)
    customLimbs = false
    if (coachModel) coachModel.customLimbs = false
    snapIkToTips()
    syncPlaceHandles()
    updateBadge()
    return true
  }

  function applyDuoLayout(interact) {
    if (freePlacement) {
      syncPlaceHandles()
      return
    }
    if (!vrmB) {
      vrmA.scene.position.x = 0
      resetSceneOrient(vrmA)
      syncPlaceHandles()
      return
    }
    // Lap pillow is no longer a hard-coded layout — use snap assist or free drag.
    // Side-by-side remains the only auto layout.
    void interact
    applyDuoSpacing()
    syncPlaceHandles()
  }

  function rebuildPlaceHandles() {
    while (placeHandles.length) {
      const h = placeHandles.pop()
      placeRoot.remove(h.group)
      h.ring.geometry?.dispose?.()
      h.ring.material?.dispose?.()
      h.yaw.geometry?.dispose?.()
      h.yaw.material?.dispose?.()
      h.yawHit.geometry?.dispose?.()
      h.yawHit.material?.dispose?.()
      h.hit.geometry?.dispose?.()
      h.hit.material?.dispose?.()
    }
    if (!vrmB) return
    const specs = [
      { vrm: vrmA, who: 'a', color: 0xf59e0b },
      { vrm: vrmB, who: 'b', color: 0x38bdf8 },
    ]
    for (const s of specs) {
      const group = new THREE.Group()
      const ring = new THREE.Mesh(
        new THREE.TorusGeometry(0.3, 0.016, 8, 48),
        new THREE.MeshBasicMaterial({
          color: s.color, transparent: true, opacity: 0.8, depthWrite: false,
        }),
      )
      ring.rotation.x = -Math.PI / 2
      const hit = new THREE.Mesh(
        new THREE.CylinderGeometry(0.38, 0.38, 0.1, 20),
        new THREE.MeshBasicMaterial({
          transparent: true, opacity: 0, depthWrite: false, depthTest: false,
        }),
      )
      hit.position.y = 0.02
      const yaw = new THREE.Mesh(
        new THREE.SphereGeometry(0.055, 12, 10),
        new THREE.MeshBasicMaterial({
          color: 0xfde68a, transparent: true, opacity: 0.95, depthWrite: false,
        }),
      )
      yaw.position.set(0.34, 0.04, 0)
      const yawHit = new THREE.Mesh(
        new THREE.SphereGeometry(0.11, 10, 8),
        new THREE.MeshBasicMaterial({
          transparent: true, opacity: 0, depthWrite: false, depthTest: false,
        }),
      )
      yawHit.position.copy(yaw.position)
      group.add(hit, ring, yawHit, yaw)
      placeRoot.add(group)
      placeHandles.push({ group, ring, yaw, yawHit, hit, vrm: s.vrm, who: s.who })
    }
    syncPlaceHandles()
  }

  function syncPlaceHandles() {
    if (!placeHandles.length) return
    for (const h of placeHandles) {
      const p = h.vrm.scene.position
      h.group.position.set(p.x, 0.02, p.z)
      const on = coachSubject === h.who
      h.ring.material.opacity = on ? 0.95 : 0.45
      h.ring.scale.setScalar(on ? 1.08 : 1)
      h.yaw.visible = on || freePlacement
    }
  }

  function rebuildHandles() {
    while (ikEffectors.length) {
      const h = ikEffectors.pop()
      handleRoot.remove(h.mesh)
      handleRoot.remove(h.hit)
      h.mesh.geometry?.dispose?.()
      h.mesh.material?.dispose?.()
      h.hit.geometry?.dispose?.()
      h.hit.material?.dispose?.()
    }
    const v = coachVrm()
    for (const spec of IK_SPECS) {
      const bones = spec.bones.map((n) => bone(v, n)).filter(Boolean)
      const tip = bone(v, spec.tip)
      if (bones.length < 2 || !tip) continue
      const mat = new THREE.MeshBasicMaterial({
        color: spec.color,
        transparent: true,
        opacity: 0.92,
        depthWrite: false,
      })
      const mesh = new THREE.Mesh(new THREE.SphereGeometry(spec.size, 16, 14), mat)
      // Invisible larger pick target
      const hit = new THREE.Mesh(
        new THREE.SphereGeometry(spec.hit, 10, 8),
        new THREE.MeshBasicMaterial({
          transparent: true, opacity: 0, depthWrite: false, depthTest: false,
        }),
      )
      mesh.userData.ikId = spec.id
      hit.userData.ikId = spec.id
      tip.getWorldPosition(mesh.position)
      hit.position.copy(mesh.position)
      handleRoot.add(hit)
      handleRoot.add(mesh)
      ikEffectors.push({
        mesh, hit, id: spec.id, vrm: v, bones, tip,
        side: spec.side, kind: spec.kind, baseColor: spec.color,
      })
    }
  }

  function setIkHighlight(entry, on) {
    if (!entry) return
    const mat = entry.mesh.material
    mat.color.setHex(on ? 0xfde68a : entry.baseColor)
    mat.opacity = on ? 1 : 0.92
    entry.mesh.scale.setScalar(on ? 1.25 : 1)
  }

  function snapIkToTips() {
    for (const h of ikEffectors) {
      h.tip.updateWorldMatrix(true, false)
      h.tip.getWorldPosition(h.mesh.position)
      h.hit.position.copy(h.mesh.position)
    }
  }

  function syncHandles() {
    for (const h of ikEffectors) {
      if (dragKind === 'ik' && dragIk === h) {
        h.hit.position.copy(h.mesh.position)
        continue
      }
      h.tip.updateWorldMatrix(true, false)
      h.tip.getWorldPosition(h.mesh.position)
      h.hit.position.copy(h.mesh.position)
    }
  }

  function runIk(effector) {
    if (!effector) return
    const [upper, lower] = effector.bones
    solveLimbIk(upper, lower, effector.tip, effector.mesh.position, {
      side: effector.side,
      kind: effector.kind,
    })
    effector.vrm.humanoid?.update?.()
    effector.vrm.update?.(0)
    customLimbs = true
    if (coachModel) coachModel.customLimbs = true
  }

  function setCanvasCursor(kind) {
    const el = renderer.domElement
    if (kind === 'ik') el.style.cursor = 'grabbing'
    else if (kind === 'hover') el.style.cursor = 'grab'
    else if (kind === 'shot') el.style.cursor = 'move'
    else if (kind === 'body') el.style.cursor = 'move'
    else if (kind === 'yaw') el.style.cursor = 'ew-resize'
    else el.style.cursor = viewMode === 'shot' || coachMode ? 'grab' : 'default'
  }

  const raycaster = new THREE.Raycaster()
  const pointerNdc = new THREE.Vector2()

  function setPointerNdc(ev) {
    const rect = renderer.domElement.getBoundingClientRect()
    pointerNdc.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1
    pointerNdc.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1
  }

  function activeModel() {
    if (coachMode && coachModel) return coachModel
    return buildPoseSketch(latest.tags, {
      beat: latest.beat,
      beat_b: latest.beatB,
      frame: latest.frame,
      duo: latest.duo,
    })
  }

  function subjectLookAt(model) {
    const crouch = ['squatting', 'crouching', 'kneeling'].includes(model?.posture)
    const lookY = crouch ? 0.55 : (vrmB ? 1.0 : 0.95)
    return new THREE.Vector3(0, lookY, 0)
  }

  function currentShot(model = activeModel()) {
    if (shotOverride) {
      const lookAt = subjectLookAt(model)
      const shot = {
        position: shotOverride.clone(),
        lookAt,
        fov: model?.cameraDistance === 'close' ? 28 : model?.cameraDistance === 'upper' ? 32 : 34,
        subjectY: lookAt.y,
      }
      shotGizmo.place(shot)
      return shot
    }
    const shot = shotCameraWorld(model, { duo: Boolean(vrmB) })
    shotGizmo.place(shot)
    return shot
  }

  function placeShotFromModel(model) {
    return currentShot(model)
  }

  function updateBadge() {
    if (viewMode === 'shot') {
      badge.textContent = 'ショット視点 · この絵になるよ'
      badge.style.color = 'rgba(125,211,252,0.98)'
    } else if (coachMode) {
      badge.textContent = '全景 · ポーズコーチング（IK＋カメラ移動）'
      badge.style.color = 'rgba(253,224,71,0.95)'
    } else {
      badge.textContent = '撮影現場プレビュー · 全身＋ショットカメラ'
      badge.style.color = 'rgba(253,224,71,0.95)'
    }
  }

  function applyViewMode() {
    const model = coachMode && coachModel ? coachModel : activeModel()
    const shot = currentShot(model)
    if (viewMode === 'shot') {
      shotGizmo.root.visible = false
      // Keep IK visible in shot mode so you can pose while checking the frame
      handleRoot.visible = coachMode
      viewCam.position.copy(shot.position)
      viewCam.lookAt(shot.lookAt)
      viewCam.fov = shot.fov || 34
      viewCam.near = 0.05
      viewCam.far = 40
      viewCam.updateProjectionMatrix()
      orbit.target.copy(shot.lookAt)
      orbit.minDistance = 0.6
      orbit.maxDistance = 6
      orbit.enabled = true
      orbit.update()
    } else {
      shotGizmo.root.visible = true
      handleRoot.visible = coachMode
      if (overviewSnapshot) {
        viewCam.position.copy(overviewSnapshot.position)
        orbit.target.copy(overviewSnapshot.target)
        viewCam.fov = 42
        viewCam.updateProjectionMatrix()
        orbit.minDistance = 1.2
        orbit.maxDistance = 12
        orbit.enabled = coachMode
        orbit.update()
      } else {
        placeSetOverviewCamera(viewCam, model, shot, { duo: Boolean(vrmB) })
        orbit.target.copy(shot.lookAt)
        orbit.minDistance = 1.2
        orbit.maxDistance = 12
        orbit.enabled = coachMode
        orbit.update()
      }
    }
    updateBadge()
  }

  function syncShotFromViewCam(model) {
    if (viewMode !== 'shot') return
    if (!shotOverride) shotOverride = new THREE.Vector3()
    shotOverride.copy(viewCam.position)
    if (model && coachMode && coachModel) {
      const crouch = ['squatting', 'crouching', 'kneeling'].includes(model.posture)
      Object.assign(coachModel, cameraEnumsFromShotPosition(shotOverride, { crouch }))
      if (coachModel.cameraPitch === 'below') coachModel.gazePitch = 'looking_up'
      else if (coachModel.cameraPitch === 'above') coachModel.gazePitch = 'looking_down'
    }
  }

  function sync() {
    const model = activeModel()
    if (model.empty && !coachMode) return model

    // In coach mode: don't clobber IK-edited limbs with preset apply.
    if (coachMode && coachModel) {
      const interact = coachModel.interact || ''
      if (!customLimbs) {
        if (interact === 'lap_pillow' || interact === 'head_on_lap' || interact === 'head_in_lap') {
          applyVrmPose(vrmA, {
            ...coachModel,
            posture: 'sitting',
            arms: coachModel.arms || 'arms_at_sides',
            gazePitch: coachModel.gazePitch || 'looking_down',
          })
          if (vrmB) {
            applyVrmPose(vrmB, {
              ...coachModel,
              posture: 'lying',
              arms: 'arms_at_sides',
              gazePitch: 'looking_up',
            })
          }
        } else if (coachSubject === 'b' && vrmB) {
          applyVrmPose(vrmA, buildPoseSketch(latest.tags, {
            beat: latest.beat, frame: latest.frame, duo: true,
          }))
          applyVrmPose(vrmB, coachModel)
        } else {
          applyVrmPose(vrmA, coachModel)
          if (vrmB) {
            applyVrmPose(vrmB, buildPoseSketch(latest.tags, {
              beat: latest.beatB || latest.beat, frame: latest.frame, duo: true,
            }))
          }
        }
        // Preserve scene lie flags when free-placing
        if (freePlacement) {
          if (vrmA.scene.userData?.sceneLie) setSceneLie(vrmA)
          else setSceneUpright(vrmA)
          if (vrmB) {
            if (vrmB.scene.userData?.sceneLie) setSceneLie(vrmB)
            else setSceneUpright(vrmB)
          }
        }
        applyDuoLayout(interact)
        snapIkToTips()
      } else {
        applyDuoLayout(interact)
      }
    } else {
      applyVrmPose(vrmA, model)
      if (vrmB) {
        const interact = model.interact || ''
        if (interact === 'lap_pillow' || interact === 'head_on_lap' || interact === 'head_in_lap') {
          applyVrmPose(vrmA, { ...model, posture: 'sitting', gazePitch: model.gazePitch || 'looking_down' })
          applyVrmPose(vrmB, { ...model, posture: 'lying', arms: 'arms_at_sides', gazePitch: 'looking_up' })
        } else {
          applyVrmPose(vrmB, buildPoseSketch(latest.tags, {
            beat: latest.beatB || latest.beat,
            frame: latest.frame,
            duo: true,
          }))
        }
        applyDuoLayout(interact || model.interact)
      } else {
        vrmA.scene.position.x = 0
      }
    }

    const shot = placeShotFromModel(coachMode && coachModel ? coachModel : model)
    if (viewMode === 'shot') {
      applyViewMode()
    } else if (!coachMode) {
      placeSetOverviewCamera(viewCam, model, shot, { duo: Boolean(vrmB) })
      orbit.target.copy(shot.lookAt)
      orbit.update()
      shotGizmo.root.visible = true
      updateBadge()
    } else {
      // Coach overview: keep director orbit; only refresh gizmo / badge
      shotGizmo.root.visible = true
      handleRoot.visible = true
      placeRoot.visible = Boolean(vrmB)
      syncPlaceHandles()
      updateBadge()
    }
    return coachMode && coachModel ? coachModel : model
  }

  function onPointerDown(ev) {
    if (!coachMode && viewMode !== 'shot') return
    setPointerNdc(ev)
    raycaster.setFromCamera(pointerNdc, viewCam)

    if (coachMode && handleRoot.visible && ikEffectors.length) {
      const hits = raycaster.intersectObjects(ikEffectors.map((h) => h.hit), false)
      if (hits.length) {
        dragKind = 'ik'
        dragIk = ikEffectors.find((h) => h.hit === hits[0].object || h.mesh === hits[0].object) || null
        if (dragIk) {
          setIkHighlight(dragIk, true)
          orbit.enabled = false
          viewCam.getWorldDirection(viewDir)
          dragPlane.setFromNormalAndCoplanarPoint(viewDir.clone().negate(), dragIk.mesh.position)
          lastPointer = { x: ev.clientX, y: ev.clientY }
          setCanvasCursor('ik')
          renderer.domElement.setPointerCapture?.(ev.pointerId)
          ev.preventDefault()
          return
        }
      }
    }

    // Duo body place / yaw rings (before shot gizmo so floor rings win nearby)
    if (coachMode && placeRoot.visible && placeHandles.length && viewMode === 'overview') {
      const yawHits = raycaster.intersectObjects(placeHandles.map((h) => h.yawHit), false)
      if (yawHits.length) {
        const ph = placeHandles.find((h) => h.yawHit === yawHits[0].object)
        if (ph) {
          dragKind = 'yaw'
          dragBodyVrm = ph.vrm
          coachSubject = ph.who
          freePlacement = true
          orbit.enabled = false
          lastPointer = { x: ev.clientX, y: ev.clientY }
          setCanvasCursor('yaw')
          rebuildHandles()
          syncPlaceHandles()
          renderer.domElement.setPointerCapture?.(ev.pointerId)
          ev.preventDefault()
          return
        }
      }
      const bodyHits = raycaster.intersectObjects(placeHandles.map((h) => h.hit), false)
      if (bodyHits.length) {
        const ph = placeHandles.find((h) => h.hit === bodyHits[0].object)
        if (ph) {
          dragKind = ev.altKey || ev.shiftKey ? 'yaw' : 'body'
          dragBodyVrm = ph.vrm
          coachSubject = ph.who
          freePlacement = true
          orbit.enabled = false
          lastPointer = { x: ev.clientX, y: ev.clientY }
          setCanvasCursor(dragKind === 'yaw' ? 'yaw' : 'body')
          rebuildHandles()
          syncPlaceHandles()
          renderer.domElement.setPointerCapture?.(ev.pointerId)
          ev.preventDefault()
          return
        }
      }
    }

    const gizmoHits = (viewMode === 'overview' && coachMode)
      ? raycaster.intersectObject(shotGizmo.root, true)
      : []
    if (gizmoHits.length) {
      dragKind = 'shot'
      orbit.enabled = false
      lastPointer = { x: ev.clientX, y: ev.clientY }
      setCanvasCursor('shot')
      renderer.domElement.setPointerCapture?.(ev.pointerId)
      ev.preventDefault()
    }
  }

  function onPointerMove(ev) {
    // Hover highlight when idle
    if ((coachMode || viewMode === 'shot') && !dragKind && coachMode && handleRoot.visible) {
      setPointerNdc(ev)
      raycaster.setFromCamera(pointerNdc, viewCam)
      const hits = raycaster.intersectObjects(ikEffectors.map((h) => h.hit), false)
      const next = hits.length
        ? ikEffectors.find((h) => h.hit === hits[0].object)
        : null
      if (next !== hoverIk) {
        if (hoverIk) setIkHighlight(hoverIk, false)
        hoverIk = next
        if (hoverIk) setIkHighlight(hoverIk, true)
        setCanvasCursor(hoverIk ? 'hover' : '')
      } else if (!hoverIk && placeRoot.visible) {
        const yawHover = raycaster.intersectObjects(placeHandles.map((h) => h.yawHit), false)
        const bodyHover = !yawHover.length
          ? raycaster.intersectObjects(placeHandles.map((h) => h.hit), false)
          : []
        if (yawHover.length) setCanvasCursor('yaw')
        else if (bodyHover.length) setCanvasCursor('body')
        else setCanvasCursor('')
      }
    }

    if ((!coachMode && viewMode !== 'shot') || !dragKind) return
    const dx = ev.clientX - lastPointer.x
    const dy = ev.clientY - lastPointer.y
    lastPointer = { x: ev.clientX, y: ev.clientY }

    if (dragKind === 'ik' && dragIk) {
      setPointerNdc(ev)
      raycaster.setFromCamera(pointerNdc, viewCam)
      // Keep plane facing camera for stable screen-space drag
      viewCam.getWorldDirection(viewDir)
      dragPlane.setFromNormalAndCoplanarPoint(viewDir.clone().negate(), dragIk.mesh.position)
      if (raycaster.ray.intersectPlane(dragPlane, dragHit)) {
        // Soft clamp: don't yank farther than ~arm/leg reach from shoulder/hip
        const root = dragIk.bones[0]
        root.updateWorldMatrix(true, false)
        const rootPos = new THREE.Vector3().setFromMatrixPosition(root.matrixWorld)
        const maxR = dragIk.kind === 'arm' ? 0.75 : 0.95
        const offset = dragHit.clone().sub(rootPos)
        if (offset.length() > maxR) offset.setLength(maxR)
        dragIk.mesh.position.copy(rootPos).add(offset)
        dragIk.hit.position.copy(dragIk.mesh.position)
        runIk(dragIk)
      }
      return
    }

    if ((dragKind === 'body' || dragKind === 'yaw') && dragBodyVrm) {
      setPointerNdc(ev)
      raycaster.setFromCamera(pointerNdc, viewCam)
      if (dragKind === 'body' && raycaster.ray.intersectPlane(groundPlane, dragHit)) {
        dragBodyVrm.scene.position.x = THREE.MathUtils.clamp(dragHit.x, -2.2, 2.2)
        dragBodyVrm.scene.position.z = THREE.MathUtils.clamp(dragHit.z, -2.2, 2.2)
        syncPlaceHandles()
        syncHandles()
      } else if (dragKind === 'yaw') {
        const yaw = getSceneYaw(dragBodyVrm) - dx * 0.015
        if (dragBodyVrm.scene.userData?.sceneLie) setSceneLie(dragBodyVrm, yaw)
        else {
          dragBodyVrm.scene.rotation.order = 'YXZ'
          dragBodyVrm.scene.rotation.y = yaw
        }
        syncPlaceHandles()
        syncHandles()
      }
      return
    }

    if (dragKind === 'shot' && viewMode === 'overview') {
      const model = coachMode && coachModel ? coachModel : activeModel()
      const base = shotOverride || shotCameraWorld(model, { duo: Boolean(vrmB) }).position
      if (!shotOverride) shotOverride = base.clone()
      const sph = new THREE.Spherical().setFromVector3(shotOverride)
      sph.theta -= dx * 0.012
      sph.phi = THREE.MathUtils.clamp(sph.phi + dy * 0.012, 0.12, Math.PI - 0.18)
      sph.radius = THREE.MathUtils.clamp(sph.radius, 0.75, 5.5)
      shotOverride.setFromSpherical(sph)
      if (coachModel) {
        const crouch = ['squatting', 'crouching', 'kneeling'].includes(coachModel.posture)
        Object.assign(coachModel, cameraEnumsFromShotPosition(shotOverride, { crouch }))
        if (coachModel.cameraPitch === 'below') coachModel.gazePitch = 'looking_up'
        else if (coachModel.cameraPitch === 'above') coachModel.gazePitch = 'looking_down'
      }
      placeShotFromModel(model)
    }
  }

  function onPointerUp(ev) {
    if (!coachMode && viewMode !== 'shot') return
    if (dragIk) setIkHighlight(dragIk, Boolean(hoverIk === dragIk))
    dragKind = null
    dragIk = null
    dragBodyVrm = null
    orbit.enabled = coachMode || viewMode === 'shot'
    setCanvasCursor(hoverIk ? 'hover' : '')
    try { renderer.domElement.releasePointerCapture?.(ev.pointerId) } catch { /* */ }
  }

  function onWheelShot(ev) {
    if (!ev.shiftKey) return
    if (!shotOverride && !(coachMode && coachModel)) return
    ev.preventDefault()
    const model = coachMode && coachModel ? coachModel : activeModel()
    const base = shotOverride || shotCameraWorld(model, { duo: Boolean(vrmB) }).position
    if (!shotOverride) shotOverride = base.clone()
    const sph = new THREE.Spherical().setFromVector3(shotOverride)
    sph.radius = THREE.MathUtils.clamp(sph.radius + ev.deltaY * 0.002, 0.8, 5.5)
    shotOverride.setFromSpherical(sph)
    if (coachModel) {
      const crouch = ['squatting', 'crouching', 'kneeling'].includes(coachModel.posture)
      Object.assign(coachModel, cameraEnumsFromShotPosition(shotOverride, { crouch }))
    }
    if (viewMode === 'shot') applyViewMode()
    else placeShotFromModel(model)
  }

  renderer.domElement.addEventListener('pointerdown', onPointerDown)
  window.addEventListener('pointermove', onPointerMove)
  window.addEventListener('pointerup', onPointerUp)
  renderer.domElement.addEventListener('wheel', onWheelShot, { passive: false })

  function tick() {
    const dt = clock.getDelta()
    vrmA.update(dt)
    if (vrmB) vrmB.update(dt)
    if (coachMode || viewMode === 'shot') {
      orbit.update()
      if (viewMode === 'shot' && !dragKind) {
        const model = coachMode && coachModel ? coachModel : activeModel()
        syncShotFromViewCam(model)
      }
      if (coachMode) syncHandles()
      if (coachMode && placeRoot.visible) syncPlaceHandles()
    }
    renderer.render(scene, viewCam)
    raf = requestAnimationFrame(tick)
  }

  const ro = new ResizeObserver(() => {
    const w = width()
    const h = height()
    viewCam.aspect = w / h
    viewCam.updateProjectionMatrix()
    renderer.setSize(w, h)
  })
  ro.observe(container)

  sync()
  raf = requestAnimationFrame(tick)

  return {
    update(payload) {
      latest = { ...latest, ...payload }
      if (coachMode) return coachModel
      return sync()
    },
    setCoachMode(on) {
      coachMode = Boolean(on)
      customLimbs = false
      shotOverride = null
      freePlacement = false
      if (coachMode) {
        coachModel = {
          ...buildPoseSketch(latest.tags, {
            beat: latest.beat,
            beat_b: latest.beatB,
            frame: latest.frame,
            duo: latest.duo,
          }),
        }
        if (coachModel.empty) {
          Object.assign(coachModel, {
            posture: 'standing',
            arms: 'arms_at_sides',
            gazePitch: 'looking_ahead',
            gazeTarget: '',
            cameraPitch: 'eye',
            cameraSide: 'front',
            cameraDistance: 'full',
            empty: false,
            active: ['standing'],
          })
        }
        badge.textContent = '全景 · ポーズコーチング（配置・IK・カメラ）'
        orbit.enabled = true
        handleRoot.visible = true
        placeRoot.visible = Boolean(vrmB)
        rebuildPlaceHandles()
        rebuildHandles()
        const seedShot = shotCameraWorld(coachModel, { duo: Boolean(vrmB) })
        shotOverride = seedShot.position.clone()
        placeSetOverviewCamera(viewCam, coachModel, seedShot, { duo: Boolean(vrmB) })
        orbit.target.copy(seedShot.lookAt)
        orbit.update()
        overviewSnapshot = {
          position: viewCam.position.clone(),
          target: orbit.target.clone(),
        }
        sync()
        snapIkToTips()
        applyViewMode()
      } else {
        coachModel = null
        viewMode = 'overview'
        badge.textContent = '撮影現場プレビュー · 全身＋ショットカメラ'
        orbit.enabled = false
        handleRoot.visible = false
        placeRoot.visible = false
        shotGizmo.root.visible = true
        sync()
        applyViewMode()
      }
      return coachModel
    },
    setViewMode(mode) {
      const next = mode === 'shot' ? 'shot' : 'overview'
      if (next === viewMode) return viewMode
      if (viewMode === 'overview' && next === 'shot') {
        overviewSnapshot = {
          position: viewCam.position.clone(),
          target: orbit.target.clone(),
        }
        const model = coachMode && coachModel ? coachModel : activeModel()
        const shot = currentShot(model)
        if (!shotOverride) shotOverride = shot.position.clone()
      }
      viewMode = next
      applyViewMode()
      return viewMode
    },
    getViewMode() {
      return viewMode
    },
    setCoachSubject(who) {
      coachSubject = who === 'b' ? 'b' : 'a'
      if (coachMode) {
        rebuildHandles()
        snapIkToTips()
        syncPlaceHandles()
        sync()
      }
    },
    patchCoachModel(partial) {
      if (!coachMode) return null
      coachModel = { ...coachModel, ...partial, empty: false, customLimbs: customLimbs || Boolean(partial.customLimbs) }
      if (partial.posture || partial.arms) {
        if (!partial.customLimbs) {
          customLimbs = false
          coachModel.customLimbs = false
        }
        const target = coachVrm()
        applyVrmPose(target, coachModel)
        // Lying = flatten on floor (scene pitch); other postures stand upright — keep XZ.
        if (partial.posture === 'lying') setSceneLie(target)
        else if (partial.posture) setSceneUpright(target)
        if (partial.posture) freePlacement = true
        snapIkToTips()
        syncPlaceHandles()
      }
      if (partial.cameraPitch || partial.cameraSide || partial.cameraDistance) {
        shotOverride = shotCameraWorld(coachModel, { duo: Boolean(vrmB) }).position.clone()
      }
      sync()
      return coachModel
    },
    getCoachSnapshot() {
      const model = coachMode && coachModel ? { ...coachModel, customLimbs, freePlacement } : activeModel()
      return {
        model,
        duo: Boolean(vrmB),
        subject: coachSubject,
        customLimbs,
        freePlacement,
        duoSpacing,
        shot: shotOverride ? shotOverride.clone() : null,
      }
    },
    setDuoSpacing(gap) {
      freePlacement = false
      duoSpacing = THREE.MathUtils.clamp(Number(gap) || 0.55, 0.22, 1.35)
      applyDuoLayout(coachModel?.interact || activeModel()?.interact)
      return duoSpacing
    },
    nudgeDuoSpacing(delta = 0.08) {
      return this.setDuoSpacing(duoSpacing + (Number(delta) || 0))
    },
    getDuoSpacing() {
      return duoSpacing
    },
    getFreePlacement() {
      return freePlacement
    },
    /** Clear freehand offsets and restore side-by-side. */
    resetPlacement() {
      freePlacement = false
      if (vrmA) setSceneUpright(vrmA)
      if (vrmB) setSceneUpright(vrmB)
      if (coachModel) coachModel = { ...coachModel, interact: '' }
      applyDuoSpacing()
      snapIkToTips()
      syncPlaceHandles()
      return true
    },
    /**
     * Bone-aware lap-pillow assist, then leave freePlacement on for nudging.
     * Prefer this over a hard-coded duo layout.
     */
    snapHeadToLap() {
      if (!coachMode) return false
      const ok = doSnapHeadToLap()
      if (ok && coachModel) {
        coachModel = {
          ...coachModel,
          interact: 'lap_pillow',
          posture: 'sitting',
          empty: false,
        }
      }
      updateBadge()
      return ok
    },
    /** One-tap duo interaction presets (e.g. lap_pillow → snap assist). */
    applyInteraction(name) {
      if (!coachMode || !coachModel) return null
      const n = String(name || '')
      coachModel = { ...coachModel, interact: n, empty: false }
      customLimbs = false
      coachModel.customLimbs = false
      if (n === 'lap_pillow' || n === 'head_on_lap' || n === 'head_in_lap') {
        duoSpacing = Math.min(duoSpacing, 0.42)
        coachModel.posture = 'sitting'
        coachModel.gazePitch = 'looking_down'
        coachModel.cameraSide = coachModel.cameraSide || 'side'
        coachModel.cameraDistance = coachModel.cameraDistance === 'close' ? 'upper' : (coachModel.cameraDistance || 'full')
        doSnapHeadToLap()
        return coachModel
      }
      freePlacement = false
      sync()
      return coachModel
    },
    dispose() {
      cancelAnimationFrame(raf)
      ro.disconnect()
      orbit.dispose()
      renderer.domElement.removeEventListener('pointerdown', onPointerDown)
      window.removeEventListener('pointermove', onPointerMove)
      window.removeEventListener('pointerup', onPointerUp)
      renderer.domElement.removeEventListener('wheel', onWheelShot)
      renderer.dispose()
      renderer.domElement.remove()
      badge.remove()
      scene.remove(vrmA.scene)
      if (vrmB) scene.remove(vrmB.scene)
      try { VRMUtils.deepDispose?.(vrmA.scene) } catch { /* */ }
      if (vrmB) {
        try { VRMUtils.deepDispose?.(vrmB.scene) } catch { /* */ }
      }
    },
    vrm: vrmA,
  }
}
