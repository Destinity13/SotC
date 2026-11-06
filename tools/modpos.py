import zipfile,glob,os,sys,struct
ref=sys.argv[1]; d=sys.argv[2]; gc=int(sys.argv[3])
with zipfile.ZipFile(f'referenz/laeufe/{ref}/gc{gc:06d}.p2s') as z: a=z.read(next(x for x in z.namelist() if x.lower().startswith('eememory')))
b=open(f'build/{d}/sotc_ram_gc{gc:05d}.bin','rb').read()
for nm,m in (('PCSX2',a),('Port',b)):
    i=m.find(b'xff2'); hits=[]
    while i>=0 and len(hits)<8: hits.append(i); i=m.find(b'xff2',i+1)
    print(nm,'xff2 bei',[hex(h) for h in hits])
    for h in hits:
        print('   ',hex(h), m[h:h+16].hex(' '), '| Woerter', ' '.join(f'{struct.unpack_from("<I",m,h+4*k)[0]:08x}' for k in range(1,12)))
