/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef NIKON_Z8_STILL_HEVC_PIXELS_H
#define NIKON_Z8_STILL_HEVC_PIXELS_H
#include <stddef.h>
#include <string.h>
#include "nikon-z8-hlg-pipeline-pixels.h"

/* Declared model rule: extend the last visible luma/chroma sample to the
 * right tile edge. Two wholly invisible transport slots reuse that row's
 * last real encoded tile. No extra image NAL is returned by any encoder. */
#define Z8_HEVC_PADDING_EDGE_REPEAT 1
/* Native C2b=1 -> 400.bit15; C38/39/3a=9/18/9. Full tuple only. */
static inline const char *z8_hevc_vui_range(uint32_t control,uint32_t colour)
{ return control==0x1d800 && colour==0x91209 ? "full" : NULL; }
static inline unsigned z8_hevc_slot(unsigned tile) { return (tile/11)*12+tile%11; }
static inline unsigned z8_hevc_slot_source(unsigned slot)
{ return (slot/12)*11+(slot%12<11?slot%12:10); }
static inline uint32_t z8_hevc_le32(const uint8_t *p)
{ return (uint32_t)p[0]|(uint32_t)p[1]<<8|(uint32_t)p[2]<<16|(uint32_t)p[3]<<24; }
static inline void z8_hevc_put32(uint8_t *p,uint32_t v)
{ for(unsigned i=0;i<4;i++)p[i]=(uint8_t)(v>>(8*i)); }

static inline bool z8_hevc_tile(const uint8_t *y,const uint8_t *uv,unsigned w,unsigned h,unsigned stride,
    unsigned tw,unsigned th,unsigned col,unsigned row,uint16_t *out,size_t samples)
{
    if(!y || !uv || !out || !w || !h || !tw || !th || ((w|h|tw|th)&1) ||
       w>16384 || h>8192 || tw>4096 || th>4096 || stride<((w+2)/3)*4 ||
       col>=(w+tw-1)/tw || row>=(h+th-1)/th || samples<(size_t)tw*th*3/2 ||
       !Z8_HLG_PACK10_LSB_FIRST_HYPOTHESIS) return false;
    size_t plane=(size_t)tw*th;
    for(unsigned j=0;j<th;j++)for(unsigned i=0;i<tw;i++) {
        unsigned x=col*tw+i,sy=row*th+j;
        if(x>=w) { x=w-1; }
        if(sy>=h) { sy=h-1; }
        out[(size_t)j*tw+i]=z8_hlg_load(y+(size_t)sy*stride,x,10);
    }
    for(unsigned j=0;j<th/2;j++)for(unsigned i=0;i<tw/2;i++) {
        unsigned x=col*(tw/2)+i,sy=row*(th/2)+j;
        if(x>=w/2) { x=w/2-1; }
        if(sy>=h/2) { sy=h/2-1; }
        unsigned u=Z8_HLG_C1_UV_U_FIRST_HYPOTHESIS?0:1;
        out[plane+(size_t)j*(tw/2)+i]=z8_hlg_load(uv+(size_t)sy*stride,2*x+u,10);
        out[plane+plane/4+(size_t)j*(tw/2)+i]=z8_hlg_load(uv+(size_t)sy*stride,2*x+1-u,10);
    }
    return true;
}
static inline unsigned z8_hevc_prefix(const uint8_t *p,size_t n)
{
    if(n>=6 && !p[0] && !p[1] && !p[2] && p[3]==1)return 4;
    if(n>=5 && !p[0] && !p[1] && p[2]==1)return 3;
    return 0;
}
/* Only the type bits change. Caller has collected/flushed the entire one-frame
 * encoder result and passes its actual VCL count; layer0/tid1 mandatory. */
static inline bool z8_hevc_transform_single(uint8_t *p,size_t n,unsigned vcl_count)
{
    unsigned prefix=z8_hevc_prefix(p,n);
    if(!prefix || vcl_count!=1 || p[prefix]!=0x28 || p[prefix+1]!=1)return false;
    p[prefix]=0x26;return true;
}
typedef struct Z8HEVCLane {
    uint8_t *data,*table;uint32_t capacity,table_capacity,used,count;
} Z8HEVCLane;
static inline bool z8_hevc_kib_limit(uint64_t bytes,uint32_t kib)
{ return kib && kib<0x1000000 && bytes<=(uint64_t)kib*1024; }
/* Non-grid 128=01020001 masks bit18. Publishing both raw bits would make
 * 40c5bde0 clear both but deliver only event1, losing final completion. */
static inline uint32_t z8_hevc_completion_mask(unsigned lanes)
{ return lanes==4?0x60000:lanes==1?0x20000:0; }
static inline bool z8_hevc_lane_add(Z8HEVCLane *l,unsigned type,const uint8_t *nal,size_t bytes)
{
    if(!l || !l->data || !l->table || !nal || bytes<6 || bytes>UINT32_MAX ||
       z8_hevc_prefix(nal,bytes)!=4 || nal[4]!=(type<<1) || nal[5]!=1 ||
       (type!=19 && type!=32 && type!=33 && type!=34) || l->used>l->capacity ||
       bytes>l->capacity-l->used || l->count>=l->table_capacity/16)return false;
    uint8_t *t=l->table+l->count*16;
    memcpy(l->data+l->used,nal,bytes);memset(t,0,16);
    z8_hevc_put32(t,type);z8_hevc_put32(t+4,bytes);z8_hevc_put32(t+8,l->used);
    l->used+=bytes;l->count++;return true;
}
typedef struct Z8HEVCTileReceipt {
    char input_sha256[65],before_sha256[65],after_sha256[65],dma_nal_sha256[65];
    uint32_t bytes,raw_bytes,raw_prefix;
} Z8HEVCTileReceipt;
typedef struct Z8HEVCHistory {
    uint64_t job,source_job,frame;
    unsigned width,height,lanes,tile_count,profile_idc;
    bool observed;
    char input_sha256[2][65],lane_sha256[4][65],table_sha256[4][65];
    uint32_t lane_bytes[4],lane_records[4];
    Z8HEVCTileReceipt tiles[22];
} Z8HEVCHistory;
typedef struct Z8HEVCBackend {
    bool enabled,busy,result_valid,failed;
    void *work;
    uint64_t jobs,dma_bytes,completion_irqs,consumed_job,source_job,source_frame,cancels;
    uint64_t stage1_acks,stage2_acks;
    uint32_t lane_bytes[4],lane_records[4],profile_idc,total_frames,level_idc,compatibility;
    uint64_t constraints;
    uint64_t consumed_jobs[3];
    unsigned source_output,width,height,lane_count,tile_count,transport_slots;
    bool observed_profile;
    Z8HEVCHistory history[3];
    char input_sha256[2][65],lane_sha256[4][65],table_sha256[4][65];
    Z8HEVCTileReceipt tiles[22];
    const char *error;
} Z8HEVCBackend;
#endif
