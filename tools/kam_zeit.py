import sys,zipfile,os,struct,glob
ref=sys.argv[1]; d=sys.argv[2]; cands=[int(x,0) for x in sys.argv[3].split(',')]
def u32(m,a): return struct.unpack_from('<I',m,a)[0]
def f3(m,a): return struct.unpack_from('<3f',m,a)
import math
for p in sorted(glob.glob(f'referenz/laeufe/{ref}/gc*.p2s')):
    gc=int(os.path.basename(p)[2:8]); pf=f'build/{d}/sotc_ram_gc{gc:05d}.bin'
    if not os.path.exists(pf): continue
    with zipfile.ZipFile(p) as z: rm=z.read(next(x for x in z.namelist() if x.lower().startswith('eememory')))
    pm=open(pf,'rb').read(); pc=u32(pm,0x01239E80+4*u32(pm,0x01199634))
    def yaw(m,a): dx,dy,dz=f3(m,a+0x150); return math.degrees(math.atan2(dx,dz))
    line=f'{gc}: Port pos {f3(pm,pc+0x140)[0]:8.2f} {f3(pm,pc+0x140)[1]:7.2f} {f3(pm,pc+0x140)[2]:8.2f} yaw {yaw(pm,pc):7.1f}'
    for c in cands: line+=f' | PCSX2@{c:#x} pos {f3(rm,c+0x140)[0]:8.2f} {f3(rm,c+0x140)[1]:7.2f} {f3(rm,c+0x140)[2]:8.2f} yaw {yaw(rm,c):7.1f}'
    print(line)
