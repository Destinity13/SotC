import sys,zipfile,os,struct,glob
ref=sys.argv[1]; d=sys.argv[2]; offs=[int(x,0) for x in sys.argv[3].split(',')]
def f(m,a):
    v=struct.unpack_from('<f',m,a)[0]; u=struct.unpack_from('<I',m,a)[0]
    return f'{v:10.3f}' if 1e-6<abs(v)<1e8 or u==0 else f'{u:#010x}'
R={}
for p in sorted(glob.glob(f'referenz/laeufe/{ref}/gc*.p2s')):
    gc=int(os.path.basename(p)[2:8])
    with zipfile.ZipFile(p) as z: m=z.read(next(x for x in z.namelist() if x.lower().startswith('eememory')))
    R[gc]=' '.join(f(m,0x1e0f760+o) for o in offs)
for p in sorted(glob.glob(f'build/{d}/sotc_ram_gc*.bin')):
    gc=int(p[-9:-4]); m=open(p,'rb').read()
    print(f'{gc}: PCSX2 [{R.get(gc,"-")}]   Port [{" ".join(f(m,0x1e11780+o) for o in offs)}]')
