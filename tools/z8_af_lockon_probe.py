# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded C3.11 AF instruction experiments, never a firmware patch generator.

Requires requirements-af-probe.txt and a private identity-checked MAIN. All
calibration fixtures are deliberately synthetic. A passing run does not mean
that the missing factory AF item, full AF loop, or physical camera is verified.
"""
import argparse
import hashlib
import itertools
import json
from pathlib import Path

MAIN_SHA = '2b43a31c9de838f84b12416de4348a7004e65be07f853ec56ec2b10f3a8e3853'
MAIN_SIZE = 81807709
BASE = 0x40160000
DATA = 0xa02808000
ITEMS = 0xa0291c948
GROUPS = 0xa0291dab8
DISPATCH = 0xa029eb4a8
STOCK_TARGET = 0x40e131fc
USER_TARGET = 0x40e132f4
CONFIG = 0x50002000
PARAM = 0x50003000
RING = 0x50004000
OUTER = 0x50005000
HISTORY = 0xa037dbbe0
STATE = 0xa037dbf50
CONTEXT = 0xa037df350
STACK = 0x5001f000
STOP = 0x5001e000


def require(condition, message):
    # Validation must also run under python -O.
    if not condition:
        raise ValueError(message)


def checked_main(path):
    data = Path(path).read_bytes()
    require(len(data) == MAIN_SIZE and hashlib.sha256(data).hexdigest() == MAIN_SHA,
            'Expected the unmodified Z8 C3.11 MAIN, not a package or derived image')
    return data


class Machine:
    """Real instructions, explicitly seeded memory, observed stops/reads only."""
    def __init__(self, image):
        try:
            import unicorn
            from unicorn import arm64_const
        except ImportError as exc:
            raise RuntimeError('Install requirements-af-probe.txt for this optional probe') from exc
        self.uc_api = unicorn
        self.regs = arm64_const
        self.u = unicorn.Uc(unicorn.UC_ARCH_ARM64, unicorn.UC_MODE_ARM)
        self.u.mem_map(BASE, (len(image) + 4095) & ~4095)
        self.u.mem_write(BASE, image)
        self.u.mem_protect(BASE, (len(image) + 4095) & ~4095,
                           unicorn.UC_PROT_READ | unicorn.UC_PROT_EXEC)
        self.u.mem_map(DATA, 0x2ad000)
        self.u.mem_write(DATA, image[0x4b13000:0x4dc0000])
        self.u.mem_map(0x50000000, 0x20000)
        self.u.mem_map(0xa03400000, 0x400000)
        self.u.mem_map(0xa03b90000, 0x20000)
        self.u.reg_write(arm64_const.UC_ARM64_REG_CPACR_EL1, 3 << 20)
        self.stops = set()
        self.reads = []
        self.watch = False
        self.u.hook_add(unicorn.UC_HOOK_CODE, self._stop)
        self.u.hook_add(unicorn.UC_HOOK_MEM_READ, self._read)
        require(self.rd(DISPATCH, 8) == STOCK_TARGET, 'Unexpected 3D dispatch entry')

    def _stop(self, uc, pc, _size, _data):
        if pc in self.stops:
            uc.emu_stop()

    def _read(self, uc, _access, address, size, _value, _data):
        if self.watch and PARAM <= address < PARAM + 0x400:
            self.reads.append((uc.reg_read(self.regs.UC_ARM64_REG_PC), address - PARAM, size))

    def x(self, number, value=None):
        # Unicorn's X29/X30 enum values are not consecutive with X0..X28.
        reg = getattr(self.regs, 'UC_ARM64_REG_X' + str(number))
        if value is None:
            return self.u.reg_read(reg)
        self.u.reg_write(reg, value & ((1 << 64) - 1))

    def wr(self, address, value, size=4):
        self.u.mem_write(address, (value & ((1 << (size * 8)) - 1)).to_bytes(size, 'little'))

    def rd(self, address, size=4):
        return int.from_bytes(self.u.mem_read(address, size), 'little')

    def run(self, start, *stops, count=20000):
        self.stops = set(stops)
        self.u.emu_start(start, 0, count=count)
        pc = self.u.reg_read(self.regs.UC_ARM64_REG_PC)
        require(pc in self.stops, f'Instruction bound hit at {pc:#x}; expected {stops}')
        return pc

    def fresh(self):
        for n in range(31):
            self.x(n, 0)
        self.u.reg_write(self.regs.UC_ARM64_REG_SP, STACK)
        self.u.reg_write(self.regs.UC_ARM64_REG_NZCV, 0)
        self.u.mem_write(0x50000000, bytes(0x20000))
        self.u.mem_write(HISTORY, bytes(0x20))
        self.u.mem_write(STATE, bytes(0x19f0))
        self.u.mem_write(CONTEXT, bytes(0xb0))
        self.wr(CONTEXT + 0x10, CONFIG, 8)
        self.wr(CONTEXT + 0x18, PARAM, 8)
        self.wr(CONTEXT + 0x30, STATE, 8)
        self.wr(CONTEXT + 0x38, STATE + 0xb00, 8)
        self.wr(CONTEXT + 0x48, HISTORY, 8)
        self.wr(CONTEXT + 0x50, STATE, 8)
        self.wr(DISPATCH, STOCK_TARGET, 8)
        # Distinct tagged halfwords, not borrowed Nikon calibration values.
        for offset in range(0, 0x400, 2):
            self.wr(PARAM + offset, 1000 + offset // 2, 2)
        self.reads = []
        self.watch = False

    def normalize(self, raw):
        self.wr(RING + 0x208, raw)
        self.x(25, RING)
        self.x(19, CONFIG - 0x1188)
        self.x(11, PARAM)
        self.run(0x40e1947c, 0x40e19498)
        return self.rd(CONFIG + 0x48)

    def policy(self, mode, raw, event, enable, special, video, variant, movie_index=1,
               alternate_movie=False):
        self.fresh()
        index = self.normalize(raw)
        self.wr(CONFIG + 0x14, mode)
        self.wr(CONFIG + 0x18, int(video))
        self.wr(CONFIG + 0x1a9, special, 1)
        self.wr(CONFIG + 0x50, movie_index)
        self.wr(CONFIG + 0x54, movie_index)
        self.wr(STATE + 0xb38, int(alternate_movie), 1)
        self.wr(HISTORY + 0xd, event, 1)
        self.wr(PARAM + 0x5c, enable, 2)
        return self.select_time(variant, index)

    def select_time(self, variant, index):
        self.reads = []
        self.wr(DISPATCH, USER_TARGET if variant == 'user' else STOCK_TARGET, 8)
        self.x(8, CONFIG)
        self.x(9, HISTORY)
        self.x(10, STATE)
        self.x(23, CONTEXT)
        self.watch = True
        self.run(0x40e13170, 0x40e13320)
        result = self.x(21)
        reads = [(hex(pc), hex(offset), size) for pc, offset, size in self.reads]
        self.watch = False
        return dict(value=result, normalized_index=index, parameter_reads=reads)


def resource_cases(m):
    """Derive allocation and execute the original direct-ROM resource reader."""
    m.fresh()
    # Execute both item8 accessors as well as the original allocation math;
    # stop before unrelated configuration service calls at the end of init.
    m.run(0x40d70dbc, 0x40d70f88)
    item = ITEMS + 0x25 * 0x30
    aggregate = ITEMS + 0x12 * 0x30
    group = GROUPS + 0x12 * 0x28
    ram = m.rd(aggregate + 0x10, 8)
    af = m.rd(item + 0x10, 8)
    length = m.rd(aggregate + 0x18)
    require(af - ram == 0x10800 and m.rd(item + 0x18) == 0x2d00, 'AF layout changed')
    require(m.rd(group, 8) == 0x462e0000, 'Group12 source changed')
    m.u.mem_map(ram & ~4095, (ram % 4096 + length + 4095) & ~4095)
    m.u.mem_map(0x462e0000, 0x20000)
    rows = []
    for trailer in (False, True):
        fixture = bytearray(b'\xff' * 0x20000)
        if trailer:
            fixture[-4:] = b'ENDM'
        m.u.mem_write(0x462e0000, bytes(fixture))
        m.u.mem_write(ram, b'\xa5' * length)
        m.x(0, aggregate)
        m.x(1, group)
        m.x(30, STOP)
        m.run(0x40d6c320, STOP, count=1000000)
        require(m.x(0) == int(trailer), 'Unexpected original resource-reader result')
        sample = bytes(m.u.mem_read(af + 0xcd4, 10))
        require(sample == (b'\xff' if trailer else b'\xa5') * 10,
                'Missing data unexpectedly became calibration')
        m.run(0x40db29a8, STOP)
        m.run(0x40db2a1c, STOP)
        require(m.x(0) == af + 0xc04, 'AF getter did not use initialized item25 pointer')
        rows.append(dict(synthetic_endm=trailer,reader_success=trailer,
                         af_table_sample=sample.hex(),parameter_pointer=hex(m.x(0))))
    return dict(source_rom='0x462f0800',item_length='0x2d00',group_ram=hex(ram),
                af_ram=hex(af),parameter_rom='0x462f1404',
                timing_table_rom='0x462f14d4',clamp_enable_rom='0x462f1460',
                original_reader_cases=rows,factory_af_body_recovered=False,
                note='ENDM proves reader framing only; even erased payload is copied when framed')


def history_step(m, mode, tick, *, classifier=1, enabled=1, metric=0, metric_kind=0):
    m.normalize(5)
    m.wr(CONFIG + 0x14, mode)
    m.wr(CONFIG + 0x18, 0)
    m.wr(CONFIG + 0x10, 1)
    m.wr(CONFIG + 0x7c, enabled, 2)
    m.wr(PARAM + 0x126, 1, 2)
    m.wr(0xa03766474, classifier)
    m.wr(STATE, metric)
    m.wr(STATE + 0x1c, metric_kind)
    m.wr(STATE + 0xc, tick)
    m.x(8, CONFIG)
    m.x(9, PARAM)
    m.x(12, 1)
    m.x(20, 1)
    m.x(23, CONTEXT)
    m.run(0x40e130c0, 0x40e13168, 0x40e1316c)
    before_copy = dict(current_latch=m.rd(HISTORY + 4, 1),
                       current_hold=m.rd(HISTORY + 5, 1),
                       consumed_previous_hold=m.rd(HISTORY + 0xd, 1))
    # Feed the actual history byte into the candidate selector without replacing
    # its value or rebuilding memory between producer and consumer.
    threshold = m.select_time('user', 4)['value']
    if mode in (0,1,8):
        require(threshold == (1106 if before_copy['consumed_previous_hold'] else 1108),
                'Producer/history/cap/lookup chain mismatch')
    # Execute the real history shift; +0xd is copied from +5, not from the
    # unrelated output+0xd written by function 40e11a50.
    m.wr(OUTER + 0x50, STATE, 8)
    m.x(25, OUTER)
    m.x(27, 0x50006000)
    m.x(28, HISTORY)
    m.run(0x40e1161c, 0x40e11654)
    require(m.rd(HISTORY + 0xd, 1) == before_copy['current_hold'], 'History shift mismatch')
    return dict(mode=mode,tick=tick,**before_copy,next_previous_hold=m.rd(HISTORY+0xd,1),
                setting=5,synthetic_selected_threshold=threshold)


def history_cases(m):
    rows = []
    for mode in range(10):
        m.fresh()
        row = history_step(m, mode, 100)
        require(row['current_hold'] == int(mode <= 1), 'Unexpected flag arming mode')
        rows.append(row)
    m.fresh()
    transition = [history_step(m, 0, 100), history_step(m, 8, 200),
                  history_step(m, 8, 700), history_step(m, 8, 701),
                  history_step(m, 8, 702)]
    require([r['current_hold'] for r in transition] == [1,1,1,0,0], '601-tick boundary')
    require([r['consumed_previous_hold'] for r in transition] == [0,1,1,1,0],
            'One-iteration history delay changed')
    negatives = []
    for case in (dict(classifier=0),dict(enabled=0),dict(metric=256),dict(metric=-256),dict(metric_kind=2)):
        m.fresh()
        row = history_step(m, 0, 100, **case)
        require(row['current_hold'] == 0, 'Arming prerequisite ignored')
        negatives.append(dict(inputs=case,result=row))
    m.fresh()
    history_step(m, 0, 100)
    m.x(30, STOP)
    m.run(0x40e11718, STOP)
    require(all(m.rd(HISTORY + off, 2) == 0 for off in (4,12,20,28)), 'Reset failed')
    return dict(arming_modes=rows,mode_change=transition,negative_prerequisites=negatives,
                original_reset_clears_all_four_slots=True,
                note='Synthetic reachable slices; does not establish whether full mode change invokes reset')


def policy_cases(m):
    rows = []
    changed = 0
    for mode,raw,event,enable,special,video in itertools.product(
            range(10),range(1,6),(0,1),(0,1),(0,1),(False,True)):
        args=(mode,raw,event,enable,special,video)
        stock=m.policy(*args,'stock')
        user=m.policy(*args,'user')
        if mode != 8 or video:
            require(stock == user, f'Unintended path change {args}')
        elif special:
            # The user route reads its table before the original override
            # replaces the result. Preserve the result, report the extra read.
            require(stock['value'] == user['value'] == 1000+0xd8//2,
                    'Special override changed')
        else:
            index=min(raw-1,2) if event and enable else raw-1
            require(stock['value'] == 500, 'Stock 3D threshold not fixed')
            require(user['value'] == 1000+0xd0//2+index, 'User lookup or cap mismatch')
            require(('0x40e132fc',hex(0xd0+2*index),2) in user['parameter_reads'],
                    'Expected lookup was not observed')
            changed += 1
        rows.append(dict(mode=mode,setting=raw,event=event,enable=enable,special=special,
                         video=video,stock=stock,user=user))
    # Both explicit movie lookup paths, using indices 0..6 as declared fixtures.
    movie_cases=0
    for mode,index,alternate in itertools.product(range(10),range(7),(False,True)):
        a=m.policy(mode,5,1,1,0,True,'stock',index,alternate)
        b=m.policy(mode,5,1,1,0,True,'user',index,alternate)
        require(a == b, 'Movie branch changed')
        movie_cases += 1
    invalid=[]
    for raw in (0,6,255,0xffffffff):
        row=m.policy(8,raw,0,1,0,False,'user')
        require(row['normalized_index']==0 and row['value']==1104,'Invalid-setting fallback changed')
        invalid.append(dict(raw=raw,result=row))
    return dict(comparison_cases=len(rows),executions=2*len(rows),
                intentionally_changed_cases=changed,movie_comparison_cases=movie_cases,
                invalid_settings=invalid,rows=rows)


def secondary_cases(m):
    rows=[]
    for mode,raw,event,enable in itertools.product(range(10),range(1,6),(0,1),(0,1)):
        results=[]
        for variant in ('stock','user'):
            m.policy(mode,raw,event,enable,0,False,variant)
            m.x(21,CONTEXT)
            m.reads=[]
            m.watch=True
            m.run(0x40e17a48,0x40e17b20)
            offset=m.x(12)-PARAM
            m.x(8,CONFIG)
            m.x(21,CONTEXT)
            m.run(0x40e18010,0x40e18088)
            m.watch=False
            results.append(dict(threshold_offset=hex(offset),lower=m.x(9),upper=m.x(10),
                                reads=[(hex(pc),hex(off),size) for pc,off,size in m.reads]))
        require(results[0]==results[1],'Secondary AF consumer changed')
        if mode==8:
            index=min(raw-1,2) if event and enable else raw-1
            require(results[0]['threshold_offset']==hex(0x80+2*index),'3D dedicated table mismatch')
            expected_lower=(-((1000+0xae//2+index)<<8))&0xffffffff
            require(results[0]['lower']==expected_lower,'3D lower-limit table mismatch')
            require(results[0]['upper']==(1000+0xc2//2+index)<<8,'3D upper-limit table mismatch')
        rows.append(dict(mode=mode,raw=raw,event=event,enable=enable,result=results[0]))
    return dict(comparison_cases=len(rows),rows=rows)


def setting_cases(m):
    rows=[]
    for ordinary,recalled,active,mask in itertools.product(range(1,6),range(1,6),(0,1),(0,0x200)):
        m.fresh()
        m.u.mem_write(0xa03b98570,bytes(0xc00))
        m.u.mem_write(0xa03b9915c,bytes(0x200))
        m.wr(0xa03b98570+0x978,ordinary)
        m.wr(0xa03b98570+0x980,recalled)
        m.wr(0xa03b98570+0x97c,99)
        m.wr(0xa03b98570+0x34,mask)
        m.wr(0xa03b9915c+0x14,active)
        m.x(1,OUTER)
        m.run(0x40f152f4,0x40ed3e24)
        value=m.rd(m.x(2))
        require(m.rd(m.x(1))==0x21d3 and value==(recalled if active and mask else ordinary),
                'Recall selection changed')
        rows.append(dict(ordinary=ordinary,recalled=recalled,active=active,mask=mask,value=value))
    return rows


def mode_mapping_cases(m):
    rows=[]
    for raw in range(14):
        m.fresh()
        m.x(0,raw)
        m.x(30,STOP)
        m.run(0x40e0dd60,STOP)
        rows.append(dict(photo_area=raw,algorithm_mode=m.x(0)))
    require(rows[5]['algorithm_mode']==8,'Photo area 5 no longer maps to algorithm mode 8')
    return rows


def timer_boundary_cases(m):
    # Execute original scaling/comparison/store, starting after the clock reads.
    # This proves the selected threshold affects the decision boundary, not AF success.
    rows=[]
    for variant,setting in itertools.product(('stock','user'),range(1,6)):
        threshold=m.policy(8,setting,0,1,0,False,variant)['value']
        for elapsed in (threshold-1,threshold,threshold+1):
            m.wr(STATE+0x1988,elapsed,2)
            m.x(8,CONFIG);m.x(9,STATE);m.x(20,0);m.x(21,threshold);m.x(23,CONTEXT)
            m.run(0x40e13450,0x40e134a4)
            result=m.rd(STATE+0x19bc,1)
            require(result==int(elapsed>=threshold),'Timer decision boundary changed')
            rows.append(dict(variant=variant,setting=setting,synthetic_threshold=threshold,
                             synthetic_elapsed=elapsed,decision=result))
    return rows


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firmware',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True,help='New JSON path; existing output refused')
    args=parser.parse_args()
    require(not args.output.exists(),'Refusing to overwrite existing evidence')
    image=checked_main(args.firmware)
    m=Machine(image)
    resources=resource_cases(m)
    history=history_cases(m)
    policies=policy_cases(m)
    secondary=secondary_cases(m)
    settings=setting_cases(m)
    modes=mode_mapping_cases(m)
    timer=timer_boundary_cases(m)
    require(checked_main(args.firmware)==image,'Source changed during probe')
    result=dict(schema=1,source_sha256=MAIN_SHA,source_bytes=MAIN_SIZE,
                source_unchanged=True,all_checks_passed=True,
                calibration='synthetic distinct halfwords only; no Z9 substitution',
                factory_af_body_recovered=False,physical_camera_verified=False,
                full_af_loop_verified=False,firmware_file_written=False,
                candidate=dict(scope='Unicorn RAM only',address=hex(DISPATCH),
                               original=hex(STOCK_TARGET),replacement=hex(USER_TARGET),
                               register_or_return_value_hooks=False,shared_caps_preserved=True),
                resources=resources,history=history,policy=policies,secondary=secondary,
                effective_settings=settings,mode_mapping=modes,timer_boundaries=timer)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as stream:
        json.dump(result,stream,indent=2)
        stream.write('\n')
    print(json.dumps(dict(all_checks_passed=True,resource_cases=2,
                          history_cases=21,policy_comparisons=policies['comparison_cases'],
                          movie_comparisons=policies['movie_comparison_cases'],
                          secondary_comparisons=secondary['comparison_cases'],
                          effective_settings=len(settings),mode_mappings=len(modes),timer_boundaries=len(timer),
                          factory_af_body_recovered=False,output=str(args.output)),indent=2))


if __name__=='__main__':
    main()
