/* SPDX-License-Identifier: GPL-2.0-or-later
 * Standalone libx265 experiment. Synthetic I420 only, no QEMU or Nikon RAM.
 * Build: cc -std=c11 -O2 -Wall -Wextra -Werror tests/x265_still_probe.c
 *        $(pkg-config --cflags --libs x265) -o x265-still-probe
 * Args: WIDTH HEIGHT KEYINT BFRAMES PROFILE NEW_OUTPUT.h265 [TOTAL_FRAMES=1]
 */
#define _POSIX_C_SOURCE 200809L
#include <x265.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "hevc_probe_parse.h"

typedef struct ProbeOutput {
    FILE *file;
    unsigned count,vcl,type19,type20,vps,sps,pps;
    uint64_t bytes;
    bool types_match,parsed;
    ProbeSPS info;
} ProbeOutput;

static void string(const char *s)
{
    if (!s) { printf("null");return; }
    putchar('"');
    for (const unsigned char *p=(const unsigned char *)s;*p;p++) {
        if (*p=='"' || *p=='\\') { putchar('\\');putchar(*p); }
        else if (*p<32) { printf("\\u%04x",*p); }
        else { putchar(*p); }
    }
    putchar('"');
}
static double seconds(void)
{
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC,&t)) { return 0; }
    return t.tv_sec+t.tv_nsec/1e9;
}
static bool number(const char *s,unsigned *value,unsigned low,unsigned high)
{
    char *end;unsigned long v=strtoul(s,&end,10);
    if (!*s || *end || v<low || v>high) { return false; }
    *value=v;return true;
}
static bool nals(ProbeOutput *o,x265_nal *ns,unsigned count)
{
    if (count>64-o->count || (count && !ns)) { return false; }
    for (unsigned i=0;i<count;i++) {
        unsigned type=0,prefix=0;
        bool valid=ns[i].payload && probe_nal_type(ns[i].payload,ns[i].sizeBytes,&type,&prefix);
        bool same=valid && type==ns[i].type;
        if (ns[i].sizeBytes>64*1024*1024-o->bytes) { return false; }
        if (o->count++) { putchar(','); }
        printf("{\"api_type\":%u,\"header_type\":",ns[i].type);
        if (valid) { printf("%u",type); } else { printf("null"); }
        printf(",\"header_valid\":%s,\"types_match\":%s,\"prefix_bytes\":%u,\"bytes\":%u,\"file_offset\":%" PRIu64 "}",
            valid?"true":"false",same?"true":"false",prefix,ns[i].sizeBytes,o->bytes);
        o->types_match &= same;
        if (!valid) { return false; }
        if (type<32) { o->vcl++;o->type19+=type==19;o->type20+=type==20; }
        o->vps+=type==32;o->sps+=type==33;o->pps+=type==34;
        if (type==33) { o->parsed=probe_sps(ns[i].payload,ns[i].sizeBytes,&o->info); }
        if (fwrite(ns[i].payload,1,ns[i].sizeBytes,o->file)!=ns[i].sizeBytes) { return false; }
        o->bytes+=ns[i].sizeBytes;
    }
    return true;
}

int main(int argc,char **argv)
{
    unsigned width,height,keyint,bframes,total_frames=1;
    if ((argc!=7 && argc!=8) || (argc==8 && !number(argv[7],&total_frames,0,1)) || !number(argv[1],&width,320,8256) || !number(argv[2],&height,128,5504) ||
        ((width|height)&1) || !number(argv[3],&keyint,1,64) || !number(argv[4],&bframes,0,4) ||
        (strcmp(argv[5],"main10") && strcmp(argv[5],"main10-intra"))) {
        fprintf(stderr,"usage: %s WIDTH HEIGHT KEYINT BFRAMES {main10|main10-intra} NEW_OUTPUT.h265 [TOTAL_FRAMES=1]\n",argv[0]);return 2;
    }
    const x265_api *api=x265_api_get(10);
    x265_param *p=NULL,*actual=NULL;
    x265_picture *pic=NULL;
    x265_encoder *enc=NULL;
    uint16_t *pixels=NULL;
    ProbeOutput out={.types_match=true};
    const char *status="api10_unavailable";
    double start=seconds();
    unsigned frames=0,flushes=0;
    bool drained=false;
    printf("{\"width\":%u,\"height\":%u,\"requested\":{\"keyint\":%u,\"bframes\":%u,\"profile\":",
        width,height,keyint,bframes);string(argv[5]);
    printf(",\"total_frames\":%u,\"force_idr\":true,\"open_gop\":false,\"qp\":12},\"header_build\":%d,\"api\":",total_frames,X265_BUILD);
    if (api) {
        printf("{\"build\":%d,\"major\":%d,\"depth\":%d,\"version\":",api->api_build_number,api->api_major_version,api->bit_depth);
        string(api->version_str);printf(",\"build_info\":");string(api->build_info_str);putchar('}');
    } else { printf("null"); }
    printf(",\"nals\":[");
    if (!api) { goto done; }
    status="api_abi_mismatch";
    if (api->bit_depth!=10 || (size_t)api->sizeof_param!=sizeof(x265_param) || (size_t)api->sizeof_picture!=sizeof(x265_picture) ||
        api->api_build_number!=X265_BUILD) { goto done; }
    status="allocation_failed";
    p=api->param_alloc();actual=api->param_alloc();pic=api->picture_alloc();
    if (!p || !actual || !pic) { goto done; }
    status="parameter_rejected";
    if (api->param_default_preset(p,"ultrafast",NULL)<0) { goto done; }
    p->sourceWidth=width;p->sourceHeight=height;p->internalCsp=X265_CSP_I420;p->internalBitDepth=10;
    p->fpsNum=1;p->fpsDenom=1;p->totalFrames=total_frames;p->frameNumThreads=1;
    p->bframes=bframes;p->lookaheadDepth=bframes?4:0;p->bFrameAdaptive=0;
    p->keyframeMax=keyint;p->keyframeMin=1;p->bOpenGOP=0;p->scenecutThreshold=0;
    p->bRepeatHeaders=0;p->bAnnexB=1;p->bEnableWavefront=0;p->maxSlices=1;
    p->bEnablePsnr=0;p->bEnableSsim=0;p->logLevel=X265_LOG_ERROR;
    p->rc.rateControlMode=X265_RC_CQP;p->rc.qp=12;
    if (api->param_parse(p,"pools","none")<0 || api->param_parse(p,"info","0")<0 ||
        api->param_parse(p,"aud","0")<0 || api->param_apply_profile(p,argv[5])<0) { goto done; }
    status="encoder_open_failed";
    enc=api->encoder_open(p);
    if (!enc) { goto done; }
    api->encoder_parameters(enc,actual);
    status="allocation_failed";
    size_t plane=(size_t)width*height;
    pixels=malloc(plane*3); /* 10-bit I420 in three uint16_t planes */
    if (!pixels) { goto done; }
    for (unsigned y=0;y<height;y++) {
        for (unsigned x=0;x<width;x++) { pixels[(size_t)y*width+x]=(x*3+y*5+(x/17)*11)&1023; }
    }
    for (unsigned y=0;y<height/2;y++) {
        for (unsigned x=0;x<width/2;x++) {
            pixels[plane+(size_t)y*(width/2)+x]=(129+x*7+y*3)&1023;
            pixels[plane+plane/4+(size_t)y*(width/2)+x]=(811+x*2+y*13)&1023;
        }
    }
    api->picture_init(p,pic);pic->bitDepth=10;pic->colorSpace=X265_CSP_I420;pic->sliceType=X265_TYPE_IDR;
    pic->planes[0]=pixels;pic->planes[1]=pixels+plane;pic->planes[2]=pixels+plane+plane/4;
    pic->stride[0]=width*2;pic->stride[1]=pic->stride[2]=width;pic->pts=0;
    status="output_open_failed";
    out.file=fopen(argv[6],"wbx");
    if (!out.file) { goto done; }
    x265_nal *ns=NULL;uint32_t count=0;
    status="headers_failed";
    if (api->encoder_headers(enc,&ns,&count)<0 || !nals(&out,ns,count)) { goto done; }
    status="encode_failed";
    int result=api->encoder_encode(enc,&ns,&count,pic,NULL);
    if (result<0 || !nals(&out,ns,count)) { goto done; }
    frames+=result;
    while (flushes<16) {
        flushes++;
        result=api->encoder_encode(enc,&ns,&count,NULL,NULL);
        if (result<0 || !nals(&out,ns,count)) { goto done; }
        frames+=result;
        if (!result) { drained=true;break; }
    }
    status=frames==1 && drained?"encoded":"flush_or_frame_count_failed";
done:
    if (out.file && fclose(out.file)) { status="output_close_failed"; }
    printf("],\"actual\":");
    if (enc && actual) {
        printf("{\"keyint\":%d,\"bframes\":%d,\"lookahead\":%d,\"total_frames\":%d,\"bit_depth\":%d,\"chroma\":%d}",
            actual->keyframeMax,actual->bframes,actual->lookaheadDepth,actual->totalFrames,actual->internalBitDepth,actual->internalCsp);
    } else { printf("null"); }
    printf(",\"sps\":");
    ProbeSPS *s=&out.info;
    if (out.parsed) {
        printf("{\"profile_space\":%u,\"tier\":%u,\"profile_idc\":%u,\"compatibility\":%u,\"constraint_flags_hex\":\"%012" PRIx64
            "\",\"level_idc\":%u,\"max_sub_layers_minus1\":%u,\"chroma\":%u,\"luma_depth\":%u,\"chroma_depth\":%u,"
            "\"width\":%u,\"height\":%u,\"visible_width\":%u,\"visible_height\":%u}",
            s->profile_space,s->tier,s->profile_idc,s->compatibility,s->constraints,s->level_idc,s->max_sub_layers,
            s->chroma,s->luma_depth,s->chroma_depth,s->width,s->height,s->visible_width,s->visible_height);
    } else { printf("null"); }
    bool compatible=!strcmp(status,"encoded") && out.parsed && out.vcl==1 && out.type19==1 && out.types_match &&
        out.vps==1 && out.sps==1 && out.pps==1 && s->luma_depth==10 && s->chroma_depth==10 && s->chroma==1 &&
        s->visible_width==width && s->visible_height==height;
    bool transform=!strcmp(status,"encoded") && out.parsed && out.vcl==1 && out.type20==1 && out.types_match &&
        out.vps==1 && out.sps==1 && out.pps==1 && s->luma_depth==10 && s->chroma_depth==10 && s->chroma==1 &&
        s->visible_width==width && s->visible_height==height;
    printf(",\"transform20_candidate\":%s,\"status\":",transform?"true":"false");string(status);
    printf(",\"frames\":%u,\"flush_calls\":%u,\"bytes\":%" PRIu64 ",\"elapsed_seconds\":%.6f,\"type19_compatible\":%s,"
        "\"synthetic_input\":true,\"firmware_executed\":false}\n",
        frames,flushes,out.bytes,seconds()-start,compatible?"true":"false");
    if (enc) { api->encoder_close(enc); }
    if (p) { api->param_free(p); }
    if (actual) { api->param_free(actual); }
    if (pic) { api->picture_free(pic); }
    free(pixels);
    return strcmp(status,"encoded")?1:0; /* type20 is an observation, never a type19 success. */
}
