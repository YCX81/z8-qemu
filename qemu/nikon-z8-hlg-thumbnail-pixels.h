/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef NIKON_Z8_HLG_THUMBNAIL_PIXELS_H
#define NIKON_Z8_HLG_THUMBNAIL_PIXELS_H
#include "nikon-z8-hlg-pixels.h"
#include <string.h>
/* Native run04 snapshot af25d2b9788f67b39d13fed92c83601253e4e2e2186f856b71a51270e1ed623f.
 * Scalars only. All source and destination RAM addresses are parameters. */
static const uint32_t z8_hlg_thumb_a[64]={
    [4/4]=0x1002,[8/4]=0x10000000,[0xc/4]=0x31000200,[0x10/4]=1,[0x18/4]=0x11111,
    [0x38/4]=448,[0x3c/4]=448,[0x40/4]=0x0f000022,[0x48/4]=0x11,[0x4c/4]=320,[0x50/4]=212};
static const uint32_t z8_hlg_thumb_b[64]={
    [0xc/4]=0x41000010,[0x10/4]=1,[0x38/4]=192,[0x3c/4]=192,[0x40/4]=0x22,[0x48/4]=0x11};
static const uint32_t z8_hlg_thumb_f[64]={
    [4/4]=1,[0x10/4]=16384,[0x14/4]=16384,[0x18/4]=320,[0x1c/4]=212,
    [0x20/4]=1,[0x2c/4]=160,[0x30/4]=106};
/* Explicit replaceable numerical conventions, not recovered Nikon hardware. */
#define Z8_HLG_THUMB_ROUND_BIAS_HYPOTHESIS 2u
#define Z8_HLG_THUMB_UV_U_FIRST_HYPOTHESIS 1
typedef struct Z8HLGThumbnail {
    bool enabled, failed, valid;
    uint64_t requests, jobs, rejects, source_job, source_frame, consumed_job, jpeg_commits;
    uint64_t source[2], target[2];
    uint32_t a[64],b[64],f[64];
    char input_sha256[2][65], output_sha256[2][65];
    const char *last_error;
} Z8HLGThumbnail;
static inline bool z8_hlg_thumb_address(unsigned off)
{ return off==0x20 || off==0x24 || off==0x28 || off==0x2c; }
typedef struct Z8HLGThumbMismatch {
    unsigned bank, offset;
    uint32_t actual, expected;
} Z8HLGThumbMismatch;
static inline bool z8_hlg_thumb_profile_check(const uint32_t *a,const uint32_t *b,const uint32_t *f,
                                             Z8HLGThumbMismatch *mismatch)
{
    const uint32_t *actual[3]={a,b,f};
    const uint32_t *expected[3]={z8_hlg_thumb_a,z8_hlg_thumb_b,z8_hlg_thumb_f};
    for(unsigned i=0;i<64;i++) {
        for(unsigned bank=0;bank<3;bank++) {
            if(bank<2 && z8_hlg_thumb_address(i*4)) { continue; }
            if(actual[bank][i]!=expected[bank][i]) {
                if(mismatch) { *mismatch=(Z8HLGThumbMismatch){bank,i*4,actual[bank][i],expected[bank][i]}; }
                return false;
            }
        }
    }
    return true;
}
static inline bool z8_hlg_thumb_profile(const uint32_t *a,const uint32_t *b,const uint32_t *f)
{ return z8_hlg_thumb_profile_check(a,b,f,NULL); }
static inline uint8_t z8_hlg_thumb_quantize(unsigned value)
{
    value=(value+Z8_HLG_THUMB_ROUND_BIAS_HYPOTHESIS)>>2;
    return value>255?255:value;
}
static inline void z8_hlg_thumb_convert(const uint8_t *iy,const uint8_t *iu,uint8_t *oy,uint8_t *ou)
{
    memset(oy,0,192*106);memset(ou,128,192*106);
    /* run04: phase=0, scale=16384, shift=1 -> source coordinate=2*at.
     * Source UV is half height. Selecting floor(source_y/2) gives row=at;
     * the output is full-height NV16. No additional OETF/tone mapping. */
    for(unsigned y=0;y<106;y++) for(unsigned x=0;x<160;x++) {
        oy[y*192+x]=z8_hlg_thumb_quantize(z8_hlg_load(iy+y*2*448,x*2,10));
        unsigned sx=(x&~1u)*2+(Z8_HLG_THUMB_UV_U_FIRST_HYPOTHESIS?(x&1):1-(x&1));
        unsigned sy=(y*2)/2;
        ou[y*192+x]=z8_hlg_thumb_quantize(z8_hlg_load(iu+sy*448,sx,10));
    }
}
#endif
