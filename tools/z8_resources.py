# SPDX-License-Identifier: GPL-2.0-or-later
"""Explicit Z8 virtual ROM diagnostics outside the original program image.

Locations come from C3.11 resource readers; small gate/index/timing values
remain functional hypotheses. No physical sensor/panel calibration is supplied.
"""
import hashlib
import json

MAIN_SHA = '2b43a31c9de838f84b12416de4348a7004e65be07f853ec56ec2b10f3a8e3853'


def plan(*, zero_parameters=False, native_sampling=False, battery_limits=False):
    if zero_parameters and native_sampling:
        raise ValueError('Zero control and native-derived sampling are separate experiments')
    if battery_limits and not native_sampling:
        raise ValueError('Battery limits require the explicit native-sampling parameter image')
    entries = [
        ('version',0x47f62dd4,bytes.fromhex('1500'),'C3.11-compatible synthetic version tag'),
        ('primary-gate',0x47e8b568,bytes.fromhex('01000000'),'synthetic first halfword gate, not sensor programming'),
        ('processing-index',0x47f5af40,bytes.fromhex('0101'),'synthetic selectors 0/1; actual mode selection not assumed verified'),
        ('timing-0',0x47f62f62,(1200).to_bytes(2,'little'),'synthetic nonzero line coefficient, mode0/sub3'),
        ('timing-13',0x47f63922,(1200).to_bytes(2,'little'),'synthetic nonzero line coefficient, mode13/sub3'),
    ]
    for index,address in enumerate((0x45cffffc,0x460dfffc,0x46d7fffc,0x47bffffc,0x47f7fffc)):
        entries.append((f'sequence-{index}',address,b'ENDM','synthetic extent trailer; resource body remains missing'))
    if zero_parameters or native_sampling:
        body = bytearray(0x1fffc) + b'ENDM'
        meaning = 'explicit zero parameter experiment, not Z8 default data or valid panel calibration'
        if native_sampling:
            # Original initializer 44c8080c/10 contains interval20/mask0xf.
            # Original item1c consumer 4072c088..0a0 supplies count then interval
            # to 40317f00. Four samples reconstruct that same low-four-bit mask.
            # ENDM is required for the original reader to load this body.
            body[0xb870:0xb878] = bytes.fromhex('1400000004000000')
            meaning = ('synthetic group12 with debounce interval20/count4 derived from Z8 initialized globals; '
                       'other calibration fields remain zero and unverified')
            if battery_limits:
                # mode1 loads these fields and retains its original signed
                # comparisons. Values are from the same program's mode2 path;
                # no mode, fault flag, temperature sample or task state is edited.
                body[0xb7e0:0xb7e8] = bytes.fromhex('580000004d000000')
                meaning += '; virtual battery thresholds88/77 from the native alternative-mode branch'
        entries.append(('parameters',0x462e0000,bytes(body),meaning))
    return [dict(name=name,address=address,data=data,meaning=meaning,synthetic=True)
            for name,address,data,meaning in entries]


def attach(guest, *, zero_parameters=False, native_sampling=False, battery_limits=False):
    if guest.data.get('firmware_sha256') != MAIN_SHA:
        raise ValueError('Virtual inputs require the identity-checked Z8 C3.11 MAIN')
    entries = plan(zero_parameters=zero_parameters,native_sampling=native_sampling,battery_limits=battery_limits)
    paths = [guest.out / ('z8-rom-' + item['name'] + '.bin') for item in entries]
    metadata = guest.out / 'z8-virtual-rom.json'
    if metadata.exists() or any(path.exists() for path in paths):
        raise FileExistsError('Virtual ROM outputs must be new')
    regions = []
    commands = []
    for item,path in zip(entries,paths):
        with path.open('xb') as stream:
            stream.write(item['data'])
        regions.append(dict(name=item['name'],address=hex(item['address']),bytes=len(item['data']),
                            sha256=hashlib.sha256(item['data']).hexdigest(),file=path.name,
                            meaning=item['meaning'],synthetic=True))
        commands += ['-device',f'loader,file={path},addr={item["address"]:#x},force-raw=on']
    record = dict(firmware_sha256=MAIN_SHA,hardware_calibration=False,original_program_modified=False,
                  zero_parameter_experiment=zero_parameters,regions=regions,
                  limitations='Minimal virtual inputs; missing resource bodies and physical calibration remain unresolved')
    if native_sampling:
        record['native_sampling_derivation'] = dict(
            interval=20,history_mask=15,history_samples=4,physical_time_unit_verified=False,
            initializer_file_offsets=['0x4b2080c','0x4b20810'],
            initializer_ram=['0xa0281580c','0xa02815810'],
            resource_fields=['0x462eb870','0x462eb874'],consumer='0x40317f00',
            scope='Reconstructs original initialized debounce state, not recovered factory calibration')
    if battery_limits:
        record['battery_limit_derivation'] = dict(
            values=[88,77],resource_fields=['0x462eb7e0','0x462eb7e4'],
            source_pcs=['0x40eb9ac0','0x40eb9ac4'],active_mode_changed=False,
            native_mode=1,source_mode=2,physical_units_verified=False,
            comparison='original signed threshold <= signed-byte sample sets the fault',
            scope='Native alternative-mode values used as explicit virtual thresholds; not factory calibration')
    metadata.write_text(json.dumps(record,indent=2)+'\n')
    guest.command += commands
    guest.data['z8_virtual_rom_inputs'] = record
    return record
