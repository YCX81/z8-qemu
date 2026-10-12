# SPDX-License-Identifier: GPL-2.0-or-later
"""Explicit lab-only AArch64 function invocation in an owned paused Z8 guest.

This changes guest CPU registers. It is never part of stock boot acceptance.
The caller must own and reserve the scratch stack and serialize guest access.
It does not stub callee results or turn an unexpected stop into success.
"""
import json
import re
import sys
import time

MAIN_SHA = '2b43a31c9de838f84b12416de4348a7004e65be07f853ec56ec2b10f3a8e3853'
RETURN_PC = 0x40160000


def _registers(text):
    return {key: int(value, 16) for key, value in
            re.findall(r'\b(X\d\d|PC|SP)=([0-9a-fA-F]+)', text)}


def invoke(guest, address, args, *, cpu, stack_top, timeout=5.0, advance=None, isolated=False,
           observers=None, interrupt_policy='masked'):
    """Run one function until its LR trap, restoring the complete GDB context.

    advance(running), if supplied, services known peripherals and returns True
    only when it handled a stop that execution may legitimately continue from.
    isolated resumes only the selected CPU. The default masks DAIF; preserve
    requires an already IRQ-enabled EL1 task boundary checked by the caller,
    then retains the callee's DAIF at every continuation. A lock held by another
    stopped CPU fails by timeout rather than being forced to succeed.
    observers maps image PCs to trusted read-only callbacks(guest, registers).
    Each must be hit once before return and produce JSON-serializable data.
    A hit is observed, its breakpoint removed, then the original function
    continues. Callbacks must not write RAM/MMIO, redirect CPUs or resume them;
    selected-CPU register equality and paused state are checked, but arbitrary
    guest memory writes cannot be detected by this API.
    Metadata distinguishes this active lab call from passive observation.
    """
    if guest.data.get('firmware_sha256') != MAIN_SHA:
        raise ValueError('Native calls require the pinned unmodified input identity')
    if not isinstance(address, int) or address % 4 or not 0x40160000 < address < 0x48000000:
        raise ValueError('Function address must be aligned within the lab image view')
    if not isinstance(stack_top, int) or stack_top % 16 or not 0xa00000000 < stack_top < 0xa40000000:
        raise ValueError('Caller must provide an aligned reserved work-RAM stack')
    if cpu not in range(4) or not 0 < timeout <= 30 or len(args) > 8 or (isolated and advance is not None):
        raise ValueError('Invalid CPU, timeout, or argument count')
    if interrupt_policy not in ('masked', 'preserve') or (interrupt_policy == 'preserve' and not isolated):
        raise ValueError('Task interrupt preservation requires isolated CPU execution')
    if any(not isinstance(arg, int) or not 0 <= arg < 2**64 for arg in args):
        raise ValueError('Arguments must be unsigned 64-bit integers')
    if observers is None:
        observers = {}
    if (not isinstance(observers, dict) or any(
            type(pc) is not int or pc % 4 or not RETURN_PC < pc < 0x48000000 or
            not callable(callback) for pc, callback in observers.items())):
        raise ValueError('Observers require aligned image PCs and read-only callable callbacks')
    pending = dict(observers)
    if guest.qmp.command('query-status')['running']:
        raise RuntimeError('Native call requires an already paused owned guest')
    row = dict(address=hex(address), args=[hex(x) for x in args], cpu=cpu,
               scratch_stack=hex(stack_top), returned=False,
               isolated_cpu=bool(isolated), interrupt_policy=interrupt_policy,
               interrupt_mask_override=bool(isolated and interrupt_policy == 'masked'),
               classification='active_lab_function_call_not_stock_boot')
    if pending:
        row.update(observer_points=[hex(pc) for pc in pending], observations=[])
    guest.data.setdefault('native_calls', []).append(row)
    guest.data['cpu_register_redirection'] = True
    guest.rsp(f'Hg{cpu + 1:x}')
    features = guest.rsp('qXfer:features:read:target.xml:0,1000', expect_ok=False)
    if not features or not features.startswith(('l', 'm')) or 'aarch64' not in features:
        raise RuntimeError('Cannot negotiate the AArch64 register description')
    saved = guest.rsp('g', expect_ok=False)
    if not saved or not re.fullmatch(r'[0-9a-fA-F]+', saved):
        raise RuntimeError('GDB did not return a complete register packet')
    row['register_packet_bytes'] = len(saved) // 2
    if len(saved) != 268 * 2:
        raise RuntimeError('Native calls require the verified AArch64 core packet layout')
    status_at_entry = int.from_bytes(bytes.fromhex(saved[-8:]), 'little')
    if interrupt_policy == 'preserve' and (status_at_entry & 0x80 or status_at_entry & 15 not in (4, 5)):
        raise RuntimeError('Task calls require a verified IRQ-enabled EL1 entry context')
    # QEMU's Cortex-A53 registers its FPU with g_pos=0: the 'g' packet
    # contains core registers only. Preserve v0-v31, FPSR and FPCR separately.
    floating = {}
    for number in range(34, 68):
        value = guest.rsp(f'p{number:x}', expect_ok=False)
        expected = 32 if number < 66 else 8
        if not value or len(value) != expected or not re.fullmatch(r'[0-9a-fA-F]+', value):
            raise RuntimeError('Cannot preserve the Cortex-A53 floating-point context')
        floating[number] = value
    row['floating_registers_preserved'] = len(floating)
    breakpoints = set()
    def resume():
        if isolated:
            # An observer can inspect another RSP thread. Reselect our CPU.
            # Task mode never overwrites a callee's live critical-section DAIF.
            guest.rsp(f'Hg{cpu + 1:x}')
            status = guest.rsp('p21', expect_ok=False)
            if not status or not re.fullmatch(r'[0-9a-fA-F]{8}', status):
                raise RuntimeError('Cannot read current PSTATE before isolated resume')
            if interrupt_policy == 'masked':
                masked = int.from_bytes(bytes.fromhex(status), 'little') | 0x3c0
                guest.rsp(f'P21={masked.to_bytes(4, "little").hex()}')
            # RSP continue replies only at the next stop. Send it without a
            # blocking receive so QMP can enforce the wall-clock timeout.
            command = f'vCont;c:{cpu + 1:x}'.encode()
            guest.gdb.sendall(b'$' + command + b'#' + f'{sum(command) & 255:02x}'.encode())
            if guest.gdb.recv(1) != b'+':
                raise RuntimeError('GDB did not acknowledge the isolated resume request')
        else:
            guest.qmp.command('cont')

    def observer_context():
        guest.rsp(f'Hg{cpu + 1:x}')
        core = guest.rsp('g', expect_ok=False)
        if not core or not re.fullmatch(r'[0-9a-fA-F]{536}', core):
            raise RuntimeError('Cannot verify observer core register context')
        fp = {number: guest.rsp(f'p{number:x}', expect_ok=False) for number in floating}
        if any(not value or not re.fullmatch(r'[0-9a-fA-F]+', value) or
               len(value) != len(floating[number]) for number, value in fp.items()):
            raise RuntimeError('Cannot verify observer floating register context')
        return core, fp

    try:
        for index, value in enumerate(args):
            guest.rsp(f'P{index:x}={value.to_bytes(8, "little").hex()}')
        for index, value in ((31, stack_top), (30, RETURN_PC), (32, address)):
            guest.rsp(f'P{index:x}={value.to_bytes(8, "little").hex()}')
        for pc in (RETURN_PC, *pending):
            # Track attempted installs too: a transport error may occur after
            # QEMU has accepted a breakpoint but before its OK is consumed.
            breakpoints.add(pc)
            guest.rsp(f'Z1,{pc:x},4')
        deadline = time.monotonic() + timeout
        resume()
        while True:
            if 'Z9_UNKNOWN' in guest.log():
                raise RuntimeError('Unmodeled MMIO during native call')
            if time.monotonic() >= deadline:
                raise TimeoutError('Bounded native call did not return')
            if not guest.qmp.command('query-status')['running']:
                regs = _registers(guest.qmp.hmp('info registers', cpu))
                if regs.get('PC') == RETURN_PC:
                    row.update(returned=True, result_x0=regs['X00'], return_registers=regs)
                    if pending:
                        row['observers_not_reached'] = [hex(pc) for pc in pending]
                        raise RuntimeError('Required observer PCs not reached: ' + ', '.join(row['observers_not_reached']))
                    return row
                pc = regs.get('PC')
                if pc in pending:
                    observation = dict(pc=hex(pc), cpu=cpu, registers=dict(regs), completed=False)
                    row['observations'].append(observation)
                    before_observer = observer_context()
                    data = pending[pc](guest, dict(regs))
                    if guest.qmp.command('query-status')['running']:
                        raise RuntimeError('Read-only observer resumed the guest')
                    if 'Z9_UNKNOWN' in guest.log():
                        raise RuntimeError('Unmodeled MMIO during native observer')
                    if observer_context() != before_observer:
                        raise RuntimeError('Read-only observer changed the CPU register context')
                    try:
                        observation['data'] = json.loads(json.dumps(data, allow_nan=False))
                    except (TypeError, ValueError) as error:
                        raise ValueError('Observer must return JSON-serializable data') from error
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Bounded native observer exceeded the call timeout')
                    guest.rsp(f'z1,{pc:x},4')
                    breakpoints.remove(pc)
                    del pending[pc]
                    observation['completed'] = True
                    resume()
                    continue
                if advance is None or not advance(False):
                    raise RuntimeError(f'Native call stopped at unexpected PC {regs.get("PC", 0):#x}')
                resume()
            elif advance is not None:
                advance(True)
            time.sleep(.002)
    except BaseException as exc:
        row['error'] = str(exc)
        try:
            guest.qmp.command('stop')
            row['failure_registers'] = _registers(guest.qmp.hmp('info registers', cpu))
        except BaseException as diagnostic_error:
            row['diagnostic_error'] = str(diagnostic_error)
        raise
    finally:
        original_error = sys.exc_info()[1]
        errors = []
        def attempt(label, operation):
            try:
                operation()
                return True
            except BaseException as error:
                errors.append(dict(step=label, error=str(error)))
                return False
        # A breakpoint cleanup failure must not prevent restoration. However,
        # do not redirect a possibly running CPU or an unselected RSP thread.
        if attempt('stop', lambda: guest.qmp.command('stop')):
            for pc in sorted(breakpoints):
                label = 'remove_return_breakpoint' if pc == RETURN_PC else f'remove_observer_breakpoint_{pc:#x}'
                attempt(label, lambda point=pc: guest.rsp(f'z1,{point:x},4'))
            if attempt('select_cpu', lambda: guest.rsp(f'Hg{cpu + 1:x}')):
                attempt('core_registers', lambda: guest.rsp('G' + saved))
                for number, value in floating.items():
                    attempt(f'fp_register_{number}', lambda n=number, v=value: guest.rsp(f'P{n:x}={v}'))
        row['context_restored'] = not errors
        if errors:
            row['restore_errors'] = errors
            if original_error is None:
                row['error'] = 'Native context restoration failed'
                raise RuntimeError(row['error'])
