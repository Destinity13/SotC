import re,glob,sys,collections
com=re.compile(r'^\s*// 0x([0-9a-f]+): 0x([0-9a-f]+)\s+(\S+)\s*(.*)$')
res=collections.Counter(); ex=[]
for p in glob.glob(sys.argv[1]+'/*.cpp'):
    s=open(p,errors='replace').read()
    if 'vu0_q' not in s: continue
    L=[com.match(l) for l in s.split('\n')]
    L=[m for m in L if m]
    pending=None  # (adr, mnemonic) einer Q-Berechnung ohne vwaitq danach
    for m in L:
        mn=m.group(3); ops=m.group(4)
        if mn in ('vdiv','vsqrt','vrsqrt'): pending=(m.group(1),mn); continue
        if mn=='vwaitq': pending=None; continue
        usesQ = ('q.' in mn and mn.startswith('v')) or (mn.startswith('cfc2') and '$vi22' in ops) or mn.endswith('q')
        if usesQ:
            if pending: res['OHNE vwaitq nach '+pending[1]]+=1; ex.append(f'{p.split("/")[-1]} {m.group(1)} {mn} nach {pending[1]}@{pending[0]}')
            else: res['mit vwaitq/ohne offene Berechnung']+=1
        # Sprung/Funktionsende -> unbekannt, pending zuruecksetzen bei jr
        if mn in ('jr',) : pending=None
for k,v in res.items(): print(v,k)
print('\n'.join(ex[:30]))
