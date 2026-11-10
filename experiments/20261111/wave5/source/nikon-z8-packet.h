/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef NIKON_Z8_PACKET_H
#define NIKON_Z8_PACKET_H
#include <stddef.h>
#include <stdbool.h>
#include <string.h>
#include "nikon-bayer-packet.h"

typedef enum Z8PacketKind {
    Z8_PACKET_UNKNOWN, Z8_PACKET_WLVR, Z8_PACKET_WLYV,
    Z8_PACKET_BAYER
} Z8PacketKind;

/* One magic dispatcher. NZ8BAYR1 uses R's original bounded CRC decoder;
 * WLYV is admitted solely at the fourth-output YUV endpoints. */
static inline Z8PacketKind z8_packet_kind(const void *header, size_t bytes,
                                        bool fourth_yuv)
{
    if (bytes < 8) { return Z8_PACKET_UNKNOWN; }
    if (!fourth_yuv && !memcmp(header, "NZ8BAYR1", 8)) { return Z8_PACKET_BAYER; }
    if (!fourth_yuv && !memcmp(header, "WLVR0001", 8)) { return Z8_PACKET_WLVR; }
    if (fourth_yuv && !memcmp(header, "WLYV0001", 8)) { return Z8_PACKET_WLYV; }
    return Z8_PACKET_UNKNOWN;
}
static inline bool z8_packet_decode_bayer(const uint8_t *packet, size_t packet_bytes,
    uint8_t *raw, size_t raw_capacity, NBPInfo *info)
{
    return z8_packet_kind(packet, packet_bytes, false) == Z8_PACKET_BAYER &&
           nbp_decode(packet, packet_bytes, raw, raw_capacity, info);
}
#endif
