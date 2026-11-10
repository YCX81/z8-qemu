/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef NIKON_Z8_HLG_PIPELINE_PIXELS_H
#define NIKON_Z8_HLG_PIPELINE_PIXELS_H
#include "nikon-z8-hlg-pixels.h"
#include "nikon-z8-hlg-pipeline-profile.h"

/* Replace these conventions together with their independent byte fixtures.
 * No native pixel dump has verified UV order, quantization or filter phase. */
#define Z8_HLG_C1_UV_U_FIRST_HYPOTHESIS 1
#define Z8_HLG_C1_ROUND12_TO10_HYPOTHESIS 1
#define Z8_HLG_C1_CHROMA_TWO_ROWS_HYPOTHESIS 1

typedef struct Z8HLGPipeline {
    bool enabled, requested, failed, owned;
    uint64_t requests, jobs, rejects, cancels, retired, source_job, source_frame, consumed151_job;
    uint64_t input[2], target[3][2], bytes[3][2];
    uint32_t source[0x220/4], a[0x100/4], c[0x1000/4];
    uint32_t output[3][0x100/4], geometry[3][0x100/4];
    uint64_t lut[8][128];
    char input_sha256[2][65], output_sha256[3][2][65], lut_sha256[65];
    const char *last_error;
} Z8HLGPipeline;

static inline bool z8_hlg_c1_source_dynamic(unsigned off)
{
    return off==8 || off==0x30 || (off>=0x100 && off<=0x10c);
}
static inline bool z8_hlg_c1_output_address(unsigned off)
{ return off==0x20 || off==0x24 || off==0x28 || off==0x2c; }

/* The only admitted geometry is the native run03 tuple. DMA addresses and
 * IRQ masks are validated separately; a mask never changes the pixel format.
 * C1 mode103 is the observed pre-START value; 101 is its latched active state.
 * No admission of the video/ring tuple, bank switching or ring completion. */
static inline bool z8_hlg_c1_profile(const uint32_t *src, const uint32_t *a,
    const uint32_t *c, const uint32_t b[3][0x100/4], const uint32_t f[3][0x100/4])
{
    if (src[0x30/4]>1 || (src[8/4]&~0x39f00000u) ||
        (a[0x18/4]&~0x11111u) || (c[0x10/4]&~0x20u) ||
        (c[1]!=0x103 && c[1]!=0x101)) { return false; }
    for(unsigned i=0;i<0x220/4;i++) {
        if(!z8_hlg_c1_source_dynamic(i*4) && src[i]!=z8_hlg_c1_source[i]) { return false; }
    }
    for(unsigned i=0;i<0x100/4;i++) {
        if(i!=0x18/4 && a[i]!=z8_hlg_c1_a1[i]) { return false; }
        for(unsigned j=0;j<3;j++) {
            if((!z8_hlg_c1_output_address(i*4) && b[j][i]!=z8_hlg_c1_output[j][i]) ||
                f[j][i]!=z8_hlg_c1_geometry[j][i]) { return false; }
        }
    }
    for(unsigned i=0;i<0x1000/4;i++) {
        if(i!=1 && i!=4 && c[i]!=z8_hlg_c1_control[i]) { return false; }
    }
    return true;
}

static inline uint64_t z8_hlg_c1_coordinate(const uint32_t *f, unsigned at, bool y)
{
    uint64_t phase=f[(y?0xc:8)/4], scale=f[(y?0x14:0x10)/4];
    return ((phase+(uint64_t)at*scale)<<f[0x20/4])>>14;
}

/* Pure bounded sampling kernel. Caller provides complete immutable source
 * planes and zeroed destination spans; no IRQ or MMIO here. The small-shape
 * unit fixtures test this kernel, not admission of unobserved device modes. */
static inline bool z8_hlg_c1_scale(const uint8_t *iy, const uint8_t *iu,
    unsigned sw, unsigned sh, unsigned sp, const uint32_t *f, unsigned dp,
    uint8_t *oy, uint8_t *ou)
{
    unsigned w=f[0x2c/4], h=f[0x30/4];
    if(!iy || !iu || !oy || !ou || !sw || !sh || !w || !h ||
        ((sw|sh|w|h)&1) || sw>16384 || sh>8192 || w>16384 || h>8192 ||
        sp<sw*3/2 || dp<((w+2)/3)*4 ||
        f[0x18/4]!=sw || f[0x1c/4]!=sh || !f[0x10/4] || !f[0x14/4] ||
        f[0x20/4]>7 || f[8/4]>0x1fffffff || f[0xc/4]>0x1fffffff ||
        f[0x10/4]>65535 || f[0x14/4]>65535 ||
        z8_hlg_c1_coordinate(f,w-1,false)>=sw || z8_hlg_c1_coordinate(f,h-1,true)>=sh) { return false; }
    for(unsigned y=0;y<h;y++) {
        unsigned sy=(unsigned)z8_hlg_c1_coordinate(f,y,true);
        for(unsigned x=0;x<w;x++) {
            unsigned sx=(unsigned)z8_hlg_c1_coordinate(f,x,false);
            unsigned v=(z8_hlg_load(iy+(uint64_t)sy*sp,sx,12)+(Z8_HLG_C1_ROUND12_TO10_HYPOTHESIS?2:0))>>2;
            z8_hlg_store(oy+(uint64_t)y*dp,x,10,v>1023?1023:v);
        }
    }
    for(unsigned y=0;y<h/2;y++) {
        unsigned sy0=(unsigned)z8_hlg_c1_coordinate(f,2*y,true);
        unsigned sy1=(unsigned)z8_hlg_c1_coordinate(f,2*y+1,true);
        for(unsigned x=0;x<w;x+=2) {
            unsigned sx=(unsigned)z8_hlg_c1_coordinate(f,x,false)&~1u;
            for(unsigned k=0;k<2;k++) {
                unsigned component=Z8_HLG_C1_UV_U_FIRST_HYPOTHESIS?k:1-k;
                unsigned v0=z8_hlg_load(iu+(uint64_t)sy0*sp,sx+component,12);
                unsigned v1=z8_hlg_load(iu+(uint64_t)sy1*sp,sx+component,12);
                unsigned v=Z8_HLG_C1_CHROMA_TWO_ROWS_HYPOTHESIS ?
                    (v0+v1+(Z8_HLG_C1_ROUND12_TO10_HYPOTHESIS?4:0))>>3 :
                    (v0+(Z8_HLG_C1_ROUND12_TO10_HYPOTHESIS?2:0))>>2;
                z8_hlg_store(ou+(uint64_t)y*dp,x+k,10,v>1023?1023:v);
            }
        }
    }
    return true;
}
#endif
