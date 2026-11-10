/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef NIKON_Z8_BANK0_D980_H
#define NIKON_Z8_BANK0_D980_H
#include <stdbool.h>
#include <stdint.h>
/* Additional admission only. All old descriptor/RAM/NB checks remain shared.
 * Actual D980 format codes: 2 packed12, 1 packed14; 0 is NOT ordinary U16. */
typedef enum { Z8_NR_REJECT, Z8_NR_ORDINARY, Z8_NR_HSFC, Z8_NR_HEIF } Z8NRRoute;
static inline Z8NRRoute z8_bank0_route(bool decoded, bool heif, unsigned format)
{
    if (decoded) { return Z8_NR_HSFC; }
    if (heif && format == 2) { return Z8_NR_HEIF; }
    /* Bank0 format2 is exclusively H, even when H is disabled. */
    return format == 1 ? Z8_NR_ORDINARY : Z8_NR_REJECT;
}
static inline bool z8_d980_other_bank_available(uint64_t generation,
    uint64_t other_consumed, bool other_pending, uint64_t source, uint64_t other_source)
{
    return generation && generation != other_consumed &&
        !(other_pending && source == other_source);
}
#endif
