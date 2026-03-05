#!/usr/bin/env python3
"""
Generate all PWA / favicon PNG assets for Dinky Coop.

Uses only cv2 + numpy (already in requirements.txt).
Run once from the repo root or from src/:
    python src/generateIcons.py
"""
import os
import numpy as np
import cv2


# ── Helpers ─────────────────────────────────────────────────────────────────

def _bgra(hex_color: str, a: int = 255) -> tuple:
    """#RRGGBB → (B, G, R, A)"""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (b, g, r, a)


def _rounded_mask(size: int, radius: int) -> np.ndarray:
    """Return a single-channel uint8 mask with a rounded rectangle."""
    m = np.zeros((size, size), dtype=np.uint8)
    r, s = radius, size
    cv2.rectangle(m, (r, 0),     (s - r - 1, s - 1),     255, -1)
    cv2.rectangle(m, (0, r),     (s - 1,     s - r - 1), 255, -1)
    cv2.ellipse(m, (r,         r),         (r, r), 0, 180, 270, 255, -1)
    cv2.ellipse(m, (s - r - 1, r),         (r, r), 0, 270, 360, 255, -1)
    cv2.ellipse(m, (r,         s - r - 1), (r, r), 0,  90, 180, 255, -1)
    cv2.ellipse(m, (s - r - 1, s - r - 1), (r, r), 0,   0,  90, 255, -1)
    return m


# ── Icon renderer ────────────────────────────────────────────────────────────

def render_icon(size: int) -> np.ndarray:
    """
    Render the Dinky Coop icon at `size` × `size` pixels (BGRA).

    Layout (percentages of canvas):
        Background  – app blue  (#317EFB), rounded corners
        Sky panel   – light blue inside the door frame
        Ground      – dark green strip at the bottom
        Door frame  – brown left/right posts + top beam
        Door panel  – wooden tan, slid ~half-open (top half of frame is sky)
        Slat lines  – subtle horizontal lines on the panel
        Frame border– dark brown outline around the frame opening
    """
    s = size

    def p(v: float) -> int:
        """Convert a 0-100 percentage to a pixel coordinate."""
        return int(round(v * s / 100))

    img = np.zeros((s, s, 4), dtype=np.uint8)

    # ── Background ────────────────────────────────────────────
    img[:, :] = _bgra("#317EFB")

    # ── Sky (light blue) inside the frame opening ─────────────
    cv2.rectangle(img, (p(22), p(18)), (p(78), p(82)), _bgra("#b8d4f5"), -1)

    # ── Ground strip ──────────────────────────────────────────
    cv2.rectangle(img, (0, p(80)), (s, s), _bgra("#4a6b2a"), -1)

    # ── Left post ─────────────────────────────────────────────
    cv2.rectangle(img, (p(10), p(12)), (p(22), p(82)), _bgra("#8B6F47"), -1)
    # ── Right post ────────────────────────────────────────────
    cv2.rectangle(img, (p(78), p(12)), (p(90), p(82)), _bgra("#8B6F47"), -1)
    # ── Top beam ──────────────────────────────────────────────
    cv2.rectangle(img, (p(10), p(10)), (p(90), p(22)), _bgra("#8B6F47"), -1)

    # ── Door panel half-open (covers bottom half of frame) ────
    cv2.rectangle(img, (p(22), p(50)), (p(78), p(82)), _bgra("#c8a96e"), -1)

    # ── Slats (lighter horizontal lines on the panel) ─────────
    slat_color = _bgra("#d9bc84")
    slat_h = max(1, p(4))
    for base_pct in [53, 61, 69, 77]:
        y = p(base_pct)
        if y + slat_h <= p(82):
            cv2.rectangle(img, (p(25), y), (p(75), y + slat_h), slat_color, -1)

    # ── Frame border overlay ──────────────────────────────────
    border_thick = max(2, p(2))
    cv2.rectangle(img,
                  (p(22), p(18)), (p(78), p(82)),
                  _bgra("#5a4a30"), border_thick)

    # ── Clip to rounded corners ───────────────────────────────
    radius = p(18)
    mask = _rounded_mask(s, radius)
    img[:, :, 3] = np.where(mask > 0, img[:, :, 3], 0)

    return img


def render_maskable_icon(size: int) -> np.ndarray:
    """
    Render a maskable variant of the icon at `size` × `size` pixels (BGRA).

    Maskable icons must have:
      - Full bleed opaque background (no transparent corners)
      - All content within the inner 80% safe zone (10% padding each side)
    The OS (Android etc.) applies its own adaptive shape (circle, squircle…),
    so no custom rounded-corner clipping is applied here.
    """
    s = size
    safe_start = int(round(0.10 * s))
    safe_size  = int(round(0.80 * s))

    def p(v: float) -> int:
        """Map a 0-100 percentage into the safe zone pixel coordinate."""
        return safe_start + int(round(v * safe_size / 100))

    img = np.zeros((s, s, 4), dtype=np.uint8)

    # Full-bleed background (no alpha cutout)
    img[:, :] = _bgra("#317EFB")

    # ── Sky ───────────────────────────────────────────────────
    cv2.rectangle(img, (p(22), p(18)), (p(78), p(82)), _bgra("#b8d4f5"), -1)

    # ── Ground strip ──────────────────────────────────────────
    cv2.rectangle(img, (0, p(80)), (s, s), _bgra("#4a6b2a"), -1)
    # Keep ground within safe zone horizontally for clarity
    cv2.rectangle(img, (0, p(80)), (safe_start, s), _bgra("#317EFB"), -1)
    cv2.rectangle(img, (safe_start + safe_size, p(80)), (s, s), _bgra("#317EFB"), -1)

    # ── Left post ─────────────────────────────────────────────
    cv2.rectangle(img, (p(10), p(12)), (p(22), p(82)), _bgra("#8B6F47"), -1)
    # ── Right post ────────────────────────────────────────────
    cv2.rectangle(img, (p(78), p(12)), (p(90), p(82)), _bgra("#8B6F47"), -1)
    # ── Top beam ──────────────────────────────────────────────
    cv2.rectangle(img, (p(10), p(10)), (p(90), p(22)), _bgra("#8B6F47"), -1)

    # ── Door panel half-open ──────────────────────────────────
    cv2.rectangle(img, (p(22), p(50)), (p(78), p(82)), _bgra("#c8a96e"), -1)

    # ── Slats ─────────────────────────────────────────────────
    slat_color = _bgra("#d9bc84")
    slat_h = max(1, int(round(0.04 * safe_size)))
    for base_pct in [53, 61, 69, 77]:
        y = p(base_pct)
        if y + slat_h <= p(82):
            cv2.rectangle(img, (p(25), y), (p(75), y + slat_h), slat_color, -1)

    # ── Frame border ──────────────────────────────────────────
    border_thick = max(2, int(round(0.02 * safe_size)))
    cv2.rectangle(img,
                  (p(22), p(18)), (p(78), p(82)),
                  _bgra("#5a4a30"), border_thick)

    return img


# ── Sizes ────────────────────────────────────────────────────────────────────

SIZES = {
    "icon_16x16.png":   16,
    "icon_32x32.png":   32,
    "icon_144x144.png": 144,
    "icon_192x192.png": 192,
    "icon_512x512.png": 512,
}

MASKABLE_SIZES = {
    "icon_maskable_192x192.png": 192,
    "icon_maskable_512x512.png": 512,
}


def main() -> None:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    icons_dir  = os.path.join(script_dir, "static", "icons")
    static_dir = os.path.join(script_dir, "static")
    os.makedirs(icons_dir, exist_ok=True)

    for filename, size in SIZES.items():
        icon = render_icon(size)
        path = os.path.join(icons_dir, filename)
        cv2.imwrite(path, icon)
        print(f"  ✓  {path}  ({size}×{size})")

    for filename, size in MASKABLE_SIZES.items():
        icon = render_maskable_icon(size)
        path = os.path.join(icons_dir, filename)
        cv2.imwrite(path, icon)
        print(f"  ✓  {path}  ({size}×{size}, maskable)")

    # Convenience copies in static/ root (used directly by <link rel="icon">)
    for alias, src_name in [("favicon_32.png", "icon_32x32.png"),
                             ("favicon_16.png", "icon_16x16.png")]:
        src  = os.path.join(icons_dir, src_name)
        dest = os.path.join(static_dir, alias)
        img  = cv2.imread(src, cv2.IMREAD_UNCHANGED)
        cv2.imwrite(dest, img)
        print(f"  ✓  {dest}  (alias)")

    print("\nAll icons generated.")


if __name__ == "__main__":
    main()
