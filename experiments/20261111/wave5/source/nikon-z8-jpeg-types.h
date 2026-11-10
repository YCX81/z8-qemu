/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef NIKON_Z8_JPEG_TYPES_H
#define NIKON_Z8_JPEG_TYPES_H
#define Z8_JPEG_SOURCES 16
#define Z8_JPEG_ROLES 8
/* Separate namespaces: source kinds below, JPEG input owner4 for HLG,
 * and fixed NBOwner output slots0/1/5/6/7. No numerical alias across kinds. */
/* Original callback40eb77d4 bounds the I5 video rate-control passes. */
#define Z8_JPEG_VIDEO_RATE_PASSES 30
enum { Z8_JPEG_151 = 0, Z8_JPEG_NV16 = 1, Z8_JPEG_THUMB = 2,
       Z8_JPEG_VIDEO_RESIZE = 3, Z8_JPEG_VIDEO_THUMB = 4, Z8_JPEG_HLG_THUMB = 5 };
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
    /* Video-only re-encode receipt. Not a photo ownership namespace. */
    char video_commit_signature[65];
    uint64_t video_commit_token, video_commits;
    uint64_t video_passes;
    unsigned video_commit_bank;
    bool video_commit_retired;
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
