import sys, zipfile, os, struct
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R=os.path.join(ROOT,'referenz','laeufe',sys.argv[1]); P=os.path.join(ROOT,'build',sys.argv[2]); gc=int(sys.argv[3]); H=int(sys.argv[4],0)
with zipfile.ZipFile(f'{R}/gc{gc:06d}.p2s') as z: a=z.read(next(x for x in z.namelist() if x.lower().startswith('eememory')))
b=open(f'{P}/sotc_ram_gc{gc:05d}.bin','rb').read()
u=lambda m,x: struct.unpack_from('<I',m,x&0x1ffffff)[0]
def walk(m):
    n=u(m,H); tbl=u(m,H+4); total=0; keys=[]
    for i in range(min(n,65536)):
        p=u(m,tbl+4*i); g=0
        while p and g<10000:
            total+=1; keys.append((u(m,p), p)); p=u(m,p+0xC); g+=1
    return n,tbl,total,keys
for nm,m in (('PCSX2',a),('Port',b)):
    n,tbl,total,keys=walk(m)
    print(nm,'buckets',n,'tabelle',hex(tbl),'eintraege',total)
ka=set(k for k,_ in walk(a)[3]); kb=set(k for k,_ in walk(b)[3])
print('nur PCSX2:',len(ka-kb),'nur Port:',len(kb-ka))
def s_at(m,p):
    p&=0x1ffffff
    if not (0x100000<=p<0x2000000): return ''
    e=m.find(b'\0',p,p+64); t=m[p:e]
    return t.decode('latin1') if t and all(32<=c<127 for c in t) else ''
def show(m,keys,only):
    out=[]
    for k,p in keys:
        if k in only:
            w=[u(m,p+4*j) for j in range(4)]
            nm=next((s_at(m,x) for x in w[1:3] if s_at(m,x)),'')
            out.append(f'{p:#x} {" ".join(f"{x:08x}" for x in w)} {nm}')
    return out
ka_=walk(a)[3]; kb_=walk(b)[3]
print('--- nur PCSX2'); print('\n'.join(show(a,ka_,ka-kb)[:25]))
print('--- nur Port'); print('\n'.join(show(b,kb_,kb-ka)[:25]))
