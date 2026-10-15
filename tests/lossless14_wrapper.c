/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "nikon-expeed-lossless.h"
bool lossless14_test_encode(const uint8_t *input, size_t input_size,
    unsigned width, unsigned height, unsigned stride, const uint16_t seeds[4],
    uint8_t *output, size_t capacity, size_t *written)
{
    return nikon_z8_lossless14_encode(input, input_size, width, height, stride,
                                    seeds, output, capacity, written);
}
