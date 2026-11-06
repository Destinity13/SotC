#!/usr/bin/env python3
"""Resumable PS2 DVD ISO (ISO9660) extractor using pycdlib.
Usage: extract_iso.py <iso> <out_dir> [--list]"""
import os, sys, pycdlib

def clean(name):
    return name.split(';')[0].rstrip('.') or name

def main(iso_path, out_dir, list_only=False):
    iso = pycdlib.PyCdlib(); iso.open(iso_path); n = 0
    for dirpath, dirs, files in iso.walk(iso_path='/'):
        local_dir = os.path.join(out_dir, *[clean(p) for p in dirpath.strip('/').split('/') if p])
        for f in sorted(files):
            src = dirpath.rstrip('/') + '/' + f
            rec = iso.get_record(iso_path=src); size = rec.get_data_length()
            dst = os.path.join(local_dir, clean(f))
            if list_only:
                print(f"{size:>12}  LBA {rec.extent_location():>8}  {src}"); continue
            if os.path.exists(dst) and os.path.getsize(dst) == size:
                continue
            os.makedirs(local_dir, exist_ok=True)
            iso.get_file_from_iso(dst + '.part', iso_path=src)
            os.replace(dst + '.part', dst); n += 1
            print(f"{size:>12}  {src}", flush=True)
    iso.close(); print(f"done, {n} files written this run")

if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2], '--list' in sys.argv)
