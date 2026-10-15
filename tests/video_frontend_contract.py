# SPDX-License-Identifier: GPL-2.0-or-later
"""Format-independent 152c/1530 protocol assertions for one backend owner.

Factory(binary, directory) returns a context manager with the adapter methods
used below. Adapters configure a real device and independently decode its RAM
packet; they must not synthesize pending bits or change guest task state.
"""
import json
from pathlib import Path


def check(value, message):
    if not value: raise AssertionError(message)


def run_contract(factory, binary, output):
    output = Path(output); output.mkdir(parents=True, exist_ok=False)
    results = []

    def case(name, body, expect_reject=False):
        try:
            with factory(binary, output/name) as d:
                body(d)
                check(d.rejected() == expect_reject, 'unexpected device rejection')
                results.append(dict(name=name, passed=True, final=d.stats(),
                                    independently_decoded_frames=d.frames))
        except Exception as error:
            results.append(dict(name=name, passed=False, error=str(error)))

    for port in (0, 1):
        for order in ((1, 2), (2, 1)):
            def partial(d, port=port, order=order):
                d.frame()
                check(d.pending(port) == 3, 'whole real packet has DATA0 and DATA1')
                other = d.pending(1-port)
                d.ack(port, order[0])
                check(d.pending(port) == order[1] and d.stats()['data_acks'][port] == 0,
                      'first DATA bit must not retire the packet')
                d.ack(port, 4); d.ack(port, 16)
                check(d.pending(port) == order[1] and d.stats()['data_acks'][port] == 0,
                      'END/REFILL ACK must not retire DATA')
                d.ack(port, order[1])
                check(d.stats()['data_acks'][port] == 1 and d.pending(port) == 0,
                      'last DATA bit retires exactly once')
                d.ack(port, 3)
                check(d.stats()['data_acks'][port] == 1 and d.pending(1-port) == other,
                      'duplicate ACK and peer ownership')
            case(f'p{port}-split-{order[0]}-{order[1]}', partial)

        def masks(d, port=port):
            d.frame(); d.mask(port, 0)
            check(d.pending(port) == 3 and d.masked(port) == 0 and not d.irq(port),
                  'mask suppresses IRQ, preserves raw pending')
            d.mask(port, 1)
            check(d.pending(port) == 3 and d.masked(port) == 1 and d.irq(port),
                  'unmask exposes existing cause without new data')
            d.gate(port, 0)
            check(d.pending(port) == 3 and not d.irq(port), 'gate suppresses level only')
            d.gate(port, 1)
            check(d.irq(port), 'reopen gate exposes same cause')
            d.ack(port, 1)
            check(d.pending(port) == 2 and not d.irq(port), 'unenabled DATA bit remains pending')
            d.mask(port, 2)
            check(d.irq(port), 'second DATA bit remains separately visible')
        case(f'p{port}-mask-gate', masks)

        def stop(d, port=port):
            d.frame(); d.stop(port)
            check(d.pending(port) == 3 and d.stats()['stop_ends'][port] == 0,
                  'STOP cannot reclassify old DATA as END')
            d.gate(port, 0); d.ack(port, 1); d.ack(port, 2)
            check(d.pending(port) == 4 and not d.irq(port), 'fresh END waits behind old DATA')
            d.gate(port, 1)
            check(d.irq(port), 'pending END survives gate cycle after STOP request')
            d.ack(port, 3); d.ack(port, 16)
            check(d.pending(port) == 4 and d.stats()['stop_acks'][port] == 0,
                  'unrelated ACK cannot retire END')
            d.ack(port, 4); d.stop(port)
            check(d.stats()['stop_ends'][port] == 1 and d.stats()['stop_acks'][port] == 1,
                  'one STOP owns one independently acknowledged END')
        case(f'p{port}-stop-order', stop)

        def restart(d, port=port):
            d.frame(); d.stop(port); d.ack(port, 3); d.start(port)
            check(d.pending(port) == 4, 'rejected restart preserves unretired END')
        case(f'p{port}-restart-before-end-ack', restart, True)

        def reset(d, port=port):
            d.frame(); d.stop(port); d.ack(port, 3)
            peer_pending = d.pending(1-port)
            d.reset(port)
            check(d.stats()['stop_cancelled'][port] == 1 and d.stats()['stop_acks'][port] == 0,
                  'reset cancels END rather than reporting an ACK')
            check(d.pending(port) == 0 and d.pending(1-port) == peer_pending,
                  'reset does not retire peer pending')
        case(f'p{port}-reset-cancel', reset)

    report = dict(schema=1, scope='real-device reference contract; no native-firmware acceptance',
                  cases=results, passed=all(r['passed'] for r in results),
                  not_covered=['gate closed before STOP request', 'partial/new bank generation',
                               '1543 bank authorization', 'late consumer and address ABA'])
    (output/'result.json').write_text(json.dumps(report, indent=2)+'\n')
    return report
