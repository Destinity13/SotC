#!/usr/bin/env python3
"""dds_alpha.py <quellordner> [jobs]

Schreibt zu jeder BC1/BC3/BC7-Ersatztextur (.dds) eine Zusatzdatei <name>.dds.alpha mit 2 Bytes: kleinster und
groesster Alphawert der vollen Aufloesung (Stufe 0, inkl. Randbloecke) - genau der Wert, den PCSX2 beim Laden sonst
durch komplettes Entpacken auf der CPU bestimmt (GSTextureReplacements::GetBCAlphaMinMax). Die GS-Bruecke liest die
Zusatzdatei und spart sich das Entpacken. Vorhandene, aktuelle Zusatzdateien werden uebersprungen.
Braucht: texture2ddecoder numpy
"""
import os, struct, sys
from multiprocessing import Pool


def alpha(path):
    import numpy as np, texture2ddecoder
    try:
        side = path + '.alpha'
        if os.path.exists(side) and os.path.getmtime(side) >= os.path.getmtime(path):
            return (path, 'aktuell')
        d = open(path, 'rb').read()
        if d[:4] != b'DDS ':
            return (path, 'kein DDS')
        h, w = struct.unpack_from('<II', d, 12)
        fourcc = d[84:88]
        hdr, bb = 128, 16
        if fourcc == b'DX10':
            fmt = struct.unpack_from('<I', d, 128)[0]; hdr = 148
            if fmt in (98, 99): dec = texture2ddecoder.decode_bc7
            elif fmt in (77, 78): dec = texture2ddecoder.decode_bc3
            elif fmt in (71, 72): dec, bb = texture2ddecoder.decode_bc1, 8
            else: return (path, 'Format %d uebersprungen' % fmt)
        elif fourcc == b'DXT5': dec = texture2ddecoder.decode_bc3
        elif fourcc == b'DXT1': dec, bb = texture2ddecoder.decode_bc1, 8
        else: return (path, 'Format %r uebersprungen' % fourcc)
        w4, h4 = (w + 3) // 4 * 4, (h + 3) // 4 * 4
        n0 = (w4 // 4) * (h4 // 4) * bb
        px = np.frombuffer(dec(d[hdr:hdr + n0], w4, h4), np.uint8).reshape(-1, 4)  # BGRA
        a = px[:, 3]
        with open(side + '.tmp', 'wb') as f:
            f.write(bytes([int(a.min()), int(a.max())]))
        os.replace(side + '.tmp', side)
        return (path, 'ok')
    except Exception as e:
        return (path, 'Fehler: %s' % e)


def main():
    root = sys.argv[1]
    jobs = int(sys.argv[2]) if len(sys.argv) > 2 else os.cpu_count()
    files = [os.path.join(d, f) for d, _, fs in os.walk(root) for f in fs if f.lower().endswith('.dds')]
    print('%d DDS-Dateien, %d Prozesse' % (len(files), jobs), flush=True)
    stat = {}
    with Pool(jobs) as pool:
        for p, r in pool.imap_unordered(alpha, files, chunksize=4):
            k = r.split(' ')[0]; stat[k] = stat.get(k, 0) + 1
            if k not in ('ok', 'aktuell'):
                print('  %s: %s' % (os.path.relpath(p, root), r), flush=True)
    print('fertig:', stat)


if __name__ == '__main__':
    main()
