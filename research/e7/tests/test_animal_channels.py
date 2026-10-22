# SPDX-License-Identifier: GPL-2.0-or-later
"""Reference-contract checks; not an original-DSP numerical oracle."""
import math
import os,sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from animal_channels import SHAPES,LABELS,load_spec,decode_candidates,suppress,exp_approx,sigmoid,f32

def heads():return [[-12.0]*(w*h*c) for w,h,c in SHAPES]
def candidate(t,head,anchor,x,y,cls,*,obj=8.0,score=8.0,tx=0,ty=0,tw=0,th=0):
    w,h,_=SHAPES[head];area=w*h;cell=y*w+x;base=anchor*15
    for k,v in [(0,tx),(1,ty),(2,tw),(3,th),(4,obj),(5+cls,score)]:t[head][(base+k)*area+cell]=v

@unittest.skipUnless(os.environ.get('E7_ANIMAL_INVENTORY'),'Private original calibration inventory required')
class AnimalChannelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.spec=load_spec()
    def test_silent_heads(self):self.assertEqual(decode_candidates(heads(),self.spec)['before_suppression'],[])
    def test_small_anchor_eye_geometry(self):
        t=heads();candidate(t,0,0,3,4,2);r=decode_candidates(t,self.spec)['before_suppression'][0]
        self.assertAlmostEqual(r['cx'],3.5/24,places=6);self.assertAlmostEqual(r['cy'],4.5/16,places=6)
        self.assertAlmostEqual(r['w'],7/384,places=6);self.assertAlmostEqual(r['h'],7/256,places=6)
        self.assertEqual((r['class'],r['debug_label']),(2,'AnimalR'))
    def test_large_anchor_body_geometry(self):
        t=heads();candidate(t,1,0,1,2,0);r=decode_candidates(t,self.spec)['before_suppression'][0]
        self.assertAlmostEqual(r['cx'],1.5/12,places=6);self.assertAlmostEqual(r['cy'],2.5/8,places=6)
        self.assertAlmostEqual(r['w'],160/384,places=6);self.assertAlmostEqual(r['h'],140/256,places=6)
    def test_exponent_width_height(self):
        t=heads();candidate(t,0,0,3,4,2,tw=math.log(2),th=math.log(3))
        r=decode_candidates(t,self.spec)['before_suppression'][0]
        self.assertAlmostEqual(r['w'],14/384,places=5);self.assertAlmostEqual(r['h'],21/256,places=5)
    def test_xy_logits_move_only_center(self):
        t=heads();candidate(t,0,0,3,4,2,tx=math.log(3),ty=-math.log(3))
        r=decode_candidates(t,self.spec)['before_suppression'][0]
        self.assertAlmostEqual(r['cx'],3.75/24,places=6);self.assertAlmostEqual(r['cy'],4.25/16,places=6)
    def test_disallowed_body_on_small_anchor(self):
        t=heads();candidate(t,0,0,1,1,0)
        self.assertEqual(decode_candidates(t,self.spec)['before_suppression'],[])
    def test_all_twelve_whitelist_entries(self):
        for key,cls in self.spec['pairs']:
            with self.subTest(key=key,cls=cls):
                t=heads();head=key>>4;local=(key&15)-2*head;candidate(t,head,local,1,1,cls)
                r=decode_candidates(t,self.spec)['before_suppression']
                self.assertEqual(len(r),1);self.assertEqual(r[0]['class'],cls)
    def test_objectness_gate(self):
        t=heads();candidate(t,0,0,1,1,2,obj=-1.0)
        self.assertEqual(decode_candidates(t,self.spec)['before_suppression'],[])
    def test_joint_probability_gate(self):
        t=heads();candidate(t,0,0,1,1,2,obj=0.0,score=0.0)
        self.assertEqual(decode_candidates(t,self.spec)['before_suppression'],[])
    def test_calibration_is_not_probability_times_1000(self):
        t=heads();candidate(t,0,0,1,1,2,obj=math.log(9),score=math.log(9))
        r=decode_candidates(t,self.spec)['before_suppression'][0]
        self.assertAlmostEqual(r['joint_probability'],.81,places=6)
        self.assertNotEqual(r['score'],810);self.assertTrue(0<=r['score']<=1000)
    def test_candidate_cap_is_before_suppression(self):
        t=heads()
        for i in range(40):candidate(t,0,0,i%24,i//24,2)
        r=decode_candidates(t,self.spec)
        self.assertTrue(r['candidate_cap_reached']);self.assertEqual(len(r['before_suppression']),32)
        self.assertEqual(r['before_suppression'][-1]['cell'],[7,1])
    def test_center_gate_is_not_iou_gate(self):
        a=dict(cx=.25,cy=.25,w=.2,h=.2,score=900,**{'class':2})
        b=dict(cx=.40,cy=.40,w=.2,h=.2,score=800,**{'class':2})
        # Intersection is tiny, but |dx| and |dy| are each less than 0.16.
        self.assertEqual(len(suppress([a,b],.8)),1)
    def test_different_classes_not_suppressed(self):
        a=dict(cx=.25,cy=.25,w=.2,h=.2,score=900,**{'class':2})
        b={**a,'class':4,'score':800}
        self.assertEqual(len(suppress([a,b],.8)),2)
    def test_scalar_exp_against_mathematical_exp(self):
        worst=max(abs(exp_approx(i/16)/math.exp(i/16)-1) for i in range(-192,193))
        self.assertLess(worst,3e-6)
    def test_input_shape_rejected(self):
        with self.assertRaises(ValueError):decode_candidates([[],[]],self.spec)

if __name__=='__main__':unittest.main(verbosity=2)
