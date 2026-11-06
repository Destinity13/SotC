import struct,glob,zipfile,os,sys
ref_name=sys.argv[1]; dirs=sys.argv[2:]
def pl(m,base): return struct.unpack_from('<3f',m,base+0xa0)
ref={}
for f in sorted(glob.glob(f'referenz/laeufe/{ref_name}/gc*.p2s')):
    gc=int(os.path.basename(f)[2:8])
    with zipfile.ZipFile(f) as z: m=z.read(next(x for x in z.namelist() if x.lower().startswith('eememory')))
    ref[gc]=pl(m,0x1e0f760)
for d in dirs:
    print('==',d)
    for f in sorted(glob.glob(f'build/{d}/sotc_ram_gc*.bin')):
        gc=int(f[-9:-4]); p=pl(open(f,'rb').read(),0x1e11780); r=ref.get(gc)
        if r: dd=((p[0]-r[0])**2+(p[1]-r[1])**2+(p[2]-r[2])**2)**0.5; print(f'  {gc}: Port {p[0]:.2f} {p[1]:.2f} {p[2]:.2f}  PCSX2 {r[0]:.2f} {r[1]:.2f} {r[2]:.2f}  Abstand {dd:.2f}')
