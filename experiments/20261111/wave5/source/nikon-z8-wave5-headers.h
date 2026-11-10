/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Z8 MAIN40c3ccfc/402f49d4: four payload lengths, each excluding a
 * four-byte Annex B prefix, in AUD/VPS/SPS/PPS order. No image statistics.
 * Select actual encoder parameter sets for the native code-option 0x3c;
 * encoder identification SEI is not requested. No VCL is manufactured. */
#ifndef NIKON_Z8_WAVE5_HEADERS_H
#define NIKON_Z8_WAVE5_HEADERS_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

static size_t z8_w5_start_code(const uint8_t *p, size_t n)
{
    if (n >= 4 && !p[0] && !p[1] && !p[2] && p[3] == 1) { return 4; }
    if (n >= 3 && !p[0] && !p[1] && p[2] == 1) { return 3; }
    return 0;
}

static bool z8_w5_header_pack(const uint8_t *in, size_t size, uint8_t *out,
                              size_t capacity, size_t *written, uint32_t sizes[4])
{
    size_t offsets[3] = {0}, lengths[3] = {0}, pos = 0, total = 7;
    const size_t limits[3] = {0x80 - 4, 0x800 - 4, 0x80 - 4};
    static const uint8_t prefix[4] = {0, 0, 0, 1};
    /* HEVC AUD: layer0, temporal_id_plus1=1, pic_type=0 (first IDR AU),
     * rbsp_stop_one_bit. This is a requested delimiter, not a video sample. */
    static const uint8_t aud[7] = {0, 0, 0, 1, 0x46, 1, 0x10};
    if (!in || !out || !written || !sizes || !size || size > 64 * 1024 * 1024) { return false; }
    while (pos < size) {
        size_t prefix_size = z8_w5_start_code(in + pos, size - pos);
        if (!prefix_size) { return false; }
        size_t first = pos + prefix_size, end = first;
        while (end < size && !z8_w5_start_code(in + end, size - end)) { end++; }
        if (end - first < 2 || (in[first] & 0x81) || in[first + 1] != 1) { return false; }
        unsigned type = (in[first] >> 1) & 63;
        if (type >= 32 && type <= 34) {
            unsigned index = type - 32;
            if (lengths[index] || end - first > limits[index]) { return false; }
            offsets[index] = first; lengths[index] = end - first;
            total += lengths[index] + 4;
        } else if (type != 39) { return false; }
        pos = end;
    }
    if (!lengths[0] || !lengths[1] || !lengths[2] || total > capacity) { return false; }
    /* Nothing is modified on rejection. Output is a separate DMA staging buffer. */
    memcpy(out, aud, sizeof(aud)); pos = sizeof(aud); sizes[0] = 3;
    for (unsigned n = 0; n < 3; n++) {
        memcpy(out + pos, prefix, 4); pos += 4;
        memcpy(out + pos, in + offsets[n], lengths[n]); pos += lengths[n];
        sizes[n + 1] = lengths[n];
    }
    *written = pos;
    return true;
}
/* MAIN40d0a138..40d0a50c converts a seven-byte AUD then a four-byte
 * VCL prefix to MOV lengths. x265 emits the first AUD in the separate
 * header contract, then real per-picture AUDs when enabled. Normalize
 * only Annex B prefixes; every encoder NAL payload remains unchanged. */
static bool z8_w5_picture_pack(const uint8_t *in, size_t size, bool first,
                               uint8_t *out, size_t capacity, size_t *written)
{
    size_t offsets[2], lengths[2], pos=0, total=0;
    unsigned count=0;
    if (!in || !out || !written || !size || size>64*1024*1024) { return false; }
    while (pos<size) {
        size_t prefix=z8_w5_start_code(in+pos,size-pos), end=pos+prefix;
        if (!prefix || count==2) { return false; }
        size_t begin=end;
        while (end<size && !z8_w5_start_code(in+end,size-end)) { end++; }
        if (end-begin<2 || (in[begin]&0x81) || in[begin+1]!=1) { return false; }
        unsigned type=(in[begin]>>1)&63;
        if (!first && count==0) {
            if (type!=35 || end-begin!=3 || (in[begin+2]&0x1f)!=0x10 ||
                in[begin+2]>0x50) { return false; }
        } else if (type>=32 || count!=(first?0:1)) { return false; }
        offsets[count]=begin;lengths[count]=end-begin;count++;total+=end-begin+4;pos=end;
    }
    if (count!=(first?1:2) || total>capacity) { return false; }
    pos=0;
    for (unsigned n=0;n<count;n++) {
        static const uint8_t prefix[4]={0,0,0,1};
        memcpy(out+pos,prefix,4);pos+=4;
        memcpy(out+pos,in+offsets[n],lengths[n]);pos+=lengths[n];
    }
    *written=pos;return true;
}
#endif
