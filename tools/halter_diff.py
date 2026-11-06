#!/usr/bin/env python3
"""Alle Halter-PCs im ganzen Partitionsbaum zweier Abbilder vergleichen (Anzahl Blöcke/Bytes).
  python3 -I tools/halter_diff.py <alt> <neu> [pc-verschiebung-hex, z.B. FA380 für PCSX2->Port] > tabelle
Ausgabe: pc(in Port-Adressen) alt_n neu_n diff_n neu_bytes"""
import sys
from collections import defaultdict
sys.path.insert(0, __file__.rsplit('/', 1)[0])
import p2s_auswerten as P
from ueberlebende import bloecke
def zaehl(path, shift):
    m = P.load(path); out = []; bloecke(m, P.ROOT, out); c = defaultdict(lambda: [0, 0])
    for b, s, pc, part, d in out:
        if pc >= 0x01000000: pc -= shift
        c[pc][0] += 1; c[pc][1] += s
    return c
shift = int(sys.argv[3], 16) if len(sys.argv) > 3 else 0
a, n = zaehl(sys.argv[1], shift), zaehl(sys.argv[2], shift)
for pc in sorted(set(a) | set(n), key=lambda p: -(n[p][0] - a[p][0])):
    print(f'0x{pc:08X} {a[pc][0]} {n[pc][0]} {n[pc][0]-a[pc][0]:+d} {n[pc][1]}')
