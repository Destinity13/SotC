#!/usr/bin/env python3
"""png2dds.py <quellordner> [jobs] [min_kante=256]

Wandelt PNG-Ersatztexturen in BC7-DDS mit Mipmaps (DX10-Kopf, Format 98) und schreibt die .alpha-Zusatzdatei.
Grund: PNGs werden beim Vorladen einzeln dekodiert (3 GB RGBA fuer das Origami-Paket, ein Thread, ~20 s, dazu
4x RAM) - DDS wird nur kopiert. BC7 ist fuer UI/Schrift praktisch verlustfrei. Kleine PNGs (Kante < min_kante,
z. B. Tastensymbole) bleiben PNG. Das Original bleibt als <name>.png.voll (wird von PCSX2 nicht geladen).
Braucht: etcpak pillow numpy
"""
import os, struct, sys
from multiprocessing import Pool

MIN = 256


def dds_header(w, h, mips, size0):
    DDSD = 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000 | 0x20000  # CAPS|HEIGHT|WIDTH|PIXELFORMAT|LINEARSIZE|MIPMAPCOUNT
    h_ = struct.pack('<4sIIIIIII', b'DDS ', 124, DDSD, h, w, size0, 1, mips) + b'\0' * 44
    pf = struct.pack('<II4sIIIII', 32, 0x4, b'DX10', 0, 0, 0, 0, 0)
    caps = struct.pack('<IIIII', 0x1000 | 0x8 | 0x400000, 0, 0, 0, 0)
    dx10 = struct.pack('<IIIII', 98, 3, 0, 1, 0)  # BC7_UNORM, TEXTURE2D, arraySize 1
    return h_ + pf + caps + dx10


def wandeln(path):
    import numpy as np, etcpak
    from PIL import Image
    try:
        img = Image.open(path).convert('RGBA')
        w, h = img.size
        if max(w, h) < MIN:
            return (path, 'klein (bleibt PNG)')
        if w % 4 or h % 4:
            return (path, 'Kante nicht durch 4 teilbar (bleibt PNG)')
        levels = []; cw, ch = w, h; cur = img
        while True:
            raw = cur.tobytes()
            levels.append(etcpak.compress_bc7(raw, cw, ch, None))
            if cw <= 4 and ch <= 4:
                break
            cw, ch = max(1, cw // 2), max(1, ch // 2)
            bw, bh = max(4, (cw + 3) // 4 * 4), max(4, (ch + 3) // 4 * 4)
            small = img.resize((cw, ch), Image.LANCZOS)
            if (bw, bh) != (cw, ch):
                pad = Image.new('RGBA', (bw, bh)); pad.paste(small, (0, 0)); small = pad
            cur = small; cw, ch = bw, bh
            if bw <= 4 and bh <= 4:
                levels.append(etcpak.compress_bc7(cur.tobytes(), bw, bh, None)); break
        out = path[:-4] + '.dds'
        with open(out + '.tmp', 'wb') as f:
            f.write(dds_header(w, h, len(levels), len(levels[0]))); f.write(b''.join(levels))
        os.replace(out + '.tmp', out)
        a = np.asarray(img)[..., 3]
        with open(out + '.alpha', 'wb') as f:
            f.write(bytes([int(a.min()), int(a.max())]))
        os.replace(path, path + '.voll')
        return (path, 'ok %dx%d %d Stufen' % (w, h, len(levels)))
    except Exception as e:
        return (path, 'Fehler: %s' % e)


def main():
    global MIN
    root = sys.argv[1]
    jobs = int(sys.argv[2]) if len(sys.argv) > 2 else os.cpu_count()
    if len(sys.argv) > 3: MIN = int(sys.argv[3])
    files = [os.path.join(d, f) for d, _, fs in os.walk(root) for f in fs if f.lower().endswith('.png')]
    files.sort(key=lambda p: -os.path.getsize(p))
    print('%d PNGs, %d Prozesse' % (len(files), jobs), flush=True)
    stat = {}
    with Pool(jobs) as pool:
        for i, (p, r) in enumerate(pool.imap_unordered(wandeln, files, chunksize=1), 1):
            k = r.split(' ')[0]; stat[k] = stat.get(k, 0) + 1
            if k not in ('ok', 'klein'):
                print('  %s: %s' % (os.path.relpath(p, root), r), flush=True)
            if i % 50 == 0: print('  %d/%d' % (i, len(files)), flush=True)
    print('fertig:', stat)


if __name__ == '__main__':
    main()
