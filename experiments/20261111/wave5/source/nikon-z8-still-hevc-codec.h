/* SPDX-License-Identifier: GPL-2.0-or-later
 * Host-only single-picture encoder. No guest address space or IRQ access.
 * Explicitly authorized IDR_N_LP -> IDR_W_RADL header transformation.
 */
#ifndef NIKON_Z8_STILL_HEVC_CODEC_H
#define NIKON_Z8_STILL_HEVC_CODEC_H
#include <x265.h>
#include "nikon-z8-hevc-sps.h"
typedef struct Z8HEVCEncoded {
    uint8_t *parameter[3],*image;
    size_t parameter_bytes[3],image_bytes;
    unsigned vcl;
    ProbeSPS sps;
} Z8HEVCEncoded;
static void z8_hevc_encoded_free(Z8HEVCEncoded *e)
{
    for(unsigned i=0;i<3;i++) { g_free(e->parameter[i]); }
    g_free(e->image);memset(e,0,sizeof(*e));
}
static void z8_hevc_hash(const uint8_t *data,size_t bytes,char digest[65])
{
    char *value=g_compute_checksum_for_data(G_CHECKSUM_SHA256,data,bytes);
    g_strlcpy(digest,value,65);g_free(value);
}
static bool z8_hevc_collect(Z8HEVCEncoded *out,const x265_nal *nals,unsigned count)
{
    if(count>8 || (count && !nals)) { return false; }
    for(unsigned i=0;i<count;i++) {
        const x265_nal *n=&nals[i];unsigned prefix;
        if(!n->payload || n->sizeBytes>(16u<<20) || !(prefix=z8_hevc_prefix(n->payload,n->sizeBytes))) { return false; }
        unsigned type=(n->payload[prefix]>>1)&63;
        if(n->type!=type || n->payload[prefix]!=(type<<1) || n->payload[prefix+1]!=1) { return false; }
        if(type==20) {
            if(++out->vcl!=1) { return false; }
            out->image=g_try_malloc(n->sizeBytes);
            if(!out->image) { return false; }
            memcpy(out->image,n->payload,n->sizeBytes);out->image_bytes=n->sizeBytes;
        } else if(type>=32 && type<=34) {
            unsigned p=type-32;size_t bytes=n->sizeBytes-prefix+4;
            if(out->parameter[p] || bytes>65536) { return false; }
            out->parameter[p]=g_try_malloc(bytes);
            if(!out->parameter[p]) { return false; }
            memcpy(out->parameter[p],"\0\0\0\1",4);
            memcpy(out->parameter[p]+4,n->payload+prefix,n->sizeBytes-prefix);out->parameter_bytes[p]=bytes;
        } else { return false; } /* Includes second VCL, type19, AUD/SEI: no silent filtering. */
    }
    return true;
}
static const char *z8_hevc_encode_image(const x265_api *api,uint16_t *pixels,unsigned width,unsigned height,unsigned total_frames,
                                      uint32_t vui_control,uint32_t vui_colour,
                                      Z8HEVCEncoded *out,Z8HEVCTileReceipt *receipt)
{
    x265_param *p=api->param_alloc();x265_picture *pic=api->picture_alloc();
    x265_encoder *enc=NULL;const char *error="codec-allocation";
    if(!p || !pic) { goto done; }
    const char *range=z8_hevc_vui_range(vui_control,vui_colour);
    error="codec-unobserved-vui";if(!range) { goto done; }
    error="codec-unobserved-geometry";
    if(!((width==768 && height==2752) || (width==1620 && height==1080) || (width==320 && height==212))) { goto done; }
    error="codec-parameters";
    if(api->param_default_preset(p,"ultrafast",NULL)<0) { goto done; }
    p->sourceWidth=width;p->sourceHeight=height;p->internalCsp=X265_CSP_I420;p->internalBitDepth=10;
    p->fpsNum=1;p->fpsDenom=1;p->totalFrames=total_frames;p->frameNumThreads=1;
    p->bframes=0;p->lookaheadDepth=0;p->keyframeMax=16;p->keyframeMin=1;
    p->bOpenGOP=0;p->scenecutThreshold=0;p->bRepeatHeaders=0;p->bAnnexB=1;
    p->maxSlices=1;p->bEnableWavefront=0;p->bEnablePsnr=0;p->bEnableSsim=0;
    p->rc.rateControlMode=X265_RC_CQP;p->rc.qp=12;p->logLevel=X265_LOG_ERROR;
    if(api->param_parse(p,"pools","none")<0 || api->param_parse(p,"info","0")<0 ||
       api->param_parse(p,"aud","0")<0 || api->param_parse(p,"colorprim","bt2020")<0 ||
       api->param_parse(p,"transfer","arib-std-b67")<0 || api->param_parse(p,"colormatrix","bt2020nc")<0 ||
       api->param_parse(p,"range",range)<0 || api->param_apply_profile(p,"main10")<0) { goto done; }
    error="codec-open";enc=api->encoder_open(p);if(!enc) { goto done; }
    api->picture_init(p,pic);pic->bitDepth=10;pic->colorSpace=X265_CSP_I420;pic->sliceType=X265_TYPE_IDR;pic->pts=0;
    size_t plane=(size_t)width*height;
    pic->planes[0]=pixels;pic->planes[1]=pixels+plane;pic->planes[2]=pixels+plane+plane/4;
    pic->stride[0]=width*2;pic->stride[1]=pic->stride[2]=width;
    x265_nal *nals=NULL;uint32_t count=0;int frames=0,r;
    error="codec-headers-or-nal-type";
    if(api->encoder_headers(enc,&nals,&count)<0 || !z8_hevc_collect(out,nals,count)) { goto done; }
    error="codec-picture-or-nal-type";
    r=api->encoder_encode(enc,&nals,&count,pic,NULL);
    if(r<0 || !z8_hevc_collect(out,nals,count)) { goto done; } frames+=r;
    bool drained=false;
    for(unsigned flush=0;flush<16;flush++) {
        r=api->encoder_encode(enc,&nals,&count,NULL,NULL);
        if(r<0 || !z8_hevc_collect(out,nals,count)) { goto done; }
        frames+=r;if(!r) { drained=true;break; }
    }
    error="codec-not-single-idr";
    if(!drained || frames!=1 || out->vcl!=1 || !out->image ||
       !out->parameter[0] || !out->parameter[1] || !out->parameter[2]) { goto done; }
    error="codec-sps-profile";
    if(!probe_sps(out->parameter[1],out->parameter_bytes[1],&out->sps) ||
       out->sps.profile_space || out->sps.tier || (out->sps.profile_idc!=2 && out->sps.profile_idc!=4) ||
       out->sps.chroma!=1 || out->sps.luma_depth!=10 || out->sps.chroma_depth!=10 ||
       out->sps.visible_width!=width || out->sps.visible_height!=height) { goto done; }
    receipt->raw_bytes=out->image_bytes;receipt->raw_prefix=z8_hevc_prefix(out->image,out->image_bytes);
    z8_hevc_hash(out->image,out->image_bytes,receipt->before_sha256);
    error="idr-header-transform-precondition";
    if(!z8_hevc_transform_single(out->image,out->image_bytes,out->vcl)) { goto done; }
    z8_hevc_hash(out->image,out->image_bytes,receipt->after_sha256);
    /* Prefix normalization is a separate, recorded operation. The declared
     * transform above changed exactly one header byte in the raw x265 NAL. */
    size_t bytes=out->image_bytes-receipt->raw_prefix+4;
    uint8_t *canonical=g_try_malloc(bytes);
    error="codec-allocation";if(!canonical) { goto done; }
    memcpy(canonical,"\0\0\0\1",4);
    memcpy(canonical+4,out->image+receipt->raw_prefix,out->image_bytes-receipt->raw_prefix);
    g_free(out->image);out->image=canonical;out->image_bytes=bytes;receipt->bytes=bytes;
    z8_hevc_hash(canonical,bytes,receipt->dma_nal_sha256);error=NULL;
done:
    if(enc) { api->encoder_close(enc); }
    if(pic) { api->picture_free(pic); }
    if(p) { api->param_free(p); }
    if(error) { z8_hevc_encoded_free(out); }
    return error;
}
static const char *z8_hevc_encode_tile(const x265_api *api,uint16_t *pixels,unsigned total_frames,
                                      uint32_t vui_control,uint32_t vui_colour,
                                      Z8HEVCEncoded *out,Z8HEVCTileReceipt *receipt)
{ return z8_hevc_encode_image(api,pixels,768,2752,total_frames,vui_control,vui_colour,out,receipt); }
#endif
