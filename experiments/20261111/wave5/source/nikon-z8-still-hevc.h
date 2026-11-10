/* SPDX-License-Identifier: GPL-2.0-or-later
 * Z8 C3.11 1602 protocol only. No encoder, DMA, result or event producer.
 * Sources: 40c5bb98/bc18 (reset/table41658488), 40c5bc98 (F),
 * 40c98da4 + template41f1dd90 + 40c5bfa8 (C), 40c5c884 (B).
 * Generic profiles below remain static candidates. Only the exact run06 tuple
 * in nikon-z8-still-hevc-profile.h has native request evidence.
 * Pure protocol helpers permit host unit tests; pending fields have no guest
 * injection API. IRQ aggregation is an explicitly unverified hypothesis.
 */
#ifndef NIKON_Z8_STILL_HEVC_H
#define NIKON_Z8_STILL_HEVC_H
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

typedef struct Z8HEVCWrite { uint16_t off; uint32_t value; } Z8HEVCWrite;
typedef struct Z8HEVCSpan { uint64_t address, bytes; } Z8HEVCSpan;
typedef struct Z8StillHEVC {
    bool enabled, initialized, initializing, prepared, abort_ack;
    unsigned reset_step, init_step;
    uint32_t reg[1024], pending, errors;
    uint64_t seen[16], writes, rejects, prepares, start_attempts, aborts;
    const char *reason;
} Z8StillHEVC;

static const Z8HEVCWrite z8_hevc_reset[] = {
    {0x128,0},{0xfe8,0},{8,0},{0x80,0},{0x84,1},{0xff4,0},
    {0x120,0x010668ff},{0xfe0,0x60000771},{0x84,0}
};
static const Z8HEVCWrite z8_hevc_init[] = {
    {8,0x80000003},{0x200,0},{0x300,0x12},{0x310,0x04380780},
    {0x318,0x2499},{0x370,0x500},{0x374,0},{0x378,0},{0x390,0x40d},
    {0x394,0},{0x3f0,0},{0x400,0x5000},{0x404,0},{0x408,0x20202},
    {0x40c,0},{0x410,0},{0x414,0},{0x440,0},{0x444,1},{0x454,0},
    {0x464,0},{0x704,26},{0xc40,0},
    {0xe40,0},{0xe44,0},{0xe48,0},{0xe4c,0},{0xe50,0},{0xe54,0},{0xe58,0},{0xe5c,0},
    {0xe60,0x8000000},{0xe64,0},
    {0xe90,0},{0xe94,0},{0xe98,0},{0xe9c,0},{0xea0,0},{0xea4,0},{0xea8,0},{0xeac,0},
    {0xeb0,0x1000},{0xeb8,0},{0xf00,0},{0xf04,0},{0xf08,0},{0xf0c,0},
    {0xf10,0},{0xf20,0},{0xf44,0},{0xf50,0},{0xf54,0}
};

static inline uint32_t z8_hevc_reg(const Z8StillHEVC *h, unsigned off)
{ return h->reg[off / 4]; }
static inline bool z8_hevc_seen(const Z8StillHEVC *h, unsigned off)
{ unsigned bit = off / 4; return (h->seen[bit / 64] >> (bit % 64)) & 1; }
static inline bool z8_hevc_fail(Z8StillHEVC *h, const char *reason)
{ h->rejects++; h->reason = reason; return false; }
static inline void z8_hevc_store(Z8StillHEVC *h, unsigned off, uint32_t v, bool seen)
{
    unsigned bit = off / 4;
    h->reg[bit] = v;
    if (seen) { h->seen[bit / 64] |= UINT64_C(1) << (bit % 64); }
    h->writes++;
}
static inline uint32_t z8_hevc_status(const Z8StillHEVC *h)
{
    if (!h->enabled || z8_hevc_reg(h, 8) != 0x80000003) { return 0; }
    return ((h->pending & z8_hevc_reg(h, 0x128)) ? 1 : 0) |
           ((h->errors & z8_hevc_reg(h, 0xfe8)) ? 2 : 0);
}
static inline bool z8_hevc_irq(const Z8StillHEVC *h)
{ return z8_hevc_status(h) != 0; }
static inline bool z8_hevc_power(uint32_t v, uint32_t low, uint32_t high)
{ return v >= low && v <= high && !(v & (v - 1)); }

/* Per-register write whitelist; cross-register consistency checked at command2.
 * Unknown optional analysis/workspace command80000000 stays UNKNOWN in P2a. */
static inline bool z8_hevc_config_value(unsigned off, uint32_t v)
{
    switch (off) {
    case 0xff0: return v == 0 || ((v & 0x80000000) && !(v & ~0x80f000f0u));
    case 0x204: return v == 0x14; /* R.b4=20 decimal, 40e81c30/44. */
    case 0x300: return v == 0x50a;
    case 0x310: return v == 0x15802040 || v == 0x00d40140 || v == 0x04380654;
    case 0x318: return v == 0x278 || v == 0x23c;
    case 0x370: return v == 0x480;
    case 0x390: return v == 0x80d;
    case 0x394: return v == 4;
    case 0x400: return v == 0x1d800;
    case 0x408: return v == 0x91209;
    case 0x3f0: case 0x404: case 0x40c: case 0x410: case 0x414: case 0xeb8:
    case 0xe64: case 0x740: case 0x744: case 0x748: case 0x74c: case 0x780: case 0x7a0:
        return v == 0;
    case 0xf20: return v == 0 || v == 0x0ac00300;
    case 0x200: return v == 1 || v == 5;
    case 0x080: return v == 0x5b || v == 0x3db;
    case 0x128: return v == 0 || v == 0x01020001 || v == 0x01060055;
    case 0xfe8: return v == 0 || (v & ~0x60000000u) == 0x770;
    case 0xe40: case 0xe48: case 0xe50: case 0xe58:
    case 0xe90: case 0xe98: case 0xea0: case 0xea8:
        return v != 0; /* 36-bit address in 16-byte units */
    case 0xe60: return z8_hevc_power(v, 0x1000, 0x8000000);
    case 0xeb0: return v == 0x1000; /* R+134 service profile */
    case 0xf00: case 0xf08: return !(v & 7);
    case 0xf04: case 0xf0c: return v < 16;
    case 0xf10: return v == 11008 || v == 448 || v == 2176;
    case 0xf44: return v == 8;
    case 0xf50: return v && v < 0x1000000;
    case 0xf54: return v < 0x1000000;
    case 0x704: return v <= 51;
    default: return false;
    }
}

/* Returns the inert request spans. QEMU additionally checks actual RAM backing
 * before accepting command2. No mapping, producer ownership or encoding claim
 * follows from a valid 36-bit address alone. */
static inline unsigned z8_hevc_spans(const Z8StillHEVC *h, Z8HEVCSpan spans[10])
{
    static const uint16_t required[] = {
        0xff0,0x204,0x300,0x310,0x318,0x370,0x390,0x394,0x3f0,
        0x400,0x404,0x408,0x40c,0x410,0x414,0xf20,0xeb8,
        0x200,0x80,0x128,0xfe8,0xe60,0xe64,0xeb0,
        0xf00,0xf04,0xf08,0xf0c,0xf10,0xf44,0xf50,0xf54,0x704,
        0x740,0x744,0x748,0x74c,0x780,0x7a0
    };
    uint32_t geometry = z8_hevc_reg(h, 0x310), stride = z8_hevc_reg(h, 0xf10);
    unsigned lanes = geometry == 0x15802040 ? 4 : 1, count = 2 + lanes * 2;
    uint32_t capacity = z8_hevc_reg(h, 0xe60), records = z8_hevc_reg(h, 0xeb0);
    if (!h->initialized) { return 0; }
    for (unsigned i = 0; i < sizeof(required)/sizeof(required[0]); i++) {
        if (!z8_hevc_seen(h, required[i])) { return 0; }
    }
    if ((geometry == 0x15802040 && (stride != 11008 || z8_hevc_reg(h,0xf20) != 0x0ac00300)) ||
        (geometry == 0x00d40140 && (stride != 448 || z8_hevc_reg(h,0x318) != 0x23c)) ||
        (geometry == 0x04380654 && stride != 2176) ||
        (geometry != 0x00d40140 && z8_hevc_reg(h,0x318) != 0x278) ||
        (lanes == 1 && (z8_hevc_reg(h,0xf20) || z8_hevc_reg(h,0xf54))) ||
        z8_hevc_reg(h,0x200) != (lanes == 4 ? 5 : 1) ||
        z8_hevc_reg(h,0x80) != (lanes == 4 ? 0x3db : 0x5b) ||
        z8_hevc_reg(h,0x128) != (lanes == 4 ? 0x01060055 : 0x01020001) ||
        z8_hevc_reg(h,0xfe8) != (0x20000770u | (z8_hevc_reg(h,0xf54) ? 0x40000000u : 0)) ||
        (uint64_t)z8_hevc_reg(h,0xf50) * 1024 > (uint64_t)lanes * capacity) { return 0; }
    spans[0] = (Z8HEVCSpan){((uint64_t)z8_hevc_reg(h,0xf04) << 32) | z8_hevc_reg(h,0xf00),
                          (uint64_t)stride * (geometry >> 16)};
    spans[1] = (Z8HEVCSpan){((uint64_t)z8_hevc_reg(h,0xf0c) << 32) | z8_hevc_reg(h,0xf08), spans[0].bytes / 2};
    for (unsigned i = 0; i < lanes; i++) {
        unsigned bs = 0xe40 + i * 8, rec = 0xe90 + i * 8;
        if (!z8_hevc_seen(h,bs) || !z8_hevc_seen(h,rec) ||
            (uint64_t)z8_hevc_reg(h,bs) != (uint64_t)z8_hevc_reg(h,0xe40) + i * (capacity / 16) ||
            (uint64_t)z8_hevc_reg(h,rec) != (uint64_t)z8_hevc_reg(h,0xe90) + i * (records / 16)) { return 0; }
        spans[2 + i * 2] = (Z8HEVCSpan){(uint64_t)z8_hevc_reg(h,bs) << 4, capacity};
        spans[3 + i * 2] = (Z8HEVCSpan){(uint64_t)z8_hevc_reg(h,rec) << 4, records};
    }
    for (unsigned i = 0; i < count; i++) {
        if (!spans[i].address || !spans[i].bytes || spans[i].address >= (UINT64_C(1) << 36) ||
            spans[i].bytes > (UINT64_C(1) << 36) - spans[i].address) { return 0; }
        for (unsigned j = 0; j < i; j++) {
            if (spans[i].address < spans[j].address + spans[j].bytes &&
                spans[j].address < spans[i].address + spans[i].bytes) { return 0; }
        }
    }
    return count;
}

static inline bool z8_hevc_write(Z8StillHEVC *h, bool bootstrap, unsigned off, unsigned size, uint64_t value)
{
    uint32_t v = value;
    if (!bootstrap || off >= 0x1000 || size != 4 || (off & 3) || value > UINT32_MAX) {
        return z8_hevc_fail(h,"access-width-alignment-or-bootstrap");
    }
    if ((off == 0x7f8 && v == 0x544d3234) || (off == 0x7fc && v == 0x36332020)) {
        h->writes++; return true; /* Exact legacy default-off behavior. */
    }
    if (!h->enabled) { return z8_hevc_fail(h,"disabled"); }
    if (off == 0x128 && v == 0) {
        h->reset_step = 1; h->initializing = false;
        z8_hevc_store(h,off,v,false); return true;
    }
    if (h->reset_step == 3 && off == 0xff4 && v == 1) {
        h->initialized = h->prepared = false; h->abort_ack = true;
        h->reset_step = 0; h->aborts++;
        memset(h->seen,0,sizeof(h->seen));
        z8_hevc_store(h,off,v,false); return true;
    }
    if (h->reset_step) {
        const Z8HEVCWrite *expected = &z8_hevc_reset[h->reset_step];
        if (off == expected->off && v == expected->value) {
            if (off == 0x84 && v == 1) {
                h->initialized = h->prepared = h->abort_ack = false;
                memset(h->seen,0,sizeof(h->seen));
            }
            if (off == 0x120) { h->pending &= ~v; }
            if (off == 0xfe0) { h->errors &= ~v; }
            z8_hevc_store(h,off,v,false);
            if (++h->reset_step == sizeof(z8_hevc_reset)/sizeof(z8_hevc_reset[0])) {
                h->reset_step = 0; h->init_step = 0; h->initializing = true;
            }
            return true;
        }
        /* Disabling masks need not imply a reset; initialization/abort is the
         * only route once the reset pulse has actually been asserted. */
        if (!h->initialized || h->reset_step > 3) { return z8_hevc_fail(h,"reset-sequence"); }
        h->reset_step = 0;
    }
    if (h->initializing) {
        const Z8HEVCWrite *expected = &z8_hevc_init[h->init_step];
        if (off != expected->off || v != expected->value) { return z8_hevc_fail(h,"init-table"); }
        z8_hevc_store(h,off,v,false);
        if (++h->init_step == sizeof(z8_hevc_init)/sizeof(z8_hevc_init[0])) {
            h->initializing = false; h->initialized = true;
        }
        return true;
    }
    if (!h->initialized) { return z8_hevc_fail(h,"not-initialized"); }
    if (off == 0x120 || off == 0xfe0) {
        uint32_t mask = off == 0x120 ? 0x010668ff : 0x60000771;
        if (v & ~mask) { return z8_hevc_fail(h,"w1c-mask"); }
        if (off == 0x120) { h->pending &= ~v; } else { h->errors &= ~v; }
        h->writes++; return true;
    }
    if (off == 0x100) {
        Z8HEVCSpan spans[10];
        if (v == 1) {
            h->start_attempts++;
            return z8_hevc_fail(h,h->prepared ? "start-unimplemented" : "start-before-prepare");
        }
        if (v != 2) { return z8_hevc_fail(h,"command-unimplemented"); }
        if (h->pending || h->errors) { return z8_hevc_fail(h,"pending-unacked"); }
        if (h->prepared) { return z8_hevc_fail(h,"prepared-reuse"); }
        if (!z8_hevc_spans(h,spans)) { return z8_hevc_fail(h,"configuration-incomplete-or-span"); }
        h->prepared = true; h->prepares++; h->writes++;
        return true; /* Control command2 self-clears; no job starts. */
    }
    if (h->prepared && off != 0x128 && off != 0xfe8) { return z8_hevc_fail(h,"prepared-immutable"); }
    if (!z8_hevc_config_value(off,v)) { return z8_hevc_fail(h,"configuration-value"); }
    z8_hevc_store(h,off,v,true); return true;
}

static inline bool z8_hevc_read(Z8StillHEVC *h, bool bootstrap, unsigned off, unsigned size, uint32_t *value)
{
    if (!bootstrap || !h->enabled || off >= 0x1000 || size != 4 || (off & 3)) {
        return z8_hevc_fail(h,"read-width-alignment-or-disabled");
    }
    switch (off) {
    case 0x004: *value = z8_hevc_status(h); return true;
    case 0x120: *value = h->pending; return true;
    case 0xfe0: *value = h->errors; return true;
    case 0xfe4: if (h->abort_ack) { *value = 1; return true; } break;
    case 0x100: if (h->prepared) { *value = 0; return true; } break;
    case 0x200: if (h->initialized) { *value = z8_hevc_reg(h,off); return true; } break;
    default: break;
    }
    return z8_hevc_fail(h,"result-or-read-unimplemented");
}
#include "nikon-z8-still-hevc-profile.h"
#endif
