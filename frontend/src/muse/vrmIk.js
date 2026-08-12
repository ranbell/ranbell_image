/**
 * Two-bone + CCD IK helpers for VRM normalized Object3D bones.
 */
import * as THREE from 'three'

const _root = new THREE.Vector3()
const _mid = new THREE.Vector3()
const _tip = new THREE.Vector3()
const _target = new THREE.Vector3()
const _pole = new THREE.Vector3()
const _dir = new THREE.Vector3()
const _n = new THREE.Vector3()
const _bin = new THREE.Vector3()
const _midPos = new THREE.Vector3()
const _q = new THREE.Quaternion()
const _parentQ = new THREE.Quaternion()
const _worldQ = new THREE.Quaternion()
const _invParent = new THREE.Quaternion()
const _up = new THREE.Vector3(0, 1, 0)
const _from = new THREE.Vector3()
const _to = new THREE.Vector3()
const _axis = new THREE.Vector3()

function setWorldRotation(bone, worldQuat) {
  bone.parent.getWorldQuaternion(_parentQ)
  _invParent.copy(_parentQ).invert()
  bone.quaternion.copy(_invParent).multiply(worldQuat)
  bone.updateMatrix()
}

/**
 * Analytic two-bone IK with pole vector (elbow/knee bend plane).
 * @param {THREE.Object3D} upper
 * @param {THREE.Object3D} lower
 * @param {THREE.Object3D} tip
 * @param {THREE.Vector3} targetWorld
 * @param {THREE.Vector3} poleWorld
 */
export function solveTwoBoneIk(upper, lower, tip, targetWorld, poleWorld) {
  if (!upper?.parent || !lower || !tip) return

  upper.updateWorldMatrix(true, true)
  lower.updateWorldMatrix(true, true)
  tip.updateWorldMatrix(true, true)

  _root.setFromMatrixPosition(upper.matrixWorld)
  _mid.setFromMatrixPosition(lower.matrixWorld)
  _tip.setFromMatrixPosition(tip.matrixWorld)
  _target.copy(targetWorld)
  _pole.copy(poleWorld)

  const len1 = _root.distanceTo(_mid)
  const len2 = _mid.distanceTo(_tip)
  if (len1 < 1e-5 || len2 < 1e-5) return

  let dist = _root.distanceTo(_target)
  const maxReach = (len1 + len2) * 0.998
  const minReach = Math.abs(len1 - len2) * 1.002
  if (dist < 1e-5) return
  dist = THREE.MathUtils.clamp(dist, minReach, maxReach)
  _dir.copy(_target).sub(_root).normalize()
  _target.copy(_root).addScaledVector(_dir, dist)

  // Bend plane: root → target, pushed by pole
  _n.copy(_pole).sub(_root)
  _bin.crossVectors(_dir, _n)
  if (_bin.lengthSq() < 1e-8) {
    _bin.crossVectors(_dir, _up)
    if (_bin.lengthSq() < 1e-8) _bin.set(1, 0, 0)
  }
  _bin.normalize()
  _n.crossVectors(_bin, _dir).normalize()

  // Law of cosines at root
  const cosA = THREE.MathUtils.clamp(
    (len1 * len1 + dist * dist - len2 * len2) / (2 * len1 * dist),
    -1,
    1,
  )
  const sinA = Math.sqrt(Math.max(0, 1 - cosA * cosA))
  _midPos.copy(_root)
    .addScaledVector(_dir, len1 * cosA)
    .addScaledVector(_n, len1 * sinA)

  // Rotate upper so its child (lower origin) aims at midPos
  upper.updateWorldMatrix(true, false)
  _from.copy(_mid).sub(_root)
  if (_from.lengthSq() < 1e-10) return
  _from.normalize()
  _to.copy(_midPos).sub(_root).normalize()
  _q.setFromUnitVectors(_from, _to)
  upper.getWorldQuaternion(_worldQ)
  _worldQ.premultiply(_q)
  setWorldRotation(upper, _worldQ)

  // Rotate lower so tip aims at target
  upper.updateWorldMatrix(true, true)
  lower.updateWorldMatrix(true, true)
  tip.updateWorldMatrix(true, true)
  _mid.setFromMatrixPosition(lower.matrixWorld)
  _tip.setFromMatrixPosition(tip.matrixWorld)
  _from.copy(_tip).sub(_mid)
  if (_from.lengthSq() < 1e-10) return
  _from.normalize()
  _to.copy(_target).sub(_mid).normalize()
  _q.setFromUnitVectors(_from, _to)
  lower.getWorldQuaternion(_worldQ)
  _worldQ.premultiply(_q)
  setWorldRotation(lower, _worldQ)
}

/**
 * CCD refine pass (small angles) after two-bone solve.
 */
export function solveCcdIk(bones, tip, target, opts = {}) {
  const iterations = opts.iterations ?? 8
  const maxAngle = opts.maxAngle ?? 0.25
  if (!bones?.length || !tip || !target) return

  for (let iter = 0; iter < iterations; iter++) {
    for (let i = bones.length - 1; i >= 0; i--) {
      const bone = bones[i]
      if (!bone?.parent) continue
      bone.updateWorldMatrix(true, true)
      tip.updateWorldMatrix(true, true)

      _root.setFromMatrixPosition(bone.matrixWorld)
      _tip.setFromMatrixPosition(tip.matrixWorld)
      _from.copy(_tip).sub(_root)
      _to.copy(target).sub(_root)
      if (_from.lengthSq() < 1e-8 || _to.lengthSq() < 1e-8) continue
      _from.normalize()
      _to.normalize()

      const dot = THREE.MathUtils.clamp(_from.dot(_to), -1, 1)
      let angle = Math.acos(dot)
      if (angle < 1e-4) continue
      if (angle > maxAngle) angle = maxAngle

      _axis.crossVectors(_from, _to)
      if (_axis.lengthSq() < 1e-10) continue
      _axis.normalize()
      _q.setFromAxisAngle(_axis, angle)

      bone.parent.getWorldQuaternion(_parentQ)
      bone.getWorldQuaternion(_worldQ)
      _worldQ.premultiply(_q)
      _invParent.copy(_parentQ).invert()
      bone.quaternion.copy(_invParent).multiply(_worldQ)
      bone.updateMatrix()
    }
  }
}

/**
 * Full limb solve: two-bone + light CCD polish.
 * poleHint: 'out' | 'back' | Vector3
 */
export function solveLimbIk(upper, lower, tip, targetWorld, { side = 'left', kind = 'arm' } = {}) {
  upper.updateWorldMatrix(true, true)
  const root = new THREE.Vector3().setFromMatrixPosition(upper.matrixWorld)
  const target = targetWorld.clone()
  // Default poles: arms bend outward/back, legs bend forward
  const pole = new THREE.Vector3()
  if (kind === 'arm') {
    pole.set(side === 'left' ? -0.6 : 0.6, root.y + 0.15, -0.35)
  } else {
    pole.set(side === 'left' ? -0.15 : 0.15, root.y + 0.4, 0.85)
  }
  solveTwoBoneIk(upper, lower, tip, target, pole)
  solveCcdIk([upper, lower], tip, target, { iterations: 6, maxAngle: 0.2 })
}
