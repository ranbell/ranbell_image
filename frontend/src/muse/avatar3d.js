/**
 * Normal-proportion VRM avatar preview driven by poseSketch tags.
 * Replaces the procedural chibi for conversation pose/camera checks.
 */
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { VRMLoaderPlugin, VRMUtils, VRMHumanBoneName } from '@pixiv/three-vrm'
import { buildPoseSketch } from './poseSketch.js'

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

  // Default: slight natural arm drop (avoid rigid T-pose)
  setEuler(lUpperArm, 0, 0, 1.15)
  setEuler(rUpperArm, 0, 0, -1.15)
  setEuler(lLowerArm, 0, 0, 0.15)
  setEuler(rLowerArm, 0, 0, -0.15)

  if (posture === 'standing' || posture === 'walking' || posture === 'running') {
    if (hips) hips.position.y = 0
    setEuler(lUpperLeg, 0, 0, 0)
    setEuler(rUpperLeg, 0, 0, 0)
    if (posture === 'walking' || posture === 'running') {
      const swing = posture === 'running' ? 0.55 : 0.3
      setEuler(lUpperLeg, -swing, 0, 0)
      setEuler(rUpperLeg, swing, 0, 0)
      setEuler(lLowerLeg, swing * 0.8, 0, 0)
      setEuler(rLowerLeg, swing * 0.4, 0, 0)
    }
  } else if (posture === 'sitting') {
    if (hips) hips.position.y = -0.35
    setEuler(spine, 0.1, 0, 0)
    setEuler(lUpperLeg, -Math.PI / 2, 0.08, 0)
    setEuler(rUpperLeg, -Math.PI / 2, -0.08, 0)
    setEuler(lLowerLeg, Math.PI / 2, 0, 0)
    setEuler(rLowerLeg, Math.PI / 2, 0, 0)
    setEuler(lFoot, 0.2, 0, 0)
    setEuler(rFoot, 0.2, 0, 0)
  } else if (posture === 'squatting' || posture === 'crouching' || posture === 'kneeling') {
    // Deep crouch — hero combo with side + low angle
    if (hips) hips.position.y = -0.42
    setEuler(spine, 0.18, 0, 0)
    setEuler(chest, 0.08, 0, 0)
    setEuler(lUpperLeg, -1.35, 0.12, 0.08)
    setEuler(rUpperLeg, -1.35, -0.12, -0.08)
    setEuler(lLowerLeg, 2.05, 0, 0)
    setEuler(rLowerLeg, 2.05, 0, 0)
    setEuler(lFoot, 0.35, 0, 0)
    setEuler(rFoot, 0.35, 0, 0)
    // Hands near knees
    setEuler(lUpperArm, -0.4, 0.2, 0.9)
    setEuler(rUpperArm, -0.4, -0.2, -0.9)
    setEuler(lLowerArm, -0.6, 0, 0.2)
    setEuler(rLowerArm, -0.6, 0, -0.2)
  } else if (posture === 'lying') {
    if (hips) {
      hips.position.y = -0.55
      hips.rotation.z = Math.PI / 2
    }
  } else if (posture === 'jumping') {
    if (hips) hips.position.y = 0.25
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
  if (gaze === 'looking_up') {
    setEuler(neck, -0.25, 0, 0)
    setEuler(head, -0.35, 0, 0)
  } else if (gaze === 'looking_down') {
    setEuler(neck, 0.15, 0, 0)
    setEuler(head, 0.35, 0, 0)
  }

  // Face camera on side shots: slight head yaw toward lens (+X)
  if (side === 'side') {
    setEuler(head, head?.rotation.x || 0, -0.35, 0)
  } else if (side === 'behind') {
    // keep looking away
  }

  vrm.update(0)
}

/** Camera for adult-proportion VRM (~1.5m). */
export function placeAvatarCamera(camera, model, { duo = false } = {}) {
  const pitch = model.cameraPitch || 'eye'
  const side = model.cameraSide || 'front'
  const dist = model.cameraDistance || 'full'
  const posture = model.posture || 'standing'
  const crouch = posture === 'squatting' || posture === 'crouching' || posture === 'kneeling'

  let x = 0
  let y = 1.25
  let z = 2.6
  let lookY = 1.0
  let fov = 35

  if (dist === 'close') {
    z = 1.1
    y = 1.45
    lookY = 1.45
    fov = 28
  } else if (dist === 'upper') {
    z = 1.7
    y = 1.35
    lookY = 1.25
    fov = 32
  }

  if (pitch === 'below') {
    y = crouch ? 0.05 : 0.25
    z = dist === 'close' ? 1.3 : 2.0
    lookY = crouch ? 0.55 : 1.1
    fov = 34
  } else if (pitch === 'above') {
    y = 2.6
    z = 1.8
    lookY = 0.9
  }

  if (side === 'side') {
    x = pitch === 'below' ? 1.9 : 2.4
    z = pitch === 'below' ? 0.35 : 0.45
    if (pitch === 'below' && crouch) {
      x = 1.7
      y = -0.05
      z = 0.4
      lookY = 0.45
      fov = 32
    }
  } else if (side === 'behind') {
    z = -Math.abs(z)
    x = 0.2
  }

  if (duo) {
    x *= 1.15
    z *= 1.2
    fov += 4
  }

  camera.position.set(x, y, z)
  camera.lookAt(0, lookY, 0)
  camera.fov = fov
  camera.updateProjectionMatrix()
}

async function loadVrm(url) {
  const loader = new GLTFLoader()
  loader.register((parser) => new VRMLoaderPlugin(parser))
  const gltf = await loader.loadAsync(url)
  const vrm = gltf.userData.vrm
  if (!vrm) throw new Error('VRM missing in glTF userData')
  VRMUtils.removeUnnecessaryVertices(gltf.scene)
  try { VRMUtils.combineSkeletons(gltf.scene) } catch { /* optional */ }
  try { VRMUtils.combineMorphs(vrm) } catch { /* optional */ }
  VRMUtils.rotateVRM0(vrm)
  vrm.scene.traverse((obj) => {
    obj.frustumCulled = false
    if (obj.isMesh) {
      obj.castShadow = true
      obj.receiveShadow = true
    }
  })
  return vrm
}

/**
 * Mount a VRM stage into `container`.
 * @returns {Promise<{ update, dispose, ready: Promise<void> }>}
 */
export async function createAvatarStage(container, {
  duo = false,
  modelUrl = DEFAULT_VRM_URL,
} = {}) {
  const width = () => Math.max(160, container.clientWidth || 320)
  const height = () => Math.max(200, Math.min(420, (container.clientWidth || 320) * 0.95))

  const scene = new THREE.Scene()
  scene.background = new THREE.Color(0x2a2a2e)

  const camera = new THREE.PerspectiveCamera(35, width() / height(), 0.05, 40)
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
  renderer.setSize(width(), height())
  renderer.shadowMap.enabled = true
  renderer.shadowMap.type = THREE.PCFSoftShadowMap
  container.appendChild(renderer.domElement)
  Object.assign(renderer.domElement.style, {
    display: 'block', width: '100%', borderRadius: '0.75rem',
  })

  scene.add(new THREE.HemisphereLight(0xfff0f5, 0x1a1a22, 0.9))
  const key = new THREE.DirectionalLight(0xffffff, 1.15)
  key.position.set(2.5, 4.5, 3)
  key.castShadow = true
  key.shadow.mapSize.set(1024, 1024)
  const fill = new THREE.DirectionalLight(0xa5f3fc, 0.35)
  fill.position.set(-3, 2, -1)
  const rim = new THREE.DirectionalLight(0xffc1d5, 0.35)
  rim.position.set(-1.5, 2.5, -3)
  scene.add(key, fill, rim, new THREE.AmbientLight(0xffffff, 0.22))

  const ground = new THREE.Mesh(
    new THREE.CircleGeometry(2.2, 64),
    new THREE.MeshStandardMaterial({ color: 0x3a3a40, roughness: 0.85, metalness: 0.05 }),
  )
  ground.rotation.x = -Math.PI / 2
  ground.position.y = 0
  ground.receiveShadow = true
  scene.add(ground)

  const status = document.createElement('div')
  status.textContent = 'モデル読込中…'
  Object.assign(status.style, {
    position: 'absolute', left: '12px', bottom: '10px',
    fontSize: '10px', color: 'rgba(186,230,253,0.9)', pointerEvents: 'none',
  })
  container.style.position = container.style.position || 'relative'
  container.appendChild(status)

  const vrmA = await loadVrm(modelUrl)
  scene.add(vrmA.scene)
  let vrmB = null
  if (duo) {
    vrmB = await loadVrm(modelUrl)
    vrmB.scene.position.x = 0.55
    vrmA.scene.position.x = -0.55
    scene.add(vrmB.scene)
  }

  status.textContent = ''
  status.remove()

  let latest = { tags: '', beat: '', beatB: '', frame: '', duo }
  let raf = 0
  const clock = new THREE.Clock()

  function sync() {
    const model = buildPoseSketch(latest.tags, {
      beat: latest.beat,
      beat_b: latest.beatB,
      frame: latest.frame,
      duo: latest.duo,
    })
    if (model.empty) return model
    applyVrmPose(vrmA, model)
    if (vrmB) {
      const modelB = buildPoseSketch(latest.tags, {
        beat: latest.beatB || latest.beat,
        frame: latest.frame,
        duo: true,
      })
      applyVrmPose(vrmB, modelB)
    }
    placeAvatarCamera(camera, model, { duo: Boolean(vrmB) })
    return model
  }

  function tick() {
    const dt = clock.getDelta()
    vrmA.update(dt)
    if (vrmB) vrmB.update(dt)
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
      scene.remove(vrmA.scene)
      if (vrmB) scene.remove(vrmB.scene)
      VRMUtils.deepDispose?.(vrmA.scene)
      if (vrmB) VRMUtils.deepDispose?.(vrmB.scene)
    },
    vrm: vrmA,
  }
}
