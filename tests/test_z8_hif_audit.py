# SPDX-License-Identifier: GPL-2.0-or-later
import struct
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from z8_hif_audit import HIF, AuditError, sps_info, compare_color

def box(kind,body):return struct.pack('>I4s',len(body)+8,kind.encode())+body
def full(kind,body,version=0):return box(kind,bytes([version,0,0,0])+body)
def ue(v):
    b=bin(v+1)[2:];return '0'*(len(b)-1)+b
def sps():
    bits='00000001'+f'{2:08b}{0x60000000:032b}{0x900000000000:048b}{60:08b}'
    bits+=ue(0)+ue(1)+ue(320)+ue(224)+'0'+ue(2)+ue(2)+'1'
    bits+='0'*((-len(bits))%8);raw=int(bits,2).to_bytes(len(bits)//8,'big');out=bytearray(b'\x42\x01');zeros=0
    for b in raw:
        if zeros==2 and b<=3:out.append(3);zeros=0
        out.append(b);zeros=zeros+1 if b==0 else 0
    return bytes(out)
def hvcc(ps=None):
    ps=ps or [b'\x40\x01\xff',sps(),b'\x44\x01\xff']
    info=sps_info(ps[1]);head=bytes([1,info['profile_idc']])+info['compatibility'].to_bytes(4,'big')+info['constraints'].to_bytes(6,'big')
    head+=bytes([info['level_idc']])+b'\xf0\0\xfc\xfd\xfa\xfa\0\0\x0f\x03'
    return head+b''.join(bytes([0x80|t])+b'\0\1'+len(n).to_bytes(2,'big')+n for t,n in zip((32,33,34),ps))
def fixture(hidden=(),unknown_flags=0,refs_override=None):
    payloads=[b'\0\0\0\0'+struct.pack('>HH',320,224),b'\0\0\0\3\x26\x01\x80',
              b'\0\0\0\0II\x2a\0\x08\0\0\0\0\0\0\0\0\0']
    ftyp=box('ftyp',b'heix\0\0\0\0mif1heix')
    def meta(offset):
        infe=b''.join(box('infe',bytes([2])+((1 if i in hidden else 0)|unknown_flags).to_bytes(3,'big')+
                     struct.pack('>HH',i,0)+typ.encode()+b'x\0') for i,typ in enumerate(('grid','hvc1','Exif'),1))
        rows=[]
        for i,p in enumerate(payloads,1):rows.append(struct.pack('>HHHII',i,0,1,offset,len(p)));offset+=len(p)
        iloc=full('iloc',b'\x44\0\0\3'+b''.join(rows))
        props=box('ipco',full('ispe',struct.pack('>II',320,224))+full('pixi',b'\3\x0a\x0a\x0a')+box('hvcC',hvcc()))
        assoc=full('ipma',struct.pack('>I',2)+b'\0\1\2\x81\x82\0\2\3\x81\x82\x83')
        refs=full('iref',refs_override if refs_override is not None else box('dimg',b'\0\1\0\1\0\2')+box('cdsc',b'\0\3\0\1\0\1'))
        return full('meta',full('pitm',b'\0\1')+full('iinf',b'\0\3'+infe)+iloc+box('iprp',props+assoc)+refs)
    m=meta(0);return ftyp+meta(len(ftyp)+len(m)+8)+box('mdat',b''.join(payloads))

class HIFAuditTests(unittest.TestCase):
    def test_color_signaling_difference_is_reported_not_silently_normalized(self):
        container=dict(primaries=9,transfer=18,matrix=9,full_range=True)
        stream=dict(color_primaries='bt2020',color_transfer='arib-std-b67',color_space='bt2020nc',color_range='tv')
        row=compare_color(container,stream)
        self.assertFalse(row['consistent']);self.assertEqual(row['differences'],['full_range'])
        self.assertTrue(compare_color(dict(container,full_range=False),stream)['consistent'])

    def test_pixi_optional_only_for_hidden_hevc_leaf_but_present_depth_checked(self):
        data=fixture(hidden=(2,3)).replace(b'\0\2\3\x81\x82\x83',b'\0\2\3\x81\0\x83')
        h=HIF(data);self.assertEqual(h.plan()['tiles'],[2]);self.assertEqual(h.codec(2)['sps']['luma_depth'],10)
        visible=fixture().replace(b'\0\2\3\x81\x82\x83',b'\0\2\3\x81\0\x83')
        with self.assertRaises(AuditError):HIF(visible).plan()
        bad=fixture(hidden=(2,3)).replace(b'\3\x0a\x0a\x0a',b'\3\x08\x08\x08')
        with self.assertRaises(AuditError):HIF(bad).plan()

    def test_hidden_grid_input_and_metadata_are_not_display_items(self):
        h=HIF(fixture(hidden=(2,3)));p=h.plan()
        self.assertEqual(p['hidden_items'],[2,3]);self.assertFalse(h.hidden[h.primary])
        self.assertEqual(p['item_roles']['2'],['derived_input'])
        self.assertEqual(p['item_roles']['3'],['metadata'])

    def test_hidden_primary_and_reserved_flags_rejected(self):
        for data,message in ((fixture(hidden=(1,)),'Hidden primary'),(fixture(unknown_flags=2),'infe flags'),
                             (fixture(unknown_flags=0x800000),'infe flags')):
            with self.subTest(message=message),self.assertRaisesRegex(AuditError,message):HIF(data).plan()

    def test_hidden_reference_roles_wrong_direction_types_and_cycles_rejected(self):
        good_dimg=box('dimg',b'\0\1\0\1\0\2');good_cdsc=box('cdsc',b'\0\3\0\1\0\1')
        for refs in (box('dimg',b'\0\2\0\1\0\1')+good_cdsc,
                     good_dimg+box('cdsc',b'\0\1\0\1\0\3'),
                     good_dimg+good_cdsc+box('thmb',b'\0\3\0\1\0\1'),
                     good_dimg+good_cdsc+box('thmb',b'\0\2\0\1\0\2')):
            with self.subTest(refs=refs),self.assertRaises(AuditError):HIF(fixture(hidden=(2,3),refs_override=refs)).plan()

    def test_complete_grid_metadata_and_byte_ranges(self):
        h=HIF(fixture());p=h.plan()
        self.assertEqual((p['width'],p['height'],p['rows'],p['columns']),(320,224,1,1))
        self.assertEqual(p['tiles'],[2]);self.assertEqual(p['exif'][0]['byte_order'],'II')
        n=h.tile_stream(2)
        self.assertEqual(n[-7:],b'\0\0\0\1\x26\x01\x80')
        self.assertEqual(h.codec(2)['sps']['luma_depth'],10)

    def test_reject_truncated_and_invalid_box_sizes(self):
        data=fixture()
        for bad in (data[:-1],b'\0\0\0\4ftyp',b'\0\0\0\1ftyp'+b'\0'*8):
            with self.subTest(size=len(bad)),self.assertRaises(AuditError):HIF(bad).plan()

    def test_iloc_outside_mdat_rejected(self):
        data=bytearray(fixture());at=data.index(b'iloc')+4+4+4+6
        data[at:at+4]=b'\xff'*4
        with self.assertRaises(AuditError):HIF(bytes(data)).plan()

    def test_pixi_depth_disagreement_rejected(self):
        data=fixture().replace(b'\3\x0a\x0a\x0a',b'\3\x08\x08\x08')
        with self.assertRaises(AuditError):HIF(data).plan()

    def test_nal_length_and_actual_type_rejected(self):
        data=fixture()
        for sample in (b'\0\0\0\x20\x26\x01\x80',b'\0\0\0\3\x28\x01\x80'):
            with self.assertRaises(AuditError):HIF(data.replace(b'\0\0\0\3\x26\x01\x80',sample)).tile_stream(2)

    def test_exif_ifd_offset_bounded(self):
        data=fixture().replace(b'II\x2a\0\x08\0\0\0',b'II\x2a\0\xff\xff\xff\x7f')
        with self.assertRaises(AuditError):HIF(data).plan()

    def test_external_reference_and_partial_overlap_rejected(self):
        data=bytearray(fixture());item=data.index(b'iloc')+12
        data[item+2:item+4]=b'\0\1'
        with self.assertRaisesRegex(AuditError,'External data reference'):HIF(bytes(data))
        data=bytearray(fixture());first=int.from_bytes(data[item+6:item+10],'big')
        data[item+14+6:item+14+10]=(first+4).to_bytes(4,'big')
        with self.assertRaisesRegex(AuditError,'Partially overlapping'):HIF(bytes(data))

if __name__=='__main__':unittest.main()
