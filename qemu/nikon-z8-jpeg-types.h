/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef NIKON_Z8_JPEG_TYPES_H
#define NIKON_Z8_JPEG_TYPES_H
#define Z8_JPEG_SOURCES 16
#define Z8_JPEG_ROLES 8
enum { Z8_JPEG_151 = 0, Z8_JPEG_NV16 = 1, Z8_JPEG_THUMB = 2 };
typedef struct Z8JpegRole {
    bool used, committed;
    char signature[65];
    uint64_t counts, committed_count;
    unsigned claim; /* bank+1, zero means no accepted commit request */
} Z8JpegRole;
typedef struct Z8JpegSource {
    bool reserved, published, live;
    unsigned kind, index, source_kind, readers;
    uint64_t generation, producer_job, parent_generation, parent_151_job;
    uint64_t frame, decoder_job, isp_record_job, captured_ns;
    uint64_t y, uv;
    unsigned width, height, stride;
    char y_sha[65], uv_sha[65], parent_y_sha[65], parent_uv_sha[65];
    Z8JpegRole roles[Z8_JPEG_ROLES];
} Z8JpegSource;
typedef struct Z8JpegSources {
    Z8JpegSource entries[Z8_JPEG_SOURCES];
    uint64_t serial, last151, nv16_tokens[3], thumb_token;
    const char *failure;
} Z8JpegSources;
typedef struct Z8JpegRequest {
    uint32_t words[0x500/4];
    uint64_t generation, token, frame, parent_151_job, decoder_job, parent_generation;
    unsigned source_kind, producer_kind, producer_index;
    unsigned role;
    bool snapshot, lease, count_only, no_limit;
    const char *failure;
} Z8JpegRequest;
#endif
