import struct, sys, collections
ram=open(sys.argv[1],'rb').read()
def w(a): return struct.unpack_from('<I',ram,a&0x1FFFFFF)[0]
def walk(tadr, maxsteps=200000):
    stack=[]; steps=0; mscal=collections.Counter(); ends=None; qw=0
    while steps<maxsteps:
        steps+=1
        lo=struct.unpack_from('<Q',ram,tadr&0x1FFFFF0)[0]; v0,v1=struct.unpack_from('<II',ram,(tadr&0x1FFFFF0)+8)
        qwc=lo&0xffff; tid=(lo>>28)&7; addr=(lo>>32)&0x7ffffff0; irq=(lo>>31)&1
        for v in (v0,v1):
            cmd=(v>>24)&0x7f
            if cmd in (0x14,0x15): mscal[(v&0xffff)*8]+=1
        # scan data for MSCAL vifcodes (crude: aligned words)
        def scan(a,n):
            i=0
            while i<n*4:
                x=w(a+i*4); c=(x>>24)&0x7f
                if c in (0x14,0x15) and (x&0xffff)<0x800: mscal[(x&0xffff)*8]+=1
                i+=1
        if tid==1: scan(tadr+16,qwc); qw+=qwc; tadr=tadr+16+qwc*16
        elif tid==2: scan(tadr+16,qwc); qw+=qwc; tadr=addr
        elif tid in (3,4): scan(addr,qwc); qw+=qwc; tadr=tadr+16
        elif tid==5:
            scan(tadr+16,qwc); qw+=qwc
            if len(stack)>=2: ends=('call depth>2',hex(tadr)); break
            stack.append(tadr+16+qwc*16); tadr=addr
        elif tid==6:
            scan(tadr+16,qwc); qw+=qwc
            if stack: tadr=stack.pop()
            else: ends=('ret',hex(tadr)); break
        elif tid==0: scan(addr,qwc); qw+=qwc; ends=('refe',hex(tadr)); break
        elif tid==7: scan(tadr+16,qwc); qw+=qwc; ends=('end',hex(tadr)); break
    return steps,qw,ends,mscal
base0,base1,lt0,lt1=w(0x20E4D8),w(0x20E4DC),w(0x20E4E0),w(0x20E4E4)
print('work',w(0x20E4F0),'cur',hex(w(0x20E4E8)))
for name,lt in (('lt0',lt0),('lt1',lt1)):
    s,q,e,m=walk(lt)
    print(name,hex(lt),'steps',s,'qw',q,'end',e,'mscal',sorted(m.items()))
