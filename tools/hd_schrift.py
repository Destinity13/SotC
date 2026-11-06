#!/usr/bin/env python3
"""HD-Schrift/Logos ohne KI: 4x hochrechnen, Buchstabenkanten wie Vektorschrift formen, weiche Schatten/Glow behalten.
Aufruf: hd_schrift.py <zielordner> <dump.png> ..."""
import sys, os
import numpy as np
from PIL import Image
S = 4

def blur(x, sig):
    r = int(sig * 3) + 1
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sig) ** 2); k /= k.sum()
    x = np.apply_along_axis(lambda v: np.convolve(np.pad(v, r, mode='edge'), k, 'valid'), 1, x)
    return np.apply_along_axis(lambda v: np.convolve(np.pad(v, r, mode='edge'), k, 'valid'), 0, x)

def up(x, w, h):
    return np.asarray(Image.fromarray(x.astype(np.float32), 'F').resize((w * S, h * S), Image.LANCZOS))

def hd(src):
    im = Image.open(src).convert('RGBA'); w, h = im.size
    a = np.asarray(im, dtype=np.float32) / 255.0
    al = a[..., 3]; pre = a[..., :3] * al[..., None]
    A = np.clip(blur(np.clip(up(al, w, h), 0, 1), 1.6), 0, 1)
    P = np.stack([np.clip(blur(np.clip(up(pre[..., i], w, h), 0, 1), 1.6), 0, 1) for i in range(3)], -1)
    # Farbe: wo Alpha klein ist, Farbe aus grossem Umkreis (keine dunklen Raender beim Filtern)
    Af = blur(A, 6.0); Pf = np.stack([blur(P[..., i], 6.0) for i in range(3)], -1)
    rgb = np.where(A[..., None] > 0.03, P / np.maximum(A[..., None], 1e-4), Pf / np.maximum(Af[..., None], 1e-4))
    rgb = np.clip(rgb, 0, 1)
    amax = float(A.max()) if A.max() > 0 else 1.0
    t = np.clip((A / amax - 0.40) / 0.20, 0, 1); edge = (t * t * (3 - 2 * t)) * amax
    # Nur helle Pixel (Buchstaben) bekommen harte Kanten, dunkle Schatten/Glow bleiben weich
    lum = (rgb[..., 0] * 0.3 + rgb[..., 1] * 0.59 + rgb[..., 2] * 0.11)
    wgt = np.clip((lum - 0.35) / 0.25, 0, 1)
    An = A * (1 - wgt) + np.maximum(edge, A * 0.0) * wgt
    out = np.concatenate([rgb, An[..., None]], -1)
    return Image.fromarray((out * 255 + 0.5).astype(np.uint8), 'RGBA')

if __name__ == '__main__':
    os.makedirs(sys.argv[1], exist_ok=True)
    for f in sys.argv[2:]:
        hd(f).save(os.path.join(sys.argv[1], os.path.basename(f)))
