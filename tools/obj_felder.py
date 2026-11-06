#!/usr/bin/env python3
"""Felder des Spielerobjekts, die sich in PCSX2 zwischen gcA und gcB aendern, mit Port-Werten.
python3 -I tools/obj_felder.py ref8 ref8 2050 2100 [laenge]"""
import sys, zipfile, os, struct
import numpy as np
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R=os.path.join(ROOT,'referenz','laeufe',sys.argv[1]); P=os.path.join(ROOT,'build',sys.argv[2])
A,B=int(sys.argv[3]),int(sys.argv[4]); L=int(sys.argv[5],0) if len(sys.argv)>5 else 0x800
def ref(gc):
    with zipfile.ZipFile(f'{R}/gc{gc:06d}.p2s') as z:
        return z.read(next(x for x in z.namelist() if x.lower().startswith('eememory')))
def port(gc): return open(f'{P}/sotc_ram_gc{gc:05d}.bin','rb').read()
PO,QO=0x1e0f760,0x1e11780
ra,rb,pa,pb=ref(A),ref(B),port(A),port(B)
for off in range(0,L,4):
    va,vb=ra[PO+off:PO+off+4],rb[PO+off:PO+off+4]
    wa,wb=pa[QO+off:QO+off+4],pb[QO+off:QO+off+4]
    if va!=vb or wa!=wb:
        f=lambda x: struct.unpack('<f',x)[0]; u=lambda x: struct.unpack('<I',x)[0]
        def s(x):
            v=f(x); return f'{v:11.4f}' if 1e-6<abs(v)<1e7 else f'{u(x):#011x}'
        print(f'+{off:#05x}  PCSX2 {s(va)} -> {s(vb)}   Port {s(wa)} -> {s(wb)}')
