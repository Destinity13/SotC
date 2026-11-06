#!/usr/bin/env python3
"""ramvgl.py reflauf portdir gc addr len : Hexvergleich"""
import sys, zipfile, os
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R=os.path.join(ROOT,'referenz','laeufe',sys.argv[1]); P=os.path.join(ROOT,'build',sys.argv[2])
gc=int(sys.argv[3]); a=int(sys.argv[4],0); n=int(sys.argv[5],0)
with zipfile.ZipFile(f'{R}/gc{gc:06d}.p2s') as z: m=z.read(next(x for x in z.namelist() if x.lower().startswith('eememory')))
p=open(f'{P}/sotc_ram_gc{gc:05d}.bin','rb').read()
for o in range(a,a+n,16):
    print(f'{o:08x} PCSX2 {m[o:o+16].hex(" ")}\n         Port  {p[o:o+16].hex(" ")}')
