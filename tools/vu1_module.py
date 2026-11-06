#!/usr/bin/env python3
"""vu1_module.py <MANAGER.XFF> <mitschnittordner> <zielordner>

Liest alle VU1-Mikroprogramm-Module aus den Spieldateien (VIF-MPG-Ketten ab Adresse 0, SotC: MANAGER.XFF) und legt sie
als Mikrospeicher-Abbilder vu1_<hash>.bin ab, damit tools/vu1rec.py sie vorab uebersetzen kann - ohne dass jemand das
Spiel bis dorthin spielen muss. Der Speicher oberhalb eines Moduls kommt aus einem mitgeschnittenen Abbild, dessen
Inhalt dort von den meisten Abbildern geteilt wird (gemeinsamer Code). Index-Eintraege: Sprungtabelle des Moduls.
Die Abbilder stammen aus dem eigenen Dump des Nutzers und gehoeren nicht ins Repository."""
import glob, os, struct, sys, collections

def fnv(b):
    h = 1469598103934665603
    for x in b:
        h = ((h ^ x) * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return h

def mpg(d, i):
    if i + 4 > len(d): return None
    w = struct.unpack_from("<I", d, i)[0]
    if (w >> 24) & 0x7F != 0x4A: return None
    return ((w >> 16) & 0xFF or 256, (w & 0x3FF) * 8)

def chains(d):
    out = []; i = 0
    while i < len(d) - 8:
        m = mpg(d, i)
        if m and m[1] == 0 and m[0] >= 32:
            ch = []; j = i
            while True:
                m = mpg(d, j)
                if not m: break
                ch.append((j, m[0], m[1])); j += 4 + m[0] * 8
                k = 0
                while k < 4 and mpg(d, j) is None and j + 4 <= len(d) and (d[j + 3] & 0x7F) in (0x00, 0x10, 0x11, 0x13, 0x14, 0x17):
                    j += 4; k += 1
                if mpg(d, j) is None or mpg(d, j)[1] == 0: break
            # plausibel: Teile lueckenlos ab 0
            pos = 0; ok = True
            for _, n, a in ch:
                if a != pos: ok = False; break
                pos = a + n * 8
            if ok:
                out.append(ch); i = j; continue
        i += 4
    return out

def main():
    xff, dumps, ziel = sys.argv[1:4]
    d = open(xff, "rb").read()
    bins = {os.path.basename(f)[4:20]: open(f, "rb").read() for f in glob.glob(os.path.join(dumps, "vu1_*.bin"))}
    os.makedirs(ziel, exist_ok=True)
    idx = []
    for ch in chains(d):
        end = max(a + n * 8 for _, n, a in ch)
        mod = bytearray(end)
        for j, n, a in ch: mod[a:a + n * 8] = d[j + 4:j + 4 + n * 8]
        # Basis fuer den Speicher oberhalb: das Abbild, dessen oberer Teil am haeufigsten vorkommt
        cnt = collections.Counter(b[end:] for b in bins.values())
        upper = cnt.most_common(1)[0][0] if cnt else bytes(16384 - end)
        img = bytes(mod) + upper
        h = "%016x" % fnv(img)
        known = [k for k, b in bins.items() if b[:end] == bytes(mod)]
        # Modullaenge fuer bekannte Abbilder (Fortsetzungen nach E-Bit nur innerhalb des Moduls, siehe vu1rec.py)
        for k in known:
            open(os.path.join(dumps, "vu1_%s.len" % k), "w").write("%d\n" % end)
        if known and "--alle" not in sys.argv:
            print("Modul @%08x  %5d Byte  bekannt in %s (uebersprungen)" % (ch[0][0], end, ",".join(known)))
            continue
        open(os.path.join(ziel, "vu1_%s.bin" % h), "wb").write(img)
        open(os.path.join(ziel, "vu1_%s.cov" % h), "w").write("0000\n")
        open(os.path.join(ziel, "vu1_%s.len" % h), "w").write("%d\n" % end)
        pc = 0
        while pc + 16 <= end:
            lo, up = struct.unpack_from("<II", img, pc)
            if (lo >> 31) == 0 and ((lo >> 25) & 0x7F) == 0x20 and (up >> 27) == 0:
                idx.append("%s %04x 0 1\n" % (h, pc))
            elif not (lo in (0x8000033C, 0) and (up & 0x3F) == 0x3F): break
            pc += 16
        if not any(l.startswith(h + " 0000") for l in idx): idx.append("%s 0000 0 1\n" % h)
        print("Modul @%08x  %5d Byte  -> %s  %s" % (ch[0][0], end, h, ("bekannt in " + ",".join(known)) if known else "NEU"))
    open(os.path.join(ziel, "vu1_index.txt"), "w").writelines(idx)

if __name__ == "__main__":
    main()
