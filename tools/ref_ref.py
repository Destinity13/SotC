import sys,zipfile,os,struct,glob
a,b=sys.argv[1],sys.argv[2]; cam=int(sys.argv[3],0); gcs=[int(x) for x in sys.argv[4].split(',')]
def ld(r,gc):
    with zipfile.ZipFile(f'referenz/laeufe/{r}/gc{gc:06d}.p2s') as z: return z.read(next(x for x in z.namelist() if x.lower().startswith('eememory')))
def f3(m,o): return struct.unpack_from('<3f',m,o)
for gc in gcs:
    try: ma,mb=ld(a,gc),ld(b,gc)
    except Exception as e: print(gc,'fehlt',e); continue
    pa,pb=f3(ma,cam+0x140),f3(mb,cam+0x140); ua,ub=f3(ma,cam+0x160),f3(mb,cam+0x160)
    pl_a,pl_b=f3(ma,0x1e0f760+0xa0),f3(mb,0x1e0f760+0xa0)
    print(f'{gc}: Kamera {a} {pa[0]:.4f} {pa[1]:.4f} {pa[2]:.4f} | {b} {pb[0]:.4f} {pb[1]:.4f} {pb[2]:.4f}   up.x {ua[0]:.6f}|{ub[0]:.6f}   Spieler {pl_a[1]:.4f}|{pl_b[1]:.4f}')
