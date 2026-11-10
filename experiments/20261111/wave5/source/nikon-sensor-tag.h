/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef NIKON_SENSOR_TAG_H
#define NIKON_SENSOR_TAG_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* Authored external sensor fixture, not Nikon image processing. Each pair of
 * columns carries one bit of a nonzero frame ID. The second eight rows carry
 * its complement, preserving CFA cell alignment. Only pre-DMA input changes;
 * guest frame queues, metadata, processing results and NEF bytes are untouched.
 */
static inline bool nikon_sensor_tag_apply(uint8_t *data, size_t bytes,
    unsigned width, unsigned height, unsigned stride, unsigned bits,
    uint64_t frame_id)
{
    if (!data || width < 64 || height < 16 || width > 16384 || height > 16384 ||
        ((width | height) & 1) || (bits != 12 && bits != 14) ||
        !frame_id || frame_id > UINT32_MAX ||
        stride < ((uint64_t)width * bits + 7) / 8 ||
        (uint64_t)stride * height > bytes || bytes > 128u * 1024u * 1024u) {
        return false;
    }
    unsigned white = (1u << bits) - 1;
    for (unsigned y = 0; y < 16; y++) {
        uint8_t *row = data + (size_t)y * stride;
        for (unsigned x = 0; x < 64; x++) {
            unsigned bit = ((frame_id >> (x / 2)) & 1) ^ (y >= 8);
            unsigned sample = bit ? white * 7 / 8 : white / 8;
            for (unsigned n = 0; n < bits; n++) {
                unsigned at = x * bits + n;
                uint8_t mask = 1u << (at & 7);
                row[at / 8] = (row[at / 8] & ~mask) |
                              (((sample >> n) & 1) ? mask : 0);
            }
        }
    }
    return true;
}
/* Optional second copy entirely inside the Z8 photo crop. External scene
 * fixture only; keep the original top-left tag and its verifier unchanged. */
static inline bool nikon_sensor_tag_visible_copy(uint8_t *data,size_t bytes,
    unsigned width,unsigned height,unsigned stride,unsigned bits,uint64_t frame_id)
{
    if(!data || width<128 || height<48 || width>16384 || height>16384 ||
       (bits!=12 && bits!=14) || stride<((uint64_t)width*bits+7)/8 ||
       (uint64_t)stride*height>bytes || bytes>128u*1024u*1024u) { return false; }
    uint8_t marker[16*128]={0};
    if(!nikon_sensor_tag_apply(marker,sizeof(marker),64,16,128,bits,frame_id)) { return false; }
    for(unsigned row=0;row<16;row++)for(unsigned x=0;x<64*bits/8;x++) {
        data[(size_t)(row+32)*stride+64*bits/8+x]=marker[row*128+x];
    }
    return true;
}
#endif
