import sys,zipfile,os,struct,glob
ref=sys.argv[1]; d=sys.argv[2]; gcs=[int(x) for x in sys.argv[3].split(',')]
def load_ref(gc):
    with zipfile.ZipFile(f'referenz/laeufe/{ref}/gc{gc:06d}.p2s') as z: return z.read(next(x for x in z.namelist() if x.lower().startswith('eememory')))
def load_port(gc): return open(f'build/{d}/sotc_ram_gc{gc:05d}.bin','rb').read()
def u32(m,a): return struct.unpack_from('<I',m,a)[0]
def camptr_port(m): return u32(m,0x01239E80+4*u32(m,0x01199634))
def find_cam(m, probe):
    # probe: 32 Byte aus Port-Kamera +0x40 (Projektionskonstanten) suchen
    i=m.find(probe); hits=[]
    while i>=0 and len(hits)<6: hits.append(i); i=m.find(probe,i+1)
    return hits
def f3(m,a): return struct.unpack_from('<3f',m,a)
for gc in gcs:
    pm=load_port(gc); pc=camptr_port(pm)
    probe=pm[pc:pc+0x34]
    rm=load_ref(gc); hits=find_cam(rm,probe)
    print(f'== gc {gc}: Port-Kamera {pc:#x}; PCSX2-Kandidaten {[hex(h) for h in hits]}')
    for h in hits[:1]:
        for off,name in ((0x140,'pos'),(0x150,'dir'),(0x160,'up'),(0x1d0,'target?')):
            a=f3(rm,h+off); b=f3(pm,pc+off)
            print(f'   +{off:#x} {name:8s} PCSX2 {a[0]:9.3f} {a[1]:9.3f} {a[2]:9.3f}   Port {b[0]:9.3f} {b[1]:9.3f} {b[2]:9.3f}')
