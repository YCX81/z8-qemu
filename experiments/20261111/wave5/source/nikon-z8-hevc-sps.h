/* SPDX-License-Identifier: GPL-2.0-or-later
 * Independent, bounded Annex-B/SPS prefix reader for the still encoder and standalone probe.
 * Parses PTL and geometry/depth only, not a full conformance validator.
 */
#ifndef NIKON_Z8_HEVC_SPS_H
#define NIKON_Z8_HEVC_SPS_H
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
typedef struct ProbeBits { const uint8_t *data; size_t bits,pos; bool bad; } ProbeBits;
typedef struct ProbeSPS {
    unsigned profile_space,tier,profile_idc,level_idc,max_sub_layers;
    uint32_t compatibility;
    uint64_t constraints;
    unsigned chroma,luma_depth,chroma_depth,width,height,visible_width,visible_height;
} ProbeSPS;
static inline uint32_t probe_bits(ProbeBits *b,unsigned n)
{
    uint32_t v=0;
    if (n>32 || b->pos>b->bits || n>b->bits-b->pos) { b->bad=true;return 0; }
    for (unsigned i=0;i<n;i++,b->pos++) { v=v*2+((b->data[b->pos/8]>>(7-b->pos%8))&1); }
    return v;
}
static inline uint32_t probe_ue(ProbeBits *b)
{
    unsigned n=0;
    while (!b->bad && !probe_bits(b,1)) {
        if (++n>30) { b->bad=true;return 0; }
    }
    if (b->bad) { return 0; }
    return ((UINT32_C(1)<<n)-1)+probe_bits(b,n);
}
static inline bool probe_nal_type(const uint8_t *p,size_t n,unsigned *type,unsigned *prefix)
{
    unsigned s;
    if (n>=6 && !p[0] && !p[1] && !p[2] && p[3]==1) { s=4; }
    else if (n>=5 && !p[0] && !p[1] && p[2]==1) { s=3; }
    else { return false; }
    if ((p[s]&0x80) || !(p[s+1]&7)) { return false; }
    *prefix=s;*type=(p[s]>>1)&63;
    return true;
}
static inline bool probe_sps(const uint8_t *nal,size_t n,ProbeSPS *s)
{
    unsigned type,prefix,used=0,zeros=0;
    uint8_t *rbsp;
    bool profile[7]={0},level[7]={0};
    uint32_t left=0,right=0,top=0,bottom=0,separate=0;
    if (!s || n>65536 || !probe_nal_type(nal,n,&type,&prefix) || type!=33) { return false; }
    rbsp=malloc(n);
    if (!rbsp) { return false; }
    for (size_t i=prefix+2;i<n;i++) {
        if (zeros==2 && nal[i]==3) {
            if (i+1>=n || nal[i+1]>3) { free(rbsp);return false; }
            zeros=0;continue;
        }
        rbsp[used++]=nal[i];zeros=nal[i]==0?zeros+1:0;
    }
    ProbeBits b={rbsp,(size_t)used*8,0,false};
    memset(s,0,sizeof(*s));
    probe_bits(&b,4);s->max_sub_layers=probe_bits(&b,3);probe_bits(&b,1);
    if (s->max_sub_layers>6) { free(rbsp);return false; }
    s->profile_space=probe_bits(&b,2);s->tier=probe_bits(&b,1);s->profile_idc=probe_bits(&b,5);
    s->compatibility=probe_bits(&b,32);
    s->constraints=(uint64_t)probe_bits(&b,16)<<32;s->constraints|=probe_bits(&b,32);
    s->level_idc=probe_bits(&b,8);
    for (unsigned i=0;i<s->max_sub_layers;i++) { profile[i]=probe_bits(&b,1);level[i]=probe_bits(&b,1); }
    if (s->max_sub_layers) {
        for (unsigned i=s->max_sub_layers;i<8;i++) { if (probe_bits(&b,2)) { b.bad=true; } }
    }
    for (unsigned i=0;i<s->max_sub_layers;i++) {
        if (profile[i]) { probe_bits(&b,32);probe_bits(&b,32);probe_bits(&b,24); }
        if (level[i]) { probe_bits(&b,8); }
    }
    if (probe_ue(&b)>15) { b.bad=true; }
    s->chroma=probe_ue(&b);
    if (s->chroma==3) { separate=probe_bits(&b,1); }
    s->width=probe_ue(&b);s->height=probe_ue(&b);
    if (probe_bits(&b,1)) { left=probe_ue(&b);right=probe_ue(&b);top=probe_ue(&b);bottom=probe_ue(&b); }
    s->luma_depth=8+probe_ue(&b);s->chroma_depth=8+probe_ue(&b);
    unsigned sx=!separate && (s->chroma==1 || s->chroma==2)?2:1;
    unsigned sy=!separate && s->chroma==1?2:1;
    uint64_t cropx=((uint64_t)left+right)*sx,cropy=((uint64_t)top+bottom)*sy;
    bool ok=!b.bad && s->chroma<=3 && s->width && s->width<=16384 && s->height && s->height<=16384 &&
        cropx<s->width && cropy<s->height && s->luma_depth<=16 && s->chroma_depth<=16;
    if (ok) { s->visible_width=s->width-cropx;s->visible_height=s->height-cropy; }
    free(rbsp);return ok;
}
#endif
