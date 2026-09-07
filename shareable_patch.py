#!/usr/bin/env python3
"""Build and verify the shareable Apple 1.3 theme delta.

This module never opens a device. It accepts a regular 160 MiB firmware
partition dump, verifies its native iPod 5.5 structure and applies only the
small byte ranges in firmware-theme-002.patch.json. Bytes outside those
ranges are preserved, including per-device boot metadata.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import stat
import struct

SIZE=167772160
PREFIX=13905920
SECTOR=2048
OSOS_START=0x5000
OSOS_LENGTH=7561216
RSRC_START=0x73b800
RSRC_LENGTH=5244928
AUPD_START=0xc3c800
AUPD_LENGTH=1075200
ORIGINAL_OSOS_SHA='784ae3d5540fd2f89e8c947b93629e97f62a06dc311d9745aab143e4db6bb251'
ORIGINAL_RSRC_SHA='080d43f7cf87fb4b4a3f079ce7f35731a217031bf59f1fc80cb87fa49821af11'
ORIGINAL_AUPD_SHA='86c24824839bc8a94dd53a14fca64894eac6d60059ada1db6c0235008dbb0a87'
TARGET_OSOS_SHA='4a51edfbc7e3db2008fd218d5e3733df0300def6fc4711dc258fdbedb4d5c327'
TARGET_FULL_REFERENCE_SHA='f3503843f58391a1e555601fea6064fa5dc6012d3345042f06de94f29dd1142b'
MANIFEST_NAME='firmware-theme-002.patch.json'

def sha(data):return hashlib.sha256(data).hexdigest()
def require(value,message):
    if not value:raise ValueError(message)

def regular(path):
    path=Path(path)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        require(stat.S_ISREG(os.fstat(fd).st_mode),'Input must be a regular file, never a device.')
        chunks=[];total=0
        while total<=SIZE:
            chunk=os.read(fd,min(8*1024*1024,SIZE+1-total))
            if not chunk:break
            chunks.append(chunk);total+=len(chunk)
        return b''.join(chunks)
    finally:os.close(fd)

def native_layout(data,require_full=True):
    require(len(data)==SIZE if require_full else len(data)>=PREFIX,'Expected an iPod firmware partition length.')
    require(data[0x100:0x104]==b']ih[' and struct.unpack_from('<H',data,0x10a)[0]==3,
            'Not an Apple iPod firmware format-3 partition.')
    directory=struct.unpack_from('<I',data,0x104)[0]+512
    require(directory==0x4200,'Unexpected iPod firmware directory offset.')
    expected=[('osos',OSOS_START,OSOS_LENGTH,ORIGINAL_OSOS_SHA),
              ('rsrc',RSRC_START,RSRC_LENGTH,ORIGINAL_RSRC_SHA),
              ('aupd',AUPD_START,AUPD_LENGTH,ORIGINAL_AUPD_SHA)]
    entries=[]
    for i,(kind,start,length,digest) in enumerate(expected):
        at=directory+i*40
        require(data[at:at+4]==b'!ATA' and data[at+4:at+8]==kind[::-1].encode('ascii'),
                'Firmware image order or type does not match iPod 5.5 1.3.')
        require(struct.unpack_from('<II',data,at+12)==(start-SECTOR,length),'Unexpected '+kind+' extent.')
        require(start+length<=len(data),'Truncated '+kind+' image.')
        payload=data[start:start+length]
        require(sha(payload)==digest,'Source '+kind+' image hash does not match pristine Apple 1.3.')
        if kind in ('osos','rsrc'):
            require(sum(payload)&0xffffffff==struct.unpack_from('<I',data,at+28)[0],kind+' checksum is invalid.')
        entries.append({'kind':kind,'directory_offset':at,'file_offset':start,'length':length,
                        'sha256':digest,'checksum':struct.unpack_from('<I',data,at+28)[0]})
    return {'directory_offset':directory,'images':entries}

def load_manifest(path):
    p=Path(path);raw=regular(p)
    require(len(raw)<20*1024*1024,'Manifest is unexpectedly large.')
    m=json.loads(raw)
    require(m.get('schema')==1 and m.get('experiment')=='002-integral-apple13-share',
            'Unsupported shareable patch manifest.')
    require(m.get('bytes')==SIZE and m.get('sector_size')==SECTOR,'Manifest geometry mismatch.')
    require(m.get('recovery_prefix_bytes')==PREFIX and m.get('checksum_sector')==0x4000 and
            m.get('checksum_sector_last') is True,'Manifest safety metadata mismatch.')
    require(m.get('target_osos_sha256')==TARGET_OSOS_SHA and
            isinstance(m.get('candidate_reference_sha256'),str) and
            len(m['candidate_reference_sha256'])==64 and
            all(c in '0123456789abcdef' for c in m['candidate_reference_sha256']),
            'Manifest target fingerprint mismatch.')
    spans=m.get('changes');require(isinstance(spans,list) and spans,'Manifest contains no patch ranges.')
    previous=0;decoded=[];changed=0
    for e in spans:
        at=e.get('offset');length=e.get('length')
        require(type(at)is int and type(length)is int and at>=previous and length>0 and at+length<=SIZE,
                'Manifest has invalid or overlapping range.')
        old=base64.b64decode(e['before_base64'],validate=True);new=base64.b64decode(e['after_base64'],validate=True)
        require(len(old)==len(new)==length,'Manifest range payload has the wrong size.')
        changed+=sum(a!=b for a,b in zip(old,new))
        decoded.append((at,old,new,e.get('reason','unlabelled')));previous=at+length
    sectors=sorted({at//SECTOR*SECTOR for at,old,new,_ in decoded for at in range(at,at+len(old))})
    require(changed==m.get('changed_byte_count') and len(sectors)==m.get('physical_sector_count') and
            m.get('physical_write_bytes')==len(sectors)*SECTOR and 0x4000 in sectors,
            'Manifest sector set is inconsistent.')
    require(tuple(m.get('sector_write_order',[]))==tuple([s for s in sectors if s!=0x4000]+[0x4000]),
            'Manifest write order is not payload-first/checksum-last.')
    return m,decoded,sectors

def apply_patch(data,manifest_path):
    m,spans,sectors=load_manifest(manifest_path)
    layout=native_layout(data)
    require(sha(data)!=m['candidate_reference_sha256'],'Input is already the shareable candidate.')
    out=bytearray(data);changed=0
    for at,old,new,_ in spans:
        require(data[at:at+len(old)]==old,'Input differs at a reviewed patch range; refusing to guess or merge.')
        out[at:at+len(new)]=new;changed+=sum(a!=b for a,b in zip(old,new))
    result=bytes(out)
    require(sum(a!=b for a,b in zip(data,result))==m['changed_byte_count'],'Patch byte count mismatch.')
    for at,old,new,_ in spans:require(result[at:at+len(new)]==new,'Patch application did not settle.')
    # Any source metadata outside the manifest is deliberately retained.
    cursor=0
    for at,old,new,_ in spans:
        require(data[cursor:at]==result[cursor:at],'Unexpected byte changed outside manifest ranges.')
        cursor=at+len(old)
    require(data[cursor:]==result[cursor:],'Unexpected trailing byte changed outside manifest ranges.')
    require(sha(result[OSOS_START:OSOS_START+OSOS_LENGTH])==m['target_osos_sha256'],
            'Resulting OSOS is not the reviewed Apple 1.3 image.')
    require(sum(result[OSOS_START:OSOS_START+OSOS_LENGTH])&0xffffffff==struct.unpack_from('<I',result,0x421c)[0],
            'Resulting OSOS checksum does not match its directory.')
    require(result[RSRC_START:RSRC_START+RSRC_LENGTH]==data[RSRC_START:RSRC_START+RSRC_LENGTH],
            'RSRC changed unexpectedly.')
    require(result[AUPD_START:AUPD_START+AUPD_LENGTH]==data[AUPD_START:AUPD_START+AUPD_LENGTH],
            'AUPD changed unexpectedly.')
    require(max(sectors)+SECTOR<=PREFIX,'Patch extends beyond the tested native prefix.')
    write_order=[s for s in sectors if s!=0x4000]+[0x4000]
    return result,{'source_full_sha256':sha(data),'target_full_sha256':sha(result),
                    'changed_byte_count':changed,'physical_sector_count':len(sectors),
                    'sector_write_order':write_order,'source_native':layout}

def write_new(path,data):
    p=Path(path);require(not p.exists() and not p.is_symlink(),'Output must be a new regular file.')
    fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        total=0
        while total<len(data):
            written=os.write(fd,data[total:]);require(written>0,'Short output write.');total+=written
        os.fsync(fd)
    finally:os.close(fd)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ('verify','build'):
        q=sub.add_parser(name);q.add_argument('source',type=Path);q.add_argument('--manifest',type=Path,default=Path(__file__).with_name(MANIFEST_NAME))
        if name=='build':q.add_argument('output',type=Path)
    a=parser.parse_args();source=regular(a.source)
    result,report=apply_patch(source,a.manifest)
    if a.command=='build':write_new(a.output,result)
    print(json.dumps(report,indent=2))

if __name__=='__main__':
    try:main()
    except Exception as e:
        print(type(e).__name__+': '+str(e),file=__import__('sys').stderr);raise SystemExit(1)
