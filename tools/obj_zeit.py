#!/usr/bin/env python3
"""Ein Feldbereich des Spielerobjekts ueber alle Savestates/Abbilder. obj_zeit.py reflauf portdir off,off,..."""
import sys, zipfile, os, struct, glob
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R=os.path.join(ROOT,'referenz','laeufe',sys.argv[1]); P=os.path.join(ROOT,'build',sys.argv[2])
offs=[int(x,0) for x in sys.argv[3].split(',')]
def u32(m,a): return struct.unpack_from('<I',m,a&0x1ffffff)[0]
for f in sorted(glob.glob(R+'/gc*.p2s')):
    gc=int(os.path.basename(f)[2:8])
    with zipfile.ZipFile(f) as z: m=z.read(next(x for x in z.namelist() if x.lower().startswith('eememory')))
    print(f'PCSX2 {gc}: '+' '.join(f'{u32(m,0x1e0f760+o):08x}' for o in offs))
for f in sorted(glob.glob(P+'/sotc_ram_gc*.bin')):
    m=open(f,'rb').read(); gc=int(f[-9:-4])
    print(f'Port  {gc}: '+' '.join(f'{u32(m,0x1e11780+o):08x}' for o in offs))
