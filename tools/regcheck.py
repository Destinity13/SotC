#!/usr/bin/env python3
"""Vergleicht die Register in der Disassembly-Zeile (unabhaengiger Disassembler) mit den Registern im erzeugten C++-Code.
Ausgabe: Mnemonic -> Anzahl Abweichungen + Beispiele."""
import re, glob, sys, collections
G='zero at v0 v1 a0 a1 a2 a3 t0 t1 t2 t3 t4 t5 t6 t7 s0 s1 s2 s3 s4 s5 s6 s7 t8 t9 k0 k1 gp sp fp ra'.split()
GI={n:i for i,n in enumerate(G)}; GI['s8']=30
com=re.compile(r'^\s*// 0x([0-9a-f]+): 0x([0-9a-f]+)\s+(\S+)\s*(.*)$')
fre=re.compile(r'\$f(\d+)'); gre=re.compile(r'\$([a-z][a-z0-9]+)')
cf=re.compile(r'ctx->f\[(\d+)\]'); cg=re.compile(r'(?:GPR_[A-Z0-9]+|SET_GPR_[A-Z0-9]+)\(ctx,\s*(\d+)')
skip=('j','jal','jr','jalr','b','beq','bne','beql','bnel','bc1t','bc1f','bc1tl','bc1fl','syscall','nop','sync','break','eret','cache','pref')
stat=collections.Counter(); ordbad=collections.defaultdict(list); unk=collections.Counter(); bad=collections.defaultdict(list)
files=sorted(glob.glob(sys.argv[1]+'/*.cpp'))
for p in files:
    L=open(p,errors='replace').read().split('\n')
    i=0
    while i<len(L):
        m=com.match(L[i])
        if not m: i+=1; continue
        mn=m.group(3); ops=m.group(4).split('#')[0]
        j=i+1; code=[]
        while j<len(L) and not com.match(L[j]) and len(code)<8:
            if not L[j].strip().startswith('//'): code.append(L[j])
            j+=1
        i=j
        if mn.startswith('c1') or mn.startswith('.'): unk[mn+' '+ops.split()[0] if ops.split() else mn]+=1
        if mn in skip or mn.startswith('b') or mn.startswith('.') or mn.startswith('c1') or 'Delay' in ops: continue
        c=' '.join(code)
        if 'dispatchGuest' in c or 'goto' in c: continue
        df={int(x) for x in fre.findall(ops)}
        dg={GI[x] for x in gre.findall(ops) if x in GI}
        kf={int(x) for x in cf.findall(c)}
        kg={int(x) for x in cg.findall(c)}
        dg.discard(0); kg.discard(0)
        stat[mn]+=1
        # Reihenfolge-Pruefung
        order_d=[]
        for t in re.findall(r'\$(f?\d+|[a-z][a-z0-9]+)', ops):
            k=('f',int(t[1:])) if t.startswith('f') and t[1:].isdigit() else (('g',GI[t]) if t in GI else None)
            if k and k[1]!=0 and k not in order_d: order_d.append(k)
        order_c=[]
        for mm in re.finditer(r'ctx->f\[(\d+)\]|(?:GPR_[A-Z0-9]+|SET_GPR_[A-Z0-9]+)\(ctx,\s*(\d+)', c):
            k=('f',int(mm.group(1))) if mm.group(1) else ('g',int(mm.group(2)))
            if k[1]!=0 and k not in order_c: order_c.append(k)
        if order_d and order_c and order_d!=order_c and set(order_d)==set(order_c):
            ordbad[mn].append(f'{m.group(1)} {mn} {ops.strip()} || {c.strip()[:200]}')
        if (df and kf and df!=kf) or (dg and kg and not kg<=dg|{29,31}) or (dg and not kg and 'GPR' in c is False):
            if len(bad[mn])<3: bad[mn].append(f'{p.split("/")[-1]}:{L[i-1][:0]} {m.group(1)} {mn} {ops.strip()} || {c.strip()[:220]}')
            else: bad[mn].append(None)
for mn,v in sorted(bad.items(), key=lambda kv:-len(kv[1])):
    print(f'{mn}: {len(v)} von {stat[mn]}')
    for e in v[:3]:
        if e: print('   ',e)
print('geprueft',sum(stat.values()),'Befehle,',len(stat),'Mnemonics')
print('== Reihenfolge-Abweichungen')
for mn,v in sorted(ordbad.items(), key=lambda kv:-len(kv[1])):
    print(f'{mn}: {len(v)} von {stat[mn]}')
    for e in v[:2]: print('   ',e)
print('== vom Disassembler unbekannt:', sum(unk.values()))
for k,v in unk.most_common(15): print('  ',v,k)
