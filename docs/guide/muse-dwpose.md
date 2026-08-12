# Muse × DWPose — ComfyUI node wiring

How to wire a workflow so Muse can inject the **shot-preview still** (from pose coaching) into ControlNet on board / final renders.

Muse inspects the graph and only auto-injects when:

- A `LoadImage` (or equivalent) sits **upstream of a DWPose / OpenPose preprocessor**
- That preprocessor feeds ControlNet

ControlNet-only graphs that expect a pre-baked skeleton are **not** auto-injected.

---

## Minimal recommended graph

```
[LoadImage]  ← Muse overwrites this with the direction JPEG
    │
    ▼
[DWPreprocessor]
    │  IMAGE (pose map)
    ▼
[ControlNetApply / ControlNetApplyAdvanced]
    ▲
[ControlNetLoader]  ← openpose-family weights (e.g. control_v11p_sd15_openpose)
    │
    └── positive / negative → KSampler as usual
```

The ControlNet **weight** is still usually named openpose.  
What changes is the **preprocessor = DWPose**.

---

## Node notes

### LoadImage

- Placeholder image is fine
- Prefer a **dedicated** LoadImage for the pose path (don’t share with IP-Adapter)
- Muse sets `inputs.image` on this node

### DWPreprocessor

Typical class names: `DWPreprocessor`, or anything containing `DWPose` / `dwpose`.

| Setting | Suggestion |
|---------|------------|
| detect_body | ON |
| detect_hand | ON |
| detect_face | optional |
| resolution | 512–1024 |

### ControlNetLoader + Apply

- SD1.5: `control_v11p_sd15_openpose` (or your pack’s openpose model)
- strength ≈ **0.6–0.85**

---

## Muse usage

1. Select this workflow → UI shows OpenPose path ready  
2. Turn pose coaching (direction) ON  
3. Pose in the on-set preview  
4. Send a short line (still is attached and stored)  
5. Board / final → Muse uploads into LoadImage → DWPose → ControlNet  

---

## Anti-patterns

- LoadImage → ControlNet with **no** pose preprocessor (Muse will not inject)
- Pose LoadImage not wired into DWPose
- Relying on classic OpenPose only for new workflows (prefer DWPose)

---

## Checklist

- [ ] LoadImage → DWPreprocessor → ControlNetApply*
- [ ] Openpose ControlNet weights loaded
- [ ] Muse shows “OpenPose path ready”
- [ ] Chat once with direction ON before board/final
- [ ] Comfy queue shows `muse_direction_*.jpg` on LoadImage
