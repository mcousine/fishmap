#!/usr/bin/env python3.11
"""Generate PWA icons 192x192 and 512x512."""
from PIL import Image, ImageDraw, ImageFont
import math

def make_icon(size):
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # Dark background circle
    d.ellipse([0, 0, size-1, size-1], fill=(10, 14, 26, 255))
    # Cyan glow ring
    ring = size * 0.04
    d.ellipse([ring, ring, size-1-ring, size-1-ring], outline=(6, 182, 212, 180), width=max(2, size//50))
    # Simple fish shape using polygon
    cx, cy = size // 2, size // 2
    s = size * 0.28  # scale
    # Fish body (ellipse)
    d.ellipse([cx - s*1.1, cy - s*0.55, cx + s*0.6, cy + s*0.55], fill=(6, 182, 212, 255))
    # Tail (triangle)
    tail_pts = [
        (cx + s*0.5,  cy - s*0.55),
        (cx + s*1.5,  cy - s*0.9),
        (cx + s*1.5,  cy + s*0.9),
        (cx + s*0.5,  cy + s*0.55),
    ]
    d.polygon(tail_pts, fill=(6, 182, 212, 200))
    # Eye
    ex, ey = int(cx - s*0.55), int(cy - s*0.12)
    er = max(2, int(size * 0.028))
    d.ellipse([ex-er, ey-er, ex+er, ey+er], fill=(255, 255, 255, 255))
    d.ellipse([ex-er//2, ey-er//2, ex+er//2, ey+er//2], fill=(10, 14, 26, 255))
    # Depth line (wavy)
    wy = cy + int(s * 0.9)
    pts = []
    for i in range(size):
        pts.append((i, wy + int(math.sin(i / (size/8) * math.pi) * size * 0.018)))
    d.line(pts, fill=(59, 130, 246, 120), width=max(1, size//80))
    return img

for sz in (192, 512):
    img = make_icon(sz)
    img.save(f'/Users/michelcousineau/Downloads/fishmap/icon-{sz}.png')
    print(f'icon-{sz}.png created')
