/* SPDX-License-Identifier: GPL-2.0-or-later
 * P21 virtual completion delta from integration U-HM5-01 (see p21/provenance.json).
 * Photo-only 1602 backend. Worker owns immutable host snapshots, never guest
 * memory or device state. BQL/main-loop completion validates again, writes and
 * reads back RAM, then publishes grid stages or the non-grid final event.
 */
#ifndef NIKON_Z8_STILL_HEVC_BACKEND_H
#define NIKON_Z8_STILL_HEVC_BACKEND_H
#include "nikon-z8-still-hevc-codec.h"
typedef struct Z8HEVCWork {
    NikonZ9State *owner;
    Z8StillHEVC config;
    Z8HEVCSpan spans[10];
    uint8_t *input[2];
    Z8HEVCLane lane[4];
    Z8HEVCTileReceipt tile[22];
    Z8HEVCEncoded encoded[22];
    char source_hash[2][65];
    uint64_t source_job,source_frame;
    unsigned completed,profile,total_frames;
    unsigned source_output,width,height,stride,tw,th,lanes,span_count,tile_count;
    ProbeSPS sps;
    gint cancel;
    QemuThread thread;
    QEMUBH *bh;
    QEMUTimer *completion_timer;
    const char *error;
} Z8HEVCWork;
typedef struct Z8HEVCEncodePart {
    Z8HEVCWork *job;const x265_api *api;unsigned part,completed;
    const char *error;QemuThread thread;
} Z8HEVCEncodePart;
static void z8_hevc_work_free(Z8HEVCWork *j)
{
    for(unsigned i=0;i<2;i++) { g_free(j->input[i]); }
    for(unsigned i=0;i<4;i++) { g_free(j->lane[i].data);g_free(j->lane[i].table); }
    if (j->bh) { qemu_bh_delete(j->bh); }
    if (j->completion_timer) { timer_del(j->completion_timer);timer_free(j->completion_timer); }
    object_unref(OBJECT(j->owner));g_free(j);
}
static void z8_hevc_backend_exit(Notifier *notifier,void *data)
{
    NikonZ9State *s=container_of(notifier,NikonZ9State,still_hevc_exit);
    Z8HEVCWork *j=s->still_hevc_backend.work;
    qemu_remove_exit_notifier(notifier);
    if(j) {
        g_atomic_int_set(&j->cancel,1);qemu_thread_join(&j->thread);
        s->still_hevc_backend.work=NULL;s->still_hevc_backend.busy=false;
        z8_hevc_work_free(j); /* Deletes the scheduled BH before device teardown. */
    }
}
static bool z8_hevc_source_ready(NikonZ9State *s,const Z8HEVCSpan *spans,unsigned count,
    unsigned index,unsigned width,unsigned height,unsigned stride)
{
    Z8HLGPipeline *p=&s->hlg_pipeline;
    if(index>=3 || (count!=4 && count!=10)) { return false; }
    for(unsigned i=2;i<count;i++)for(unsigned aux=0;aux<3;aux++)for(unsigned plane=0;plane<2;plane++) {
        if(spans[i].address<p->target[aux][plane]+p->bytes[aux][plane] &&
           p->target[aux][plane]<spans[i].address+spans[i].bytes) { return false; }
    }
    return p->enabled && p->jobs && p->source_frame && !p->requested && !p->owned && !p->failed && p->retired>=p->jobs &&
        p->source_job==s->engine151_capture_jobs && p->source_frame==s->hlg_capture.source_frame &&
        s->hlg_capture.last_output_hlg && p->geometry[index][0x2c/4]==width && p->geometry[index][0x30/4]==height &&
        p->output[index][0x38/4]==stride && p->output[index][0x3c/4]==stride &&
        p->target[index][0]==spans[0].address && p->target[index][1]==spans[1].address &&
        p->bytes[index][0]==spans[0].bytes && p->bytes[index][1]==spans[1].bytes &&
        strlen(p->output_sha256[index][0])==64 && strlen(p->output_sha256[index][1])==64;
}
static const char *z8_hevc_encode_index(Z8HEVCWork *j,const x265_api *api,unsigned i,uint16_t *tile)
{
    if(g_atomic_int_get(&j->cancel)) { return "cancelled"; }
    if(!z8_hevc_tile(j->input[0],j->input[1],j->width,j->height,j->stride,j->tw,j->th,
        i%11,i/11,tile,(size_t)j->tw*j->th*3/2)) { return "packed10-tile"; }
    z8_hevc_hash((uint8_t *)tile,(size_t)j->tw*j->th*3,j->tile[i].input_sha256);
    uint32_t control=z8_hevc_reg(&j->config,0x400),colour=z8_hevc_reg(&j->config,0x408);
    return j->source_output==0?z8_hevc_encode_tile(api,tile,j->total_frames,control,colour,&j->encoded[i],&j->tile[i]):
        z8_hevc_encode_image(api,tile,j->tw,j->th,j->total_frames,control,colour,&j->encoded[i],&j->tile[i]);
}
static gpointer z8_hevc_encode_part(gpointer opaque)
{
    Z8HEVCEncodePart *p=opaque;uint16_t *tile=g_try_malloc(768u*2752u*3u);
    if(!tile) { p->error="host-allocation";return NULL; }
    for(unsigned i=1+p->part;i<p->job->tile_count;i+=4) {
        p->error=z8_hevc_encode_index(p->job,p->api,i,tile);
        if(p->error) { break; }
        p->completed++;
    }
    g_free(tile);return NULL;
}
static gpointer z8_hevc_worker(gpointer opaque)
{
    Z8HEVCWork *j=opaque;const x265_api *api=x265_api_get(10);
    uint16_t *tile=NULL;Z8HEVCEncodePart parts[4]={0};
    j->error="x265-10bit-api-unavailable";
    if(!api || api->bit_depth!=10 || (size_t)api->sizeof_param!=sizeof(x265_param) ||
       (size_t)api->sizeof_picture!=sizeof(x265_picture) || api->api_build_number!=X265_BUILD) { goto done; }
    j->error="host-little-endian-required";
    if(G_BYTE_ORDER!=G_LITTLE_ENDIAN) { goto done; }
    tile=g_try_malloc((size_t)j->tw*j->th*3);
    j->error="host-allocation";if(!tile) { goto done; }
    j->total_frames=0; /* Prefer actual SPS Main10; record a checked RExt fallback. */
    j->error=z8_hevc_encode_index(j,api,0,tile);
    if(j->error && !strcmp(j->error,"codec-open")) {
        j->total_frames=1;j->error=z8_hevc_encode_index(j,api,0,tile);
    }
    if(j->error) { goto done; }
    j->completed=1;j->sps=j->encoded[0].sps;j->profile=j->sps.profile_idc;
    /* Four bounded host encoders, all identical CTU configuration. Avoid a
     * serial 22-picture host delay consuming the original guest OS timeout;
     * never change guest clocks or its timeout/return state. */
    for(unsigned p=0;p<(j->tile_count>1?4u:0u);p++) {
        parts[p].job=j;parts[p].api=api;parts[p].part=p;
        qemu_thread_create(&parts[p].thread,"z8-hevc-tile",z8_hevc_encode_part,&parts[p],QEMU_THREAD_JOINABLE);
    }
    for(unsigned p=0;p<(j->tile_count>1?4u:0u);p++) {
        qemu_thread_join(&parts[p].thread);j->completed+=parts[p].completed;
        if(parts[p].error) { j->error=parts[p].error; }
    }
    if(j->error) { goto done; }
    Z8HEVCEncoded *reference=&j->encoded[0];
    for(unsigned l=0;l<j->lanes;l++)for(unsigned p=0;p<3;p++) {
        if(!z8_hevc_lane_add(&j->lane[l],32+p,reference->parameter[p],reference->parameter_bytes[p])) {
            j->error="lane-parameter-capacity";goto done;
        }
    }
    for(unsigned i=0;i<j->tile_count;i++) {
        Z8HEVCEncoded *e=&j->encoded[i];
        if(j->lanes==4 && !z8_hevc_kib_limit(e->image_bytes,z8_hevc_reg(&j->config,0xf54))) {
            j->error="tile-byte-limit";goto done;
        }
        for(unsigned p=0;p<3;p++) {
            if(e->parameter_bytes[p]!=reference->parameter_bytes[p] ||
               memcmp(e->parameter[p],reference->parameter[p],reference->parameter_bytes[p])) {
                j->error="inconsistent-parameter-sets";goto done;
            }
        }
        unsigned slot=z8_hevc_slot(i);
        if(!z8_hevc_lane_add(&j->lane[slot%4],19,e->image,e->image_bytes) ||
           (i%11==10 && !z8_hevc_lane_add(&j->lane[(slot+1)%4],19,e->image,e->image_bytes))) {
            j->error="lane-image-capacity";goto done;
        }
    }
    uint64_t total=0;
    for(unsigned l=0;l<j->lanes;l++) {
        if(j->lane[l].count!=(j->lanes==4?9u:4u)) { j->error="lane-record-count";goto done; }
        total+=j->lane[l].used;
    }
    if(!z8_hevc_kib_limit(total,z8_hevc_reg(&j->config,0xf50))) { j->error="total-byte-limit";goto done; }
    j->error=NULL;
done:
    g_free(tile);for(unsigned i=0;i<22;i++) { z8_hevc_encoded_free(&j->encoded[i]); }
    /* Legacy mode publishes via host-scheduled BH. Virtual mode has exactly
     * one owner: its already armed virtual timer, which joins this worker. */
    if (j->bh) { qemu_bh_schedule(j->bh); }
    return NULL;
}
static void z8_hevc_complete(void *opaque)
{
    Z8HEVCWork *j=opaque;NikonZ9State *s=j->owner;
    Z8HEVCBackend *b=&s->still_hevc_backend;Z8StillHEVC *h=&s->still_hevc;
    qemu_thread_join(&j->thread);
    const char *error=j->error;
    if(g_atomic_int_get(&j->cancel)) { goto cancelled; }
    if(error) { goto failed; }
    error="source-or-config-changed-before-commit";
    if(s->stopped_unknown || !h->initialized || !h->prepared ||
       !z8_hevc_source_ready(s,j->spans,j->span_count,j->source_output,j->width,j->height,j->stride) || s->hlg_pipeline.jobs!=j->source_job ||
       s->hlg_pipeline.source_frame!=j->source_frame) { goto failed; }
    for(unsigned off=0;off<0x1000;off+=4) {
        /* Mask/global gating may change while a real DMA job is in flight.
         * They control the line, not data ownership or completion pending. */
        if(off!=8 && off!=0x128 && off!=0xfe8 && h->reg[off/4]!=j->config.reg[off/4]) { goto failed; }
    }
    /* Validate every input and every capacity before any output write. */
    for(unsigned i=0;i<j->span_count;i++) {
        if(!dma_ram_range(s,j->spans[i].address,j->spans[i].bytes)) { error="ram-span-changed";goto failed; }
    }
    for(unsigned i=0;i<2;i++) {
        char hash[65];
        if(!z8_hlg_hash_ram(s,j->spans[i].address,j->spans[i].bytes,hash) || strcmp(hash,j->source_hash[i])) {
            error="source-hash-changed-before-commit";goto failed;
        }
    }
    for(unsigned l=0;l<j->lanes;l++) {
        Z8HEVCLane *lane=&j->lane[l];
        error="output-dma-or-readback";
        if(address_space_write(&address_space_memory,j->spans[2+2*l].address,MEMTXATTRS_UNSPECIFIED,lane->data,lane->used)!=MEMTX_OK ||
           address_space_write(&address_space_memory,j->spans[3+2*l].address,MEMTXATTRS_UNSPECIFIED,lane->table,lane->table_capacity)!=MEMTX_OK) { goto failed; }
        char expected[65],actual[65];z8_hevc_hash(lane->data,lane->used,expected);
        if(!z8_hlg_hash_ram(s,j->spans[2+2*l].address,lane->used,actual) || strcmp(expected,actual)) { goto failed; }
        g_strlcpy(b->lane_sha256[l],actual,65);z8_hevc_hash(lane->table,lane->table_capacity,expected);
        if(!z8_hlg_hash_ram(s,j->spans[3+2*l].address,lane->table_capacity,actual) || strcmp(expected,actual)) { goto failed; }
        g_strlcpy(b->table_sha256[l],actual,65);
    }
    /* All data are real and verified before these control/result publications. */
    for(unsigned l=0;l<j->lanes;l++) {
        b->lane_bytes[l]=j->lane[l].used;b->lane_records[l]=j->lane[l].count;
        b->dma_bytes+=j->lane[l].used+j->lane[l].table_capacity;
    }
    memcpy(b->tiles,j->tile,sizeof(b->tiles));b->profile_idc=j->profile;b->total_frames=j->total_frames;
    b->level_idc=j->sps.level_idc;b->compatibility=j->sps.compatibility;b->constraints=j->sps.constraints;
    b->jobs++;b->result_valid=true;b->failed=false;b->error=NULL;
    Z8HEVCHistory *receipt=&b->history[j->source_output];
    receipt->job=b->jobs;receipt->source_job=j->source_job;receipt->frame=j->source_frame;
    receipt->width=j->width;receipt->height=j->height;receipt->lanes=j->lanes;
    receipt->tile_count=j->tile_count;receipt->profile_idc=j->profile;receipt->observed=b->observed_profile;
    memcpy(receipt->input_sha256,b->input_sha256,sizeof(receipt->input_sha256));
    memcpy(receipt->lane_sha256,b->lane_sha256,sizeof(receipt->lane_sha256));
    memcpy(receipt->table_sha256,b->table_sha256,sizeof(receipt->table_sha256));
    memcpy(receipt->lane_bytes,b->lane_bytes,sizeof(receipt->lane_bytes));
    memcpy(receipt->lane_records,b->lane_records,sizeof(receipt->lane_records));
    memcpy(receipt->tiles,b->tiles,sizeof(receipt->tiles));
    if (b->virtual_completion) {
        b->committed_virtual_ns=qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    }
    h->pending|=z8_hevc_completion_mask(j->lanes);
    b->completion_irqs+=z8_hevc_irq(h);qemu_set_irq(s->still_hevc_irq,z8_hevc_irq(h));
    fprintf(stderr,"Z8_STILL_HEVC job=%" PRIu64 " c1=%" PRIu64 " frame=%" PRIu64
        " output=%u geometry=%ux%u tiles=%u transport_slots=%u profile=%u raw_type=20 emitted_type=19 bytes=%u,%u,%u,%u\n",
        b->jobs,b->source_job,b->source_frame,b->source_output,b->width,b->height,b->tile_count,b->transport_slots,
        b->profile_idc,b->lane_bytes[0],b->lane_bytes[1],b->lane_bytes[2],b->lane_bytes[3]);
    goto cancelled;
failed:
    b->failed=true;b->error=error;b->result_valid=false;
    memcpy(b->tiles,j->tile,sizeof(b->tiles));
    z8_hevc_fail(h,error);
    fprintf(stderr,"Z8_STILL_HEVC_BACKEND_REJECT reason=%s tiles_encoded=%u; no completion published\n",error,j->completed);
    unknown_access(s,0x16020100,1,4,true);
cancelled:
    b->busy=false;b->work=NULL;z8_hevc_work_free(j);
}
static void z8_hevc_profile_diagnostic(const Z8StillHEVC *h)
{
    uint32_t geometry=z8_hevc_reg(h,0x310);bool main=geometry==0x15802040,small=geometry==0x00d40140;
    const Z8HEVCWrite *profile=main?z8_hevc_run06:z8_hevc_run07;
    unsigned count=main?sizeof(z8_hevc_run06)/sizeof(z8_hevc_run06[0]):sizeof(z8_hevc_run07)/sizeof(z8_hevc_run07[0]);
    uint64_t expected[16]={0};
    for(unsigned i=0;i<count;i++) {
        unsigned off=profile[i].off;uint32_t value=z8_hevc_aux_value(off,profile[i].value,small);
        expected[(off/4)/64]|=UINT64_C(1)<<((off/4)%64);
        if(!z8_hevc_seen(h,off) || z8_hevc_reg(h,off)!=value) {
            fprintf(stderr,"Z8_STILL_HEVC_PROFILE_MISMATCH geometry=%08x off=%03x expected=%08x actual=%08x written=%u\n",
                geometry,off,value,z8_hevc_reg(h,off),z8_hevc_seen(h,off));return;
        }
    }
    for(unsigned off=0;off<0x1000;off+=4) {
        if(z8_hevc_seen(h,off) && !(expected[(off/4)/64]&(UINT64_C(1)<<((off/4)%64)))) {
            fprintf(stderr,"Z8_STILL_HEVC_PROFILE_MISMATCH extra-written-offset=%03x\n",off);return;
        }
    }
    fprintf(stderr,"Z8_STILL_HEVC_PROFILE_MISMATCH initialized=%u prepared=%u span_count=%u\n",
        h->initialized,h->prepared,z8_hevc_spans(h,(Z8HEVCSpan[10]){{0}}));
}
static bool z8_hevc_backend_start(NikonZ9State *s)
{
    Z8StillHEVC *h=&s->still_hevc;Z8HEVCBackend *b=&s->still_hevc_backend;Z8HEVCSpan spans[10]={0};
    h->start_attempts++;
    if(b->busy || b->result_valid || b->failed || h->pending || h->errors) { return z8_hevc_fail(h,"backend-busy-result-or-failed"); }
    int index=z8_hevc_profile_index(h);
    unsigned count=z8_hevc_spans(h,spans),lanes=index==0?4:1;
    if(index<0 || count!=2+2*lanes) { z8_hevc_profile_diagnostic(h);return z8_hevc_fail(h,"backend-unobserved-profile"); }
    unsigned width=z8_hevc_reg(h,0x310)&0xffff,height=z8_hevc_reg(h,0x310)>>16,stride=z8_hevc_reg(h,0xf10);
    if(!z8_hevc_source_ready(s,spans,count,index,width,height,stride) ||
       s->hlg_pipeline.jobs<=b->consumed_jobs[index]) { return z8_hevc_fail(h,"backend-source-ownership"); }
    for(unsigned i=0;i<count;i++) {
        if(!dma_ram_range(s,spans[i].address,spans[i].bytes)) { return z8_hevc_fail(h,"backend-ram-span"); }
    }
    Z8HEVCWork *j=g_new0(Z8HEVCWork,1);
    j->owner=s;j->config=*h;memcpy(j->spans,spans,sizeof(spans));
    j->source_output=index;j->width=width;j->height=height;j->stride=stride;j->lanes=lanes;j->span_count=count;
    j->tw=index==0?768:width;j->th=index==0?2752:height;j->tile_count=index==0?22:1;
    j->source_job=s->hlg_pipeline.jobs;j->source_frame=s->hlg_pipeline.source_frame;
    b->source_job=j->source_job;b->source_frame=j->source_frame;
    b->source_output=index;b->width=width;b->height=height;b->lane_count=lanes;
    b->tile_count=j->tile_count;b->transport_slots=index==0?24:1;b->observed_profile=index!=1;
    memset(b->lane_bytes,0,sizeof(b->lane_bytes));memset(b->lane_records,0,sizeof(b->lane_records));
    memset(b->lane_sha256,0,sizeof(b->lane_sha256));memset(b->table_sha256,0,sizeof(b->table_sha256));
    memset(b->tiles,0,sizeof(b->tiles));
    memset(b->input_sha256,0,sizeof(b->input_sha256));
    object_ref(OBJECT(s));
    if (b->virtual_completion) {
        j->completion_timer=timer_new_ns(QEMU_CLOCK_VIRTUAL,z8_hevc_complete,j);
    } else {
        j->bh=qemu_bh_new(z8_hevc_complete,j);
    }
    const char *error="backend-input-allocation-or-read";
    for(unsigned i=0;i<2;i++) {
        j->input[i]=g_try_malloc(spans[i].bytes);
        if(!j->input[i] || address_space_read(&address_space_memory,spans[i].address,MEMTXATTRS_UNSPECIFIED,j->input[i],spans[i].bytes)!=MEMTX_OK) { goto fail; }
        z8_hevc_hash(j->input[i],spans[i].bytes,j->source_hash[i]);
        g_strlcpy(b->input_sha256[i],j->source_hash[i],65);
        if(strcmp(j->source_hash[i],s->hlg_pipeline.output_sha256[index][i])) { error="backend-source-hash";goto fail; }
    }
    error="backend-output-allocation";
    for(unsigned i=0;i<lanes;i++) {
        j->lane[i]=(Z8HEVCLane){g_try_malloc(spans[2+i*2].bytes),g_try_malloc0(spans[3+i*2].bytes),
            spans[2+i*2].bytes,spans[3+i*2].bytes,0,0};
        if(!j->lane[i].data || !j->lane[i].table) { goto fail; }
    }
    b->busy=true;b->work=j;
    qemu_thread_create(&j->thread,"z8-still-hevc",z8_hevc_worker,j,QEMU_THREAD_JOINABLE);
    if (j->completion_timer) {
        /* Functional, not physical-latency, model. At the current virtual
         * deadline the BQL timer callback joins the real encoder. Host compute
         * may block that callback, but must not consume guest timeout ticks.
         * No completion is published until existing source/ownership checks,
         * real encode, DMA and readback have all succeeded. No clock is set,
         * rewound or advanced here, and no measured hardware delay is claimed. */
        b->scheduled_virtual_ns=qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
        timer_mod_ns(j->completion_timer,b->scheduled_virtual_ns);
    }
    b->consumed_job=j->source_job;b->source_job=j->source_job;b->source_frame=j->source_frame;
    b->consumed_jobs[index]=j->source_job;
    memcpy(b->input_sha256,j->source_hash,sizeof(b->input_sha256));h->writes++;return true;
fail:
    z8_hevc_work_free(j);return z8_hevc_fail(h,error);
}
static void z8_hevc_backend_control(NikonZ9State *s,unsigned off,uint32_t value,uint32_t before)
{
    Z8HEVCBackend *b=&s->still_hevc_backend;
    if((off==0x84 || off==0xff4) && value==1) {
        if(b->work) { g_atomic_int_set(&((Z8HEVCWork *)b->work)->cancel,1);b->cancels++; }
        b->result_valid=false;b->failed=false;b->error=NULL;
    }
    if(off==0x120 && b->result_valid) {
        b->stage1_acks+=!!(before&value&0x40000);b->stage2_acks+=!!(before&value&0x20000);
    }
}
static bool z8_hevc_backend_read(NikonZ9State *s,unsigned off,uint32_t *value)
{
    Z8HEVCBackend *b=&s->still_hevc_backend;
    if(!b->enabled || !b->result_valid) { return false; }
    switch(off) {
    case 0x110: *value=0;return true; /* Verified finite job, all output DMA retired. */
    case 0x920: if(b->lane_count!=4) { return false; } *value=(2u<<16)|12;return true;
    case 0x900: *value=b->lane_records[0]|b->lane_records[1]<<16;return true;
    case 0x904: *value=b->lane_records[2]|b->lane_records[3]<<16;return true;
    case 0xf60: case 0xf64: case 0xf68: case 0xf6c:
        *value=b->lane_bytes[(off-0xf60)/4];return true;
    default:return false;
    }
}
static bool get_z8_still_hevc_x265(Object *obj,Error **errp)
{ return NIKON_Z9_LAB(obj)->still_hevc_backend.enabled; }
static void set_z8_still_hevc_x265(Object *obj,bool value,Error **errp)
{
    NikonZ9State *s=NIKON_Z9_LAB(obj);
    if(s->image_ram.ram || s->image_ram.rom_device) { error_setg(errp,"z8-still-hevc-x265-model is immutable after realization");return; }
    s->still_hevc_backend.enabled=value;
}
static bool get_z8_still_hevc_virtual_completion(Object *obj,Error **errp)
{ return NIKON_Z9_LAB(obj)->still_hevc_backend.virtual_completion; }
static void set_z8_still_hevc_virtual_completion(Object *obj,bool value,Error **errp)
{
    NikonZ9State *s=NIKON_Z9_LAB(obj);
    if(s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp,"z8-still-hevc-virtual-completion-model is immutable after realization");return;
    }
    s->still_hevc_backend.virtual_completion=value;
}
static char *get_z8_still_hevc_codec_state(Object *obj,Error **errp)
{
    Z8HEVCBackend *b=&NIKON_Z9_LAB(obj)->still_hevc_backend;
    GString *out=g_string_new(NULL);
    g_string_append_printf(out,"{\"enabled\":%s,\"busy\":%s,\"result_valid\":%s,\"failed\":%s,\"jobs\":%" PRIu64
        ",\"dma_bytes\":%" PRIu64 ",\"source_c1_job\":%" PRIu64 ",\"source_frame\":%" PRIu64
        ",\"consumed_c1_job\":%" PRIu64 ",\"cancels\":%" PRIu64 ",\"stage1_acks\":%" PRIu64 ",\"stage2_acks\":%" PRIu64
        ",\"profile_idc\":%u,\"level_idc\":%u,\"compatibility_flags\":%u,\"constraint_flags_hex\":\"%012" PRIx64 "\","
        "\"total_frames_parameter\":%u,\"input_sha256\":[\"%s\",\"%s\"],\"last_error\":\"%s\","
        "\"source_c1_output\":%u,\"width\":%u,\"height\":%u,\"native_profile_observed\":%s,"
        "\"logical_tiles\":%u,\"transport_slots\":%u,\"transport_padding_reuses\":%s,"
        "\"padding_rule\":\"right-edge sample replication; invisible slots reuse last tile\","
        "\"bit_order_verified\":false,\"uv_order_verified\":false,\"padding_verified\":false,\"lane_report_units_verified\":false,"
        "\"irq_timing_verified\":false,\"colorimetry_mapping_verified\":false,"
        "\"vui_mapping\":\"native C2b=1; 400=1d800,408=91209\","
        "\"colorimetry\":\"bt2020/arib-std-b67/bt2020nc full\",\"lanes\":[",
        b->enabled?"true":"false",b->busy?"true":"false",b->result_valid?"true":"false",b->failed?"true":"false",
        b->jobs,b->dma_bytes,b->source_job,b->source_frame,b->consumed_job,b->cancels,b->stage1_acks,b->stage2_acks,
        b->profile_idc,b->level_idc,b->compatibility,b->constraints,b->total_frames,b->input_sha256[0],b->input_sha256[1],b->error?b->error:"",
        b->source_output,b->width,b->height,b->observed_profile?"true":"false",b->tile_count,b->transport_slots,
        b->lane_count==4?"[{\"slot\":11,\"tile\":10},{\"slot\":23,\"tile\":21}]":"[]");
    for(unsigned l=0;l<4;l++) {
        g_string_append_printf(out,"%s{\"bytes\":%u,\"records\":%u,\"sha256\":\"%s\",\"table_sha256\":\"%s\"}",
            l?",":"",b->lane_bytes[l],b->lane_records[l],b->lane_sha256[l],b->table_sha256[l]);
    }
    g_string_append(out,"],\"tiles\":[");
    for(unsigned i=0;i<b->tile_count;i++) {
        Z8HEVCTileReceipt *r=&b->tiles[i];
        g_string_append_printf(out,"%s{\"tile\":%u,\"slot\":%u,\"bytes\":%u,\"input_sha256\":\"%s\","
            "\"raw_bytes\":%u,\"raw_prefix\":%u,\"encoder_api_type\":%s,\"encoder_header_type\":%s,\"emitted_header_type\":%s,"
            "\"nal_header_transform\":{\"from\":20,\"to\":19},"
            "\"single_picture_no_leading_verified\":%s,\"before_sha256\":\"%s\",\"after_sha256\":\"%s\",\"dma_nal_sha256\":\"%s\"}",
            i?",":"",i,z8_hevc_slot(i),r->bytes,r->input_sha256,r->raw_bytes,r->raw_prefix,
            r->bytes?"20":"null",r->bytes?"20":"null",r->bytes?"19":"null",
            r->bytes?"true":"false",r->before_sha256,r->after_sha256,r->dma_nal_sha256);
    }
    g_string_append(out,"],\"history\":[");
    bool comma=false;
    for(unsigned index=0;index<3;index++) {
        Z8HEVCHistory *r=&b->history[index];if(!r->job) { continue; }
        g_string_append_printf(out,"%s{\"source_c1_output\":%u,\"job\":%" PRIu64 ",\"source_c1_job\":%" PRIu64
            ",\"source_frame\":%" PRIu64 ",\"width\":%u,\"height\":%u,\"profile_idc\":%u,"
            "\"native_profile_observed\":%s,\"input_sha256\":[\"%s\",\"%s\"],\"lanes\":[",
            comma?",":"",index,r->job,r->source_job,r->frame,r->width,r->height,r->profile_idc,
            r->observed?"true":"false",r->input_sha256[0],r->input_sha256[1]);comma=true;
        for(unsigned l=0;l<r->lanes;l++) {
            g_string_append_printf(out,"%s{\"bytes\":%u,\"records\":%u,\"sha256\":\"%s\",\"table_sha256\":\"%s\"}",
                l?",":"",r->lane_bytes[l],r->lane_records[l],r->lane_sha256[l],r->table_sha256[l]);
        }
        g_string_append(out,"],\"tiles\":[");
        for(unsigned i=0;i<r->tile_count;i++) {
            Z8HEVCTileReceipt *t=&r->tiles[i];
            g_string_append_printf(out,"%s{\"tile\":%u,\"bytes\":%u,\"raw_bytes\":%u,\"raw_prefix\":%u,"
                "\"input_sha256\":\"%s\",\"before_sha256\":\"%s\",\"after_sha256\":\"%s\",\"dma_nal_sha256\":\"%s\","
                "\"encoder_api_type\":20,\"encoder_header_type\":20,\"emitted_header_type\":19,"
                "\"nal_header_transform\":{\"from\":20,\"to\":19},\"single_picture_no_leading_verified\":true}",
                i?",":"",i,t->bytes,t->raw_bytes,t->raw_prefix,t->input_sha256,t->before_sha256,t->after_sha256,t->dma_nal_sha256);
        }
        g_string_append(out,"]}");
    }
    g_string_append(out,"]");
    if (b->virtual_completion) {
        g_string_append_printf(out,",\"virtual_completion\":{\"enabled\":true,"
            "\"clock\":\"QEMU_CLOCK_VIRTUAL\",\"scheduled_ns\":%" PRId64
            ",\"committed_ns\":%" PRId64 ",\"physical_latency_verified\":false}",
            b->scheduled_virtual_ns,b->committed_virtual_ns);
    }
    g_string_append(out,"}");return g_string_free(out,false);
}
#endif
