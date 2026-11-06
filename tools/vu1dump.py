import struct, sys
f=sys.argv[1]; d=open(f,'rb').read()
data=d[:16384]; code=d[16384:32768]; vf=d[32768:32768+512]; vi=d[32768+512:32768+512+64]
kick,pc=struct.unpack_from('<II',d,32768+512+64)
print('kick',hex(kick),'pc',hex(pc))
for i in range(32):
    v=struct.unpack_from('<4f',vf,i*16)
    print('vf%02d'%i, ' '.join('%12.5g'%x for x in v), end='   ' if i%2==0 else '\n')
print('vi', struct.unpack('<16i',vi))
def qw(a): return struct.unpack_from('<4I',data,a*16)
def qf(a): return struct.unpack_from('<4f',data,a*16)
t=qw(kick); print('giftag', ' '.join('%08x'%x for x in t))
nloop=t[0]&0x7fff; nreg=(t[1]>>28)&0xf or 16; print('nloop',nloop,'nreg',nreg,'prim',hex((t[1]>>15)&0x7ff), 'regs',hex(t[2]))
for k in range(1,min(13,1+nloop*nreg)):
    w=qw(kick+k); fl=qf(kick+k)
    print('  +%d'%k,' '.join('%08x'%x for x in w), ' f:',' '.join('%.4g'%x for x in fl))
