/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Functional RAW transport adapted from WonderLantern/zr-qemu f9cc5d1:
 * nikon-z9-video-raw.h and nikon-z9-d980.h. WLVR/zlib is an authored
 * simulator envelope, NOT Nikon RAW compression or a recording format.
 * Both endpoints use actual guest packet RAM; no OS flags/files injected. */
#include <zlib.h>

typedef struct Z8VideoData {
    bool packet_owned, frame_ready;
    bool stream_proven[2],end_requested[2],end_ready[2];
    uint64_t stop_requests[2],stop_ends[2],stop_acks[2],stop_cancelled[2];
    bool front_latched[2],front_waiting[2],front_notified[2];
    unsigned front_bank[2],front_width[2],front_height[2],front_capacity[2];
    uint8_t front_valid[2][2],front_written[2][2];
    uint64_t front_physical[2][4],front_commits[2],refills[2],refill_acks[2];
    uint16_t input_written[2];
    uint32_t input_template[4][4];
    unsigned input_bank;
    uint64_t input_bank_refills,consumed_epoch;
    unsigned source_group;
    uint64_t source_ends,source_end_acks,source_end_cancelled,source_end_epoch;
    unsigned row, packet_row, width, height, pitch, capacity;
    uint64_t packet_address, epoch, decoded_epoch;
    uint64_t packets, decoded, packet_bytes, decode_rejects, rejects, cancelled;
    uint64_t acks[2];
    uint8_t *compressed;
    unsigned compressed_bytes;
    uint32_t crc, decoded_crc;
    NikonRawPixels *pixels;
    uint64_t output_frames, output_bytes, ring_start, ring_latches, ring_waits;
    uint64_t ring_repeats;
    uint32_t ring[0x600/4], cache_crc;
    unsigned output_row, output_bank;
    bool ring_ready, refill_notified, wait_bank;
    uint8_t *cache[3];
    unsigned widths[3], heights[3], strides[3];
    bool halves[3];
} Z8VideoData;

static void z8_video_source_retire(NikonZ9State *s,uint32_t pending,uint32_t mask,bool reset)
{
    Z8VideoData *d=s->z8_video_data;
    if (!s->z8_video_source_end_model || !d) { return; }
    if (pending&mask&0x20000000) { if (reset) { d->source_end_cancelled++; }else { d->source_end_acks++; } }
    if (reset) { d->source_group=0; }
}

/* Single IRQ level writer for both physical ports, independent of the
 * selected data backend. R must reuse this pending/mask/W1C lifecycle. */
static void z8_front_irq_update(NikonZ9State *s)
{
    uint32_t masks[2]={s->engine152c_config[0][6],s->engine1530_config[6]};
    for (unsigned i=0;i<2;i++) {
        const uint32_t *c=i?s->engine1530_config:s->engine152c_config[0];
        if (s->z8_front_irq[i]) { qemu_set_irq(s->z8_front_irq[i],c[5]==1 && !!(s->z8_front_pending[i]&masks[i])); }
    }
}

static void z8_video_front_end(NikonZ9State *s,unsigned port)
{
    Z8VideoData *d=s->z8_video_data;
    if (d->end_requested[port] && !d->end_ready[port] && !s->z8_front_pending[port]) {
        s->z8_front_pending[port]=4;d->end_ready[port]=true;d->stop_ends[port]++;
    }
    z8_front_irq_update(s);
}

static void z8_video_front_reset(NikonZ9State *s,unsigned port)
{
    if (s->z8_front_mode[port] == 3) {
        if (!port) {
            s->z8_hsfc152c_pixel_cancelled += !!s->z8_front_pending[0];
            s->z8_hsfc152c_cancellations += s->z8_hsfc152c_pending;
            s->z8_hsfc152c_pending = s->engine152c_request_pending = false;
            s->engine152c_fifo_words = 0;
        } else {
            s->z8_hsfc1530_cancelled += s->z8_hsfc1530_active || !!s->z8_front_pending[1];
            s->z8_hsfc1530_active = false;
        }
    }
    s->z8_front_mode[port] = 0;
    s->z8_front_pending[port] = 0;
    Z8VideoData *d=s->z8_video_data;
    if (!d) { z8_front_irq_update(s); return; }
    bool owned=port?d->frame_ready:d->packet_owned;d->cancelled+=owned;
    if (port) { d->frame_ready=false; }else { d->packet_owned=false;d->row=0; }
    d->front_valid[port][0]=d->front_valid[port][1]=0;
    d->front_written[port][0]=d->front_written[port][1]=0;
    d->front_latched[port]=d->front_waiting[port]=d->front_notified[port]=false;
    d->stop_cancelled[port]+=d->end_requested[port];
    d->stream_proven[port]=d->end_requested[port]=d->end_ready[port]=false;
    z8_front_irq_update(s);
}

static bool z8_video_receiver_ready(NikonZ9State *s,unsigned bank,uint64_t spans[4])
{
    const uint32_t *c=s->engine152c_config[0],*side=s->engine152c_config[1];
    unsigned width=c[0x548/4],height=c[0x54c/4],amount=c[0x550/4];
    if (c[0] || c[1] || c[0x404/4] || c[0x14/4]!=1 || side[0x38/4]!=1 ||
        c[0x52c/4] || c[0x530/4] || c[0x534/4] || c[0x544/4] ||
        c[0x100/4]!=1 || c[0x104/4]!=1 || c[0x108/4]>1 || bank>1 || c[0x114/4]!=3 ||
        !width || !height || ((width|height)&1) || width>NIKON_RAW_MAX_DIMENSION || height>NIKON_RAW_MAX_DIMENSION ||
        (uint64_t)width*height>NIKON_RAW_MAX_SAMPLES || side[0x14/4]!=width || side[0x1c/4]!=height ||
        amount<64 || amount>128*MiB || side[0x44/4]!=amount || side[0x48/4]!=14 ||
        !s->z8_video_table_writes) { return false; }
    for (unsigned row=0;row<4;row++) {
        unsigned off=(0x120+bank*0x20+row*8)/4;
        uint64_t address=c[off]|(uint64_t)c[off+1]<<32;
        if ((address&31) || !raw_pipeline_address(s,address,amount,&spans[row])) { return false; }
        for (unsigned j=0;j<row;j++) {
            if (spans[row]<spans[j]+amount && spans[j]<spans[row]+amount) { return false; }
        }
    }
    return true;
}

/* R integration point: HSFC single 4140x2756, U16 stride8288,
 * decoder+114==0 and its history pool/descriptors must be validated here.
 * Full R profile validation includes its retained packet/pool identity. */
static bool z8_history_front_matches(NikonZ9State *s, unsigned port)
{
    return port ? z8_hsfc1530_matches(s) : z8_hsfc152c_admit(s, false);
}

static bool z8_video_stream_admitted(NikonZ9State *s)
{
    return s->primary_video_active || (s->z8_video_isp_owner && s->engine151_active[0]);
}

static bool z8_video_front_matches(NikonZ9State *s, unsigned port)
{
    Z8VideoData *d = s->z8_video_data;
    const uint32_t *c = port ? s->engine1530_config : s->engine152c_config[0];
    unsigned bank = c[0x108 / 4];
    uint64_t spans[4];
    if (!z8_video_stream_admitted(s) || !d || bank > 1 || d->front_valid[port][bank] != 0xff ||
        c[0] || c[1] || c[0x404 / 4] || c[5] != 1 ||
        (c[6] & ~0x7ffu) || c[0x114 / 4] != 3) { return false; }
    if (!port) { return z8_video_receiver_ready(s, bank, spans); }
    const uint32_t *r = s->engine152c_config[0], *core = s->engine15b0_config;
    unsigned width = c[0x548 / 4], height = c[0x54c / 4], capacity = c[0x534 / 4];
    if (!width || !height || ((width | height) & 1) ||
        width > NIKON_RAW_MAX_DIMENSION || height > NIKON_RAW_MAX_DIMENSION ||
        (uint64_t)width * height > NIKON_RAW_MAX_SAMPLES ||
        width != r[0x548 / 4] || height != r[0x54c / 4] ||
        capacity < 64 || capacity > 128 * MiB || capacity != r[0x550 / 4] ||
        core[0x38 / 4] != 1 || core[0x3c / 4] > 0xffff || core[0x4c / 4] > 1) { return false; }
    for (unsigned row = 0; row < 4; row++) {
        unsigned at = (0x120 + bank * 0x20 + row * 8) / 4;
        uint64_t address = c[at] | (uint64_t)c[at + 1] << 32;
        if ((address & 31) || !raw_pipeline_address(s, address, capacity, &spans[row])) { return false; }
        for (unsigned j = 0; j < row; j++) {
            if (graphics_spans_overlap(spans[row], capacity, spans[j], capacity)) { return false; }
        }
    }
    return true;
}

static bool z8_video_front_bank(NikonZ9State *s,unsigned port)
{
    Z8VideoData *d=s->z8_video_data;
    const uint32_t *c=port?s->engine1530_config:s->engine152c_config[0];
    unsigned bank=c[0x108/4],width=c[0x548/4],height=c[0x54c/4];
    unsigned capacity=c[(port?0x534:0x550)/4];
    if (bank>1 || (d->front_latched[port] &&
        (width!=d->front_width[port] || height!=d->front_height[port] || capacity!=d->front_capacity[port]))) { return false; }
    if (d->front_latched[port] && !d->front_waiting[port]) { return true; }
    if (d->front_valid[port][bank]!=0xff || (d->front_waiting[port] &&
        (bank==d->front_bank[port] || d->front_written[port][bank]!=0xff))) { return false; }
    uint64_t spans[4];
    if (!port) { if (!z8_video_receiver_ready(s,bank,spans)) { return false; } }
    else {
        /* Decoder+534 is packet capacity; +550 is a different parameter. */
        if (width!=d->width || height!=d->height || capacity!=d->capacity || c[0x114/4]!=3) { return false; }
        for (unsigned row=0;row<4;row++) {
            unsigned off=(0x120+bank*0x20+row*8)/4;
            uint64_t encoded=c[off]|(uint64_t)c[off+1]<<32;
            if ((encoded&31) || !raw_pipeline_address(s,encoded,capacity,&spans[row])) { return false; }
            for (unsigned j=0;j<row;j++) { if (graphics_spans_overlap(spans[row],capacity,spans[j],capacity)) { return false; } }
        }
    }
    memcpy(d->front_physical[port],spans,sizeof(spans));d->front_bank[port]=bank;
    d->front_width[port]=width;d->front_height[port]=height;d->front_capacity[port]=capacity;
    d->front_written[port][bank]=0;d->front_latched[port]=true;d->front_waiting[port]=false;
    d->front_notified[port]=false;d->front_commits[port]++;return true;
}

static uint8_t *z8_video_pack14(const NikonRawPixels *p,unsigned width,unsigned height,unsigned pitch)
{
    uint8_t *out=g_malloc0((size_t)pitch*height);
    for (unsigned y=0;y<height;y++) {
        unsigned sy=(uint64_t)(y/2)*(p->height/2)/(height/2)*2+(y&1);
        for (unsigned x=0;x<width;x+=4) {
            uint64_t bits=0;unsigned count=MIN(4,width-x);
            for (unsigned j=0;j<count;j++) {
                unsigned sx=(uint64_t)((x+j)/2)*(p->width/2)/(width/2)*2+((x+j)&1);
                unsigned v=MIN(16383,((uint64_t)p->samples[(size_t)sy*p->width+sx]*16383+p->white/2)/p->white);
                bits|=(uint64_t)v<<(14*j);
            }
            for (unsigned b=0;b<(count*14+7)/8;b++) { out[(size_t)y*pitch+x*14/8+b]=bits>>(8*b); }
        }
    }
    return out;
}

static void z8_video_data_receive(NikonZ9State *s)
{
    Z8VideoData *d=s->z8_video_data;
    if (!s->z8_video_data_model || !d || s->z8_front_mode[0] != 2 || !s->primary_video_active || !s->raw_pixels || !s->event_timing_active ||
        s->primary_reset_stage || !(s->engine150c_active&0x4000) ||
        ((s->engine150c_stop_requested|s->engine150c_stop_done|s->engine_150c_init[6])&0x4000) ||
        d->packet_owned || d->frame_ready || s->z8_front_pending[0] || s->z8_front_pending[1] || d->end_requested[0] || d->end_requested[1] ||
        (s->event_pending_1508&0x2000) ||
        s->engine152c_config[0][2]!=1 || s->engine152c_config[0][0x14/4]!=1 ||
        s->engine152c_config[0][0x404/4] || !s->engine152c_config[1][0x38/4] || !z8_video_front_bank(s,0)) { return; }
    const uint32_t *g=s->z8_video_geometry_latched[0],*c=s->engine152c_config[0];
    unsigned width=g[1]>>16,height=g[2]>>16,pitch=g[0x100/4],amount=c[0x550/4];
    if (g[0]!=1 || (g[1]&0xffff) || (g[2]&0xffff) || width!=c[0x548/4] || height!=c[0x54c/4] ||
        pitch<((width*14+7)/8) || (pitch&31) || (uint64_t)pitch*height>128*MiB ||
        g[0x104/4]!=1 || g[0x108/4]!=1 ||
        g[0x10c/4] || g[0x110/4]!=1 || g[0x114/4] || g[0x118/4]!=3 || g[0x11c/4]) { return; }
    for (unsigned row=0;row<4;row++) {
        if (g[0x120/4+row*2]!=0xe0000000 || g[0x124/4+row*2]) { return; }
    }
    if (!d->compressed || d->width!=width || d->height!=height || d->pitch!=pitch) {
        uint8_t *raw=z8_video_pack14(s->raw_pixels,width,height,pitch);
        uLongf bytes=compressBound((size_t)pitch*height);uint8_t *out=g_malloc(bytes);
        int rc=compress2(out,&bytes,raw,(size_t)pitch*height,3);
        uint32_t crc=crc32(0,raw,(size_t)pitch*height);g_free(raw);
        if (rc!=Z_OK || bytes>amount-64) { g_free(out);d->rejects++;return; }
        g_free(d->compressed);d->compressed=out;d->compressed_bytes=bytes;d->crc=crc;
        d->width=width;d->height=height;d->pitch=pitch;
    }
    if (d->compressed_bytes>amount-64) { d->rejects++;return; }
    uint64_t address=d->front_physical[0][d->row];
    uint8_t *packet=g_malloc0(amount);
    memcpy(packet,"WLVR0001",8);stl_le_p(packet+8,64);stl_le_p(packet+12,1);
    stq_le_p(packet+16,d->epoch+1);stl_le_p(packet+24,width);stl_le_p(packet+28,height);
    stl_le_p(packet+32,pitch);stl_le_p(packet+36,14);stq_le_p(packet+40,(uint64_t)pitch*height);
    stl_le_p(packet+48,d->compressed_bytes);stl_le_p(packet+52,d->crc);
    memcpy(packet+64,d->compressed,d->compressed_bytes);
    if (!nb_writable(&s->output_owner, (NBSpan){address, amount})) { g_free(packet); d->rejects++; return; }
    bool ok=address_space_write(&address_space_memory,address,MEMTXATTRS_UNSPECIFIED,packet,amount)==MEMTX_OK;
    g_free(packet);if (!ok) { d->rejects++;return; }
    d->epoch++;d->packet_row=d->row;d->capacity=amount;d->packet_address=address;
    d->packet_owned=true;d->packets++;d->packet_bytes+=amount;s->z8_front_pending[0]|=3;d->stream_proven[0]=true;
    /* Z8 40e8b538 registers 40e8b6f8 in slot13 via40c2c700.
     * Table4164be9c binds it to channel cause2000. One accepted RAW input
     * owns this source boundary; its original callback advances frame IDs.
     * This does not write the software counter or post its task flags. */
    s->event_pending_1508|=0x2000;s->event_channel_inputs++;event_channel_update_irq(s);
    z8_front_irq_update(s);
}

static void z8_video_data_tick(NikonZ9State *s)
{
    Z8VideoData *d=s->z8_video_data;
    if (!s->z8_video_data_model || !d) { return; }
    uint32_t *c=s->engine1530_config;
    if (s->z8_front_mode[1] == 2 && d->packet_owned && !d->frame_ready && !s->z8_front_pending[1] && c[2]==1 &&
        !c[0x404/4] && c[0x14/4]==1 && s->engine15b0_config[0x38/4]==1) {
        if (!z8_video_front_bank(s,1)) { return; }
        uint64_t address=d->front_physical[1][d->packet_row];
        uint8_t header[64];bool ok=c[0x548/4]==d->width && c[0x54c/4]==d->height && c[0x534/4]==d->capacity &&
            address==d->packet_address &&
            address_space_read(&address_space_memory,address,MEMTXATTRS_UNSPECIFIED,header,64)==MEMTX_OK;
        if (!ok || z8_packet_kind(header, sizeof(header), false) != Z8_PACKET_WLVR || ldl_le_p(header+8)!=64 || ldl_le_p(header+12)!=1 ||
            ldq_le_p(header+16)!=d->epoch || ldl_le_p(header+24)!=d->width || ldl_le_p(header+28)!=d->height ||
            ldl_le_p(header+32)!=d->pitch || ldl_le_p(header+36)!=14 ||
            ldq_le_p(header+40)!=(uint64_t)d->pitch*d->height || !ldl_le_p(header+48) ||
            ldl_le_p(header+48)>d->capacity-64) { d->decode_rejects++;return; }
        unsigned bytes=ldl_le_p(header+48),crc=ldl_le_p(header+52);
        uint8_t *packet=g_malloc(bytes),*raw=g_malloc((size_t)d->pitch*d->height);
        uLongf decoded=(size_t)d->pitch*d->height;
        ok=address_space_read(&address_space_memory,address+64,MEMTXATTRS_UNSPECIFIED,packet,bytes)==MEMTX_OK &&
            uncompress(raw,&decoded,packet,bytes)==Z_OK && decoded==(size_t)d->pitch*d->height && crc32(0,raw,decoded)==crc;
        g_free(packet);
        if (!ok) { g_free(raw);d->decode_rejects++;return; }
        if (!d->pixels || d->decoded_crc!=crc || d->pixels->width!=d->width || d->pixels->height!=d->height) {
            uint8_t *samples=g_malloc((size_t)d->width*d->height*2);
            for (unsigned y=0;y<d->height;y++) {
                for (unsigned x=0;x<d->width;x++) {
                    unsigned at=x*14/8,shift=x*14%8;uint8_t *p=raw+(size_t)y*d->pitch+at;
                    uint32_t bits=p[0]|(uint32_t)p[1]<<8;
                    if (shift>2) { bits|=(uint32_t)p[2]<<16; }
                    stw_le_p(samples+((size_t)y*d->width+x)*2,(bits>>shift)&0x3fff);
                }
            }
            nikon_raw_pixels_free(d->pixels);d->pixels=nikon_raw_pixels_new(samples,d->width,d->height,0,16383);
            g_free(samples);d->decoded_crc=crc;
        }
        g_free(raw);if (!d->pixels) { d->decode_rejects++;return; }
        d->packet_owned=false;d->frame_ready=true;d->decoded++;d->decoded_epoch=d->epoch;
        s->z8_front_pending[1]|=3;d->stream_proven[1]=true;
        if (d->packet_row==3) { d->front_waiting[1]=true; }
        z8_front_irq_update(s);
    }
}

/* Shared descriptor observation; it never chooses a producer. */
static void z8_front_config_written(NikonZ9State *s, unsigned port, bool front, unsigned off)
{
    Z8VideoData *d = s->z8_video_data;
    if (!d || !front || off < 0x120 || off >= 0x160) { return; }
    unsigned bank = (off - 0x120) / 0x20, bit = ((off - 0x120) & 31) / 4;
    d->front_valid[port][bank] |= 1u << bit;
    if (!d->front_latched[port] || bank != d->front_bank[port]) {
        d->front_written[port][bank] |= 1u << bit;
    }
}

/* Hook called only by the shared M register handlers, after register lookup. */
static bool z8_front_access(NikonZ9State *s, unsigned i, bool front,
    unsigned off, uint32_t *word, uint32_t mask, hwaddr address,
    uint64_t *value, unsigned size, bool write)
{
    bool history_capable = i ? s->z8_hsfc1530_decode_model : s->z8_hsfc152c_control_model;
    if (!(s->z8_video_interface_model || history_capable)) { return false; }
    Z8VideoData *d = s->z8_video_data_model ? s->z8_video_data : NULL;
    if (size != 4 || !mask || (address & 3)) { goto invalid; }
    if (front && off == 0x10) {
        if (write) { goto invalid; }
        *value = s->z8_front_pending[i] &
                 (i ? s->engine1530_config : s->engine152c_config[0])[6];
        return true;
    }
    if (write && (*value & ~(uint64_t)mask)) { goto invalid; }
    /* START selects exactly one full native program. Capability flags only
     * authorize a backend; geometry, source request and descriptor identity
     * determine the mode. Inert O configuration retention is not a data job. */
    if (front && write && off == 8 && *value == 1 && *word != 1 &&
        (d || history_capable || (!i && s->engine152c_request_model))) {
        bool video = d && z8_video_front_matches(s, i);
        bool request = !i && engine152c_request_matches(s);
        bool history = z8_history_front_matches(s, i);
        fprintf(stderr, "Z8_FRONT_START port=%u pc=0x%" PRIx64
            " video=%u request=%u history=%u primary_video_active=%u isp0_owner=%u isp0_active=%u\n",
            i, current_cpu ? ARM_CPU(current_cpu)->env.pc : 0, video, request, history,
            s->primary_video_active, s->z8_video_isp_owner, s->engine151_active[0]);
        if ((unsigned)video + request + history != 1) { goto invalid; }
        if (history) { s->z8_front_mode[i] = 3; return false; }
        if (request) { return false; }
    }
    if (s->z8_front_mode[i] == 3 ||
        (history_capable && s->z8_front_mode[i] != 2 &&
         !(front && write && off == 8 && *value == 1))) { return false; }
    if (!s->z8_video_interface_model) { return false; }
    /* Configuration-only O tests retain START, but create no accepted data
     * job. With the data backend enabled, START must select exactly one
     * complete profile. An M request is handled by M below this hook. */
    if (!d) {
        if (front && off == 8 && write && *value &&
            (s->engine152c_request_model || s->engine153_config_model)) {
            return false;
        }
        if (!write) {
            if (!front && !i && off == 0x50) { goto invalid; }
            *value = front && off == 0x1c ? s->z8_front_pending[i] : *word;
        } else if (front && off == 0x1c) {
            s->z8_front_pending[i] &= ~*value;
        } else if (!front && !i && off == 0x50) {
            s->z8_video_table_writes++;
        } else { *word = *value; }
        return true;
    }
    if (!i && (s->z8_front_mode[0] == 1 ||
        (!front && off == 0x50 && s->engine152c_request_model &&
         s->engine152c_config[0][0x52c/4] == 1))) { return false; }
    if (front && off == 8 && write && *value == 1 && *word != 1) {
        bool video = z8_video_front_matches(s, i);
        bool request = !i && engine152c_request_matches(s);
        bool history = z8_history_front_matches(s, i);
        fprintf(stderr, "Z8_FRONT_START port=%u pc=0x%" PRIx64
            " video=%u request=%u history=%u primary_video_active=%u isp0_owner=%u isp0_active=%u\n",
            i, current_cpu ? ARM_CPU(current_cpu)->env.pc : 0, video, request, history,
            s->primary_video_active, s->z8_video_isp_owner, s->engine151_active[0]);
        if ((unsigned)video + request + history != 1) { goto invalid; }
        if (request) { return false; }
        if (!video || s->z8_front_pending[i] || d->end_requested[i] || d->end_ready[i]) { goto invalid; }
        const uint32_t *c = i ? s->engine1530_config : s->engine152c_config[0];
        unsigned bank = c[0x108/4], capacity = c[(i ? 0x534 : 0x550)/4];
        uint64_t spans[4];
        for (unsigned row = 0; row < 4; row++) {
            unsigned at = (0x120 + bank*0x20 + row*8)/4;
            uint64_t encoded_address = c[at] | (uint64_t)c[at+1] << 32;
            if (!raw_pipeline_address(s, encoded_address, capacity, &spans[row])) { goto invalid; }
        }
        memcpy(d->front_physical[i], spans, sizeof(spans));
        d->front_bank[i] = bank;
        d->front_width[i] = c[0x548/4]; d->front_height[i] = c[0x54c/4];
        d->front_capacity[i] = capacity; d->front_written[i][bank] = 0;
        d->front_latched[i] = true; d->front_waiting[i] = d->front_notified[i] = false;
        d->front_commits[i]++;
        s->z8_front_mode[i] = 2;
    }
    /* A latched stream cannot silently change geometry/core under an
     * outstanding job. Bank selectors and fresh descriptor submission are
     * handled by O's bank protocol; control/mask/W1C stay live. */
    const uint32_t *config = i ? s->engine1530_config : s->engine152c_config[0];
    bool live_control = front && (off <= 0x1c || off == 0x404 || off == 0x108 ||
                                  (off >= 0x120 && off < 0x160));
    if (write && s->z8_front_mode[i] == 2 && config[2] &&
        !live_control && *word != *value) { goto invalid; }
    if (front && write && (off<=0x18 || off==0x404)) {
        const uint32_t *c=i?s->engine1530_config:s->engine152c_config[0];
        fprintf(stderr,"Z8_VIDEO_FRONT port=%u pc=%"PRIx64" off=%x value=%"PRIx64" active=%u proven=%u hold=%u gate=%u mask=%x pending=%x\n",
            i,current_cpu?ARM_CPU(current_cpu)->env.pc:0,off,*value,c[2],d->stream_proven[i],c[0x404/4],c[5],c[6],s->z8_front_pending[i]);
    }
    if (front && off==0x1c) {
        if (write) {
            if (*value&~0x7ffu) { goto invalid; }
            uint32_t old=s->z8_front_pending[i];const uint32_t *c=i?s->engine1530_config:s->engine152c_config[0];
            if ((old&*value&4) && d->end_ready[i]) { d->stop_acks[i]++;d->end_ready[i]=d->end_requested[i]=false; }
            s->z8_front_pending[i]&=~*value;d->refill_acks[i]+=!!(old&*value&0x10);
            if ((old&3) && !(s->z8_front_pending[i]&3)) {
                d->acks[i]++;
                if (!i) { d->row=(d->packet_row+1)&3;if (!d->row) { d->front_waiting[0]=true; } }
                if (d->packet_row==2 && !d->front_notified[i] && c[2]==1 && !c[0x404/4] && !d->end_requested[i]) {
                    d->front_notified[i]=true;d->refills[i]++;s->z8_front_pending[i]|=0x10;
                }
            }
            z8_video_front_end(s,i);
        } else { *value=s->z8_front_pending[i]; }
        return true;
    }
    if (front && off==0x10 && !write) { *value=s->z8_front_pending[i]&(i?s->engine1530_config:s->engine152c_config[0])[6];return true; }
    if (address==0x15ac0050 && write) { s->z8_video_table_writes++;return true; }
    if (!write) {
        if (!front && !i && off == 0x50) { goto invalid; }
        *value=*word;return true; }
    if (*value&~(uint64_t)mask) { goto invalid; }
    if (front && off==8 && *value==0) {
        bool active=*word==1;*word=0;
        if (active) {
            const uint32_t *c=i?s->engine1530_config:s->engine152c_config[0];
            if (d->stream_proven[i] && !c[0x404/4] && c[5] && (c[6]&4)) {
                d->stop_requests[i]++;d->end_requested[i]=true;d->stream_proven[i]=false;
            }
            d->cancelled+=d->packet_owned || d->frame_ready;d->packet_owned=d->frame_ready=d->ring_ready=false;
            d->row=0;d->input_written[0]=d->input_written[1]=0;
            for (unsigned p=0;p<2;p++) { d->front_latched[p]=d->front_waiting[p]=d->front_notified[p]=false; }
            z8_video_front_end(s,i);
        }
        return true;
    }
    if (front && off==8 && *value==1 && *word!=1) {
        if (d->end_requested[i] || d->end_ready[i] || s->z8_front_pending[i]) { goto invalid; }
        d->stream_proven[i]=false;if (!i) { d->row=0; }
    }
    if (front && (off==0 || off==4) && *value) {
        bool owned=i?d->frame_ready:d->packet_owned;
        if (owned || s->z8_front_pending[i]) {
            bool idle=!(s->engine150c_active&0x4000) && !s->engine1543_active[1] && !s->engine151_active[0];
            if (!idle || (i?s->engine1530_config:s->engine152c_config[0])[0x404/4]!=1) { goto invalid; }
            d->cancelled+=owned;
            if (i) { d->frame_ready=false; } else { d->packet_owned=false; }
        }
        /* Original40c31610/40c30e60 perform a held local flush, explicitly
         * ACK old causes, then write STOP. Retain the proven stream until
         * that STOP; only the separate SoC reset destroys its lifetime. */
    }
    if (front && off>=0x120 && off<0x160) {
        unsigned bank=(off-0x120)/0x20,bit=((off-0x120)&31)/4;
        d->front_valid[i][bank]|=1u<<bit;
        if (!d->front_latched[i] || bank!=d->front_bank[i]) { d->front_written[i][bank]|=1u<<bit; }
    }
    *word=*value;
    if (front && (off==0x18 || off==0x14)) { z8_front_irq_update(s); }
    return true;
invalid:
    unknown_access(s,address,*value,size,write);return true;
}

static bool z8_video_isp_refill(NikonZ9State *s,bool recording)
{
    Z8VideoData *d=s->z8_video_data;const uint32_t *input=s->engine1542_config[11];
    unsigned bank=input[2]&1;
    if (bank==d->input_bank || d->input_written[bank]!=0xffff) { return false; }
    for (unsigned row=0;row<4;row++) { for (unsigned word=0;word<4;word++) {
        if (input[(0x200+bank*0x200+row*0x80)/4+word]!=d->input_template[row][word]) { return false; }
    } }
    const uint64_t pairs=UINT64_C(0xfff0fff0fff0fff0);
    uint64_t fresh=s->raw_pipeline_bank_written[bank]&pairs,other=s->raw_pipeline_bank_written[bank^1]&pairs;
    if ((fresh && fresh!=pairs) || (other && other!=pairs)) { return false; }
    unsigned output=d->output_bank;
    if (fresh==pairs) { output=bank; }
    else if (recording || other) { return false; }
    if (recording && (!s->z8_yuv_data || output!=bank || s->z8_yuv_data->written[bank]!=0xffff)) { return false; }
    /* A complete fresh C1 submission can retain C0's complete cache.
     * New output tables must belong to that C1 bank, even when the stopped
     * C0 programming selector remains unchanged. Partial tables stay inert. */
    if (fresh==pairs) {
        unsigned off=(0x200+output*0x200)/4;
        memcpy(&d->ring[off],&s->engine1542_config[10][off],0x200);
        s->raw_pipeline_bank_written[output]=0;d->output_bank=output;d->ring_latches++;
        if (s->z8_yuv_data) { s->z8_yuv_data->written[output]=0; }
    }
    d->input_bank=bank;d->input_written[bank]=0;d->input_bank_refills++;d->wait_bank=false;
    return true;
}

/* Original Z8's active 1920x1080 ISP0/C1 program matches the ZR primary
 * internal route. Use its admitted snapshot, live gates/masks and actual
 * decoded packet. C0 owns output addresses even when not separately active. */
static bool z8_video_isp_tick(NikonZ9State *s)
{
    Z8VideoData *d=s->z8_video_data;
    if (!s->z8_video_data_model || !s->z8_video_isp_owner || !s->engine151_active[0]) { return false; }
    const uint32_t *c=s->engine151_latched[0],*src=s->engine1518[0].config;
    const uint32_t *a=s->engine1542_config[1],*h=s->engine1543_latched[1];
    unsigned width=a[0x4c/4],height=a[0x50/4],pitch=a[0x38/4];
    if (!d->frame_ready || !d->pixels || !s->engine1543_active[1] || s->engine1543_held[1] ||
        !(a[1]&0x200) || !src[0x30/4] || !width || !height || ((width|height)&1) ||
        width>4096 || height>2160 || width*9!=height*16 || pitch!=((width*3+255)&~255u) || a[0x3c/4]!=pitch ||
        src[0xb0/4]!=width*3 || src[0xb4/4]!=width*3 || src[0xbc/4]!=1 || src[0xc0/4]!=1 ||
        src[0xc4/4]!=0x22 || src[0xd4/4]!=0x22 || (h[2]&~1u)!=0x302 || h[4]!=0x29 ||
        c[0x200/4]!=1 || c[0x204/4]!=1 || c[0x208/4] || c[0x20c/4] || c[0x210/4] ||
        c[0x214/4]!=1 || c[0x218/4]!=1 || c[0x21c/4]!=5 || c[0x220/4]!=2 ||
        c[0x400/4]!=(width|(height<<16)) || c[0x450/4]!=c[0x400/4] ||
        c[0x430/4]!=width*3 || c[0x434/4]!=width*3 || c[0x480/4]!=pitch || c[0x484/4]!=pitch ||
        c[0x740/4]!=h[0x200/4] || c[0x744/4]!=h[0x204/4] ||
        c[0x9c0/4]!=h[0x208/4] || c[0x9c4/4]!=h[0x20c/4] ||
        s->engine1542_pending[1] || s->engine1542_pending[10] || s->engine1542_pending[11] ||
        (s->engine151_pending[0]&8) || (s->engine1518[0].pending&(0x08100000|(s->z8_video_source_end_model?0x20000000:0))) ||
        s->z8_video_primary_pending ||
        (s->event1508_global1_model && (s->event_global1_pending&0x10))) { return true; }
    const uint64_t required=UINT64_C(0xfff0fff0fff0fff0);
    const uint32_t *submitted=s->engine1542_config[10];
    /* Native A1 0x202 preview -> 0xa02 recording enables fourth output.
     * B3 parameters can be prepared while the preview remains active. */
    bool recording=(a[1]&0x800)!=0;
    unsigned selected=submitted[2]&1;
    if (!d->ring_ready || d->ring_start!=s->engine1543_starts[1]) {
        unsigned input=s->engine1542_config[11][2]&1;
        if (d->input_written[input]!=0xffff) { return true; }
        if (recording) { selected=input; }
        if ((s->engine1543_buffer_valid[0]&required)!=required) { return true; }
        if (recording && (!s->z8_yuv_data || s->z8_yuv_data->valid[selected]!=0xffff)) { return true; }
        memcpy(d->ring,submitted,sizeof(d->ring));d->output_row=0;d->output_bank=selected;
        d->ring_ready=true;d->ring_start=s->engine1543_starts[1];d->ring_latches++;
        d->wait_bank=false;d->refill_notified=false;s->raw_pipeline_bank_written[selected]=0;
        if (s->z8_yuv_data) { s->z8_yuv_data->written[selected]=0; }
        for (unsigned row=0;row<4;row++) { memcpy(d->input_template[row],&h[(0x200+input*0x200+row*0x80)/4],16); }
        d->input_bank=input;d->input_written[input]=0;
    }
    if (d->wait_bank && !s->z8_video_ring_repeat_model) {
        if (!z8_video_isp_refill(s,recording)) { d->ring_waits++;return true; }
    }
    if (d->wait_bank) {
        /* Z8 native03 refills the alternate address table without changing
         * C0's programming selector after batch two. Select only a complete
         * fresh table; never use a partial low/high pair or rewrite MMIO. */
        unsigned alternate=d->output_bank^1;
        if ((s->raw_pipeline_bank_written[alternate]&required)==required) { selected=alternate; }
        bool complete=(s->raw_pipeline_bank_written[selected]&required)==required &&
            (!recording || (s->z8_yuv_data && s->z8_yuv_data->written[selected]==0xffff));
        if (!complete) {
            /* Same explicit continuous-table hypothesis as the shared
             * photo pipeline. Never read a partial replacement. Every
             * repeated batch still needs fresh decoded RAW and all ACKs. */
            bool pending=s->raw_pipeline_bank_written[0] || s->raw_pipeline_bank_written[1] ||
                (recording && (!s->z8_yuv_data || s->z8_yuv_data->written[0] || s->z8_yuv_data->written[1]));
            if (!s->z8_video_ring_repeat_model || pending) { d->ring_waits++;return true; }
            d->ring_repeats++;d->wait_bank=false;
        } else {
            memcpy(d->ring,submitted,sizeof(d->ring));d->output_bank=selected;d->wait_bank=false;
            d->ring_latches++;s->raw_pipeline_bank_written[selected]=0;
            if (s->z8_yuv_data) { s->z8_yuv_data->written[selected]=0; }
        }
    }
    if (d->output_row==3 && !d->refill_notified) {
        d->refill_notified=true;s->engine1542_pending[10]|=8;s->engine1542_pending[11]|=8;
        raw_pipeline_irq_update(s);return true;
    }
    uint64_t ys[3],uvs[3];unsigned widths[3],heights[3],strides[3];bool halves[3];
    for (unsigned ch=0;ch<3;ch++) {
        const uint32_t *b=s->engine1542_config[3+ch],*f=s->engine1542_config[15+ch];
        unsigned off=(0x210+d->output_bank*0x200+d->output_row*0x80+ch*0x10)/4;
        unsigned w=f[0x2c/4],out_height=f[0x30/4],stride=b[0x38/4];bool half=!(b[3]&0x10);
        uint64_t yn=(uint64_t)stride*out_height,uvn=yn/(half?2:1);
        if (!w || !out_height || ((w|out_height|stride)&1) || w>4096 || out_height>4096 || stride<w ||
            stride>8192 || stride!=b[0x3c/4] || yn>16*MiB || b[4]!=1 ||
            !raw_pipeline_address(s,d->ring[off]|(uint64_t)d->ring[off+1]<<32,yn,&ys[ch]) ||
            !raw_pipeline_address(s,d->ring[off+2]|(uint64_t)d->ring[off+3]<<32,uvn,&uvs[ch])) { d->rejects++;return true; }
        widths[ch]=w;heights[ch]=out_height;strides[ch]=stride;halves[ch]=half;
    }
    if (!raw_input_pair_spans_disjoint(ys,uvs,strides,heights,halves)) { d->rejects++;return true; }
    for (unsigned ch=0;ch<3;ch++) {
        uint64_t n=(uint64_t)strides[ch]*heights[ch];
        if (!nb_writable(&s->output_owner, (NBSpan){ys[ch],n}) ||
            !nb_writable(&s->output_owner, (NBSpan){uvs[ch],n/(halves[ch]?2:1)}) ||
            graphics_spans_overlap(ys[ch],n,d->packet_address,d->capacity) ||
            graphics_spans_overlap(uvs[ch],n/(halves[ch]?2:1),d->packet_address,d->capacity)) { d->rejects++;return true; }
    }
    if (recording) {
        unsigned off=(0x240+d->output_bank*0x200+d->output_row*0x80)/4;
        uint64_t y=d->ring[off]|(uint64_t)d->ring[off+1]<<32,uv=d->ring[off+2]|(uint64_t)d->ring[off+3]<<32;
        const uint32_t *b=s->engine1542_config[6],*f=s->engine1542_config[18];
        if (b[4]!=1 || (b[3]&0x10) || b[0x38/4]!=b[0x3c/4] ||
            !z8_yuv_store(s,d->pixels,d->decoded_epoch,y,uv,f[0x2c/4],f[0x30/4],b[0x38/4],ys,uvs,strides,heights,halves,d->packet_address,d->capacity)) { return true; }
    }
    uint64_t total=0;
    for (unsigned ch=0;ch<3;ch++) {
        uint64_t yn=(uint64_t)strides[ch]*heights[ch],uvn=yn/(halves[ch]?2:1);
        if (!d->cache[ch] || d->cache_crc!=d->decoded_crc || d->widths[ch]!=widths[ch] ||
            d->heights[ch]!=heights[ch] || d->strides[ch]!=strides[ch] || d->halves[ch]!=halves[ch]) {
            g_free(d->cache[ch]);d->cache[ch]=nikon_raw_pixels_yuv(d->pixels,widths[ch],heights[ch],strides[ch],halves[ch]);
            d->widths[ch]=widths[ch];d->heights[ch]=heights[ch];d->strides[ch]=strides[ch];d->halves[ch]=halves[ch];
        }
        if (!d->cache[ch] || address_space_write(&address_space_memory,ys[ch],MEMTXATTRS_UNSPECIFIED,d->cache[ch],yn)!=MEMTX_OK ||
            address_space_write(&address_space_memory,uvs[ch],MEMTXATTRS_UNSPECIFIED,d->cache[ch]+yn,uvn)!=MEMTX_OK) { d->rejects++;return true; }
        total+=yn+uvn;
        s->raw_pipeline_y[ch]=ys[ch];s->raw_pipeline_uv[ch]=uvs[ch];
        s->raw_pipeline_width[ch]=widths[ch];s->raw_pipeline_height[ch]=heights[ch];
        s->raw_pipeline_stride[ch]=strides[ch];s->raw_pipeline_uv_half[ch]=halves[ch];
    }
    d->cache_crc=d->decoded_crc;d->frame_ready=false;d->output_frames++;d->output_bytes+=total;
    d->consumed_epoch=d->decoded_epoch;
    s->raw_pipeline_frames++;s->raw_pipeline_bytes+=total;
    d->output_row=(d->output_row+1)&3;
    if (!d->output_row) { d->wait_bank=true;d->refill_notified=false; }
    s->engine1518[0].pending|=0x08100000;s->engine1518[0].stream_frames++;
    /* Original40e9671c waits80800000 when countera03b6ff9c==3.
     * Source0 bit29 maps to800000. This optional grouped completion follows
     * actual pixel writes; it is distinct from frontend STOP END4. */
    if (s->z8_video_source_end_model) {
        d->source_group=(d->source_group+1)&3;
        if (d->source_group==3) { s->engine1518[0].pending|=0x20000000;d->source_ends++;d->source_end_epoch=d->consumed_epoch; }
    }
    engine1518_update_irq(&s->engine1518[0]);
    s->engine151_pending[0]|=8;engine151_update_irq(s,0);
    if (a[4]==1) { s->engine1542_pending[1]|=0x11; }
    s->engine1542_pending[10]|=1;s->engine1542_pending[11]|=1;raw_pipeline_irq_update(s);
    s->z8_video_primary_pending|=0x18;s->primary_bit3_owner=2;engine150c_stop_irq_update(s);
    /* Preserve the shared pipeline's original sequence boundary after
     * actual pixels. Timing-only global bit7 does not replace bit4. */
    if (s->event1508_global1_model && (s->event_extended_1508[0x68/4]&0x10)) {
        s->event_global1_pending|=0x10;s->event_global1_inputs++;event_global1_update_irq(s);
    }
    return true;
}

static bool get_z8_video_data_model(Object *obj,Error **errp)
{ return NIKON_Z9_LAB(obj)->z8_video_data_model; }
static bool get_z8_video_source_end_model(Object *obj,Error **errp)
{ return NIKON_Z9_LAB(obj)->z8_video_source_end_model; }
static void set_z8_video_source_end_model(Object *obj,bool value,Error **errp)
{
    NikonZ9State *s=NIKON_Z9_LAB(obj);
    if (!object_dynamic_cast(obj,MACHINE_TYPE_NAME("nikon-z8-bringup")) || s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp,"video source END requires Z8 at startup");return;
    }
    s->z8_video_source_end_model=value;
}
static bool get_z8_video_ring_repeat_model(Object *obj,Error **errp)
{ return NIKON_Z9_LAB(obj)->z8_video_ring_repeat_model; }
static void set_z8_video_ring_repeat_model(Object *obj,bool value,Error **errp)
{
    NikonZ9State *s=NIKON_Z9_LAB(obj);
    if (!object_dynamic_cast(obj,MACHINE_TYPE_NAME("nikon-z8-bringup")) || s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp,"video ring repeat requires Z8 at startup");return;
    }
    s->z8_video_ring_repeat_model=value;
}
static void set_z8_video_data_model(Object *obj,bool value,Error **errp)
{
    NikonZ9State *s=NIKON_Z9_LAB(obj);
    if (!object_dynamic_cast(obj,MACHINE_TYPE_NAME("nikon-z8-bringup")) || s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp,"z8-video-data-model requires Z8 at machine creation");return;
    }
    s->z8_video_data_model=value;
    if (value && !s->z8_video_data) { s->z8_video_data=g_new0(Z8VideoData,1); }
}
static char *get_z8_video_data_state(Object *obj,Error **errp)
{
    NikonZ9State *s=NIKON_Z9_LAB(obj);Z8VideoData zero={0},*d=s->z8_video_data?s->z8_video_data:&zero;
    g_autofree char *base=g_strdup_printf("{\"enabled\":%s,\"packets\":%"PRIu64",\"decoded\":%"PRIu64
        ",\"packet_bytes\":%"PRIu64",\"receiver_acks\":%"PRIu64",\"decoder_acks\":%"PRIu64
        ",\"decode_rejects\":%"PRIu64",\"rejects\":%"PRIu64",\"epoch\":%"PRIu64",\"cancelled\":%"PRIu64
        ",\"packet_owned\":%s,\"frame_ready\":%s,\"row\":%u,\"pending\":[%u,%u],"
        "\"output_frames\":%"PRIu64",\"output_bytes\":%"PRIu64",\"ring_latches\":%"PRIu64",\"ring_waits\":%"PRIu64",\"ring_repeats\":%"PRIu64","
        "\"primary_pending\":%u,\"primary_acks\":%"PRIu64","
        "\"bank_written\":[%"PRIu64",%"PRIu64"],\"output_row\":%u,\"output_bank\":%u,\"selected_bank\":%u,"
        "\"width\":%u,\"height\":%u,\"capacity\":%u,\"recording_verified\":false,"
        "\"scope\":\"actual packed14 WLVR/zlib packet RAM; authored substitute, not Nikon codec\"}",
        s->z8_video_data_model?"true":"false",d->packets,d->decoded,d->packet_bytes,d->acks[0],d->acks[1],
        d->decode_rejects,d->rejects,d->epoch,d->cancelled,d->packet_owned?"true":"false",d->frame_ready?"true":"false",
        d->row,s->z8_front_pending[0],s->z8_front_pending[1],d->output_frames,d->output_bytes,d->ring_latches,d->ring_waits,d->ring_repeats,
        s->z8_video_primary_pending,s->z8_video_primary_acks,s->raw_pipeline_bank_written[0],s->raw_pipeline_bank_written[1],
        d->output_row,d->output_bank,s->engine1542_config[10][2]&1,d->width,d->height,d->capacity);
    GString *j=g_string_new_len(base,strlen(base)-1);
    g_string_append_printf(j,",\"front_modes\":[%u,%u],\"isp_video_owner\":%s",
        s->z8_front_mode[0],s->z8_front_mode[1],s->z8_video_isp_owner?"true":"false");
    g_string_append_printf(j,",\"stop_requests\":[%"PRIu64",%"PRIu64"],\"stop_ends\":[%"PRIu64",%"PRIu64"],"
        "\"stop_acks\":[%"PRIu64",%"PRIu64"],\"stop_cancelled\":[%"PRIu64",%"PRIu64"],"
        "\"refills\":[%"PRIu64",%"PRIu64"],\"refill_acks\":[%"PRIu64",%"PRIu64"],"
        "\"front_banks\":[%u,%u],\"front_commits\":[%"PRIu64",%"PRIu64"],"
        "\"front_valid\":[[%u,%u],[%u,%u]],\"front_written\":[[%u,%u],[%u,%u]],"
        "\"input_bank\":%u,\"input_bank_refills\":%"PRIu64",\"consumed_epoch\":%"PRIu64","
        "\"source_ends\":%"PRIu64",\"source_end_acks\":%"PRIu64",\"source_end_cancelled\":%"PRIu64",\"source_end_epoch\":%"PRIu64"}",
        d->stop_requests[0],d->stop_requests[1],d->stop_ends[0],d->stop_ends[1],d->stop_acks[0],d->stop_acks[1],
        d->stop_cancelled[0],d->stop_cancelled[1],d->refills[0],d->refills[1],d->refill_acks[0],d->refill_acks[1],
        d->front_bank[0],d->front_bank[1],d->front_commits[0],d->front_commits[1],
        d->front_valid[0][0],d->front_valid[0][1],d->front_valid[1][0],d->front_valid[1][1],
        d->front_written[0][0],d->front_written[0][1],d->front_written[1][0],d->front_written[1][1],
        d->input_bank,d->input_bank_refills,d->consumed_epoch,d->source_ends,d->source_end_acks,d->source_end_cancelled,d->source_end_epoch);
    return g_string_free(j,false);
}

static char *get_z8_video_interface_state(Object *obj, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    return g_strdup_printf("{\"enabled\":%s,\"unit1_reset\":%s,"
        "\"reset_asserts\":%" PRIu64 ",\"reset_releases\":%" PRIu64
        ",\"unit21_reset\":%s,\"unit21_reset_asserts\":%" PRIu64
        ",\"unit21_reset_releases\":%" PRIu64
        ",\"unit39_released\":%s"
        ",\"shared_reset_requests\":%" PRIu64
        ",\"clock_latched\":%u,\"clock_commits\":%" PRIu64
        ",\"task_pulses\":[%" PRIu64 ",%" PRIu64 ",%" PRIu64 "]"
        ",\"transferred_bytes\":0,\"completion_events\":0,"
        "\"scope\":\"configuration and gate requests; no audio, encoded data or recording\"}",
        s->z8_video_interface_model ? "true" : "false",
        s->primary20_reset_asserted ? "true" : "false",
        s->primary20_reset_asserts, s->primary20_reset_releases,
        s->primary21_reset_asserted ? "true" : "false",
        s->primary21_reset_asserts, s->primary21_reset_releases,
        s->yuvcmp_released ? "true" : "false",
        s->yuvcmp_local_resets,
        s->z8_video_clock_latched, s->z8_video_clock_commits,
        s->z8_video_task_pulses[0], s->z8_video_task_pulses[1], s->z8_video_task_pulses[2]);
}

static void set_z8_video_interface_model(Object *obj, bool value, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    if (!object_dynamic_cast(obj, MACHINE_TYPE_NAME("nikon-z8-bringup")) ||
        s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp, "z8-video-interface-model requires Z8 at machine creation");
        return;
    }
    s->z8_video_interface_model = value;
}


static char *get_engine151_control_state(Object *obj, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    GString *out = g_string_new(NULL);
    g_string_append_printf(out, "{\"enabled\":%s,\"pixel_bytes\":0,\"completion_events\":0,\"instances\":[",
                           s->engine151_control_model ? "true" : "false");
    for (unsigned i = 0; i < 2; i++) {
        bool comma = false;
        g_string_append_printf(out, "%s{\"base\":%u,\"ready\":%s,\"active\":%s,"
            "\"starts\":%" PRIu64 ",\"stops\":%" PRIu64 ",\"parameter_valid\":%" PRIu64 ",\"latched\":{",
            i ? "," : "", 0x15100000 + i * 0x40000,
            engine151_control_ready(s, i) ? "true" : "false",
            s->engine151_active[i] ? "true" : "false",
            s->engine151_starts[i], s->engine151_stops[i], s->engine151_parameter_valid[i]);
        for (unsigned j = 0; j < ARRAY_SIZE(s->engine151_latched[i]); j++) {
            if (s->engine151_latched[i][j]) {
                g_string_append_printf(out, "%s\"0x%x\":%u", comma ? "," : "", j * 4,
                                       s->engine151_latched[i][j]);
                comma = true;
            }
        }
        g_string_append(out, "}}");
    }
    g_string_append(out, "],\"scope\":\"functional start/stop and request snapshot; no parameter DMA, pixel processing, completion IRQ or physical timing proof\"}");
    return g_string_free(out, false);
}


static char *get_engine1518_state(Object *obj, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    GString *out = g_string_new("{\"processed_jobs\":0,\"completion_events\":0,\"instances\":[");
    for (unsigned i = 0; i < ARRAY_SIZE(s->engine1518); i++) {
        Z9Engine1518 *e = &s->engine1518[i];
        g_string_append_printf(out, "%s{\"drains\":%" PRIu64
            ",\"drain_acks\":%" PRIu64 ",\"irq\":%s,",
            i ? "," : "", e->drains, e->drain_acks,
            e->irq_level ? "true" : "false");
        g_string_append_printf(out, "\"pending\":%u,\"reset_pulses\":%" PRIu64
            ",\"lut_bank\":%u,\"lut_cursor\":%u,\"lut_selected\":%s,"
            "\"lut_low_pending\":%s,\"lut_low\":%u,\"lut_entries\":%" PRIu64 ",\"config\":[",
            e->pending, e->reset_pulses, e->lut_bank, e->lut_cursor,
            e->lut_selected ? "true" : "false", e->lut_low_pending ? "true" : "false",
            e->lut_low, e->lut_entries);
        for (unsigned j = 0; j < ARRAY_SIZE(e->config); j++) {
            g_string_append_printf(out, "%s%u", j ? "," : "", e->config[j]);
        }
        g_string_append(out, "],\"luts\":[");
        for (unsigned bank = 0; bank < 8; bank++) {
            g_string_append_printf(out, "%s[", bank ? "," : "");
            for (unsigned j = 0; j < 128; j++) {
                g_string_append_printf(out, "%s%" PRIu64, j ? "," : "", e->lut[bank][j]);
            }
            g_string_append_c(out, ']');
        }
        g_string_append(out, "]}");
    }
    g_string_append(out, "]}");
    return g_string_free(out, false);
}

