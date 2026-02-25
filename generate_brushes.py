#!/usr/bin/env python3
"""
Generate Procreate .brush files based on visual brush samples and grain textures.

Each .brush file is a ZIP archive containing:
  - Brush.archive  (NSKeyedArchiver binary plist with brush settings)
  - Shape.png      (256x256 greyscale brush stamp shape)
  - Grain.png      (512x512 greyscale grain/texture, when applicable)

Brush types generated (matching the visual samples):
  1. Ink Smooth      – clean round ink tip, no grain
  2. Ink Rough       – hard-edge jagged tip, paper grain
  3. Ink Dry         – bristle/dry-brush spread, canvas grain
  4. Ink Splatter    – scattered round stamps, noise grain
  5. Ink Marker      – chisel-flat tip, no grain
  6. Ink Watercolor  – soft radial gradient tip, paper grain

Run:
  python3 generate_brushes.py
Output goes to ./procreate_brushes/
"""

import io
import math
import os
import plistlib
import random
import zipfile

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

OUTPUT_DIR = "procreate_brushes"
SHAPE_SIZE = 256   # pixels
GRAIN_SIZE = 512   # pixels
random.seed(42)
np.random.seed(42)


# ─────────────────────────────────────────────────────────────
# Minimal NSKeyedArchiver encoder
# ─────────────────────────────────────────────────────────────

class _NSKA:
    """Build an NSKeyedArchiver binary-plist payload from a plain dict."""

    def __init__(self):
        self._objects = ["$null"]
        self._str_cache: dict[str, plistlib.UID] = {}
        self._cls_cache: dict[str, plistlib.UID] = {}

    def _str(self, s: str) -> plistlib.UID:
        if s not in self._str_cache:
            uid = plistlib.UID(len(self._objects))
            self._objects.append(s)
            self._str_cache[s] = uid
        return self._str_cache[s]

    def _cls(self, classname: str, parents: list[str] | None = None) -> plistlib.UID:
        if classname not in self._cls_cache:
            if parents is None:
                parents = [classname, "NSObject"]
            uid = plistlib.UID(len(self._objects))
            self._objects.append({"$classname": classname, "$classes": parents})
            self._cls_cache[classname] = uid
        return self._cls_cache[classname]

    def _obj(self, d: dict) -> plistlib.UID:
        uid = plistlib.UID(len(self._objects))
        self._objects.append(d)
        return uid

    def encode_brush(self, props: dict) -> bytes:
        """Return binary-plist bytes for the given brush property dict."""
        cls_uid = self._cls("BrushDocument", ["BrushDocument", "NSObject"])

        root: dict = {"$class": cls_uid}

        for key, val in props.items():
            if isinstance(val, str):
                root[key] = self._str(val)
            else:
                root[key] = val   # int, float, bool inline

        root_uid = self._obj(root)

        payload = {
            "$archiver": "NSKeyedArchiver",
            "$version": 100000,
            "$top": {"root": root_uid},
            "$objects": self._objects,
        }
        return plistlib.dumps(payload, fmt=plistlib.FMT_BINARY)


def make_brush_archive(props: dict) -> bytes:
    return _NSKA().encode_brush(props)


# ─────────────────────────────────────────────────────────────
# Shape generators  (256×256 L-mode, white shape on black bg)
# ─────────────────────────────────────────────────────────────

def _new_shape() -> Image.Image:
    return Image.new("L", (SHAPE_SIZE, SHAPE_SIZE), 0)


def shape_round_smooth() -> Image.Image:
    """Perfectly soft circular stamp."""
    img = _new_shape()
    cx = cy = SHAPE_SIZE // 2
    r = SHAPE_SIZE // 2 - 4
    for y in range(SHAPE_SIZE):
        for x in range(SHAPE_SIZE):
            d = math.hypot(x - cx, y - cy)
            v = max(0.0, 1.0 - (d / r) ** 1.5)
            img.putpixel((x, y), int(v * 255))
    return img


def shape_round_rough() -> Image.Image:
    """Hard-edge circle with irregular perimeter noise."""
    img = _new_shape()
    cx = cy = SHAPE_SIZE // 2
    r = SHAPE_SIZE // 2 - 10
    arr = np.zeros((SHAPE_SIZE, SHAPE_SIZE), dtype=np.float32)
    for y in range(SHAPE_SIZE):
        for x in range(SHAPE_SIZE):
            d = math.hypot(x - cx, y - cy)
            # radial noise to roughen the edge
            angle = math.atan2(y - cy, x - cx)
            noise = 8 * math.sin(angle * 7) + 5 * math.cos(angle * 13)
            edge = r + noise
            if d < edge - 6:
                arr[y, x] = 1.0
            elif d < edge:
                arr[y, x] = (edge - d) / 6.0
    return Image.fromarray((arr * 255).astype(np.uint8), "L")


def shape_dry_brush() -> Image.Image:
    """Elongated bristle-spread shape."""
    img = _new_shape()
    draw = ImageDraw.Draw(img)
    cx, cy = SHAPE_SIZE // 2, SHAPE_SIZE // 2
    # Draw thin horizontal bristles
    n_bristles = 40
    for i in range(n_bristles):
        y_off = int((i - n_bristles // 2) * 3.2)
        alpha = int(255 * math.exp(-0.5 * (y_off / (SHAPE_SIZE * 0.18)) ** 2))
        x0 = cx - random.randint(60, 110)
        x1 = cx + random.randint(60, 110)
        draw.line([(x0, cy + y_off), (x1, cy + y_off)],
                  fill=alpha, width=random.randint(1, 3))
    return img.filter(ImageFilter.GaussianBlur(1.5))


def shape_splatter() -> Image.Image:
    """Cluster of small round dots for splatter effect."""
    img = _new_shape()
    draw = ImageDraw.Draw(img)
    cx, cy = SHAPE_SIZE // 2, SHAPE_SIZE // 2
    # Large central dot
    r0 = 30
    draw.ellipse([cx - r0, cy - r0, cx + r0, cy + r0], fill=255)
    # Surrounding smaller dots
    for _ in range(22):
        angle = random.uniform(0, 2 * math.pi)
        dist = random.uniform(20, 90)
        px = int(cx + dist * math.cos(angle))
        py = int(cy + dist * math.sin(angle))
        r = random.randint(3, 18)
        draw.ellipse([px - r, py - r, px + r, py + r],
                     fill=random.randint(180, 255))
    return img.filter(ImageFilter.GaussianBlur(0.8))


def shape_chisel_marker() -> Image.Image:
    """Flat chisel/rectangular tip like a marker."""
    img = _new_shape()
    draw = ImageDraw.Draw(img)
    cx, cy = SHAPE_SIZE // 2, SHAPE_SIZE // 2
    w, h = 90, 30
    # Rotated 45°
    pts = []
    angle = math.radians(45)
    corners = [(-w, -h), (w, -h), (w, h), (-w, h)]
    for dx, dy in corners:
        rx = dx * math.cos(angle) - dy * math.sin(angle)
        ry = dx * math.sin(angle) + dy * math.cos(angle)
        pts.append((cx + rx, cy + ry))
    draw.polygon(pts, fill=230)
    return img.filter(ImageFilter.GaussianBlur(1.0))


def shape_watercolor() -> Image.Image:
    """Very soft radial gradient with organic fringe."""
    img = _new_shape()
    cx = cy = SHAPE_SIZE // 2
    r = SHAPE_SIZE // 2 - 8
    arr = np.zeros((SHAPE_SIZE, SHAPE_SIZE), dtype=np.float32)
    for y in range(SHAPE_SIZE):
        for x in range(SHAPE_SIZE):
            d = math.hypot(x - cx, y - cy)
            # soft cubic falloff
            t = max(0.0, 1.0 - d / r)
            arr[y, x] = t ** 3
    # Add slight organic fringe noise
    noise = np.random.normal(0, 0.04, arr.shape).astype(np.float32)
    arr = np.clip(arr + noise * (arr > 0.05), 0, 1)
    return Image.fromarray((arr * 255).astype(np.uint8), "L").filter(
        ImageFilter.GaussianBlur(3))


# ─────────────────────────────────────────────────────────────
# Grain generators  (512×512 L-mode, light = high texture)
# ─────────────────────────────────────────────────────────────

def _new_grain() -> np.ndarray:
    return np.zeros((GRAIN_SIZE, GRAIN_SIZE), dtype=np.float32)


def grain_paper() -> Image.Image:
    """Fine paper/laid texture with horizontal fibre hint."""
    arr = _new_grain()
    # Base white noise
    arr += np.random.uniform(0, 0.25, arr.shape)
    # Horizontal fibre lines
    for i in range(0, GRAIN_SIZE, random.randint(3, 7)):
        arr[i, :] += random.uniform(0.05, 0.15)
    # Smooth slightly
    img = Image.fromarray((np.clip(arr, 0, 1) * 255).astype(np.uint8), "L")
    return img.filter(ImageFilter.GaussianBlur(0.7))


def grain_canvas() -> Image.Image:
    """Woven canvas texture (cross-hatch grid)."""
    arr = _new_grain()
    arr += np.random.uniform(0, 0.1, arr.shape)
    step = 8
    for i in range(0, GRAIN_SIZE, step):
        arr[i, :] += 0.30
        arr[:, i] += 0.30
    arr = np.clip(arr, 0, 1)
    # Add a slight diagonal weave
    for i in range(0, GRAIN_SIZE, step * 2):
        for j in range(0, GRAIN_SIZE, step * 2):
            r = 3
            arr[max(0, i - r):i + r, max(0, j - r):j + r] = np.clip(
                arr[max(0, i - r):i + r, max(0, j - r):j + r] + 0.15, 0, 1)
    img = Image.fromarray((arr * 255).astype(np.uint8), "L")
    return img.filter(ImageFilter.GaussianBlur(0.5))


def grain_rough() -> Image.Image:
    """Coarse rough/granular stone-like texture."""
    # Multi-octave coherent noise approximation
    arr = _new_grain()
    for octave in range(1, 5):
        scale = 2 ** octave
        small = np.random.uniform(0, 1.0 / scale,
                                  (GRAIN_SIZE // scale + 1,
                                   GRAIN_SIZE // scale + 1)).astype(np.float32)
        big = np.array(
            Image.fromarray((small * 255).astype(np.uint8)).resize(
                (GRAIN_SIZE, GRAIN_SIZE), Image.BILINEAR
            )
        ) / 255.0
        arr += big / scale
    arr = (arr - arr.min()) / (arr.max() - arr.min() + 1e-9)
    img = Image.fromarray((arr * 255).astype(np.uint8), "L")
    return img.filter(ImageFilter.GaussianBlur(1.2))


# ─────────────────────────────────────────────────────────────
# Brush definitions  (matching the visual samples)
# ─────────────────────────────────────────────────────────────

def _img_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


BRUSHES = [
    {
        "filename": "Ink_Smooth.brush",
        "shape_fn": shape_round_smooth,
        "grain_fn": None,
        "archive_props": {
            "name": "Ink Smooth",
            "spacing": 0.02,
            "streamlineAmount": 0.80,
            "size": 80.0,
            "opacity": 1.0,
            "bleed": 0.0,
            "jitter": 0.0,
            "count": 1,
            "scatterX": 0.0,
            "scatterY": 0.0,
            "grainZoom": 1.0,
            "grainMoveAmount": 0.0,
            "grainRotation": 0.0,
        },
    },
    {
        "filename": "Ink_Rough.brush",
        "shape_fn": shape_round_rough,
        "grain_fn": grain_paper,
        "archive_props": {
            "name": "Ink Rough",
            "spacing": 0.04,
            "streamlineAmount": 0.40,
            "size": 90.0,
            "opacity": 0.95,
            "bleed": 0.05,
            "jitter": 0.03,
            "count": 1,
            "scatterX": 0.02,
            "scatterY": 0.02,
            "grainZoom": 0.60,
            "grainMoveAmount": 0.10,
            "grainRotation": 0.0,
        },
    },
    {
        "filename": "Ink_Dry.brush",
        "shape_fn": shape_dry_brush,
        "grain_fn": grain_canvas,
        "archive_props": {
            "name": "Ink Dry",
            "spacing": 0.06,
            "streamlineAmount": 0.30,
            "size": 120.0,
            "opacity": 0.75,
            "bleed": 0.20,
            "jitter": 0.05,
            "count": 1,
            "scatterX": 0.01,
            "scatterY": 0.01,
            "grainZoom": 0.45,
            "grainMoveAmount": 0.30,
            "grainRotation": 0.0,
        },
    },
    {
        "filename": "Ink_Splatter.brush",
        "shape_fn": shape_splatter,
        "grain_fn": grain_rough,
        "archive_props": {
            "name": "Ink Splatter",
            "spacing": 0.35,
            "streamlineAmount": 0.00,
            "size": 110.0,
            "opacity": 0.90,
            "bleed": 0.10,
            "jitter": 0.40,
            "count": 3,
            "scatterX": 0.50,
            "scatterY": 0.50,
            "grainZoom": 0.70,
            "grainMoveAmount": 0.50,
            "grainRotation": 1.0,
        },
    },
    {
        "filename": "Ink_Marker.brush",
        "shape_fn": shape_chisel_marker,
        "grain_fn": None,
        "archive_props": {
            "name": "Ink Marker",
            "spacing": 0.01,
            "streamlineAmount": 0.60,
            "size": 70.0,
            "opacity": 0.88,
            "bleed": 0.0,
            "jitter": 0.0,
            "count": 1,
            "scatterX": 0.0,
            "scatterY": 0.0,
            "grainZoom": 1.0,
            "grainMoveAmount": 0.0,
            "grainRotation": 0.0,
        },
    },
    {
        "filename": "Ink_Watercolor.brush",
        "shape_fn": shape_watercolor,
        "grain_fn": grain_paper,
        "archive_props": {
            "name": "Ink Watercolor",
            "spacing": 0.05,
            "streamlineAmount": 0.65,
            "size": 150.0,
            "opacity": 0.45,
            "bleed": 0.35,
            "jitter": 0.02,
            "count": 1,
            "scatterX": 0.02,
            "scatterY": 0.02,
            "grainZoom": 0.55,
            "grainMoveAmount": 0.20,
            "grainRotation": 0.0,
        },
    },
]


# ─────────────────────────────────────────────────────────────
# Packaging
# ─────────────────────────────────────────────────────────────

def build_brush_file(brush_def: dict, out_dir: str) -> str:
    """Create a single .brush ZIP file. Returns the output path."""
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, brush_def["filename"])

    # 1. Generate shape PNG
    shape_img = brush_def["shape_fn"]()

    # 2. Generate grain PNG (if applicable)
    has_grain = brush_def["grain_fn"] is not None
    grain_img = brush_def["grain_fn"]() if has_grain else None

    # 3. Build Brush.archive
    props = dict(brush_def["archive_props"])
    archive_bytes = make_brush_archive(props)

    # 4. Pack as ZIP (.brush)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Brush.archive", archive_bytes)
        zf.writestr("Shape.png", _img_bytes(shape_img))
        if grain_img is not None:
            zf.writestr("Grain.png", _img_bytes(grain_img))

    return out_path


def main():
    print(f"Generating {len(BRUSHES)} Procreate brushes → {OUTPUT_DIR}/\n")
    for brush in BRUSHES:
        path = build_brush_file(brush, OUTPUT_DIR)
        has_grain = brush["grain_fn"] is not None
        grain_tag = "shape + grain" if has_grain else "shape only"
        print(f"  ✓  {brush['filename']}  ({grain_tag})")
    print(f"\nDone. Import the .brush files into Procreate via "
          f"Files → Open or the Procreate brush panel.")


if __name__ == "__main__":
    main()
