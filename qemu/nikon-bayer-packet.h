/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef NIKON_BAYER_PACKET_H
#define NIKON_BAYER_PACKET_H

/* Authored, reversible simulation transport for packed Bayer samples.
 * This is NOT Nikon's internal compression format. Packet bytes must live
 * in the guest's actual configured RAM; a consumer must read and validate
 * them there. No archived pixels, guest objects, or completion events exist
 * in this helper. Callers stage output privately until every check passes.
 */
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <zlib.h>

#define NBP_HEADER_BYTES 80u
#define NBP_MAX_RAW_BYTES (128u * 1024u * 1024u)
#define NBP_MAX_PACKET_BYTES (129u * 1024u * 1024u)

typedef struct NBPMeta {
    uint32_t width, height, stride, bits;
    uint64_t frame_id, virtual_ns;
} NBPMeta;

typedef struct NBPInfo {
    NBPMeta meta;
    uint64_t raw_bytes, payload_bytes;
    uint32_t packet_bytes, raw_crc, payload_crc;
} NBPInfo;

static inline uint32_t nbp_get32(const uint8_t *p)
{
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 |
           (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

static inline uint64_t nbp_get64(const uint8_t *p)
{
    return nbp_get32(p) | (uint64_t)nbp_get32(p + 4) << 32;
}

static inline void nbp_put32(uint8_t *p, uint32_t value)
{
    for (unsigned i = 0; i < 4; i++) { p[i] = value >> (i * 8); }
}

static inline void nbp_put64(uint8_t *p, uint64_t value)
{
    nbp_put32(p, value);
    nbp_put32(p + 4, value >> 32);
}

static inline uint32_t nbp_crc(const uint8_t *p, size_t bytes)
{
    return (uint32_t)crc32(0, p, (uInt)bytes);
}

static inline bool nbp_geometry(const NBPMeta *meta, uint64_t *bytes)
{
    if (!meta || !bytes || !meta->width || !meta->height ||
        ((meta->width | meta->height) & 1) || meta->width > 16384 || meta->height > 16384 ||
        (meta->bits != 12 && meta->bits != 14) ||
        !meta->frame_id || meta->frame_id > UINT32_MAX) {
        return false;
    }
    uint64_t row = ((uint64_t)meta->width * meta->bits + 7) / 8;
    if (meta->stride != ((row + 31) & ~UINT64_C(31))) { return false; }
    *bytes = (uint64_t)meta->stride * meta->height;
    return *bytes && *bytes <= NBP_MAX_RAW_BYTES;
}

static inline bool nbp_disjoint(const void *a, size_t an, const void *b, size_t bn)
{
    uintptr_t ap = (uintptr_t)a, bp = (uintptr_t)b;
    if (!a || !b || ap > UINTPTR_MAX - an || bp > UINTPTR_MAX - bn) { return false; }
    return ap + an <= bp || bp + bn <= ap;
}

static inline bool nbp_header(const uint8_t *packet, size_t bytes, NBPInfo *out)
{
    if (!packet || !out || bytes < NBP_HEADER_BYTES ||
        memcmp(packet, "NZ8BAYR1", 8) || nbp_get32(packet + 8) != NBP_HEADER_BYTES ||
        nbp_get32(packet + 12) != 1 || nbp_get32(packet + 76) != nbp_crc(packet, 76)) {
        return false;
    }
    NBPInfo value = {0};
    value.meta.width = nbp_get32(packet + 16);
    value.meta.height = nbp_get32(packet + 20);
    value.meta.stride = nbp_get32(packet + 24);
    value.meta.bits = nbp_get32(packet + 28);
    value.raw_bytes = nbp_get64(packet + 32);
    value.payload_bytes = nbp_get64(packet + 40);
    value.meta.frame_id = nbp_get64(packet + 48);
    value.meta.virtual_ns = nbp_get64(packet + 56);
    value.raw_crc = nbp_get32(packet + 64);
    value.payload_crc = nbp_get32(packet + 68);
    value.packet_bytes = nbp_get32(packet + 72);
    uint64_t expected;
    if (!nbp_geometry(&value.meta, &expected) || value.raw_bytes != expected ||
        !value.payload_bytes || value.payload_bytes > NBP_MAX_PACKET_BYTES - NBP_HEADER_BYTES ||
        value.payload_bytes > compressBound((uLong)value.raw_bytes) ||
        value.packet_bytes > NBP_MAX_PACKET_BYTES ||
        value.packet_bytes != ((NBP_HEADER_BYTES + value.payload_bytes + 511) & ~UINT64_C(511))) {
        return false;
    }
    *out = value;
    return true;
}

static inline bool nbp_encode(const uint8_t *raw, size_t raw_bytes, const NBPMeta *meta,
                              uint8_t *packet, size_t capacity, size_t *written)
{
    uint64_t expected;
    if (written) { *written = 0; }
    if (!written || !nbp_geometry(meta, &expected) || raw_bytes != expected ||
        capacity <= NBP_HEADER_BYTES || capacity > NBP_MAX_PACKET_BYTES ||
        !nbp_disjoint(raw, raw_bytes, packet, capacity)) {
        return false;
    }
    uLongf payload_bytes = (uLongf)(capacity - NBP_HEADER_BYTES);
    if (compress2(packet + NBP_HEADER_BYTES, &payload_bytes, raw, (uLong)raw_bytes, 6) != Z_OK) {
        return false;
    }
    uint64_t total = (NBP_HEADER_BYTES + (uint64_t)payload_bytes + 511) & ~UINT64_C(511);
    if (total > capacity || total > NBP_MAX_PACKET_BYTES) { return false; }
    memset(packet, 0, NBP_HEADER_BYTES);
    memcpy(packet, "NZ8BAYR1", 8);
    nbp_put32(packet + 8, NBP_HEADER_BYTES);
    nbp_put32(packet + 12, 1);
    nbp_put32(packet + 16, meta->width);
    nbp_put32(packet + 20, meta->height);
    nbp_put32(packet + 24, meta->stride);
    nbp_put32(packet + 28, meta->bits);
    nbp_put64(packet + 32, raw_bytes);
    nbp_put64(packet + 40, payload_bytes);
    nbp_put64(packet + 48, meta->frame_id);
    nbp_put64(packet + 56, meta->virtual_ns);
    nbp_put32(packet + 64, nbp_crc(raw, raw_bytes));
    nbp_put32(packet + 68, nbp_crc(packet + NBP_HEADER_BYTES, payload_bytes));
    nbp_put32(packet + 72, (uint32_t)total);
    nbp_put32(packet + 76, nbp_crc(packet, 76));
    memset(packet + NBP_HEADER_BYTES + payload_bytes, 0, total - NBP_HEADER_BYTES - payload_bytes);
    *written = (size_t)total;
    return true;
}

static inline bool nbp_decode(const uint8_t *packet, size_t packet_bytes,
                              uint8_t *raw, size_t raw_capacity, NBPInfo *out)
{
    NBPInfo value;
    if (!out || !nbp_header(packet, packet_bytes, &value) || packet_bytes != value.packet_bytes ||
        raw_capacity < value.raw_bytes || !nbp_disjoint(packet, packet_bytes, raw, raw_capacity) ||
        nbp_crc(packet + NBP_HEADER_BYTES, value.payload_bytes) != value.payload_crc) {
        return false;
    }
    for (size_t at = NBP_HEADER_BYTES + value.payload_bytes; at < packet_bytes; at++) {
        if (packet[at]) { return false; }
    }
    uLongf raw_bytes = (uLongf)value.raw_bytes;
    uLong payload_bytes = (uLong)value.payload_bytes;
    if (uncompress2(raw, &raw_bytes, packet + NBP_HEADER_BYTES, &payload_bytes) != Z_OK ||
        raw_bytes != value.raw_bytes || payload_bytes != value.payload_bytes ||
        nbp_crc(raw, raw_bytes) != value.raw_crc) {
        return false;
    }
    *out = value;
    return true;
}
#endif
