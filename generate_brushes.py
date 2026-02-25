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


# ── Pencil-outline shapes ─────────────────────────────────────
#    Solid pen core + noisy/granular edge falloff = pencil tooth

def shape_pencil_outline_round() -> Image.Image:
    """
    Solid pen-like core (inner 55 %) transitioning to a noisy
    pencil-grain fringe (outer 45 %).  Results in crisp, smooth
    centre strokes with slight tooth on the edges.
    """
    cx = cy = SHAPE_SIZE // 2
    r = SHAPE_SIZE // 2 - 6
    yi, xi = np.mgrid[0:SHAPE_SIZE, 0:SHAPE_SIZE]
    dist = np.sqrt((xi - cx) ** 2 + (yi - cy) ** 2).astype(np.float32)
    t = dist / r  # 0 = centre, 1 = edge, >1 = outside

    # Solid core
    arr = np.where(t <= 0.55, 1.0, 0.0).astype(np.float32)

    # Edge zone: smooth falloff + graphite particle noise
    edge = (t > 0.52) & (t < 1.02)
    edge_t = np.clip((t - 0.52) / 0.50, 0, 1)   # 0→1 across the fringe
    base_falloff = (1.0 - edge_t) ** 1.4
    noise = np.random.uniform(-0.55, 0.55, arr.shape).astype(np.float32)
    arr = np.where(edge, np.clip(base_falloff + noise * edge_t * 0.7, 0, 1), arr)
    arr = np.where(t >= 1.02, 0.0, arr)

    img = Image.fromarray((arr * 255).astype(np.uint8), "L")
    # Very light blur – just enough to anti-alias the core boundary
    return img.filter(ImageFilter.GaussianBlur(0.6))


def shape_pencil_outline_tapered() -> Image.Image:
    """
    Slightly elongated (vertical) oval with pencil-grain edges –
    gives a natural taper when the stroke angle changes, like a
    well-sharpened pencil held at a slight angle.
    """
    cx = cy = SHAPE_SIZE // 2
    rx, ry = SHAPE_SIZE // 2 - 8, int(SHAPE_SIZE * 0.38)   # wider than tall
    yi, xi = np.mgrid[0:SHAPE_SIZE, 0:SHAPE_SIZE]
    # Ellipse distance (normalised)
    t = np.sqrt(((xi - cx) / rx) ** 2 + ((yi - cy) / ry) ** 2).astype(np.float32)

    arr = np.where(t <= 0.52, 1.0, 0.0).astype(np.float32)

    edge = (t > 0.48) & (t < 1.05)
    edge_t = np.clip((t - 0.48) / 0.57, 0, 1)
    base_falloff = (1.0 - edge_t) ** 1.6
    noise = np.random.uniform(-0.50, 0.50, arr.shape).astype(np.float32)
    arr = np.where(edge, np.clip(base_falloff + noise * edge_t * 0.65, 0, 1), arr)
    arr = np.where(t >= 1.05, 0.0, arr)

    img = Image.fromarray((arr * 255).astype(np.uint8), "L")
    return img.filter(ImageFilter.GaussianBlur(0.5))


def shape_pencil_outline_fine() -> Image.Image:
    """
    Tighter, smaller core (45 %) with a thinner pencil fringe –
    for fine-detail outlines that stay crisp at small sizes.
    """
    cx = cy = SHAPE_SIZE // 2
    r = SHAPE_SIZE // 2 - 10
    yi, xi = np.mgrid[0:SHAPE_SIZE, 0:SHAPE_SIZE]
    dist = np.sqrt((xi - cx) ** 2 + (yi - cy) ** 2).astype(np.float32)
    t = dist / r

    arr = np.where(t <= 0.45, 1.0, 0.0).astype(np.float32)

    edge = (t > 0.42) & (t < 0.98)
    edge_t = np.clip((t - 0.42) / 0.56, 0, 1)
    base_falloff = (1.0 - edge_t) ** 1.2
    noise = np.random.uniform(-0.60, 0.60, arr.shape).astype(np.float32)
    arr = np.where(edge, np.clip(base_falloff + noise * edge_t * 0.75, 0, 1), arr)
    arr = np.where(t >= 0.98, 0.0, arr)

    img = Image.fromarray((arr * 255).astype(np.uint8), "L")
    return img.filter(ImageFilter.GaussianBlur(0.4))


def shape_pencil_outline_bold() -> Image.Image:
    """
    Wider core (60 %) with a coarser fringe – bold outlines with
    clearly visible pencil grain on both edges.
    """
    cx = cy = SHAPE_SIZE // 2
    r = SHAPE_SIZE // 2 - 4
    yi, xi = np.mgrid[0:SHAPE_SIZE, 0:SHAPE_SIZE]
    dist = np.sqrt((xi - cx) ** 2 + (yi - cy) ** 2).astype(np.float32)
    t = dist / r

    arr = np.where(t <= 0.60, 1.0, 0.0).astype(np.float32)

    edge = (t > 0.56) & (t < 1.04)
    edge_t = np.clip((t - 0.56) / 0.48, 0, 1)
    base_falloff = (1.0 - edge_t) ** 1.1
    # Coarser, chunkier noise to emphasise pencil grain
    noise = np.random.uniform(-0.65, 0.65, arr.shape).astype(np.float32)
    noise_blur = np.array(
        Image.fromarray(((noise + 0.65) / 1.3 * 255).astype(np.uint8)).filter(
            ImageFilter.GaussianBlur(1.5)
        )
    ) / 255.0 * 1.3 - 0.65
    arr = np.where(edge,
                   np.clip(base_falloff + noise_blur.astype(np.float32) * edge_t * 0.80,
                           0, 1),
                   arr)
    arr = np.where(t >= 1.04, 0.0, arr)

    img = Image.fromarray((arr * 255).astype(np.uint8), "L")
    return img.filter(ImageFilter.GaussianBlur(0.7))


def shape_pencil_outline_hard() -> Image.Image:
    """
    4H-pencil feel: huge solid core (72 %), whisper-thin fringe (8 %).
    Draws almost like a technical pen but retains a barely-there tooth.
    """
    cx = cy = SHAPE_SIZE // 2
    r = SHAPE_SIZE // 2 - 8
    yi, xi = np.mgrid[0:SHAPE_SIZE, 0:SHAPE_SIZE]
    t = np.sqrt((xi - cx) ** 2 + (yi - cy) ** 2).astype(np.float32) / r

    arr = np.where(t <= 0.72, 1.0, 0.0).astype(np.float32)
    edge = (t > 0.70) & (t < 0.95)
    edge_t = np.clip((t - 0.70) / 0.25, 0, 1)
    base_falloff = (1.0 - edge_t) ** 2.5          # sharper drop-off
    noise = np.random.uniform(-0.25, 0.25, arr.shape).astype(np.float32)
    arr = np.where(edge, np.clip(base_falloff + noise * edge_t * 0.35, 0, 1), arr)
    arr = np.where(t >= 0.95, 0.0, arr)

    return Image.fromarray((arr * 255).astype(np.uint8), "L").filter(
        ImageFilter.GaussianBlur(0.3))


def shape_pencil_outline_soft() -> Image.Image:
    """
    6B-pencil feel: small solid core (30 %), wide diffuse fringe (70 %).
    Lots of tooth, the edge almost dissolves into the paper.
    """
    cx = cy = SHAPE_SIZE // 2
    r = SHAPE_SIZE // 2 - 4
    yi, xi = np.mgrid[0:SHAPE_SIZE, 0:SHAPE_SIZE]
    t = np.sqrt((xi - cx) ** 2 + (yi - cy) ** 2).astype(np.float32) / r

    arr = np.where(t <= 0.30, 1.0, 0.0).astype(np.float32)
    edge = (t > 0.26) & (t < 1.10)
    edge_t = np.clip((t - 0.26) / 0.84, 0, 1)
    base_falloff = (1.0 - edge_t) ** 0.85          # slow, gentle falloff
    noise = np.random.uniform(-0.60, 0.60, arr.shape).astype(np.float32)
    arr = np.where(edge, np.clip(base_falloff + noise * edge_t * 0.65, 0, 1), arr)
    arr = np.where(t >= 1.10, 0.0, arr)

    img = Image.fromarray((arr * 255).astype(np.uint8), "L")
    return img.filter(ImageFilter.GaussianBlur(1.0))


def shape_pencil_outline_scratchy() -> Image.Image:
    """
    Worn-pencil: deliberate skip-gaps even in the core,
    and a jagged fringe, like dragging a blunt pencil fast.
    """
    cx = cy = SHAPE_SIZE // 2
    r = SHAPE_SIZE // 2 - 6
    yi, xi = np.mgrid[0:SHAPE_SIZE, 0:SHAPE_SIZE]
    t = np.sqrt((xi - cx) ** 2 + (yi - cy) ** 2).astype(np.float32) / r

    # Core has random voids (skip marks)
    core_noise = np.random.uniform(0, 1, (SHAPE_SIZE, SHAPE_SIZE)).astype(np.float32)
    arr = np.where(t <= 0.50, np.where(core_noise > 0.12, 1.0, 0.0), 0.0)

    edge = (t > 0.45) & (t < 1.05)
    edge_t = np.clip((t - 0.45) / 0.60, 0, 1)
    base_falloff = (1.0 - edge_t) ** 1.0
    noise = np.random.uniform(-0.80, 0.80, arr.shape).astype(np.float32)
    arr = np.where(edge, np.clip(base_falloff + noise * edge_t * 0.85, 0, 1), arr)
    arr = np.where(t >= 1.05, 0.0, arr)

    img = Image.fromarray((arr * 255).astype(np.uint8), "L")
    return img.filter(ImageFilter.GaussianBlur(0.4))


def shape_pencil_outline_waxy() -> Image.Image:
    """
    Colored-pencil / wax: smooth gradient edge (not noisy),
    slight translucency, like a Prismacolor outline.
    """
    cx = cy = SHAPE_SIZE // 2
    r = SHAPE_SIZE // 2 - 6
    yi, xi = np.mgrid[0:SHAPE_SIZE, 0:SHAPE_SIZE]
    t = np.sqrt((xi - cx) ** 2 + (yi - cy) ** 2).astype(np.float32) / r

    # Smooth sigmoid-style falloff – no harsh noise, just a gentle wax bloom
    arr = np.clip(1.0 - ((t - 0.48) / 0.38), 0, 1) ** 1.8
    # Tiny wax-particle imperfections (much subtler than pencil)
    wax_noise = np.random.uniform(-0.10, 0.10, arr.shape).astype(np.float32)
    arr = np.clip(arr + wax_noise * np.clip(1.0 - t, 0, 1), 0, 1)
    arr = np.where(t >= 1.05, 0.0, arr)

    img = Image.fromarray((arr * 255).astype(np.uint8), "L")
    return img.filter(ImageFilter.GaussianBlur(1.2))


def shape_pencil_outline_charcoal_edge() -> Image.Image:
    """
    Chunky charcoal-edged outline: wide fringe with big dark clumps,
    like a charcoal pencil rubbed on medium-tooth paper.
    """
    cx = cy = SHAPE_SIZE // 2
    r = SHAPE_SIZE // 2 - 4
    yi, xi = np.mgrid[0:SHAPE_SIZE, 0:SHAPE_SIZE]
    t = np.sqrt((xi - cx) ** 2 + (yi - cy) ** 2).astype(np.float32) / r

    arr = np.where(t <= 0.48, 1.0, 0.0).astype(np.float32)

    edge = (t > 0.44) & (t < 1.08)
    edge_t = np.clip((t - 0.44) / 0.64, 0, 1)
    base_falloff = (1.0 - edge_t) ** 0.90
    # Two-scale noise: coarse lumps + fine grit
    coarse = np.random.uniform(-1.0, 1.0, arr.shape).astype(np.float32)
    coarse_blur = np.array(
        Image.fromarray(((coarse + 1) / 2 * 255).astype(np.uint8)).filter(
            ImageFilter.GaussianBlur(4.0)
        )
    ) / 255.0 * 2.0 - 1.0
    fine = np.random.uniform(-0.50, 0.50, arr.shape).astype(np.float32)
    combined = coarse_blur.astype(np.float32) * 0.55 + fine * 0.45
    arr = np.where(edge, np.clip(base_falloff + combined * edge_t * 0.90, 0, 1), arr)
    arr = np.where(t >= 1.08, 0.0, arr)

    img = Image.fromarray((arr * 255).astype(np.uint8), "L")
    return img.filter(ImageFilter.GaussianBlur(0.8))


def shape_pencil_outline_mechanical() -> Image.Image:
    """
    0.5 mm mechanical pencil: near-perfect circle, ultra-thin fringe (4 %),
    extremely consistent – the most pen-like of the set while still
    carrying a microscopic graphite tooth.
    """
    cx = cy = SHAPE_SIZE // 2
    r = SHAPE_SIZE // 2 - 10
    yi, xi = np.mgrid[0:SHAPE_SIZE, 0:SHAPE_SIZE]
    t = np.sqrt((xi - cx) ** 2 + (yi - cy) ** 2).astype(np.float32) / r

    arr = np.where(t <= 0.80, 1.0, 0.0).astype(np.float32)
    edge = (t > 0.78) & (t < 0.98)
    edge_t = np.clip((t - 0.78) / 0.20, 0, 1)
    base_falloff = (1.0 - edge_t) ** 3.0          # very abrupt
    noise = np.random.uniform(-0.18, 0.18, arr.shape).astype(np.float32)
    arr = np.where(edge, np.clip(base_falloff + noise * edge_t * 0.22, 0, 1), arr)
    arr = np.where(t >= 0.98, 0.0, arr)

    return Image.fromarray((arr * 255).astype(np.uint8), "L").filter(
        ImageFilter.GaussianBlur(0.25))


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


def grain_waxy() -> Image.Image:
    """
    Smooth, slightly oily wax-pencil texture: gentle large-scale
    variation with almost no sharp particles – stays behind the stroke.
    """
    base = np.random.uniform(0.82, 1.0, (GRAIN_SIZE, GRAIN_SIZE)).astype(np.float32)
    # Large soft blobs that simulate uneven wax coverage
    blobs = np.random.uniform(0, 1, (GRAIN_SIZE // 8, GRAIN_SIZE // 8)).astype(np.float32)
    blobs_up = np.array(
        Image.fromarray((blobs * 255).astype(np.uint8)).resize(
            (GRAIN_SIZE, GRAIN_SIZE), Image.BILINEAR
        )
    ) / 255.0
    arr = base * 0.70 + blobs_up * 0.30
    arr = np.clip(arr, 0, 1)
    img = Image.fromarray((arr * 255).astype(np.uint8), "L")
    return img.filter(ImageFilter.GaussianBlur(2.0))


def grain_charcoal() -> Image.Image:
    """
    Heavy charcoal grain: coarse dark chunks on a mid-grey base,
    with directional smear marks.
    """
    arr = np.random.uniform(0.55, 0.90, (GRAIN_SIZE, GRAIN_SIZE)).astype(np.float32)
    # Directional smear (horizontal streaks at varying intervals)
    for i in range(0, GRAIN_SIZE, random.randint(4, 9)):
        arr[i, :] *= random.uniform(0.60, 1.0)
    # Chunky dark particles
    chunk_mask = np.random.uniform(0, 1, arr.shape) > 0.80
    arr[chunk_mask] *= np.random.uniform(0.10, 0.45, arr[chunk_mask].shape)
    # Blur to merge chunks into smears
    img = Image.fromarray((np.clip(arr, 0, 1) * 255).astype(np.uint8), "L")
    img = img.filter(ImageFilter.GaussianBlur(1.8))
    # Re-add fine grit on top
    grit = np.random.uniform(-0.06, 0.06, (GRAIN_SIZE, GRAIN_SIZE)).astype(np.float32)
    arr2 = np.clip(np.array(img) / 255.0 + grit, 0, 1)
    return Image.fromarray((arr2 * 255).astype(np.uint8), "L")


def grain_graphite() -> Image.Image:
    """
    Fine graphite/pencil grain: tiny dark particles on a light base,
    with faint directional striations mimicking pencil stroke direction.
    Used on the pencil-outline brushes to add tooth at the edges.
    """
    # Light mid-grey base with subtle uniform noise
    arr = np.random.uniform(0.72, 1.0, (GRAIN_SIZE, GRAIN_SIZE)).astype(np.float32)

    # Very faint horizontal striations (pencil strokes)
    for i in range(0, GRAIN_SIZE, random.randint(2, 5)):
        arr[i, :] *= random.uniform(0.88, 1.0)

    # Sparse dark graphite particles
    particle_mask = np.random.uniform(0, 1, arr.shape) > 0.91
    arr[particle_mask] *= np.random.uniform(0.20, 0.55,
                                            arr[particle_mask].shape)

    # Tiny clusters – blur by a very small amount then re-sharpen
    blurred = np.array(
        Image.fromarray((arr * 255).astype(np.uint8)).filter(
            ImageFilter.GaussianBlur(0.4)
        )
    ) / 255.0
    arr = arr * 0.55 + blurred * 0.45

    img = Image.fromarray((np.clip(arr, 0, 1) * 255).astype(np.uint8), "L")
    return img


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

    # ── Pencil-outline brushes ────────────────────────────────
    # Pen-like flow + pencil-tooth edges
    {
        "filename": "Outline_Pencil_Fine.brush",
        "shape_fn": shape_pencil_outline_fine,
        "grain_fn": grain_graphite,
        "archive_props": {
            "name": "Outline Pencil Fine",
            # tight spacing + high streamline = pen-like smooth lines
            "spacing": 0.015,
            "streamlineAmount": 0.82,
            "size": 30.0,
            "opacity": 0.96,
            "bleed": 0.02,
            "jitter": 0.0,
            "count": 1,
            "scatterX": 0.0,
            "scatterY": 0.0,
            # subtle graphite grain on the stroke edge
            "grainZoom": 0.30,
            "grainMoveAmount": 0.08,
            "grainRotation": 0.0,
        },
    },
    {
        "filename": "Outline_Pencil_Medium.brush",
        "shape_fn": shape_pencil_outline_round,
        "grain_fn": grain_graphite,
        "archive_props": {
            "name": "Outline Pencil Medium",
            "spacing": 0.018,
            "streamlineAmount": 0.75,
            "size": 55.0,
            "opacity": 0.93,
            "bleed": 0.03,
            "jitter": 0.0,
            "count": 1,
            "scatterX": 0.0,
            "scatterY": 0.0,
            "grainZoom": 0.35,
            "grainMoveAmount": 0.10,
            "grainRotation": 0.0,
        },
    },
    {
        "filename": "Outline_Pencil_Tapered.brush",
        "shape_fn": shape_pencil_outline_tapered,
        "grain_fn": grain_graphite,
        "archive_props": {
            "name": "Outline Pencil Tapered",
            # slightly lower streamline so the taper responds to speed
            "spacing": 0.016,
            "streamlineAmount": 0.68,
            "size": 60.0,
            "opacity": 0.91,
            "bleed": 0.04,
            "jitter": 0.01,
            "count": 1,
            "scatterX": 0.0,
            "scatterY": 0.0,
            "grainZoom": 0.40,
            "grainMoveAmount": 0.12,
            "grainRotation": 0.0,
        },
    },
    {
        "filename": "Outline_Pencil_Bold.brush",
        "shape_fn": shape_pencil_outline_bold,
        "grain_fn": grain_graphite,
        "archive_props": {
            "name": "Outline Pencil Bold",
            "spacing": 0.020,
            "streamlineAmount": 0.65,
            "size": 90.0,
            "opacity": 0.90,
            "bleed": 0.05,
            "jitter": 0.01,
            "count": 1,
            "scatterX": 0.0,
            "scatterY": 0.0,
            # more grain visible on the wider stroke
            "grainZoom": 0.45,
            "grainMoveAmount": 0.15,
            "grainRotation": 0.0,
        },
    },
    {
        "filename": "Outline_Pencil_Hard.brush",
        "shape_fn": shape_pencil_outline_hard,
        "grain_fn": grain_graphite,
        "archive_props": {
            "name": "Outline Pencil Hard",
            # near-pen precision; very high streamline, barely any tooth
            "spacing": 0.012,
            "streamlineAmount": 0.88,
            "size": 35.0,
            "opacity": 0.98,
            "bleed": 0.01,
            "jitter": 0.0,
            "count": 1,
            "scatterX": 0.0,
            "scatterY": 0.0,
            "grainZoom": 0.22,
            "grainMoveAmount": 0.05,
            "grainRotation": 0.0,
        },
    },
    {
        "filename": "Outline_Pencil_Soft.brush",
        "shape_fn": shape_pencil_outline_soft,
        "grain_fn": grain_graphite,
        "archive_props": {
            "name": "Outline Pencil Soft",
            # 6B feel – lower streamline lets the edge breathe
            "spacing": 0.022,
            "streamlineAmount": 0.58,
            "size": 70.0,
            "opacity": 0.85,
            "bleed": 0.06,
            "jitter": 0.01,
            "count": 1,
            "scatterX": 0.0,
            "scatterY": 0.0,
            "grainZoom": 0.50,
            "grainMoveAmount": 0.18,
            "grainRotation": 0.0,
        },
    },
    {
        "filename": "Outline_Pencil_Scratchy.brush",
        "shape_fn": shape_pencil_outline_scratchy,
        "grain_fn": grain_graphite,
        "archive_props": {
            "name": "Outline Pencil Scratchy",
            # moderate streamline so stroke wobble shows naturally
            "spacing": 0.025,
            "streamlineAmount": 0.45,
            "size": 50.0,
            "opacity": 0.88,
            "bleed": 0.08,
            "jitter": 0.02,
            "count": 1,
            "scatterX": 0.0,
            "scatterY": 0.0,
            "grainZoom": 0.38,
            "grainMoveAmount": 0.20,
            "grainRotation": 0.0,
        },
    },
    {
        "filename": "Outline_Pencil_Waxy.brush",
        "shape_fn": shape_pencil_outline_waxy,
        "grain_fn": grain_waxy,
        "archive_props": {
            "name": "Outline Pencil Waxy",
            # smooth, steady flow like a colored pencil
            "spacing": 0.016,
            "streamlineAmount": 0.72,
            "size": 65.0,
            "opacity": 0.87,
            "bleed": 0.04,
            "jitter": 0.0,
            "count": 1,
            "scatterX": 0.0,
            "scatterY": 0.0,
            "grainZoom": 0.60,
            "grainMoveAmount": 0.08,
            "grainRotation": 0.0,
        },
    },
    {
        "filename": "Outline_Pencil_Charcoal_Edge.brush",
        "shape_fn": shape_pencil_outline_charcoal_edge,
        "grain_fn": grain_charcoal,
        "archive_props": {
            "name": "Outline Pencil Charcoal Edge",
            # looser flow to let the chunky fringe breathe
            "spacing": 0.028,
            "streamlineAmount": 0.55,
            "size": 85.0,
            "opacity": 0.84,
            "bleed": 0.10,
            "jitter": 0.02,
            "count": 1,
            "scatterX": 0.0,
            "scatterY": 0.0,
            "grainZoom": 0.55,
            "grainMoveAmount": 0.22,
            "grainRotation": 0.0,
        },
    },
    {
        "filename": "Outline_Pencil_Mechanical.brush",
        "shape_fn": shape_pencil_outline_mechanical,
        "grain_fn": grain_graphite,
        "archive_props": {
            "name": "Outline Pencil Mechanical",
            # most pen-like of the set; highest streamline
            "spacing": 0.010,
            "streamlineAmount": 0.90,
            "size": 25.0,
            "opacity": 0.99,
            "bleed": 0.0,
            "jitter": 0.0,
            "count": 1,
            "scatterX": 0.0,
            "scatterY": 0.0,
            "grainZoom": 0.18,
            "grainMoveAmount": 0.03,
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


def _make_delivery_zip(zip_path: str, sets: dict) -> None:
    """
    Create one ZIP containing unzipped .brushset folders, each holding
    unzipped .brush folders with their raw files.

    Structure inside the ZIP:
        Outline_Pencil_Brushes.brushset/
            Outline_Pencil_Fine.brush/
                Brush.archive
                Shape.png
                Grain.png
            ...
        All_Procreate_Brushes.brushset/
            Ink_Smooth.brush/
                Brush.archive
                Shape.png
            ...

    sets: {"FolderName.brushset": [list of .brush file paths], ...}
    """
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for set_folder, brush_paths in sets.items():
            for brush_path in brush_paths:
                brush_name = os.path.basename(brush_path)
                with zipfile.ZipFile(brush_path, "r") as bz:
                    for entry in bz.namelist():
                        zf.writestr(
                            f"{set_folder}/{brush_name}/{entry}",
                            bz.read(entry)
                        )


def main():
    print(f"Generating {len(BRUSHES)} Procreate brushes → {OUTPUT_DIR}/\n")
    all_paths: list[str] = []
    outline_paths: list[str] = []
    for brush in BRUSHES:
        path = build_brush_file(brush, OUTPUT_DIR)
        all_paths.append(path)
        if brush["filename"].startswith("Outline_Pencil"):
            outline_paths.append(path)
        has_grain = brush["grain_fn"] is not None
        grain_tag = "shape + grain" if has_grain else "shape only"
        print(f"  ✓  {brush['filename']}  ({grain_tag})")

    # ── Single delivery ZIP: two unzipped .brushset folders inside ──
    delivery_zip = "procreate_brushes.zip"
    _make_delivery_zip(delivery_zip, {
        "Outline_Pencil_Brushes.brushset": outline_paths,
        "All_Procreate_Brushes.brushset":  all_paths,
    })

    kb = os.path.getsize(delivery_zip) // 1024
    print(f"\nDelivery ZIP: {delivery_zip}  ({kb} KB)")
    print(f"  Outline_Pencil_Brushes.brushset/  ({len(outline_paths)} brushes)")
    print(f"  All_Procreate_Brushes.brushset/   ({len(all_paths)} brushes)")
    print(f"\nUnzip → import each .brushset folder into Procreate.")


if __name__ == "__main__":
    main()
