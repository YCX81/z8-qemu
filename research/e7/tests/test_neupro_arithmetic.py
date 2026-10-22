# SPDX-License-Identifier: GPL-2.0-or-later
import importlib.util
import os
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
HAS_TORCH=importlib.util.find_spec('torch') is not None


@unittest.skipUnless(HAS_TORCH,'Optional CPU Torch dependency required')
class ArithmeticContracts(unittest.TestCase):
    def test_rounding_modes_include_negative_ties(self):
        import numpy as np
        from neupro_forward import shift
        x=np.array([-5,-3,-1,1,3,5],dtype=np.int64)
        self.assertEqual(shift(x,1,'rne').tolist(),[-2,-2,0,0,2,2])
        self.assertEqual(shift(x,1,'rtz').tolist(),[-2,-1,0,0,1,2])
        self.assertEqual(shift(x,1,'floor').tolist(),[-3,-2,-1,0,1,2])
        self.assertEqual(shift(x,1,'nearest_up').tolist(),[-2,-1,0,1,2,3])
        for bits in (-1,49):
            with self.assertRaises(ValueError):shift(x,bits,'rne')
        with self.assertRaises(ValueError):shift(x,1,'unknown')

    def test_exact_convolution_and_bound(self):
        import numpy as np
        from neupro_forward import conv_exact
        x=np.array([[[1,2],[3,4]]],dtype=np.int64)
        y,bound=conv_exact(x,np.array([[[[2]]]],dtype=np.int64),(1,1),(0,0,0,0))
        self.assertEqual(y.tolist(),[[[2,4],[6,8]]])
        self.assertLess(bound,2**53)
        with self.assertRaises(ValueError):
            conv_exact(np.array([[[2**52]]]),np.array([[[[4]]]]),(1,1),(0,0,0,0))

    def test_pool_and_quantizer(self):
        import numpy as np
        from neupro_forward import pool2,round_input
        self.assertEqual(pool2(np.array([[[1,3],[2,4]]])).tolist(),[[[4]]])
        self.assertEqual(round_input(np.array([-1.5,.5,1.5]),'rne').tolist(),[-2,0,2])

    def test_quantization_manifest_is_explicit(self):
        from unittest.mock import patch
        from neupro_forward import infer
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaises(ValueError):infer(bytes(3*256*384))


@unittest.skipUnless(HAS_TORCH and os.environ.get('E7_REPLAY_INVENTORY') and os.environ.get('E7_BIRD_SOURCE'),
                     'Private resource inventory and separately obtained pinned peer required')
class PrivateBirdIntegration(unittest.TestCase):
    def test_744_procedural_peer_fixture(self):
        import json
        import numpy as np
        from e7_inputs import bird_source
        from pcnn_replay import Replay
        peer=bird_source()
        inventory=json.loads(Path(os.environ['E7_REPLAY_INVENTORY']).read_text())
        resource=next(r for r in inventory['pcnnp'] if r['resource_id']==744)
        model=Replay(resource)
        result=model.infer((peer/'fixtures/stripe/input-chw-u8.bin').read_bytes())
        expected=np.load(peer/'fixtures/stripe/expected.npz')
        for name,actual in zip(('fine','coarse'),result['outputs']):
            self.assertTrue(np.array_equal(actual,expected[name]),name)
