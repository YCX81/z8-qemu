/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef NIKON_Z8_FRAME_PERIOD_H
#define NIKON_Z8_FRAME_PERIOD_H
#include <stdbool.h>
#include <stdint.h>
#include <limits.h>

/* Explicit hypothesis, not measured silicon timing. No shadow-register input. */
static inline bool z8_ch13_period(uint32_t hd, uint32_t lines,
                                 uint64_t clock_hz, uint64_t *period)
{
    uint64_t ticks;
    if (clock_hz != 72000000 || (hd & 0xffff) == 0xffff ||
        !(hd & 0xffff) || !lines || lines >= 0xffff) {
        return false;
    }
    ticks = ((hd & 0xffff) + 1) * ((uint64_t)lines + 1);
    /* Bound before multiplying by nanoseconds; reject, never clamp. */
    if (ticks < 72000 || ticks > 72000000) {
        return false;
    }
    *period = (ticks * UINT64_C(1000000000) + clock_hz / 2) / clock_hz;
    return true;
}

/* Advance the existing phase, skipping missed slots; never return catch-up
 * frames. Actual captured_ns always comes from the clock at the RAM write. */
static inline bool z8_ch13_deadline(uint64_t now, uint64_t deadline,
                                   uint64_t period, uint64_t *next,
                                   uint64_t *elapsed)
{
    if (!period || now > INT64_MAX || deadline > now) {
        return false;
    }
    *elapsed = (now - deadline) / period + 1;
    if (*elapsed > ((uint64_t)INT64_MAX - deadline) / period) {
        return false;
    }
    *next = deadline + *elapsed * period;
    return true;
}
#endif
