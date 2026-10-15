/* SPDX-License-Identifier: GPL-2.0-or-later */
/* U16 storage adapter for the attributed F0 encoder. No sample rescaling. */
#ifndef NIKON_Z8_LOSSLESS_U16_H
#define NIKON_Z8_LOSSLESS_U16_H

#include <stdlib.h>
#include "nikon-expeed-lossless.h"

/* The caller owns input/output extents and must not DMA partial output on
 * failure. Row padding is storage only. Reject all values above 14 bits;
 * truncating high bits would turn an invalid profile into silent data loss.
 */
static bool nikon_z8_lossless14_u16_encode(const uint8_t *input, size_t input_size,
    unsigned width, unsigned height, unsigned stride, const uint16_t seeds[4],
    uint8_t *output, size_t capacity, size_t *written)
{
    if (!written) {
        return false;
    }
    *written = 0;
    if (!input || !output || !seeds || !width || !height || (width & 3) ||
        width > 16384 || height > 16384 || stride < width * 2 ||
        height > input_size / stride) {
        return false;
    }
    unsigned packed_stride = (width * 7 / 4 + 31) & ~31U;
    size_t packed_bytes = (size_t)packed_stride * height;
    uint8_t *packed = calloc(1, packed_bytes);
    if (!packed) {
        return false;
    }
    for (unsigned y = 0; y < height; y++) {
        const uint8_t *row = input + (size_t)y * stride;
        uint8_t *out = packed + (size_t)y * packed_stride;
        for (unsigned x = 0; x < width; x += 4) {
            uint64_t word = 0;
            for (unsigned j = 0; j < 4; j++) {
                unsigned pos = (x + j) * 2;
                unsigned value = row[pos] | (unsigned)row[pos + 1] << 8;
                if (value > 16383) {
                    free(packed);
                    return false;
                }
                word |= (uint64_t)value << (14 * j);
            }
            for (unsigned j = 0; j < 7; j++) {
                out[x * 7 / 4 + j] = word >> (8 * j);
            }
        }
    }
    bool ok = nikon_z8_lossless14_encode(packed, packed_bytes, width, height,
        packed_stride, seeds, output, capacity, written);
    free(packed);
    return ok;
}
#endif
