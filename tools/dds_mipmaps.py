#!/usr/bin/env python3
"""dds_mipmaps.py <quellordner> [jobs]

Ergaenzt fehlende Mipmaps in DDS-Ersatztexturen (BC3/DXT5 und BC7), damit die Grafikkarte entfernte Flaechen aus
kleinen Vorstufen liest statt aus der vollen 4K-Textur (Texturpakete ohne Mipmaps bremsen v. a. Titelbild und
Fernsicht; PCSX2 kann fuer komprimierte Ersatztexturen keine Mipmaps selbst erzeugen).

- Stufe 0 (volle Aufloesung) bleibt BITGENAU erhalten, nur die kleineren Stufen werden neu kodiert.
- Arbeitet an Ort und Stelle; das Original jeder geaenderten Datei bleibt als <name>.dds.ohne_mips daneben
  (PCSX2 laedt nur .dds/.png, die Sicherungen stoeren nicht). Bereits umgewandelte Dateien werden uebersprungen.
Braucht: pip install --user etcpak texture2ddecoder pillow numpy
"""
import os, struct, sys
from multiprocessing import Pool

DDSD_MIPMAPCOUNT = 0x20000
DDSCAPS_COMPLEX, DDSCAPS_MIPMAP = 0x8, 0x400000


def blocks(w, h):
    return max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * 16


def umwandeln(path):
    import numpy as np
    import etcpak, texture2ddecoder
    from PIL import Image
    try:
        data = open(path, 'rb').read()
        if data[:4] != b'DDS ':
            return (path, 'kein DDS')
        h, w = struct.unpack_from('<II', data, 12)
        mips = struct.unpack_from('<I', data, 28)[0]
        if mips > 1:
            return (path, 'hat schon Mipmaps')
        fourcc = data[84:88]
        hdr = 128
        if fourcc == b'DX10':
            fmt = struct.unpack_from('<I', data, 128)[0]
            hdr = 148
            if fmt not in (98, 99):
                return (path, 'DX10-Format %d nicht unterstuetzt' % fmt)
            kind = 'bc7'
        elif fourcc == b'DXT5':
            kind = 'bc3'
        else:
            return (path, 'Format %r nicht unterstuetzt' % fourcc)
        lvl0 = data[hdr:hdr + blocks(w, h)]
        dec = texture2ddecoder.decode_bc7 if kind == 'bc7' else texture2ddecoder.decode_bc3
        bgra = np.frombuffer(dec(lvl0, w, h), np.uint8).reshape(h, w, 4)
        rgba = bgra[..., [2, 1, 0, 3]]
        img = Image.fromarray(rgba, 'RGBA')
        out = [lvl0]
        cw, ch = w, h
        while cw > 4 or ch > 4:
            cw, ch = max(1, cw // 2), max(1, ch // 2)
            # auf Blockgroesse auffuellen (BC arbeitet in 4x4-Bloecken)
            bw, bh = max(4, (cw + 3) // 4 * 4), max(4, (ch + 3) // 4 * 4)
            small = img.resize((cw, ch), Image.LANCZOS)
            if (bw, bh) != (cw, ch):
                pad = Image.new('RGBA', (bw, bh)); pad.paste(small, (0, 0)); small = pad
            raw = small.tobytes()
            enc = etcpak.compress_bc7(raw, bw, bh, None) if kind == 'bc7' else etcpak.compress_bc3(raw, bw, bh)
            out.append(enc[:blocks(cw, ch)] if len(enc) >= blocks(cw, ch) else enc)
        n = len(out)
        head = bytearray(data[:hdr])
        flags = struct.unpack_from('<I', head, 8)[0] | DDSD_MIPMAPCOUNT
        struct.pack_into('<I', head, 8, flags)
        struct.pack_into('<I', head, 28, n)
        caps = struct.unpack_from('<I', head, 108)[0] | DDSCAPS_COMPLEX | DDSCAPS_MIPMAP
        struct.pack_into('<I', head, 108, caps)
        tmp = path + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(bytes(head)); f.write(b''.join(out))
        os.replace(path, path + '.ohne_mips')
        os.replace(tmp, path)
        return (path, 'ok %d Stufen' % n)
    except Exception as e:  # eine kaputte Datei darf den Rest nicht aufhalten
        return (path, 'Fehler: %s' % e)


def main():
    root = sys.argv[1]
    jobs = int(sys.argv[2]) if len(sys.argv) > 2 else os.cpu_count()
    files = []
    for d, _, fs in os.walk(root):
        for f in fs:
            if f.lower().endswith('.dds') and not os.path.exists(os.path.join(d, f) + '.ohne_mips'):
                files.append(os.path.join(d, f))
    files.sort(key=lambda p: -os.path.getsize(p))  # grosse zuerst (bessere Auslastung)
    print('%d DDS-Dateien ohne Mipmaps, %d Prozesse' % (len(files), jobs), flush=True)
    stat = {}
    with Pool(jobs) as pool:
        for i, (p, r) in enumerate(pool.imap_unordered(umwandeln, files, chunksize=1), 1):
            k = r.split(' ')[0]
            stat[k] = stat.get(k, 0) + 1
            if not r.startswith('ok'):
                print('  %s: %s' % (os.path.relpath(p, root), r), flush=True)
            if i % 100 == 0:
                print('  %d/%d' % (i, len(files)), flush=True)
    print('fertig:', stat)


if __name__ == '__main__':
    main()
