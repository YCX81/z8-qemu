/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef NIKON_VIDEO_PLANE_H
#define NIKON_VIDEO_PLANE_H
/* Private, lossless 8-bit plane envelope for paired functional devices.
 * Not Nikon's compressed surface format. Consumers must decode real RAM. */
#include "nikon-bayer-packet.h"

typedef struct NVPInfo {
    uint32_t width, height, stride, plane;
    uint64_t epoch, virtual_ns, raw_bytes, payload_bytes;
    uint32_t packet_bytes, raw_crc;
} NVPInfo;

static inline bool nvp_geometry(const NVPInfo *v)
{
    return v && v->width && v->height && !(v->width & 1) && v->width <= 8192 &&
        v->height <= 4320 && v->stride >= v->width && v->stride <= 16384 &&
        v->plane <= 1 && v->epoch && v->raw_bytes == (uint64_t)v->stride * v->height &&
        v->raw_bytes <= 64u * 1024u * 1024u;
}

static inline bool nvp_header(const uint8_t *p, size_t bytes, NVPInfo *info)
{
    if (!p || !info || bytes < 80 || memcmp(p, "NZ8YUV1\0", 8) ||
        nbp_get32(p+8) != 80 || nbp_get32(p+12) != 1 || nbp_get32(p+76) != nbp_crc(p,76)) { return false; }
    NVPInfo v = {nbp_get32(p+16), nbp_get32(p+20), nbp_get32(p+24), nbp_get32(p+28),
                 nbp_get64(p+48), nbp_get64(p+56), nbp_get64(p+32), nbp_get64(p+40),
                 nbp_get32(p+72), nbp_get32(p+64)};
    if (!nvp_geometry(&v) || !v.payload_bytes || v.payload_bytes > 64u*1024u*1024u - 80 ||
        v.packet_bytes != ((80 + v.payload_bytes + 511) & ~UINT64_C(511))) { return false; }
    *info = v;
    return true;
}

static inline bool nvp_encode(const uint8_t *raw, const NVPInfo *v,
                              uint8_t *packet, size_t capacity, size_t *written)
{
    if (written) { *written = 0; }
    if (!written || !nvp_geometry(v) || capacity < 512 || capacity > 64u*1024u*1024u ||
        !nbp_disjoint(raw, v->raw_bytes, packet, capacity)) { return false; }
    uLongf bytes = capacity - 80;
    if (compress2(packet+80, &bytes, raw, v->raw_bytes, 3) != Z_OK) { return false; }
    uint64_t total = (80 + (uint64_t)bytes + 511) & ~UINT64_C(511);
    if (total > capacity) { return false; }
    memset(packet, 0, 80);
    memcpy(packet, "NZ8YUV1\0", 8);
    nbp_put32(packet+8,80); nbp_put32(packet+12,1);
    nbp_put32(packet+16,v->width); nbp_put32(packet+20,v->height);
    nbp_put32(packet+24,v->stride); nbp_put32(packet+28,v->plane);
    nbp_put64(packet+32,v->raw_bytes); nbp_put64(packet+40,bytes);
    nbp_put64(packet+48,v->epoch); nbp_put64(packet+56,v->virtual_ns);
    nbp_put32(packet+64,nbp_crc(raw,v->raw_bytes));
    nbp_put32(packet+68,nbp_crc(packet+80,bytes));
    nbp_put32(packet+72,total); nbp_put32(packet+76,nbp_crc(packet,76));
    memset(packet+80+bytes,0,total-80-bytes);
    *written = total;
    return true;
}

static inline bool nvp_decode(const uint8_t *packet, size_t bytes,
                              uint8_t *raw, size_t capacity, NVPInfo *info)
{
    NVPInfo v;
    if (!info || !nvp_header(packet,bytes,&v) || bytes != v.packet_bytes || capacity < v.raw_bytes ||
        !nbp_disjoint(packet,bytes,raw,capacity) ||
        nbp_crc(packet+80,v.payload_bytes) != nbp_get32(packet+68)) { return false; }
    for (size_t i = 80 + v.payload_bytes; i < bytes; i++) { if (packet[i]) { return false; } }
    uLongf output = v.raw_bytes;
    uLong input = v.payload_bytes;
    if (uncompress2(raw,&output,packet+80,&input) != Z_OK || output != v.raw_bytes ||
        input != v.payload_bytes || nbp_crc(raw,output) != v.raw_crc) { return false; }
    *info = v;
    return true;
}
#endif
