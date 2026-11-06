#!/usr/bin/env python3
"""dds_alpha_fix.py <quellordner> [jobs] [toleranz=140] [anteil=0.01]

Korrigiert Alpha-Ueberschwinger in Ersatztexturen fuer PCSX2. Auf der PS2 ist Alpha 128 voll deckend; hochskalierte
Pakete schwingen an Kanten oft auf 129..140 ueber. PCSX2 merkt sich dann "Alpha > 1.0" im Zielbild und rechnet alle
folgenden Draws mit Ziel-Alpha im Shader mit Barrieren (GSRendererHW: rt_new_alpha_max > 128) -> Ruckler.

Regel: liegen hoechstens <anteil> der Pixel ueber <toleranz>, war die Absicht 128 -> alle Werte > 128 werden 128.
Texturen mit echtem, grossflaechig hohem Alpha bleiben unveraendert. Farben bleiben; BC3/BC7 werden neu kodiert
(alle Mipmap-Stufen), PNG direkt. Original bleibt als <name>.vor_alphafix. Schreibt die .alpha-Zusatzdatei neu.
Braucht: etcpak texture2ddecoder pillow numpy
"""
import os, struct, sys
from multiprocessing import Pool

TOL, ANTEIL = 140, 0.01


def blocks(w, h):
    return max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * 16


def init(tol, anteil):
    global TOL, ANTEIL
    TOL, ANTEIL = tol, anteil


def fix(path):
    import numpy as np, etcpak, texture2ddecoder
    from PIL import Image
    try:
        if os.path.exists(path + '.vor_alphafix'):
            return (path, 'schon')
        low = path.lower()
        if low.endswith('.png'):
            img = Image.open(path).convert('RGBA'); a = np.asarray(img).copy()
            al = a[..., 3]
            if al.max() <= 128: return (path, 'ok128')
            if (al > TOL).mean() > ANTEIL: return (path, 'echt')
            al[al > 128] = 128
            os.replace(path, path + '.vor_alphafix')
            Image.fromarray(a, 'RGBA').save(path)
            open(path + '.alpha', 'wb').write(bytes([int(al.min()), int(al.max())]))
            return (path, 'korrigiert')
        d = open(path, 'rb').read()
        if d[:4] != b'DDS ': return (path, 'kein DDS')
        h, w = struct.unpack_from('<II', d, 12)
        mips = max(1, struct.unpack_from('<I', d, 28)[0])
        fourcc = d[84:88]
        if fourcc == b'DX10':
            fmt = struct.unpack_from('<I', d, 128)[0]; hdr = 148
            if fmt not in (98, 99): return (path, 'format')
            dec, enc = texture2ddecoder.decode_bc7, (lambda r, x, y: etcpak.compress_bc7(r, x, y, None))
        elif fourcc == b'DXT5':
            hdr = 128; dec, enc = texture2ddecoder.decode_bc3, etcpak.compress_bc3
        else:
            return (path, 'format')
        w4, h4 = (w + 3) // 4 * 4, (h + 3) // 4 * 4
        base = np.frombuffer(dec(d[hdr:hdr + blocks(w, h)], w4, h4), np.uint8).reshape(h4, w4, 4)[..., [2, 1, 0, 3]].copy()
        al = base[:h, :w, 3]
        if al.max() <= 128: return (path, 'ok128')
        if (al > TOL).mean() > ANTEIL: return (path, 'echt')
        base[..., 3][base[..., 3] > 128] = 128
        img = Image.fromarray(base[:h, :w], 'RGBA')
        out = []; cw, ch = w, h
        for lvl in range(mips):
            if lvl:
                cw, ch = max(1, cw // 2), max(1, ch // 2)
            bw, bh = max(4, (cw + 3) // 4 * 4), max(4, (ch + 3) // 4 * 4)
            im = img if lvl == 0 else img.resize((cw, ch), Image.LANCZOS)
            if (bw, bh) != im.size:
                pad = Image.new('RGBA', (bw, bh)); pad.paste(im, (0, 0)); im = pad
            arr = np.asarray(im).copy(); arr[..., 3][arr[..., 3] > 128] = 128  # auch nach dem Verkleinern
            out.append(enc(arr.tobytes(), bw, bh)[:blocks(cw, ch)])
        tmp = path + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(d[:hdr]); f.write(b''.join(out))
        os.replace(path, path + '.vor_alphafix')
        os.replace(tmp, path)
        a2 = base[:h, :w, 3]
        open(path + '.alpha', 'wb').write(bytes([int(a2.min()), int(a2.max())]))
        return (path, 'korrigiert')
    except Exception as e:
        return (path, 'Fehler: %s' % e)


def main():
    global TOL, ANTEIL
    root = sys.argv[1]
    jobs = int(sys.argv[2]) if len(sys.argv) > 2 else os.cpu_count()
    if len(sys.argv) > 3: TOL = int(sys.argv[3])
    if len(sys.argv) > 4: ANTEIL = float(sys.argv[4])
    # gemeldete Alpha-Werte aus einem frueheren Versuch zuruecksetzen (.alpha.roh = echter Wert)
    for d, _, fs in os.walk(root):
        for f in fs:
            if f.endswith('.alpha.roh'):
                os.replace(os.path.join(d, f), os.path.join(d, f[:-4]))
    files = [os.path.join(d, f) for d, _, fs in os.walk(root) for f in fs if f.lower().endswith(('.dds', '.png'))]
    files.sort(key=lambda p: -os.path.getsize(p))
    print('%d Texturen, %d Prozesse, Toleranz %d, Anteil %.3f' % (len(files), jobs, TOL, ANTEIL), flush=True)
    stat = {}
    with Pool(jobs, initializer=init, initargs=(TOL, ANTEIL)) as pool:
        for p, r in pool.imap_unordered(fix, files, chunksize=1):
            k = r.split(' ')[0]; stat[k] = stat.get(k, 0) + 1
            if k.startswith('Fehler'): print('  %s: %s' % (p, r), flush=True)
    print('fertig:', stat)


if __name__ == '__main__':
    main()
