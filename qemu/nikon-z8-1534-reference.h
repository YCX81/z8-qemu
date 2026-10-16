/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef NIKON_Z8_1534_REFERENCE_H
#define NIKON_Z8_1534_REFERENCE_H
#include <stdbool.h>
#include <stdint.h>
#define Z8_153_PROGRAM_BYTES 0x7c0u
typedef struct Z8Heif1534 {
    bool enabled, reference_valid, address_model;
    uint32_t reference_control[84], mismatch_offset;
    uint8_t reference_program[Z8_153_PROGRAM_BYTES];
    uint32_t accepted_control[84];
    uint8_t accepted_program[Z8_153_PROGRAM_BYTES];
    uint64_t reference_frame, reference_job, required_ack, source, bytes, rejects;
    uint64_t used_reference_frame, used_reference_job, accepted_bytes;
    unsigned width, height, stride, reference_format;
    char reference_program_sha256[65], program_sha256[65], accepted_program_sha256[65];
    char source_sha256[65], readback_sha256[65];
    const char *last_reject;
} Z8Heif1534;
static inline bool z8_1534_same_control(const uint32_t *r,const uint32_t *c,uint32_t *offset)
{
    for (unsigned n=0;n<84;n++) {
        uint32_t wanted=n==0x48/4?0x02c97500:r[n];
        if ((n==0x48/4 && r[n]!=0x02c97d00) ||
            (n==0x4c/4 && r[n]!=0xa) || c[n]!=wanted) { *offset=4*n;return false; }
    }
    *offset=UINT32_MAX;return true;
}
static inline bool z8_1534_same_program(const uint8_t *r,const uint8_t *c,uint32_t *offset)
{
    for (unsigned n=0;n<Z8_153_PROGRAM_BYTES;n++) {
        if (r[n]!=c[n]) { *offset=n;return false; }
    }
    *offset=UINT32_MAX;return true;
}
static inline bool z8_1534_next_frame(uint64_t r,uint64_t f,uint64_t consumed)
{ return r && r!=UINT64_MAX && f==r+1 && consumed<f; }
#endif
