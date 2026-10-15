# SPDX-License-Identifier: GPL-2.0-or-later
import copy,hashlib,json,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import z8_heif_receipts as r

def fixture(folder):
    memory={};outputs=[]
    for i in range(3):
        item=dict(width=6,height=4,stride=64,bits=10,chroma='420')
        for plane,rows in (('y',4),('uv',2)):
            address=0x840000000+i*0x10000+(0x1000 if plane=='uv' else 0)
            data=bytes(64*rows);memory[address]=data
            item.update({plane:address,plane+'_bytes':len(data),plane+'_sha256':hashlib.sha256(data).hexdigest()})
        outputs.append(item)
    c1=dict(enabled=True,jobs=1,source_job=1,source_frame=1,retired=1,owned=False,requested=False,failed=False,outputs=outputs)
    codec=dict(enabled=True,jobs=1,source_c1_job=1,source_frame=1,source_c1_output=0,width=6,height=4,
               busy=False,result_valid=True,failed=False,input_sha256=[outputs[0][p+'_sha256'] for p in ('y','uv')])
    states={r.C1:c1,r.CODEC:codec,r.PROTOCOL:dict(enabled=True,jobs=1)};calls=[]
    def command(name,**args):
        calls.append((name,args))
        if name=='query-status':return {'running':False}
        if name=='qom-get':return copy.deepcopy(states[args['property']])
        if name=='pmemsave':Path(args['filename']).write_bytes(memory[args['val']][:args['size']]);return {}
        raise AssertionError(name)
    return SimpleNamespace(out=Path(folder),qmp=SimpleNamespace(command=command)),states,memory,calls

class ReceiptTests(unittest.TestCase):
    def test_native_retirement_requires_exact_retained_completion(self):
        with tempfile.TemporaryDirectory() as d:
            g,s,m,calls=fixture(d);c=s[r.CODEC];c['result_valid']=False
            self.assertFalse(r.codec_completed(c))
            c['history']=[dict(job=c['jobs'],**{k:c[k] for k in ('source_c1_job','source_frame','source_c1_output','width','height','input_sha256')})]
            self.assertTrue(r.codec_completed(c));r.Session(g).capture_job()
            c['history'][0]['job']=2;self.assertFalse(r.codec_completed(c))

    def test_default_no_ram_dump_and_each_job_separate(self):
        with tempfile.TemporaryDirectory() as d:
            g,s,m,c=fixture(d);session=r.Session(g)
            self.assertIsNone(session.capture_source())
            first=session.capture_job();self.assertTrue((Path(d)/first).is_file())
            s[r.CODEC]['jobs']=s[r.PROTOCOL]['jobs']=2
            second=session.capture_job();self.assertNotEqual(first,second)
            self.assertFalse(any(n=='pmemsave' for n,a in c))
            with self.assertRaises(ValueError):session.capture_job()

    def test_source_exact_hash_and_one_export_per_photo(self):
        with tempfile.TemporaryDirectory() as d:
            g,s,m,c=fixture(d);session=r.Session(g,export_c1=True)
            path=session.capture_source();receipt=json.loads((Path(d)/path).read_text())
            self.assertTrue(receipt['verified']);self.assertEqual(session.capture_source(),path)
            self.assertEqual(sum(n=='pmemsave' for n,a in c),6)
            self.assertEqual(session.image_bytes,3*384)

    def test_budget_exhausted_before_memory_reads(self):
        with tempfile.TemporaryDirectory() as d:
            g,s,m,c=fixture(d);session=r.Session(g,export_c1=True);session.image_bytes=r.MAX_RUN_BYTES
            with self.assertRaisesRegex(ValueError,'budget'):session.capture_source()
            self.assertFalse(any(n=='pmemsave' for n,a in c))
            session.qom_bytes=r.MAX_QOM_TOTAL
            with self.assertRaisesRegex(ValueError,'budget'):session.capture_job()

    def test_changed_identity_or_bad_hash_never_publishes_verified(self):
        for identity in (False,True):
            with self.subTest(identity=identity),tempfile.TemporaryDirectory() as d:
                g,s,m,c=fixture(d);original=g.qmp.command
                if not identity:m[next(iter(m))]=b'\1'+m[next(iter(m))][1:]
                def command(name,**kw):
                    result=original(name,**kw)
                    if identity and name=='pmemsave':s[r.C1]['source_frame']=2
                    return result
                g.qmp.command=command
                with self.assertRaises(ValueError):r.Session(g,export_c1=True).capture_source()
                self.assertFalse(list(Path(d).rglob('source.json')))

    def test_job_identity_change_and_running_guest_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            g,s,m,c=fixture(d);original=g.qmp.command
            def command(name,**kw):
                result=original(name,**kw)
                if name=='qom-get' and kw['property']==r.PROTOCOL:s[r.CODEC]['source_frame']=2
                return result
            g.qmp.command=command
            with self.assertRaises(ValueError):r.Session(g).capture_job()
            g.qmp.command=lambda *a,**kw:{'running':True}
            with self.assertRaisesRegex(ValueError,'paused'):r.Session(g,export_c1=True).capture_source()

    def test_overlap_and_bad_stride_rejected(self):
        for kind in ('overlap','stride'):
            with tempfile.TemporaryDirectory() as d:
                g,s,m,c=fixture(d)
                if kind=='overlap':s[r.C1]['outputs'][1]['y']=s[r.C1]['outputs'][0]['y']
                else:s[r.C1]['outputs'][0]['stride']=32
                with self.assertRaises(ValueError):r.Session(g,export_c1=True).capture_source()
                self.assertFalse(any(n=='pmemsave' for n,a in c))
if __name__=='__main__':unittest.main()
