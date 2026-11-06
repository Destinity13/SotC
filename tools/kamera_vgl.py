#!/usr/bin/env python3
import sys, zipfile, os, struct
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R=os.path.join(ROOT,'referenz','laeufe',sys.argv[1]); P=os.path.join(ROOT,'build',sys.argv[2]); gc=int(sys.argv[3])
with zipfile.ZipFile(f'{R}/gc{gc:06d}.p2s') as z: a=z.read(next(x for x in z.namelist() if x.lower().startswith('eememory')))
b=open(f'{P}/sotc_ram_gc{gc:05d}.bin','rb').read()
u=lambda m,x: struct.unpack_from('<I',m,x&0x1ffffff)[0]
ca=u(a,0x01239E80+4*u(a,0x01199634)); cb=u(b,0x01239E80+4*u(b,0x01199634))
print(f'Kamera PCSX2 {ca:#x} Port {cb:#x}  idx {u(a,0x01199634)} {u(b,0x01199634)}')
def f(m,x):
    v=struct.unpack_from('<f',m,x&0x1ffffff)[0]; i=u(m,x)
    return f'{v:10.4f}' if 1e-5<abs(v)<1e6 or i==0 else f'{i:#010x}'
for o in range(0,0x280,16):
    print(f'+{o:03x} P ' + ' '.join(f(a,ca+o+k) for k in range(0,16,4)) + '  | Port ' + ' '.join(f(b,cb+o+k) for k in range(0,16,4)))
