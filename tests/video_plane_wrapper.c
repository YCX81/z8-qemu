/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "nikon-video-plane.h"

size_t encode_plane(const uint8_t *raw, unsigned width, unsigned height,
                    unsigned stride, unsigned plane, uint64_t epoch,
                    uint8_t *out, size_t capacity)
{
    NVPInfo info = { .width=width, .height=height, .stride=stride, .plane=plane,
                    .epoch=epoch, .virtual_ns=1234567, .raw_bytes=(uint64_t)stride*height };
    size_t written=0;
    return nvp_encode(raw,&info,out,capacity,&written) ? written : 0;
}

int decode_plane(const uint8_t *packet, size_t bytes, uint8_t *raw, size_t capacity)
{
    NVPInfo info;
    return nvp_decode(packet,bytes,raw,capacity,&info);
}
