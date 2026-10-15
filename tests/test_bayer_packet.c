/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Authored reversible simulation packet tests, not Nikon codec samples. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "nikon-bayer-packet.h"

#define CHECK(x) do { if (!(x)) { fprintf(stderr, "packet check failed at %d: %s\n", __LINE__, #x); return 1; } } while (0)

int main(int argc, char **argv)
{
    uint8_t raw[4096], restored[4096], packet[8192], changed[8192];
    NBPMeta meta = {64, 32, 128, 14, 7, UINT64_C(123456789)};
    NBPInfo info;
    size_t written = 0;
    for (size_t i = 0; i < sizeof(raw); i++) { raw[i] = (i * 13 + (i / 128) * 7) & 255; }
    CHECK(nbp_encode(raw, sizeof(raw), &meta, packet, sizeof(packet), &written));
    CHECK(written > NBP_HEADER_BYTES && written % 512 == 0);
    CHECK(nbp_header(packet, NBP_HEADER_BYTES, &info));
    CHECK(info.meta.width == 64 && info.meta.height == 32 && info.meta.stride == 128);
    CHECK(info.meta.bits == 14 && info.meta.frame_id == 7 && info.meta.virtual_ns == UINT64_C(123456789));
    CHECK(info.raw_bytes == sizeof(raw) && info.packet_bytes == written);
    CHECK(nbp_decode(packet, written, restored, sizeof(restored), &info));
    CHECK(!memcmp(raw, restored, sizeof(raw)));
    if (argc == 2) {
        FILE *stream = fopen(argv[1], "wb");
        CHECK(stream && fwrite(packet, 1, written, stream) == written);
        CHECK(fclose(stream) == 0);
    }

    memcpy(changed, packet, written); changed[0] ^= 1;
    CHECK(!nbp_decode(changed, written, restored, sizeof(restored), &info));
    memcpy(changed, packet, written); changed[20] ^= 2;
    CHECK(!nbp_header(changed, NBP_HEADER_BYTES, &info));
    memcpy(changed, packet, written); changed[NBP_HEADER_BYTES + 3] ^= 1;
    CHECK(!nbp_decode(changed, written, restored, sizeof(restored), &info));
    memcpy(changed, packet, written); changed[written - 1] ^= 1;
    CHECK(!nbp_decode(changed, written, restored, sizeof(restored), &info));
    CHECK(!nbp_decode(packet, written - 1, restored, sizeof(restored), &info));
    CHECK(!nbp_decode(packet, written, restored, sizeof(restored) - 1, &info));
    CHECK(!nbp_header(packet, NBP_HEADER_BYTES - 1, &info));
    /* Valid header CRC does not excuse impossible sizes or bad raw CRC. */
    memcpy(changed, packet, written); nbp_put64(changed + 40, UINT64_MAX);
    nbp_put32(changed + 76, nbp_crc(changed, 76));
    CHECK(!nbp_header(changed, NBP_HEADER_BYTES, &info));
    memcpy(changed, packet, written); nbp_put64(changed + 48, 0);
    nbp_put32(changed + 76, nbp_crc(changed, 76));
    CHECK(!nbp_header(changed, NBP_HEADER_BYTES, &info));
    memcpy(changed, packet, written); nbp_put32(changed + 64, nbp_get32(changed + 64) ^ 1);
    nbp_put32(changed + 76, nbp_crc(changed, 76));
    CHECK(!nbp_decode(changed, written, restored, sizeof(restored), &info));
    memcpy(changed, packet, written); changed[80] = 0;
    nbp_put32(changed + 68, nbp_crc(changed + 80, nbp_get64(changed + 40)));
    nbp_put32(changed + 76, nbp_crc(changed, 76));
    CHECK(!nbp_decode(changed, written, restored, sizeof(restored), &info));

    NBPMeta invalid = meta;
    invalid.width = 63;
    CHECK(!nbp_encode(raw, sizeof(raw), &invalid, changed, sizeof(changed), &written) && written == 0);
    invalid = meta; invalid.stride = 64;
    CHECK(!nbp_encode(raw, sizeof(raw), &invalid, changed, sizeof(changed), &written));
    invalid = meta; invalid.frame_id = 0;
    CHECK(!nbp_encode(raw, sizeof(raw), &invalid, changed, sizeof(changed), &written));
    invalid = meta; invalid.frame_id = UINT64_C(0x100000000);
    CHECK(!nbp_encode(raw, sizeof(raw), &invalid, changed, sizeof(changed), &written));
    invalid = meta; invalid.bits = 16;
    CHECK(!nbp_encode(raw, sizeof(raw), &invalid, changed, sizeof(changed), &written));
    invalid = meta; invalid.width = 16384; invalid.height = 16384; invalid.stride = 28672;
    CHECK(!nbp_encode(raw, sizeof(raw), &invalid, changed, sizeof(changed), &written));
    CHECK(!nbp_encode(raw, sizeof(raw) - 1, &meta, changed, sizeof(changed), &written));
    CHECK(!nbp_encode(raw, sizeof(raw), &meta, changed, 80, &written));
    CHECK(!nbp_encode(raw, sizeof(raw), &meta, raw, sizeof(raw), &written));

    meta.frame_id = 8; meta.virtual_ns++;
    raw[256] ^= 0x80;
    CHECK(nbp_encode(raw, sizeof(raw), &meta, changed, sizeof(changed), &written));
    CHECK(nbp_decode(changed, written, restored, sizeof(restored), &info));
    CHECK(info.meta.frame_id == 8 && info.meta.virtual_ns == UINT64_C(123456790));
    CHECK(!memcmp(raw, restored, sizeof(raw)));
    puts("bayer packet scenarios passed: 12");
    return 0;
}
