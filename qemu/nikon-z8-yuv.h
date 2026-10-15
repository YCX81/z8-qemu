/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Z8 object1f4..1f6, original40c76c68/40c76bd4. Register masks adapted
 * from WonderLantern zr-qemu f9cc5d1's yuvcmp_config_mask. Configuration
 * plus opt-in WLYV packet transport. No Nikon compression algorithm claim. */
#include <zlib.h>
typedef struct Z8YuvSlot {
    uint64_t logical[2],physical[2],epoch;
    unsigned width,height,stride,capacity[2];
    bool owned;
} Z8YuvSlot;
typedef struct Z8YuvData {
    Z8YuvSlot slots[16];
    uint16_t valid[2],written[2];
    uint64_t frames,bytes,reads,rejects,cancels;
    uint64_t dma_jobs,dma_bytes,dma_rejects;
    uint64_t dma_cache_hits,dma_last_epoch;
    uint8_t *dma_cache_packet[2],*dma_cache_pixels[2];
    unsigned dma_cache_bytes[2];
    unsigned read_stage;
    bool single_enabled;
    uint64_t single_jobs,single_rejects,single_input_bytes,single_output_bytes;
} Z8YuvData;

static void z8_yuv_descriptor_write(NikonZ9State *s,unsigned off)
{
    if (s->z8_yuv_data && off>=0x200 && off<0x600 && (off&0x7f)>=0x40 && (off&0x7f)<0x50) {
        unsigned bank=(off-0x200)/0x200,bit=((off&0x1ff)/0x80)*4+((off&0x7f)-0x40)/4;
        s->z8_yuv_data->valid[bank]|=1u<<bit;s->z8_yuv_data->written[bank]|=1u<<bit;
    }
}

static void z8_yuv_cancel(NikonZ9State *s)
{
    Z8YuvData *d=s->z8_yuv_data;if (!d) { return; }
    for (unsigned n=0;n<G_N_ELEMENTS(d->slots);n++) { if (d->slots[n].owned) { d->slots[n].owned=false;d->cancels++; } }
}

/* Functional internal WLYV/zlib transport. Both compression endpoints use
 * the original physical packet reservations. It is not Nikon YUV coding. */
static bool z8_yuv_store(NikonZ9State *s,NikonRawPixels *pixels,uint64_t epoch,
                        uint64_t y,uint64_t uv,unsigned width,unsigned height,unsigned stride,
                        const uint64_t *ys,const uint64_t *uvs,const unsigned *strides,
                        const unsigned *heights,const bool *halves,uint64_t input,unsigned input_bytes)
{
    Z8YuvData *d=s->z8_yuv_data;
    if (!s->z8_yuv_data_model || !d || !pixels || !width || !height || ((width|height)&1) ||
        width>4096 || height>2160 || stride!=width) { return false; }
    Z8YuvSlot next={.logical={y,uv},.epoch=epoch,.width=width,.height=height,.stride=stride,.owned=true};
    for (unsigned p=0;p<2;p++) {
        unsigned bank=2,row=4,matches=0;
        /* Each plane must have one enabled mapping. Matching a pair alone
         * hides duplicate Y or UV aliases, even across different banks. */
        const uint32_t *r=s->z8_video1564_config;
        if (r[(0x1000+p*0x400)/4]!=1 || r[(0x100c+p*0x400)/4]!=3) { d->rejects++;return false; }
        for (unsigned b=0;b<2;b++) { for (unsigned n=0;n<4;n++) {
            unsigned control=(0x1240+b*0x100+p*0x400)/4;
            if ((r[control]&(1u<<n)) && s->z8_video1564_addresses[b*2+p][n]==next.logical[p]) {
                bank=b;row=n;matches++;
            }
        } }
        if (matches!=1) { d->rejects++;return false; }
        unsigned off=(0x1200+bank*0x100+p*0x400)/4;
        unsigned h=height>>p,n=stride*h;
        if (r[(0x1000+p*0x400)/4]!=1 || r[off+0x1c/4]!=width || r[off+0x20/4]!=stride ||
            r[off+0x18/4]!=n || r[off+0x44/4]!=(h<<16|width)) { d->rejects++;return false; }
        next.capacity[p]=r[off+0x28/4];next.physical[p]=s->z8_video1564_addresses[bank*2+p][row+4];
        if (next.capacity[p]<64 || next.capacity[p]>32*MiB ||
            !dma_ram_range(s,next.physical[p],next.capacity[p]) ||
            graphics_spans_overlap(next.physical[p],next.capacity[p],input,input_bytes)) { d->rejects++;return false; }
        for (unsigned ch=0;ch<3;ch++) {
            uint64_t span=(uint64_t)strides[ch]*heights[ch];
            if (graphics_spans_overlap(next.physical[p],next.capacity[p],ys[ch],span) ||
                graphics_spans_overlap(next.physical[p],next.capacity[p],uvs[ch],span/(halves[ch]?2:1))) { d->rejects++;return false; }
        }
    }
    if (graphics_spans_overlap(next.physical[0],next.capacity[0],next.physical[1],next.capacity[1])) { d->rejects++;return false; }
    Z8YuvSlot *slot=NULL,*free_slot=NULL;
    /* Original descriptors authorize each admitted DMA generation. Retain
     * completed RAM across alias reprogramming; only a real subsequent DMA
     * to the same full reservation supersedes its old generation. */
    for (unsigned n=0;n<G_N_ELEMENTS(d->slots);n++) {
        Z8YuvSlot *old=&d->slots[n];
        if (old->physical[0]==next.physical[0] && old->physical[1]==next.physical[1] &&
            old->capacity[0]==next.capacity[0] && old->capacity[1]==next.capacity[1]) { slot=old;continue; }
        if (!old->owned) { if (!free_slot) { free_slot=old; }continue; }
        for (unsigned p=0;p<2;p++) { for (unsigned q=0;q<2;q++) {
            if (graphics_spans_overlap(next.physical[p],next.capacity[p],old->physical[q],old->capacity[q])) { d->rejects++;return false; }
        } }
    }
    if (!slot) { slot=free_slot; }if (!slot) { return false; }
    g_autofree uint8_t *image=nikon_raw_pixels_yuv(pixels,width,height,stride,true);
    if (!image) { d->rejects++;return false; }
    uint8_t *packets[2]={NULL,NULL};unsigned amounts[2]={0,0};bool ok=false;
    for (unsigned p=0;p<2;p++) {
        unsigned n=stride*(height>>p);uint8_t *data=image+(p?stride*height:0);
        packets[p]=g_malloc0(next.capacity[p]);uint8_t *h=packets[p];uLongf packed=next.capacity[p]-48;
        if (compress2(h+48,&packed,data,n,Z_BEST_SPEED)!=Z_OK) { goto done; }
        memcpy(h,"WLYV0001",8);stl_le_p(h+8,width);stl_le_p(h+12,height>>p);stl_le_p(h+16,stride);stl_le_p(h+20,p);
        stq_le_p(h+24,epoch);stl_le_p(h+32,n);stl_le_p(h+36,packed);stl_le_p(h+40,crc32(0,data,n));amounts[p]=48+packed;
    }
    for (unsigned p=0;p<2;p++) {
        if (address_space_write(&address_space_memory,next.physical[p],MEMTXATTRS_UNSPECIFIED,packets[p],amounts[p])!=MEMTX_OK) { goto done; }
    }
    d->cancels+=slot->owned;*slot=next;d->frames++;d->bytes+=amounts[0]+amounts[1];ok=true;
done:
    g_free(packets[0]);g_free(packets[1]);if (!ok) { d->rejects++; }return ok;
}

static bool get_z8_yuv_data_model(Object *obj,Error **errp) { return NIKON_Z9_LAB(obj)->z8_yuv_data_model; }
static bool get_z8_yuv_dma_model(Object *obj,Error **errp) { return NIKON_Z9_LAB(obj)->z8_yuv_dma_model; }
static void set_z8_yuv_dma_model(Object *obj,bool value,Error **errp)
{
    NikonZ9State *s=NIKON_Z9_LAB(obj);
    if (!object_dynamic_cast(obj,MACHINE_TYPE_NAME("nikon-z8-bringup")) || s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp,"z8-yuv-dma-model requires Z8 at startup");return;
    }
    s->z8_yuv_dma_model=value;
}
static void set_z8_yuv_data_model(Object *obj,bool value,Error **errp)
{
    NikonZ9State *s=NIKON_Z9_LAB(obj);
    if (!object_dynamic_cast(obj,MACHINE_TYPE_NAME("nikon-z8-bringup")) || s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp,"z8-yuv-data-model requires Z8 at startup");return;
    }
    s->z8_yuv_data_model=value;if (value && !s->z8_yuv_data) { s->z8_yuv_data=g_new0(Z8YuvData,1); }
}
static unsigned z8_yuv_mask(unsigned off)
{
    if (off<0x80) {
        switch(off) {
        case 0: case 0x10:return 1;
        case 0x20:case 0x28:return 0x2200000f;
        case 0x30:return 0xf;
        default:return 0;
        }
    }
    if (off<0x1000 || off>=0x2000) { return 0; }
    unsigned reg=off&0x3ff;
    switch(reg) { case 0:case 4:case 0xc:return 3;case 8:return 0x7fff; }
    if (reg<0x200) { return 0; }
    reg&=0xff;
    switch(reg) {
    case 0:return 0x700f;
    case 0x18:case 0x28:return 0x3fffffff;
    case 0x1c:case 0x2c:return 0xffff;
    case 0x20:case 0x30:return 0xffffff;
    case 0x40:return 0xf;
    case 0x44:case 0x4c:return 0x3fff7fff;
    case 0x48:return 0x03ff07ff;
    case 0x64:return 0x003f0003;
    case 0x68:return 0xfff;
    case 0x7c:return 0x3ff;
    }
    return reg>=0x80 && reg<0xc0 ? ((reg&4)?0xf:UINT32_MAX) : 0;
}

static bool z8_yuv_access(NikonZ9State *s,hwaddr address,uint64_t *value,unsigned size,bool write)
{
    if (!s->z8_video_interface_model || address<0x15660000 || address>=0x15662000) { return false; }
    unsigned off=address-0x15660000;
    uint64_t mask=z8_yuv_mask(off);
    bool pair=size==8 && !(off&7) && (off&0x3ff)>=0x280 && (off&0xff)<0xc0;
    if (pair) { mask|=(uint64_t)z8_yuv_mask(off+4)<<32; }
    if (!mask || (off&3) || (size!=4 && !pair) || !s->bootstrap_fixtures) { goto invalid; }
    uint32_t *r=s->z8_video1566_config;
    if (!write) {
        *value=r[off/4];if (pair) { *value|=(uint64_t)r[off/4+1]<<32; }return true;
    }
    if (*value&~mask) { goto invalid; }
    if (off==0x10) {
        if (*value) { memset(r,0,sizeof(s->z8_video1566_config));s->z8_video_shared_reset_requests++; }
    } else if (off==0x28) { r[off/4]&=~(uint32_t)*value; }
    else { r[off/4]=*value;if (pair) { r[off/4+1]=*value>>32; } }
    return true;
invalid:
    unknown_access(s,address,*value,size,write);return true;
}

static char *get_z8_yuv_state(Object *obj,Error **errp)
{
    NikonZ9State *s=NIKON_Z9_LAB(obj);GString *j=g_string_new("{\"registers\":{");bool comma=false;
    for (unsigned domain=0;domain<2;domain++) {
        uint32_t *r=domain?s->z8_video1566_config:s->z8_video1564_config;
        uint64_t base=domain?0x15660000:0x15648000;
        for (unsigned n=0;n<0x2000/4;n++) {
            if (!r[n]) { continue; }
            g_string_append_printf(j,"%s\"0x%"PRIx64"\":%u",comma?",":"",base+n*4,r[n]);comma=true;
        }
    }
    g_string_append(j,"},\"input_addresses\":[");
    for (unsigned bank=0;bank<4;bank++) {
        g_string_append(j,bank?",[":"[");
        for (unsigned n=0;n<8;n++) { g_string_append_printf(j,"%s%"PRIu64,n?",":"",s->z8_video1564_addresses[bank][n]); }
        g_string_append(j,"]");
    }
    Z8YuvData zero={0},*d=s->z8_yuv_data?s->z8_yuv_data:&zero;unsigned owned=0;
    for (unsigned n=0;n<G_N_ELEMENTS(d->slots);n++) { owned+=d->slots[n].owned; }
    g_string_append_printf(j,"],\"single_enabled\":%s,\"single_jobs\":%"PRIu64",\"single_rejects\":%"PRIu64","
        "\"single_input_bytes\":%"PRIu64",\"single_output_bytes\":%"PRIu64,
        d->single_enabled?"true":"false",d->single_jobs,d->single_rejects,d->single_input_bytes,d->single_output_bytes);
    g_string_append_printf(j,",\"frames\":%"PRIu64",\"packet_bytes\":%"PRIu64",\"reads\":%"PRIu64",\"rejects\":%"PRIu64",\"owned\":%u,\"cancels\":%"PRIu64","
        "\"valid\":[%u,%u],\"written\":[%u,%u],\"read_stage\":%u,\"data_enabled\":%s,"
        "\"dma_jobs\":%"PRIu64",\"dma_bytes\":%"PRIu64",\"dma_rejects\":%"PRIu64",\"dma_cache_hits\":%"PRIu64",\"dma_last_epoch\":%"PRIu64","
        "\"scope\":\"opt-in actual WLYV/zlib RAM; not Nikon YUV compression\"}",d->frames,d->bytes,d->reads,d->rejects,owned,d->cancels,d->valid[0],d->valid[1],d->written[0],d->written[1],d->read_stage,s->z8_yuv_data_model?"true":"false",d->dma_jobs,d->dma_bytes,d->dma_rejects,d->dma_cache_hits,d->dma_last_epoch);
    return g_string_free(j,false);
}
