#!/usr/bin/env python3
"""Spielerobjekt PCSX2 vs Port vergleichen. python3 -I tools/spieler_vgl.py ref6 ref6b"""
import sys, zipfile, os, glob, struct
import numpy as np
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R=os.path.join(ROOT,'referenz','laeufe',sys.argv[1]); P=os.path.join(ROOT,'build',sys.argv[2])
def ref_ram(gc):
    with zipfile.ZipFile(f'{R}/gc{gc:06d}.p2s') as z:
        n=next(x for x in z.namelist() if x.lower().startswith('eememory'))
        return z.read(n)
def u16(m,a): return struct.unpack_from('<H',m,a&0x1ffffff)[0]
def u32(m,a): return struct.unpack_from('<I',m,a&0x1ffffff)[0]
def player(m):
    i=u16(m,0x1477360); n=0
    while i!=0xffff and n<4096:
        node=0x216B00+i*24; o=u32(m,node)
        if not (m[(node+0xC)&0x1ffffff] or (o and u16(m,o+0xA)&1)): return o
        i=u16(m,node+0xE); n+=1
    return 0
gcs=sorted(int(os.path.basename(f)[2:8]) for f in glob.glob(R+'/gc*.p2s'))
for gc in gcs:
    pf=f'{P}/sotc_ram_gc{gc:05d}.bin'
    if not os.path.exists(pf): continue
    a=ref_ram(gc); b=open(pf,'rb').read()
    pa,pb=player(a),player(b)
    print(f'== gc {gc}: Spieler PCSX2 {pa:#x}  Port {pb:#x}  gc-RAM pcsx2={u32(a,0x1DC9EC)} port={u32(b,0x1DC9EC)}')
    if not pa or not pb: continue
    fa=np.frombuffer(a[pa&0x1ffffff:(pa&0x1ffffff)+0x400],dtype=np.float32)
    fb=np.frombuffer(b[pb&0x1ffffff:(pb&0x1ffffff)+0x400],dtype=np.float32)
    ia=np.frombuffer(a[pa&0x1ffffff:(pa&0x1ffffff)+0x400],dtype=np.uint32)
    ib=np.frombuffer(b[pb&0x1ffffff:(pb&0x1ffffff)+0x400],dtype=np.uint32)
    # Position: suche die ersten plausiblen vec4 mit w==1
    out=[]
    for k in range(0,256,4):
        if abs(fa[k+3]-1)<1e-6 and abs(fb[k+3]-1)<1e-6 and max(abs(fa[k:k+3]).max(),abs(fb[k:k+3]).max())>50:
            out.append(f'+{k*4:#x} P={fa[k]:.1f},{fa[k+1]:.1f},{fa[k+2]:.1f} Port={fb[k]:.1f},{fb[k+1]:.1f},{fb[k+2]:.1f}')
    print('  vec(w=1):',' | '.join(out[:4]))
    d=np.nonzero(ia!=ib)[0]
    print(f'  abweichende Woerter im Objekt (0x400): {len(d)}')
