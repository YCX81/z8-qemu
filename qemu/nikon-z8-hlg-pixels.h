/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Static C3.11 format contracts; numerical/packing hypotheses, not Nikon ISP. */
#ifndef NIKON_Z8_HLG_PIXELS_H
#define NIKON_Z8_HLG_PIXELS_H
#include <stdbool.h>
#include <stdint.h>

/* Replace here when independently verified. Three 10-bit samples per LE32,
 * ascending significance, high two bits zero; NOT proven from a native dump.
 * The earlier enum3 surface instead has two 12-bit samples per three bytes.
 * Do not put a 10-bit 4/3-width payload into its 3/2-width contract. */
#define Z8_HLG_PACK10_LSB_FIRST_HYPOTHESIS 1
#define Z8_HLG_PACK12_LSB_FIRST_HYPOTHESIS 1

typedef struct Z8HLGPlan {
    unsigned iw, ih, ow, oh, cx, cy, pitch, stride, output_bits, uv_height;
    uint64_t input_bytes, plane_y, plane_uv;
} Z8HLGPlan;

typedef struct Z8HLGCapture {
    bool enabled, last_output_hlg;
    uint64_t jobs, rejects, source_frame, source, y, uv, source_bytes;
    Z8HLGPlan plan;
    char source_sha256[65], y_sha256[65], uv_sha256[65];
    const char *last_error;
} Z8HLGCapture;

static inline bool z8_hlg_plan(const uint32_t *c, unsigned source_bits, Z8HLGPlan *out)
{
    if (!c || !out || (source_bits != 12 && source_bits != 14) ||
        c[0x200/4] != 0x10000 || c[0x204/4] || c[0x60/4] || c[0x64/4] ||
        c[0x218/4] != 1 || c[0x21c/4] != 1 || c[0x220/4] != 19) {
        return false;
    }
    /* 40e83628..50 -> 40c0e920..2c: {1,3}->2; {2,1}->5.
     * These are narrow statically derived output tuples. Runtime confirmation
     * of which tuple reaches the first HLG START is still required. */
    unsigned mode = c[0x45c/4];
    if (mode != 2 && mode != 5) { return false; }
    Z8HLGPlan p = {
        .iw=c[0x400/4]&0x3fff, .ih=c[0x400/4]>>16,
        .ow=c[0x450/4]&0x3fff, .oh=c[0x450/4]>>16,
        .cx=c[0x454/4]&0x3fff, .cy=c[0x454/4]>>16,
        .pitch=c[0x430/4], .stride=c[0x480/4], .output_bits=mode==2 ? 12 : 10,
    };
    if (!p.iw || !p.ih || !p.ow || !p.oh || p.iw>16382 || p.ih>8190 ||
        ((p.iw|p.ih|p.ow|p.oh|p.cx|p.cy)&1) || p.cx>p.iw || p.cy>p.ih ||
        p.ow>p.iw-p.cx || p.oh>p.ih-p.cy ||
        p.pitch<(p.iw*source_bits+7)/8 || p.pitch>32768 ||
        p.stride!=c[0x484/4]) { return false; }
    unsigned expected = mode==2 ? ((p.ow*3/2+31)&~31u) : (((p.ow*4+2)/3+63)&~63u);
    if (p.stride!=expected) { return false; }
    p.uv_height = mode==2 ? p.oh : p.oh/2;
    p.input_bytes=(uint64_t)p.pitch*p.ih;
    p.plane_y=(uint64_t)p.stride*p.oh;
    p.plane_uv=(uint64_t)p.stride*p.uv_height;
    if (p.input_bytes>(UINT64_C(128)<<20) || p.plane_y>(UINT64_C(128)<<20) ||
        p.plane_uv>(UINT64_C(128)<<20)) { return false; }
    *out=p; return true;
}

static inline void z8_hlg_store(uint8_t *row, unsigned x, unsigned bits, unsigned value)
{
    unsigned at, shift;
    if (bits==10 && Z8_HLG_PACK10_LSB_FIRST_HYPOTHESIS) {
        at=(x/3)*4; shift=(x%3)*10;
        uint32_t word=(uint32_t)row[at]|(uint32_t)row[at+1]<<8|
                      (uint32_t)row[at+2]<<16|(uint32_t)row[at+3]<<24;
        word=(word & ~(1023u<<shift)) | ((value&1023u)<<shift);
        for(unsigned i=0;i<4;i++) { row[at+i]=(uint8_t)(word>>(8*i)); }
    } else if (bits==12 && Z8_HLG_PACK12_LSB_FIRST_HYPOTHESIS) {
        at=(x/2)*3;
        if (x&1) { row[at+1]=(row[at+1]&15)|((value&15)<<4); row[at+2]=value>>4; }
        else { row[at]=value; row[at+1]=(row[at+1]&0xf0)|((value>>8)&15); }
    }
}

static inline unsigned z8_hlg_load(const uint8_t *row, unsigned x, unsigned bits)
{
    if (bits==10) {
        unsigned at=(x/3)*4;
        uint32_t word=(uint32_t)row[at]|(uint32_t)row[at+1]<<8|
                      (uint32_t)row[at+2]<<16|(uint32_t)row[at+3]<<24;
        return (word>>((x%3)*10))&1023;
    }
    unsigned at=(x/2)*3;
    return (x&1) ? (row[at+1]>>4)|((unsigned)row[at+2]<<4) : row[at]|((row[at+1]&15)<<8);
}
#endif
