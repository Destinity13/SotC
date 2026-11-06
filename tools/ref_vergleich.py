#!/usr/bin/env python3
"""Vergleich PCSX2-Savestates (referenz/laeufe/<lauf>/gcNNNNNN.p2s) mit Port-Abbildern (build/<dir>/sotc_ram_gcNNNNN.bin).
   python3 -I tools/ref_vergleich.py <reflauf> <portdir> [gc,...]"""
import sys, zipfile, os, glob
import numpy as np
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R=os.path.join(ROOT,'referenz','laeufe',sys.argv[1]); P=os.path.join(ROOT,'build',sys.argv[2])
gcs=[int(x) for x in sys.argv[3].split(',')] if len(sys.argv)>3 else sorted(int(os.path.basename(f)[2:8]) for f in glob.glob(R+'/gc*.p2s'))
def ref_ram(gc):
    with zipfile.ZipFile(f'{R}/gc{gc:06d}.p2s') as z:
        n=next(x for x in z.namelist() if x.lower().startswith('eememory'))
        return np.frombuffer(z.read(n),dtype=np.uint32).copy()
os.makedirs(os.path.join(P,'ref'),exist_ok=True)
for gc in gcs:
    pf=f'{P}/sotc_ram_gc{gc:05d}.bin'
    if not os.path.exists(pf): continue
    a=ref_ram(gc); b=np.fromfile(pf,dtype=np.uint32)
    a.tofile(os.path.join(P,'ref',f'pcsx2_ram_gc{gc:05d}.bin'))
    d=np.nonzero(a!=b)[0]
    pages={}
    for i in d: pages[(i*4)>>16]=pages.get((i*4)>>16,0)+1
    top=sorted(pages.items(),key=lambda kv:-kv[1])[:8]
    print(gc,'abweichende Woerter:',len(d),' Bereiche:',' '.join(f'{k<<16:#x}:{v}' for k,v in top), flush=True)
