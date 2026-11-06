import struct, sys, collections
dl=open('/tmp/sotc_dl_0_0123C0F0.bin','rb').read(); dlbase=0x123C0F0
lt=open('/tmp/sotc_dllayers_0.bin','rb').read(); ltbase=0x13357B0
def rd(a,n=16):
    if dlbase<=a and a+n<=dlbase+len(dl): return dl[a-dlbase:a-dlbase+n]
    if ltbase<=a and a+n<=ltbase+len(lt): return lt[a-ltbase:a-ltbase+n]
    return None
def layer_of(a):
    if ltbase<=a<ltbase+len(lt): return (a-ltbase)//0x30
    return None
tadr=ltbase; layer=0; stats=collections.Counter(); tagsper=collections.Counter(); outside=collections.Counter()
stack=[]; steps=0
viftypes=collections.Counter()
while steps<200000:
    steps+=1
    q=rd(tadr)
    if q is None: print('left known memory at',hex(tadr)); break
    v0,v1=struct.unpack("<II",q[8:16])
    lo=struct.unpack('<Q',q[:8])[0]
    qwc=lo&0xffff; tid=(lo>>28)&7; addr=(lo>>32)&0x7ffffff0
    l=layer_of(tadr)
    if l is not None: layer=l
    tagsper[layer]+=1; stats[layer]+=qwc
    viftypes[(v1>>24)&0x7f]+=1
    if tid==1: tadr=tadr+16+qwc*16           # cnt
    elif tid==2: tadr=addr                    # next
    elif tid==3 or tid==4: tadr=tadr+16; outside[layer]+=qwc   # ref/refs
    elif tid==5:                              # call
        stack.append(tadr+16+qwc*16); tadr=addr
        if rd(addr) is None: outside[layer]+=1; tadr=stack.pop()
    elif tid==6:                              # ret
        if stack: tadr=stack.pop()
        else: print('end(ret) at',hex(tadr)); break
    elif tid==0: outside[layer]+=qwc; print('refe end at',hex(tadr)); break
    elif tid==7: print('end at',hex(tadr)); break
print('steps',steps)
tot=0
for k in sorted(set(stats)|set(tagsper)):
    print('layer %2d tags=%6d inline_qw=%7d ref_qw=%7d'%(k,tagsper[k],stats[k],outside[k])); tot+=tagsper[k]+stats[k]
print('total inline qw incl tags',tot, hex(tot*16), 'vif cmd hist',viftypes.most_common(8))
