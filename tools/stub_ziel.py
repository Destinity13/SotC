#!/usr/bin/env python3
"""stub_ziel.py reflauf portdir gc : Wohin zeigen im PCSX2-Speicher die Aufrufe der im Port gestubbten XFF-Externs?"""
import sys, zipfile, json, struct, os
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ref,d,gc=sys.argv[1],sys.argv[2],int(sys.argv[3])
with zipfile.ZipFile(f'{ROOT}/referenz/laeufe/{ref}/gc{gc:06d}.p2s') as z: a=z.read(next(x for x in z.namelist() if x.lower().startswith('eememory')))
b=open(f'{ROOT}/build/{d}/sotc_ram_gc{gc:05d}.bin','rb').read()
L=json.load(open(f'{ROOT}/elf/sotc_layout.json'))
stubs={int(v,16):k for k,v in L['stub_functions'].items()}
# Modulversatz je Modul ueber Textmuster
offs={}
for mod,info in L['modules'].items():
    t=[s for s in info['sections'] if s.startswith('.text')][0]
    st=int(info['sections'][t][0],16); sl=int(info['sections'][t][1],16)
    from collections import Counter; c=Counter()
    for k in range(0x200, sl-0x40, max(0x800,(sl//64)&~3)):
        pat=b[st+k:st+k+0x30]
        i=a.find(pat)
        if i>=0 and a.find(pat,i+1)<0: c[(i-k)-st]+=1
    offs[mod]=c.most_common(1)[0][0] if c else None
    print(mod,'Versatz',hex(offs[mod]) if offs[mod] is not None else '?')
seen={}
for mod,info in L['modules'].items():
    if offs[mod] is None: continue
    for sname,(s0,sl) in info['sections'].items():
        if not sname.startswith('.text'): continue
        s0=int(s0,16); sl=int(sl,16)
        for o in range(s0,s0+sl,4):
            w=struct.unpack_from('<I',b,o)[0]
            if w>>26==3:
                tgt=((o+4)&0xF0000000)|((w&0x3FFFFFF)<<2)
                if tgt in stubs and stubs[tgt] not in seen:
                    po=o+offs[mod]; pw=struct.unpack_from('<I',a,po)[0]
                    ptgt=((po+4)&0xF0000000)|((pw&0x3FFFFFF)<<2) if pw>>26==3 else None
                    words=' '.join(f'{struct.unpack_from("<I",a,ptgt+k*4)[0]:08x}' for k in range(6)) if ptgt else '-'
                    seen[stubs[tgt]]=(hex(o),hex(po),hex(pw),hex(ptgt) if ptgt else None,words)
for k,v in sorted(seen.items()): print(k,v)
# Konstanten (R_MIPS_32) und lui/addiu-Paare auf Stub-Adressen
for mod,info in L['modules'].items():
    if offs[mod] is None: continue
    for sname,(s0,sl) in info['sections'].items():
        s0=int(s0,16); sl=int(sl,16)
        for o in range(s0,s0+sl-3,4):
            w=struct.unpack_from('<I',b,o)[0]
            if w in stubs and stubs[w] not in seen:
                po=o+offs[mod]; pw=struct.unpack_from('<I',a,po)[0]
                words=' '.join(f'{struct.unpack_from("<I",a,pw+k*4)[0]:08x}' for k in range(6)) if 0x100000<=pw<0x2000000 else '-'
                seen[stubs[w]]=('const',sname,hex(o),hex(pw),words); print(stubs[w],seen[stubs[w]])
            if w>>26==0xF:  # lui
                hi=(w&0xFFFF)<<16; rt=(w>>16)&0x1f
                for k in range(1,5):
                    w2=struct.unpack_from('<I',b,o+4*k)[0]
                    if w2>>26==9 and ((w2>>21)&0x1f)==rt:
                        lo=w2&0xFFFF; lo=lo-0x10000 if lo&0x8000 else lo
                        v=(hi+lo)&0xFFFFFFFF
                        if v in stubs and stubs[v] not in seen:
                            po=o+offs[mod]; pw=struct.unpack_from('<I',a,po)[0]; pw2=struct.unpack_from('<I',a,po+4*k)[0]
                            lo2=pw2&0xFFFF; lo2=lo2-0x10000 if lo2&0x8000 else lo2; pv=(((pw&0xFFFF)<<16)+lo2)&0xFFFFFFFF
                            words=' '.join(f'{struct.unpack_from("<I",a,pv+j*4)[0]:08x}' for j in range(6)) if 0x100000<=pv<0x2000000 else '-'
                            seen[stubs[v]]=('luiaddiu',sname,hex(o),hex(pv),words); print(stubs[v],seen[stubs[v]])
                        break
print('ohne jal-Aufruf:', sorted(set(stubs.values())-set(seen)))
