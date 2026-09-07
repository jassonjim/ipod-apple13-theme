#!/usr/bin/env python3
"""Device installer for the shareable Apple 1.3 theme.

The installer is deliberately model-generic: it selects one connected USB
iPod with vendor 1452/product 4617, then checks the 5.5 firmware partition and
native image fingerprints. It does not contain a serial number or disk number.
All writes are full-sector, individually verified, checksum-sector last, with a
complete readback. A local firmware snapshot is saved before the first write.
"""
from __future__ import annotations
import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import signal
import stat
import struct
import subprocess
import sys
import time

_share_spec=importlib.util.spec_from_file_location('shareable_patch_local',Path(__file__).with_name('shareable_patch.py'))
shareable_patch=importlib.util.module_from_spec(_share_spec);_share_spec.loader.exec_module(shareable_patch)
SIZE=shareable_patch.SIZE;PREFIX=shareable_patch.PREFIX;SECTOR=shareable_patch.SECTOR
MANIFEST_NAME=shareable_patch.MANIFEST_NAME;load_manifest=shareable_patch.load_manifest
apply_patch=shareable_patch.apply_patch;regular=shareable_patch.regular;sha=shareable_patch.sha;require=shareable_patch.require

VENDOR=1452
PRODUCT=4617
DKIOCSYNCHRONIZECACHE=0x20006416
DKIOCGETBLOCKSIZE=0x40046418
TIMEOUT=30
PACKAGE=Path(__file__).resolve().parent
MANIFEST=PACKAGE/MANIFEST_NAME
EXPERIMENT='002-integral-apple13-share'

def timeout(signum,frame):raise TimeoutError('A raw iPod operation did not complete within 30 seconds.')

def timed(call,*args):
    signal.alarm(TIMEOUT)
    try:return call(*args)
    finally:signal.alarm(0)

def info(node):
    p=subprocess.run(['/usr/sbin/diskutil','info','-plist',node],capture_output=True,check=True,timeout=15)
    return plistlib.loads(p.stdout)

def usb_devices():
    p=subprocess.run(['/usr/sbin/ioreg','-a','-r','-c','IOUSBHostDevice','-l','-w','0'],capture_output=True,check=True,timeout=15)
    return plistlib.loads(p.stdout)

def walk(node):
    yield node
    for child in node.get('IORegistryEntryChildren',[]):yield from walk(child)

def discover():
    matches=[]
    for device in usb_devices():
        if device.get('idVendor')!=VENDOR or device.get('idProduct')!=PRODUCT:continue
        whole=[n for n in walk(device) if n.get('Whole') is True and isinstance(n.get('BSD Name'),str)
               and n['BSD Name'].startswith('disk') and n['BSD Name'][4:].isdigit()]
        if len(whole)!=1:continue
        disk=whole[0];firmware=[n for n in walk(disk) if n.get('BSD Name')==disk['BSD Name']+'s2']
        if len(firmware)==1:matches.append((device,disk,firmware[0]))
    require(len(matches)==1,'Exactly one compatible USB iPod 5.5 must be connected.')
    device,disk,firmware=matches[0]
    d=info('/dev/'+disk['BSD Name']);f=info('/dev/'+firmware['BSD Name'])
    require(d.get('DeviceNode')=='/dev/'+disk['BSD Name'] and d.get('Internal') is False,
            'Selected medium is not an external iPod disk.')
    require(d.get('Content')=='Apple_partition_scheme' and d.get('MediaName')=='iPod' and d.get('DeviceBlockSize')==SECTOR,
            'Selected medium is not an Apple iPod partition map.')
    require(f.get('ParentWholeDisk')==disk['BSD Name'] and f.get('TotalSize')==SIZE and
            f.get('DeviceBlockSize')==SECTOR and f.get('PartitionMapPartitionOffset')==129024 and
            f.get('Content')=='Apple_MDFW','Firmware partition geometry is not iPod 5.5 compatible.')
    return {'disk_node':disk['BSD Name'],'firmware_node':firmware['BSD Name'],
            'disk':{k:d.get(k) for k in ('TotalSize','DeviceBlockSize','Content','MediaName')},
            'firmware':{k:f.get(k) for k in ('TotalSize','DeviceBlockSize','PartitionMapPartitionOffset','Content')},
            'usb_model':{'vendor':VENDOR,'product':PRODUCT}}

def data_unmounted(identity):
    data=info('/dev/'+identity['disk_node']+'s3')
    require(data.get('ParentWholeDisk')==identity['disk_node'] and
            data.get('Content') in ('Apple_HFS','DOS_FAT_32','Microsoft Basic Data'),
            'Unexpected iPod data partition.')
    require(not data.get('MountPoint'),'The iPod data volume must be unmounted.')

def guard(identity):
    fresh=discover()
    require(fresh==identity,'USB/media identity changed; transaction stopped.')
    data_unmounted(identity)

def open_raw(identity,writable=False):
    guard(identity)
    flags=(os.O_RDWR if writable else os.O_RDONLY)|os.O_NOFOLLOW
    fd=os.open('/dev/r'+identity['firmware_node'],flags)
    try:
        require(stat.S_ISCHR(os.fstat(fd).st_mode),'Raw firmware path is not a character device.')
        size=bytearray(4);fcntl.ioctl(fd,DKIOCGETBLOCKSIZE,size,True)
        require(struct.unpack('<I',size)[0]==SECTOR,'Raw sector size is not 2048 bytes.')
        guard(identity);return fd
    except BaseException:
        os.close(fd);raise

def read_full(fd,label):
    result=bytearray();next_progress=32*1024*1024
    while len(result)<SIZE:
        at=len(result);requested=min(1024*1024,SIZE-at)
        try:chunk=timed(os.read,fd,requested)
        except TimeoutError as e:raise TimeoutError(f'{label} stalled at byte {at} (0x{at:x}).') from e
        require(chunk,'Short raw firmware read.')
        result.extend(chunk)
        if len(result)>=next_progress or len(result)==SIZE:
            print(f'{label}: {len(result)}/{SIZE} bytes',flush=True);next_progress+=32*1024*1024
    return bytes(result)

def sync_cache(fd):
    timed(os.fsync,fd);timed(fcntl.ioctl,fd,DKIOCSYNCHRONIZECACHE)

def durable(path,data):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        total=0
        while total<len(data):
            written=os.write(fd,data[total:]);require(written>0,'Short local evidence write.');total+=written
        os.fsync(fd)
    finally:os.close(fd)

def json_durable(path,value):durable(path,(json.dumps(value,indent=2)+'\n').encode())

def create_run():
    dest=Path.cwd()/('ipod-theme-share-'+str(time.time_ns()))
    dest.mkdir(mode=0o700)
    return dest

def write_transaction(fd,before,target,offsets,identity,dest):
    # Check every target before the first write and check the connection again
    # before each sector. The checksum sector is always last.
    guard(identity)
    for at in offsets:require(timed(os.pread,fd,SECTOR,at)==before[at:at+SECTOR],'Target changed before writing.')
    sync_cache(fd)
    attempted=[];completed=[]
    events=dest/'events.jsonl';stream=events.open('x',encoding='utf-8')
    try:
        for at in offsets:
            guard(identity);current=timed(os.pread,fd,SECTOR,at)
            require(current==before[at:at+SECTOR],'Target changed during writing.')
            if current==target[at:at+SECTOR]:continue
            record={'state':'write_attempt','partition_offset':at};stream.write(json.dumps(record)+'\n');stream.flush();os.fsync(stream.fileno());attempted.append(at);print('write_attempt '+hex(at),flush=True)
            written=timed(os.pwrite,fd,target[at:at+SECTOR],at)
            require(written==SECTOR,'Short sector write; stop and use the saved recovery directory.')
            sync_cache(fd)
            require(timed(os.pread,fd,SECTOR,at)==target[at:at+SECTOR],'Sector readback mismatch; stop immediately.')
            record={'state':'sector_verified','partition_offset':at};stream.write(json.dumps(record)+'\n');stream.flush();os.fsync(stream.fileno());completed.append(at);print('sector_verified '+hex(at),flush=True)
    finally:stream.close()
    return attempted,completed

def restore_allowed(current,before,sectors):
    require(len(current)==len(before)==SIZE,'Recovery requires complete 160 MiB snapshots.')
    allowed=set(sectors)
    for at in range(0,SIZE,SECTOR):
        if at not in allowed:require(current[at:at+SECTOR]==before[at:at+SECTOR],'Changes outside theme sectors; refusing automatic restore.')

def install():
    signal.signal(signal.SIGALRM,timeout)
    manifest,spans,sectors=load_manifest(MANIFEST)
    identity=discover()
    # No serial number or disk number is compared. The native fingerprints in
    # apply_patch() bind this operation to pristine Apple 1.3 content.
    dest=create_run();print('Evidence directory: '+str(dest),flush=True)
    fd=None;attempted=[];completed=[]
    try:
        json_durable(dest/'started.json',{'experiment':EXPERIMENT,'mode':'install','identity':identity,'device_written':False})
        p=subprocess.run(['/usr/sbin/diskutil','unmountDisk','/dev/'+identity['disk_node']],capture_output=True,text=True,timeout=30)
        require(p.returncode==0,'Could not unmount iPod. '+p.stderr.strip())
        guard(identity);fd=open_raw(identity)
        first=read_full(fd,'Fresh preflight read');guard(identity)
        durable(dest/'firmware-before.bin',first)
        os.close(fd);fd=None
        fd=open_raw(identity)
        second=read_full(fd,'Second preflight read');guard(identity)
        require(second==first,'Two firmware reads differ; no writes made.')
        target,patch_report=apply_patch(first,MANIFEST)
        json_durable(dest/'transaction-target.bin',target)
        plan={'experiment':EXPERIMENT,'mode':'install','identity':identity,
              'source_full_sha256':sha(first),'source_second_sha256':sha(second),
              'target_full_sha256':sha(target),'physical_sector_count':len(sectors),
              'sector_write_order':[s for s in sectors if s!=0x4000]+[0x4000],
              'checksum_sector_last':True,'patch_report':patch_report,
              'device_bound':False,'device_serial_used':False,
              'physical_recovery_tested':False,'power_loss_recovery_proven':False}
        json_durable(dest/'plan.json',plan)
        print('Preflight passed. No firmware has been written.',flush=True)
        print('Review: 155 sectors, checksum sector 0x4000 last, full readback required.',flush=True)
        answer=input('Type FLASH APPLE 1.3 THEME to begin writing: ')
        require(answer=='FLASH APPLE 1.3 THEME','Confirmation did not match; no firmware writes.')
        os.close(fd);fd=None;fd=open_raw(identity,True)
        attempted,completed=write_transaction(fd,first,target,[s for s in sectors if s!=0x4000]+[0x4000],identity,dest)
        guard(identity);readback=read_full(fd,'Complete readback');guard(identity)
        require(readback==target,'Complete readback differs from generated candidate.')
        os.close(fd);fd=None
        json_durable(dest/'result.json',{'experiment':EXPERIMENT,'mode':'install','status':'patch_written_verified',
            'identity':identity,'device_bound':False,'verified_sector_offsets':completed,
            'readback_sha256':sha(readback),'readback_bytes':SIZE,'whole_partition_readback_match':True,
            'device_written':bool(attempted),'boot_test_pending':True})
        print('SHAREABLE THEME WRITTEN AND VERIFIED; BOOT TEST PENDING',flush=True)
        print('The iPod remains unmounted. Share the evidence directory before ejecting/rebooting.',flush=True)
    except BaseException as e:
        try:json_durable(dest/'failed.json',{'error':type(e).__name__+': '+str(e),'device_write_attempted':bool(attempted),'attempted_sector_offsets':attempted,'verified_sector_offsets':completed})
        except Exception:pass
        print('STOPPED. '+('Writes may have occurred; do not reboot or retry. Use the saved evidence directory.' if attempted else 'NO FIRMWARE WRITES.'),file=sys.stderr,flush=True)
        raise
    finally:
        signal.alarm(0)
        if fd is not None:os.close(fd)

def restore(path):
    signal.signal(signal.SIGALRM,timeout)
    manifest,spans,sectors=load_manifest(MANIFEST)
    source=Path(path).resolve(strict=True);require(source.is_dir() and source.name.startswith('ipod-theme-share-'),'Invalid share evidence directory.')
    before=regular(source/'firmware-before.bin');saved_target=regular(source/'transaction-target.bin')
    require(len(before)==SIZE and len(saved_target)==SIZE,'Invalid saved snapshot.')
    expected_target,_=apply_patch(before,MANIFEST)
    require(saved_target==expected_target,'Saved target is not reproducible from its saved source.')
    identity=discover();dest=create_run();print('Recovery evidence directory: '+str(dest),flush=True)
    fd=None;attempted=[];completed=[]
    try:
        p=subprocess.run(['/usr/sbin/diskutil','unmountDisk','/dev/'+identity['disk_node']],capture_output=True,text=True,timeout=30);require(p.returncode==0,'Could not unmount iPod. '+p.stderr.strip())
        guard(identity);fd=open_raw(identity);current=read_full(fd,'Current firmware read');guard(identity)
        second=read_full(fd,'Second recovery read');guard(identity)
        require(second==current,'Two recovery reads differ; no writes made.')
        restore_allowed(current,before,sectors)
        durable(dest/'firmware-before.bin',current);durable(dest/'transaction-target.bin',before)
        print('Recovery preflight passed. No firmware has been written.',flush=True)
        require(input('Type RESTORE APPLE 1.3 THEME to begin recovery: ')=='RESTORE APPLE 1.3 THEME','Confirmation did not match; no writes.')
        os.close(fd);fd=None;fd=open_raw(identity,True)
        attempted,completed=write_transaction(fd,current,before,[s for s in sectors if s!=0x4000]+[0x4000],identity,dest)
        readback=read_full(fd,'Recovery readback');guard(identity)
        require(readback==before,'Recovery readback differs from saved original.')
        os.close(fd);fd=None
        json_durable(dest/'result.json',{'experiment':EXPERIMENT,'mode':'restore','status':'restored_full_verified','identity':identity,'device_bound':False,'verified_sector_offsets':completed,'readback_sha256':sha(readback),'readback_bytes':SIZE,'device_written':bool(attempted),'boot_test_pending':True})
        print('ORIGINAL SNAPSHOT RESTORED AND VERIFIED; BOOT TEST PENDING',flush=True)
    except BaseException as e:
        try:json_durable(dest/'failed.json',{'error':type(e).__name__+': '+str(e),'device_write_attempted':bool(attempted),'attempted_sector_offsets':attempted,'verified_sector_offsets':completed})
        except Exception:pass
        print('STOPPED. '+('Writes may have occurred; do not reboot or retry.' if attempted else 'NO FIRMWARE WRITES.'),file=sys.stderr,flush=True);raise
    finally:
        signal.alarm(0)
        if fd is not None:os.close(fd)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);m=parser.add_mutually_exclusive_group(required=True)
    m.add_argument('--install',action='store_true');m.add_argument('--restore-from');m.add_argument('--check-local',action='store_true')
    args=parser.parse_args()
    try:
        if args.check_local:
            manifest,spans,sectors=load_manifest(MANIFEST)
            print(json.dumps({'status':'local_package_valid','experiment':manifest['experiment'],'patch_ranges':len(spans),'physical_sectors':len(sectors),'device_access':False},indent=2))
        elif args.install:install()
        else:restore(args.restore_from)
    except Exception as e:
        print(type(e).__name__+': '+str(e),file=sys.stderr);sys.exit(1)
