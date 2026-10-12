# SPDX-License-Identifier: GPL-2.0-or-later
"""Find an original Z8 MCU task boundary suitable for isolated native calls.

Only the existing external MCU peer advances execution. This helper never
writes guest registers, TCBs, kernel flags, mutexes or firmware. A successful
snapshot is a necessary context check, not a guarantee that a callee cannot
block, schedule or touch another peripheral. Native calls retain their own
strict timeout/stop checks. The caller must exclusively own the paused guest.
"""
import math
import re
import struct
import time
import xml.etree.ElementTree as ET

from boot import key_body
from z8_native_call import MAIN_SHA

RX_RETIRED_PC = 0x40c935d8
GLOBAL_SPINLOCK = 0xa04b70510
MUTEX_COUNT = 0xa02aaab10
MUTEX_TABLE = 0xa049b3218
MUTEX_OBJECTS = {'picture_control': 0xa047027c0, 'high_selection': 0xa047e55c8}


def _paused(guest):
    if guest.data.get('firmware_sha256') != MAIN_SHA:
        raise ValueError('Task context requires the pinned Z8 C3.11 input identity')
    if 'Z9_UNKNOWN' in guest.log():
        raise RuntimeError('Unknown hardware access before task context observation')
    if guest.qmp.command('query-status')['running']:
        raise RuntimeError('Task context must be read from a paused owned guest')


def _register_map(guest):
    cached = getattr(guest, '_z8_task_context_registers', None)
    if cached is not None:
        return cached
    result = {}; active = set(); seen = set(); next_number = 0
    descriptions = {}; numbers = set()
    required = {'PC': 64, 'CPSR': 32, 'TPIDR_EL1': 64}

    def visit(name):
        nonlocal next_number
        if name in active or name in seen or len(seen) >= 64:
            raise RuntimeError('Ambiguous or cyclic GDB register XML includes')
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', name):
            raise RuntimeError('Unsupported GDB register XML annex')
        active.add(name); seen.add(name)
        text = ''; offset = 0
        while True:
            reply = guest.rsp(f'qXfer:features:read:{name}:{offset:x},1000', expect_ok=False)
            if not reply or reply[0] not in 'lm' or (reply[0] == 'm' and len(reply) == 1):
                raise RuntimeError('Incomplete GDB register XML')
            text += reply[1:]; offset += len(reply[1:])
            if offset > 1024 * 1024:
                raise RuntimeError('GDB register XML exceeds bounded size')
            if reply[0] == 'l':
                break
        try:
            # QEMU's target description uses xi:include without declaring xi
            # (observed on the actual Cortex-A53 GDB stub). Supply only that
            # standard namespace on the target root. Other unbound prefixes,
            # malformed nesting and unknown xi elements remain parse errors.
            xi_tags = set(re.findall(r'</?xi:([A-Za-z_][A-Za-z0-9_.-]*)', text))
            if (name == 'target.xml' and xi_tags == {'include'} and
                    not re.search(r'\bxmlns:xi\s*=', text)):
                text = re.sub(r'<target(?=[\s>])',
                              '<target xmlns:xi="http://www.w3.org/2001/XInclude"',
                              text, count=1)
            root = ET.fromstring(text)
            for element in root.iter():
                tag = element.tag.rsplit('}', 1)[-1]
                if tag == 'include':
                    visit(element.attrib['href'])
                elif tag == 'reg':
                    attrs = element.attrib
                    if 'regnum' in attrs:
                        value = attrs['regnum']
                        next_number = int(value, 16 if value.lower().startswith('0x') else 10)
                    key = attrs['name'].upper()
                    bits = int(attrs['bitsize'])
                    if next_number in numbers or not 0 <= next_number < 65536:
                        raise RuntimeError('Ambiguous GDB register numbering')
                    numbers.add(next_number)
                    descriptions.setdefault(key, []).append((next_number, bits))
                    # QEMU exposes fpsr/fpcr as 32-bit FPU registers and
                    # FPSR/FPCR as 64-bit system-register aliases. They are
                    # distinct numeric registers and are not read here.
                    # Every requested name must still identify exactly one.
                    if key in required:
                        if key in result:
                            raise RuntimeError('Ambiguous required GDB register name')
                        result[key] = (next_number, bits)
                    next_number += 1
        except (ET.ParseError, KeyError, ValueError) as exc:
            raise RuntimeError('Malformed GDB register XML') from exc
        active.remove(name)

    visit('target.xml')
    for name, bits in required.items():
        if name not in result or result[name][1] != bits:
            raise RuntimeError(f'Missing or unsupported {name} GDB register')
    guest._z8_task_context_registers = result
    guest._z8_task_context_register_aliases = {key: values for key, values in descriptions.items()
                                              if len(values) > 1}
    return result


def _register(guest, registers, name):
    number, bits = registers[name]
    value = guest.rsp(f'p{number:x}', expect_ok=False)
    if not value or not re.fullmatch(r'[0-9a-fA-F]{' + str(bits // 4) + '}', value):
        raise RuntimeError(f'Cannot read complete {name} register')
    return int.from_bytes(bytes.fromhex(value), 'little')


def _read(guest, address, size):
    value = guest.read_bytes(address, size)
    if len(value) != size:
        raise RuntimeError('Incomplete native task or mutex record')
    return value


def _ram_pointer(value):
    return value % 8 == 0 and 0xa00000000 < value < 0xa40000000


def read_current_context(guest, cpu):
    """Read original lock prerequisites; return evidence with callable/reasons.

    4017fd24 reads TPIDR+50/+14/currentTCB and global spinlock. The caller's
    mutex object stores an ID; table stride is 28, descriptor +10, owner +18.
    Both another owner and recursive acquisition by this TCB are rejected.
    This is intended to be repeated immediately before each serialized call.
    """
    if type(cpu) is not int or cpu not in range(4):
        raise ValueError('A concrete Cortex-A53 CPU index is required')
    _paused(guest)
    guest.rsp(f'Hg{cpu + 1:x}')
    registers = _register_map(guest)
    pc = _register(guest, registers, 'PC')
    pstate = _register(guest, registers, 'CPSR')
    tpidr = _register(guest, registers, 'TPIDR_EL1')
    result = dict(cpu=cpu, pc=hex(pc), pstate=hex(pstate), tpidr_el1=hex(tpidr),
                  tpidr_regnum=registers['TPIDR_EL1'][0], current_tcb=None,
                  callable=False, reasons=[], mutexes=[], read_only=True)
    reasons = result['reasons']
    if pc != RX_RETIRED_PC: reasons.append('not_original_MCU_RX_retirement')
    if pstate & 15 not in (4, 5): reasons.append('not_EL1_task_mode')
    if pstate & 0x80: reasons.append('IRQ_masked')
    if not _ram_pointer(tpidr):
        reasons.append('invalid_TPIDR_pointer')
        return result
    per_cpu = _read(guest, tpidr, 0x54)
    tcb = struct.unpack_from('<Q', per_cpu)[0]
    field14 = struct.unpack_from('<I', per_cpu, 0x14)[0]
    field50 = struct.unpack_from('<I', per_cpu, 0x50)[0]
    spinlock = int.from_bytes(_read(guest, GLOBAL_SPINLOCK, 4), 'little')
    result.update(current_tcb=hex(tcb), context_field_14=field14, context_field_50=field50,
                  global_spinlock=spinlock, per_cpu_hex=per_cpu.hex())
    if not field14: reasons.append('context_field_14_zero')
    if field50: reasons.append('context_field_50_nonzero')
    if spinlock: reasons.append('global_spinlock_busy')
    if not _ram_pointer(tcb):
        reasons.append('invalid_current_TCB')
        return result
    priority = _read(guest, tcb + 0x19, 1)[0]
    result['tcb_priority_19'] = priority
    count = int.from_bytes(_read(guest, MUTEX_COUNT, 4), 'little')
    result['mutex_count'] = count
    if not 0 < count <= 65536:
        reasons.append('invalid_mutex_count')
        return result
    for name, address in MUTEX_OBJECTS.items():
        identifier = int.from_bytes(_read(guest, address, 4), 'little')
        row = dict(name=name, object=hex(address), identifier=identifier)
        result['mutexes'].append(row)
        if not 1 <= identifier <= count:
            reasons.append(name + '_invalid_mutex_ID')
            continue
        table = MUTEX_TABLE + (identifier - 1) * 0x28
        raw = _read(guest, table, 0x28)
        descriptor, owner = struct.unpack_from('<QQ', raw, 0x10)
        row.update(table_record=hex(table), record_hex=raw.hex(),
                   descriptor=hex(descriptor), owner=hex(owner))
        if owner:
            reasons.append(name + ('_already_owned_by_current_TCB' if owner == tcb else '_owned_by_other_TCB'))
        if not (_ram_pointer(descriptor) or
                descriptor % 4 == 0 and 0x40160000 <= descriptor < 0x44f64b5d):
            reasons.append(name + '_invalid_descriptor_pointer')
            continue
        attributes, ceiling = struct.unpack('<II', _read(guest, descriptor, 8))
        row.update(attributes=hex(attributes), priority_ceiling=ceiling)
        if attributes == 0xffffffff: reasons.append(name + '_deleted_descriptor')
        elif attributes & 3 == 3 and ceiling > priority: reasons.append(name + '_priority_guard')
    result['callable'] = not reasons
    return result


def wait_for_task_context(session, *, timeout=15.0, max_frames=16):
    """Return (cpu, evidence), leaving the owned RX retirement stop unhandled.

    An already suitable owned boundary needs no input. Otherwise send bounded
    neutral key-release frames and let Session.advance run the original peer.
    Busy locks are only observed again after normal guest execution. On every
    exit the guest is paused; no peer-owned breakpoint is removed here.
    """
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or
            not math.isfinite(timeout) or not 0 < timeout <= 30 or
            type(max_frames) is not int or not 1 <= max_frames <= 64):
        raise ValueError('Task boundary timeout/frame limit must be bounded')
    guest, peer = session.g, session.peer
    _paused(guest)
    if peer.rx_clear_pc != RX_RETIRED_PC:
        raise ValueError('Task boundary requires the validated Z8 RX retirement binding')
    row = dict(classification='original_MCU_task_boundary_observation',
               timeout=timeout, max_frames=max_frames, attempts=[], neutral_frames=0,
               context_register_writes=False, completed=False)
    guest.data.setdefault('task_context_observations', []).append(row)
    deadline = time.monotonic() + timeout

    def candidates():
        hits = []
        if peer.points.get(RX_RETIRED_PC) != 'rx_frame_retired':
            return hits
        for cpu in range(4):
            text = guest.qmp.hmp('info registers', cpu)
            found = re.search(r'\bPC=([0-9a-fA-F]+)', text)
            if not found:
                raise RuntimeError('Cannot read MCU boundary CPU snapshot')
            if int(found[1], 16) == RX_RETIRED_PC:
                hits.append(cpu)
        return hits

    def inspect():
        hits = candidates()
        for cpu in hits:
            current = read_current_context(guest, cpu)
            current['peer_asserted_irqs'] = sorted(peer.asserted)
            if peer.asserted:
                current['callable'] = False
                current['reasons'].append('peer_IRQ_still_asserted')
            row['attempts'].append(current)
            if current['callable']:
                row.update(cpu=cpu, context=current, completed=True,
                           retirement_peer_event_handled=False)
                return cpu, hits
        return None, hits

    def enqueue():
        if row['neutral_frames'] >= max_frames:
            raise TimeoutError('No callable task context within MCU frame bound')
        peer.enqueue_external(key_body(), 2)
        row['neutral_frames'] += 1

    try:
        cpu, hits = inspect()
        if cpu is not None:
            return cpu, row
        if hits:
            if not session.advance(False):
                raise RuntimeError('MCU retirement stop was not handled by its peer')
        enqueue()
        guest.qmp.command('cont')
        while time.monotonic() < deadline:
            if 'Z9_UNKNOWN' in guest.log():
                raise RuntimeError('Unknown hardware access while finding task context')
            running = guest.qmp.command('query-status')['running']
            if not running:
                cpu, hits = inspect()
                if cpu is not None:
                    return cpu, row
                if not session.advance(False):
                    raise RuntimeError('Unexpected guest stop while finding task context')
                if hits:
                    enqueue()
                guest.qmp.command('cont')
            else:
                session.advance(True)
            time.sleep(.002)
        raise TimeoutError('No callable MCU task context before timeout')
    except BaseException as exc:
        row['error'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        if guest.qmp.command('query-status')['running']:
            guest.qmp.command('stop')
