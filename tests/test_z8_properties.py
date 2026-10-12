# SPDX-License-Identifier: GPL-2.0-or-later
"""Z8 parameter identity, provenance and diagnostic timing contracts."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import z8_properties as props


class PropertiesTests(unittest.TestCase):
    def setUp(self):
        self.profile = props.load_model_profile()

    def test_preserves_existing_functional_values_with_explicit_z8_identity(self):
        previous = json.loads((props.ROOT / 'tools/model-properties.json').read_text())
        self.assertEqual(self.profile['properties'],
                         previous['properties'] + ['z8-sd-empty-fixture=on'])
        self.assertEqual(self.profile['machine'], 'nikon-z8-bringup')
        self.assertNotEqual(self.profile['firmware_sha256'], previous['firmware_sha256'])
        self.assertEqual(self.profile['firmware_bytes'], 81807709)

    def test_all_parameters_have_one_class_with_source_and_assumptions(self):
        names = [entry.split('=', 1)[0] for entry in self.profile['properties']]
        classified = []
        for row in self.profile['parameter_classes']:
            self.assertTrue(row['source'])
            self.assertTrue(row['assumptions'])
            self.assertFalse(row['physical_hardware_verified'])
            classified.extend(row['properties'])
        self.assertCountEqual(classified, names)

    def test_wrong_or_missing_identity_is_rejected(self):
        for key in ('model', 'version', 'machine', 'firmware_sha256', 'firmware_bytes'):
            for missing in (False, True):
                candidate = copy.deepcopy(self.profile)
                if missing:
                    del candidate[key]
                else:
                    candidate[key] = 'wrong'
                with self.subTest(key=key, missing=missing), self.assertRaises(ValueError):
                    props.validate_model_profile(candidate)

    def test_malformed_or_unclassified_properties_are_rejected(self):
        mutations = [
            lambda p: p['properties'].append(p['properties'][0]),
            lambda p: p['properties'].append('unsafe,argument=on'),
            lambda p: p['properties'].append('unclassified=on'),
            lambda p: p['parameter_classes'][0]['properties'].append('unknown'),
            lambda p: p['parameter_classes'][0].update(source=''),
            lambda p: p['parameter_classes'][0].update(assumptions=''),
            lambda p: p['parameter_classes'][0].update(physical_hardware_verified=True),
            lambda p: p.update(properties=[]),
            lambda p: p.update(schema=2),
        ]
        for mutate in mutations:
            candidate = copy.deepcopy(self.profile)
            mutate(candidate)
            with self.subTest(mutation=mutate), self.assertRaises(ValueError):
                props.validate_model_profile(candidate)

    def test_load_checks_the_independent_profile_without_shared_z9_file(self):
        with tempfile.TemporaryDirectory() as folder:
            profile = Path(folder) / 'z8.json'
            profile.write_text(json.dumps(self.profile))
            with patch.object(props, 'PROFILE', profile):
                self.assertEqual(props.load_model_profile(), self.profile)
                self.profile['firmware_sha256'] = '0' * 64
                profile.write_text(json.dumps(self.profile))
                with self.assertRaises(ValueError):
                    props.load_model_profile()

    def test_boundary_interval_is_milliseconds_and_is_not_sensor_timing(self):
        settings = self.profile['diagnostic_boundary']
        self.assertEqual(settings['period_ms'], 33)
        self.assertFalse(settings['z8_physical_timing_verified'])
        guest = SimpleNamespace(data={})
        with patch.object(props, 'BoundaryPeer') as peer:
            result = props.make_boundary_peer(guest)
            peer.assert_called_once_with(guest, 33, auxiliary_masked=True)
            self.assertIs(result, peer.return_value)
        self.assertEqual(guest.data['z8_diagnostic_boundary'], settings)

    def test_boundary_configuration_remains_explicit_and_bounded(self):
        for field, value in (('period_ms', 9), ('period_ms', 1001), ('period_ms', True),
                             ('period_ms', '33'), ('auxiliary_masked', 'true'),
                             ('z8_physical_timing_verified', True), ('source', ''),
                             ('assumptions', '')):
            candidate = copy.deepcopy(self.profile)
            candidate['diagnostic_boundary'][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                props.validate_model_profile(candidate)

    def test_menu_evidence_does_not_imply_bare_boot_or_camera_acceptance(self):
        self.assertFalse(any(props.verification_scope().values()))
        scope = props.verification_scope(menu_verified=True)
        self.assertTrue(scope.pop('external_mcu_assisted_menu_verified'))
        self.assertFalse(any(scope.values()))


if __name__ == '__main__':
    unittest.main()
