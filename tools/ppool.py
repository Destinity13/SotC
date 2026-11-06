#!/usr/bin/env python3
"""Globaler Partikel-Freispeicher (Port 0x1226380 / PCSX2 +0xFA380): [0]=Anzahl frei, [4]=Kopf, [8]=Ende; Kette über +0xA8.
  python3 -I tools/ppool.py <abbild> <adresse-hex>"""
import sys
sys.path.insert(0, __file__.rsplit('/', 1)[0])
import p2s_auswerten as P
for i in range(1, len(sys.argv), 2):
    m = P.load(sys.argv[i]); a = int(sys.argv[i + 1], 16)
    n, h, t = P.r32(m, a), P.r32(m, a + 4), P.r32(m, a + 8)
    k = 0; x = h
    while x and k < 100000: k += 1; x = P.r32(m, x + 0xA8)
    print(f'{sys.argv[i]}: frei-Zaehler={n} kopf=0x{h:08X} ende=0x{t:08X} kette={k}  umgebung: ' + ' '.join(f'{P.r32(m, a - 0x10 + 4*j):08X}' for j in range(12)))
