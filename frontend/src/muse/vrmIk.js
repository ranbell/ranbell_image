/**
 * Lightweight CCD IK for VRM normalized Object3D bone chains (no SkinnedMesh needed).
 */
import * as THREE from 'three'

const _bonePos = new THREE.Vector3()
const _tipPos = new THREE.Vector3()
const _toTip = new THREE.Vector3()
const _toTarget = new THREE.Vector3()
const _axis = new THREE.Vector3()
const _quat = new THREE.Quaternion()
const _parentWorldQ = new THREE.Quaternion()
const _boneWorldQ = new THREE.Quaternion()
const _localQ = new THREE.Quaternion()
const _invParent = new THREE.Quaternion()

/**
 * Iteratively rotate `bones` (root→…→near tip) so `tip` reaches `target` (world).
 * @param {THREE.Object3D[]} bones
 * @param {THREE.Object3D} tip
 * @param {THREE.Vector3} target
 * @param {{ iterations?: number, maxAngle?: number }} [opts]
 */
export function solveCcdIk(bones, tip, target, opts = {}) {
  const iterations = opts.iterations ?? 14
  const maxAngle = opts.maxAngle ?? 0.5
  if (!bones?.length || !tip || !target) return

  for (let iter = 0; iter < iterations; iter++) {
    for (let i = bones.length - 1; i >= 0; i--) {
      const bone = bones[i]
      if (!bone?.parent) continue
      bone.updateWorldMatrix(true, true)
      tip.updateWorldMatrix(true, true)

      _bonePos.setFromMatrixPosition(bone.matrixWorld)
      _tipPos.setFromMatrixPosition(tip.matrixWorld)
      _toTip.copy(_tipPos).sub(_bonePos)
      _toTarget.copy(target).sub(_bonePos)
      if (_toTip.lengthSq() < 1e-8 || _toTarget.lengthSq() < 1e-8) continue
      _toTip.normalize()
      _toTarget.normalize()

      const dot = THREE.MathUtils.clamp(_toTip.dot(_toTarget), -1, 1)
      let angle = Math.acos(dot)
      if (angle < 1e-4) continue
      if (angle > maxAngle) angle = maxAngle

      _axis.crossVectors(_toTip, _toTarget)
      if (_axis.lengthSq() < 1e-10) continue
      _axis.normalize()
      _quat.setFromAxisAngle(_axis, angle)

      bone.parent.getWorldQuaternion(_parentWorldQ)
      bone.getWorldQuaternion(_boneWorldQ)
      _boneWorldQ.premultiply(_quat)
      _invParent.copy(_parentWorldQ).invert()
      _localQ.copy(_invParent).multiply(_boneWorldQ)
      bone.quaternion.copy(_localQ)
      bone.updateMatrix()
    }
  }
}
