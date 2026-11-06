import re,glob,collections,sys
com=re.compile(r'^\s*// 0x([0-9a-f]+): 0x([0-9a-f]+)\s+(\S+)')
vf=re.compile(r'vu0_vf\[(\d+)\]')
res=collections.Counter(); ex={}
for p in glob.glob(sys.argv[1]+'/*.cpp'):
    s=open(p,errors='replace').read()
    if 'vu0_vf' not in s: continue
    L=s.split('\n')
    for i,l in enumerate(L):
        m=com.match(l)
        if not m: continue
        w=int(m.group(2),16)
        if (w>>26)!=0x12 or not (w>>25)&1: continue
        fn=w&63
        if fn>=0x30: continue  # special2 (einzeln)
        dest=(w>>21)&15; ft=(w>>16)&31; fs=(w>>11)&31; fd=(w>>6)&31; bc=w&3
        c=''
        for j in range(i+1,min(i+5,len(L))):
            if L[j].strip().startswith('//'): break
            c+=L[j]
        refs=[int(x) for x in vf.findall(c)]
        probs=[]
        if any(r not in (fs,ft,fd,0) for r in refs): probs.append('fremdes Register')
        mm=re.search(r'_mm_set_epi32\((-?\d+), (-?\d+), (-?\d+), (-?\d+)\)',c)
        acc_only = fn in ()  # Makro-ACC-Varianten liegen in special2
        if mm:
            got=tuple(int(x)!=0 for x in mm.groups()) # w z y x
            exp=(bool(dest&1),bool(dest&2),bool(dest&4),bool(dest&8))
            if got!=exp: probs.append('Maske')
        da=re.search(r'ctx->vu0_vf\[(\d+)\] = ',c)
        if da and int(da.group(1))!=fd and fd!=0: probs.append('Ziel')
        if fn<0x20:
            if fn<0x1C:
                sh=re.search(r'_MM_SHUFFLE\((\d),(\d),(\d),(\d)\)',c)
                if not sh or int(sh.group(4))!=bc: probs.append('bc')
        if refs and refs[0]!=fs and fs!=0 and fn not in (0x1C,0x1D,0x1E,0x1F): probs.append('Reihenfolge')
        key=f'fn{fn:#04x} '+(','.join(probs) if probs else 'ok')
        res[key]+=1
        if key not in ex: ex[key]=(p.split('/')[-1],l.strip()[:70],c.strip()[:230])
for k in sorted(res): print(res[k],k,'|',ex[k] if 'ok' not in k else '')
