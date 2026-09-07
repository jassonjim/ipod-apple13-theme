#!/usr/bin/env python3
"""Read-only generic backup for a compatible iPod 5.5 firmware partition.

The USB serial number is never used. Exactly one vendor/product-compatible
iPod must be connected. Two complete 160 MiB reads are saved and compared.
This program has no pwrite, cache synchronization or firmware mutation path.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import time

_flash_spec=importlib.util.spec_from_file_location('flash_shareable_patch_local',Path(__file__).with_name('flash_shareable_patch.py'))
flasher=importlib.util.module_from_spec(_flash_spec);_flash_spec.loader.exec_module(flasher)
SIZE=flasher.SIZE;discover=flasher.discover;guard=flasher.guard;open_raw=flasher.open_raw
read_full=flasher.read_full;sha=flasher.sha;require=flasher.require

def durable(path,data):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        total=0
        while total<len(data):
            written=os.write(fd,data[total:]);require(written>0,'Short local backup write.');total+=written
        os.fsync(fd)
    finally:os.close(fd)

def backup(output):
    require(os.geteuid()==0,'Administrator authentication is required for raw read access.')
    signal.signal(signal.SIGALRM, flasher.timeout)
    dest=Path(output).resolve();require(not dest.exists() and not dest.is_symlink(),'Backup directory must be new.')
    identity=discover();dest.mkdir(mode=0o700)
    try:
        started={'mode':'read_only_backup','identity':identity,'device_written':False,'started_ns':time.time_ns()}
        durable(dest/'started.json',(json.dumps(started,indent=2)+'\n').encode())
        p=subprocess.run(['/usr/sbin/diskutil','unmountDisk','/dev/'+identity['disk_node']],capture_output=True,text=True,timeout=30)
        require(p.returncode==0,'Could not unmount iPod. '+p.stderr.strip())
        guard(identity);fd=open_raw(identity)
        try:first=read_full(fd,'Read 1');guard(identity)
        finally:os.close(fd)
        durable(dest/'firmware-read-1.bin',first)
        fd=open_raw(identity)
        try:second=read_full(fd,'Read 2');guard(identity)
        finally:os.close(fd)
        durable(dest/'firmware-read-2.bin',second)
        require(first==second,'Two complete firmware reads differ; discard this backup.')
        report={'mode':'read_only_backup','identity':identity,'bytes':SIZE,
                'read_1_sha256':sha(first),'read_2_sha256':sha(second),
                'repeated_reads_match':True,'device_written':False,
                'source_for_shareable_patch':'firmware-read-1.bin'}
        durable(dest/'backup-report.json',(json.dumps(report,indent=2)+'\n').encode())
        print('BACKUP VERIFIED; NO DEVICE WRITES')
        print(json.dumps(report,indent=2))
    except BaseException:
        print('BACKUP STOPPED; NO FIRMWARE WRITES',file=__import__('sys').stderr)
        raise

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True)
    try:backup(p.parse_args().output)
    except Exception as e:print(type(e).__name__+': '+str(e),file=__import__('sys').stderr);raise SystemExit(1)
