#!/usr/bin/env python3
"""dds_kappen.py <quellordner> [max_kante=2048]

Kappt DDS-Ersatztexturen mit Mipmaps auf eine maximale Kantenlaenge, indem die obersten Stufen weggelassen werden
(die naechstkleinere, schon vorhandene Stufe wird zur Grundstufe - keine Neuberechnung, keine Qualitaetsverluste
ausser der Aufloesung). Sinnvoll, wenn das Paket fuer 8x gemacht ist, aber mit 4x gespielt wird: die Grafikkarte
liest dann ein Viertel der Daten. Die volle Fassung bleibt als <name>.dds.voll erhalten (Ruecknahme: umbenennen).
Danach tools/dds_alpha.py erneut laufen lassen (die .alpha-Werte werden fuer die neue Grundstufe neu berechnet).
"""
import os, struct, sys


def blocks(w, h, bb):
    return max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * bb


def kappen(path, limit):
    d = open(path, 'rb').read()
    if d[:4] != b'DDS ':
        return 'kein DDS'
    h, w = struct.unpack_from('<II', d, 12)
    mips = struct.unpack_from('<I', d, 28)[0]
    if max(w, h) <= limit:
        return 'klein genug'
    if mips <= 1:
        return 'ohne Mipmaps (erst dds_mipmaps.py)'
    fourcc = d[84:88]
    hdr, bb = 128, 16
    if fourcc == b'DX10':
        hdr = 148
        fmt = struct.unpack_from('<I', d, 128)[0]
        if fmt in (71, 72): bb = 8
    elif fourcc == b'DXT1':
        bb = 8
    off = hdr; cw, ch = w, h; drop = 0
    while max(cw, ch) > limit and drop < mips - 1:
        off += blocks(cw, ch, bb)
        cw, ch = max(1, cw // 2), max(1, ch // 2); drop += 1
    head = bytearray(d[:hdr])
    struct.pack_into('<II', head, 12, ch, cw)                  # Hoehe, Breite
    struct.pack_into('<I', head, 20, blocks(cw, ch, bb))        # pitchOrLinearSize (Grundstufe)
    struct.pack_into('<I', head, 28, mips - drop)
    tmp = path + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(bytes(head)); f.write(d[off:])
    if not os.path.exists(path + '.voll'):
        os.replace(path, path + '.voll')
    os.replace(tmp, path)
    return 'gekappt %dx%d -> %dx%d' % (w, h, cw, ch)


def main():
    root = sys.argv[1]
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 2048
    stat = {}; vorher = nachher = 0
    for d, _, fs in os.walk(root):
        for f in fs:
            if not f.lower().endswith('.dds'):
                continue
            p = os.path.join(d, f)
            vorher += os.path.getsize(p)
            r = kappen(p, limit)
            nachher += os.path.getsize(p)
            k = r.split(' ')[0]; stat[k] = stat.get(k, 0) + 1
            if k not in ('gekappt', 'klein'):
                print('  %s: %s' % (os.path.relpath(p, root), r))
    print('fertig:', stat, 'Groesse %.2f GB -> %.2f GB' % (vorher / 1e9, nachher / 1e9))


if __name__ == '__main__':
    main()
