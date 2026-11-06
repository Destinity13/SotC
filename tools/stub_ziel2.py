#!/usr/bin/env python3
import sys, zipfile, struct, os
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ref,gc=sys.argv[1],int(sys.argv[2])
with zipfile.ZipFile(f'{ROOT}/referenz/laeufe/{ref}/gc{gc:06d}.p2s') as z: a=z.read(next(x for x in z.namelist() if x.lower().startswith('eememory')))
GOFF=0x40
seen=set()
for n,site in (('createBirdCtrl2Work',0x1465590),('updateBirdCtrl2Work',0x1465a78),('setAttainObjBirdCtrl2Work',0x1466b68),('checkUser',0x1350394),('getDemoCutName',0x134f6c8),('username',0x134f4f4)):
    po=site+GOFF; w=struct.unpack_from('<I',a,po)[0]
    if w>>26!=3: print(n,hex(po),f'{w:08x}','kein jal'); continue
    t=((po+4)&0xF0000000)|((w&0x3FFFFFF)<<2)
    words=' '.join(f'{struct.unpack_from("<I",a,t+k*4)[0]:08x}' for k in range(8))
    print(f'{n:28s} PCSX2-Aufruf @{po:#x} -> {t:#x}: {words}')
