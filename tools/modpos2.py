import zipfile,sys
ref=sys.argv[1]; d=sys.argv[2]; gc=int(sys.argv[3])
with zipfile.ZipFile(f'referenz/laeufe/{ref}/gc{gc:06d}.p2s') as z: a=z.read(next(x for x in z.namelist() if x.lower().startswith('eememory')))
b=open(f'build/{d}/sotc_ram_gc{gc:05d}.bin','rb').read()
for name,adr in (('MANAGER update_camera_context',0x115d928),('GAMECORE sub_010CD338',0x10cd338),('GAMECORE .text Anfang',0x134ABC8),('MANAGER .text Anfang',0x10764B0),('KERNEL iosMulMvec',0x1b4c40)):
    pat=b[adr:adr+40]; i=a.find(pat); hits=[]
    while i>=0 and len(hits)<4: hits.append(i); i=a.find(pat,i+1)
    print(f'{name}: Port {adr:#x} -> PCSX2 {[hex(h) for h in hits]} Versatz {[hex(h-adr) if h>=adr else "-"+hex(adr-h) for h in hits]}')
