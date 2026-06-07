"""Color palette extraction using KMeans + CIE L*a*b* color space.

Stores into Qdrant payload:
  color_lab       : list[float]  — L*a*b* of most vivid dominant color (3 elements)
  palette_hues    : list[float]  — hue (0–360) of each chromatic cluster center
  palette_hex     : list[str]    — hex color of each cluster center
  avg_saturation  : float        — mean HSV saturation across all pixels (0–1)
  avg_value       : float        — mean HSV value across all pixels (0–1)
"""

import colorsys
import logging
import warnings
from pathlib import Path

import numpy as np
from PIL import Image
from sklearn.cluster import KMeans

logger = logging.getLogger(__name__)

_N_CLUSTERS = 5
_MIN_SAT = 0.15  # achromatic threshold (HSV S)


def rgb_to_lab(r: int, g: int, b: int) -> tuple[float, float, float]:
    """Convert sRGB (0–255) to CIE L*a*b* (D65 illuminant)."""
    # Linearize (gamma decode)
    def linearize(c: float) -> float:
        c /= 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    rl, gl, bl = linearize(r), linearize(g), linearize(b)

    # sRGB → XYZ (D65)
    x = rl * 0.4124564 + gl * 0.3575761 + bl * 0.1804375
    y = rl * 0.2126729 + gl * 0.7151522 + bl * 0.0721750
    z = rl * 0.0193339 + gl * 0.1191920 + bl * 0.9503041

    # Normalize by D65 white point
    x /= 0.95047
    # y /= 1.00000 (no-op)
    z /= 1.08883

    # Piecewise f(t)
    def f(t: float) -> float:
        return t ** (1.0 / 3.0) if t > 0.008856 else 7.787 * t + 16.0 / 116.0

    fx, fy, fz = f(x), f(y), f(z)
    L = 116.0 * fy - 16.0
    a = 500.0 * (fx - fy)
    b_ = 200.0 * (fy - fz)
    return (L, a, b_)


def hex_to_lab(hex_color: str) -> tuple[float, float, float]:
    """Convert a hex color string '#rrggbb' to CIE L*a*b* (D65 illuminant)."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        raise ValueError(f"Invalid hex color: {hex_color!r}")
    return rgb_to_lab(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def extract_color_palette(image_path: Path) -> dict:
    """Extract color data using KMeans clustering on a 100×100 thumbnail.

    Returns {} on any failure so callers can skip gracefully.
    """
    try:
        img = Image.open(image_path).convert("RGB").resize((100, 100), Image.LANCZOS)
    except Exception as exc:
        logger.warning("color_extractor: cannot open %s — %s", image_path, exc)
        return {}

    pixels = np.array(img).reshape(-1, 3).astype(float)  # (10000, 3)

    # Vectorized HSV stats for avg_saturation / avg_value
    pn = pixels / 255.0
    v_arr = np.max(pn, axis=1)
    s_arr = np.where(v_arr > 0, (v_arr - np.min(pn, axis=1)) / v_arr, 0.0)
    avg_saturation = round(float(np.mean(s_arr)), 3)
    avg_value = round(float(np.mean(v_arr)), 3)

    # KMeans clustering
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            km = KMeans(n_clusters=_N_CLUSTERS, n_init=3, random_state=42)
            km.fit(pixels)
        except Exception as exc:
            logger.warning("color_extractor: KMeans failed for %s — %s", image_path, exc)
            return {}

    clusters = []
    for rgb in km.cluster_centers_:
        r, g, b = int(round(rgb[0])), int(round(rgb[1])), int(round(rgb[2]))
        r, g, b = max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b))
        h, s, _ = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
        clusters.append({
            "lab": rgb_to_lab(r, g, b),
            "hue": round(h * 360.0, 1),
            "sat": s,
            "hex": f"#{r:02x}{g:02x}{b:02x}",
        })

    # Most vivid (highest saturation) chromatic cluster → color_lab
    chromatic = [c for c in clusters if c["sat"] >= _MIN_SAT]
    if chromatic:
        dominant = max(chromatic, key=lambda c: c["sat"])
    else:
        # Fully achromatic image: pick cluster closest to median L*
        dominant = sorted(clusters, key=lambda c: c["lab"][0])[len(clusters) // 2]

    palette_hues = [c["hue"] for c in clusters if c["sat"] >= _MIN_SAT]

    return {
        "color_lab":      [round(x, 2) for x in dominant["lab"]],
        "palette_hues":   palette_hues,
        "palette_hex":    [c["hex"] for c in clusters],
        "avg_saturation": avg_saturation,
        "avg_value":      avg_value,
    }
