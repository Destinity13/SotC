import sys,zipfile,os,struct,glob
ref=sys.argv[1]; d=sys.argv[2]; refcam=int(sys.argv[3],0); size=int(sys.argv[4],0)
def u32(m,a): return struct.unpack_from('<I',m,a)[0]
def fmt(m,a):
    v=struct.unpack_from('<f',m,a)[0]; u=u32(m,a)
    return f'{v:12.6f}' if 1e-7<abs(v)<1e7 or u==0 else f'{u:#010x}'
for p in sorted(glob.glob(f'referenz/laeufe/{ref}/gc*.p2s')):
    gc=int(os.path.basename(p)[2:8]); pf=f'build/{d}/sotc_ram_gc{gc:05d}.bin'
    if not os.path.exists(pf): continue
    with zipfile.ZipFile(p) as z: rm=z.read(next(x for x in z.namelist() if x.lower().startswith('eememory')))
    pm=open(pf,'rb').read(); pc=u32(pm,0x01239E80+4*u32(pm,0x01199634))
    diffs=[(o,fmt(rm,refcam+o),fmt(pm,pc+o)) for o in range(0,size,4) if rm[refcam+o:refcam+o+4]!=pm[pc+o:pc+o+4]]
    print(f'gc {gc}: {len(diffs)} abweichende Woerter', ' '.join(f'+{o:#x}[{a.strip()}|{b.strip()}]' for o,a,b in diffs[:60]))
