/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Photo C1 hook only. No video ring ownership, stop IRQ or new MMIO window. */
static bool z8_hlg_c1_reject(NikonZ9State *s, const char *reason)
{
    s->hlg_pipeline.rejects++;s->hlg_pipeline.last_error=reason;return false;
}

static void z8_hlg_c1_retire(NikonZ9State *s)
{
    Z8HLGPipeline *h=&s->hlg_pipeline;
    if(h->owned && !(s->engine1518[0].pending&(1u<<20)) &&
        !(s->engine1542_pending[1]&0x11) && !(s->engine1542_pending[11]&0x20)) {
        h->owned=false;h->retired++;
    }
}

static void z8_hlg_c1_cancel(NikonZ9State *s)
{
    Z8HLGPipeline *h=&s->hlg_pipeline;
    if(h->requested) { h->requested=false;h->cancels++; }
    /* Native status/W1C/reset remain the only pending-bit owners. */
    z8_hlg_c1_retire(s);
}

static bool z8_hlg_c1_prepare(NikonZ9State *s)
{
    Z8HLGPipeline *h=&s->hlg_pipeline;
    Z8HLGCapture *producer=&s->hlg_capture;
    Z9Engine1518 *e=&s->engine1518[0];
    uint32_t b[3][0x100/4], f[3][0x100/4];
    g_autofree char *lut_hash=NULL;
    if(!h->enabled) { return z8_hlg_c1_reject(s,"disabled"); }
    z8_hlg_c1_retire(s);
    if(h->requested || h->owned || (e->pending&(1u<<20)) ||
       (s->engine1542_pending[1]&0x11) || (s->engine1542_pending[11]&0x20)) {
        return z8_hlg_c1_reject(s,"request_or_pending_not_retired");
    }
    for(unsigned j=0;j<3;j++) {
        memcpy(b[j],s->engine1542_config[6+j],sizeof(b[j]));
        memcpy(f[j],s->engine1542_config[18+j],sizeof(f[j]));
    }
    if(!z8_hlg_c1_profile(e->config,s->engine1542_config[1],s->engine1542_config[11],b,f) ||
       s->engine1542_config[11][1]!=0x103 || e->config[0x30/4]!=0 ||
       !s->engine1543_capture_control_model || s->engine1543_valid[1]!=15 ||
       s->engine1543_buffer_valid[1]!=UINT64_C(0x000f000f000f000f) ||
       e->config[8/4]!=0x39f00000 || s->engine1542_config[1][0x18/4]!=0x11111 ||
       s->engine1542_config[11][0x10/4]!=0x20) { return z8_hlg_c1_reject(s,"unobserved_photo_profile"); }
    if(!e->reset_pulses || !e->lut_selected || e->lut_low_pending || e->lut_bank!=7 ||
       e->lut_cursor!=128 || e->lut_entries<1024) { return z8_hlg_c1_reject(s,"incomplete_lut_programming"); }
    uint8_t lut_bytes[8*128*8];
    for(unsigned bank=0;bank<8;bank++) for(unsigned n=0;n<128;n++) for(unsigned k=0;k<8;k++) {
        lut_bytes[(bank*128+n)*8+k]=(uint8_t)(e->lut[bank][n]>>(8*k));
    }
    lut_hash=g_compute_checksum_for_data(G_CHECKSUM_SHA256,lut_bytes,sizeof(lut_bytes));
    if(strcmp(lut_hash,Z8_HLG_C1_LUT_SHA256)) { return z8_hlg_c1_reject(s,"unobserved_lut"); }
    if(!producer->last_output_hlg || producer->plan.output_bits!=12 ||
       producer->plan.ow!=8256 || producer->plan.oh!=5504 || producer->plan.stride!=12384 ||
       !producer->jobs || !s->engine151_capture_jobs || s->engine151_capture_pending ||
       s->engine151_capture_jobs==h->consumed151_job || producer->source_frame!=s->primary_d980_frames) {
        return z8_hlg_c1_reject(s,"producer_not_complete_or_fresh");
    }
    NBSpan spans[8];
    spans[0]=(NBSpan){z8_hlg_address(e->config,0x100),producer->plan.plane_y};
    spans[1]=(NBSpan){z8_hlg_address(e->config,0x108),producer->plan.plane_uv};
    if(spans[0].pa!=producer->y || spans[1].pa!=producer->uv ||
       spans[0].pa!=s->engine151_capture_y || spans[1].pa!=s->engine151_capture_uv) {
        return z8_hlg_c1_reject(s,"producer_address_mismatch");
    }
    for(unsigned j=0;j<3;j++) {
        uint64_t bytes=(uint64_t)b[j][0x38/4]*f[j][0x30/4];
        spans[2+j*2]=(NBSpan){z8_hlg_address(b[j],0x20),bytes};
        spans[3+j*2]=(NBSpan){z8_hlg_address(b[j],0x28),bytes/2};
    }
    for(unsigned j=0;j<8;j++) {
        if(!z8_hlg_span(s,spans[j].pa,spans[j].bytes) ||
           (j>=2 && !nb_writable(&s->output_owner,spans[j]))) { return z8_hlg_c1_reject(s,"dma_bounds_or_owner"); }
        for(unsigned k=0;k<j;k++) if(nb_overlap(spans[j],spans[k])) { return z8_hlg_c1_reject(s,"dma_overlap"); }
    }
    memcpy(h->source,e->config,sizeof(h->source));memcpy(h->lut,e->lut,sizeof(h->lut));
    memcpy(h->a,s->engine1542_config[1],sizeof(h->a));
    memcpy(h->c,s->engine1542_config[11],sizeof(h->c));
    memcpy(h->output,b,sizeof(b));memcpy(h->geometry,f,sizeof(f));
    for(unsigned j=0;j<2;j++) { h->input[j]=spans[j].pa; }
    for(unsigned j=0;j<3;j++) for(unsigned k=0;k<2;k++) {
        h->target[j][k]=spans[2+j*2+k].pa;h->bytes[j][k]=spans[2+j*2+k].bytes;
    }
    g_strlcpy(h->input_sha256[0],producer->y_sha256,65);g_strlcpy(h->input_sha256[1],producer->uv_sha256,65);
    g_strlcpy(h->lut_sha256,lut_hash,65);memset(h->output_sha256,0,sizeof(h->output_sha256));
    h->source_job=s->engine151_capture_jobs;h->source_frame=producer->source_frame;
    h->requests++;h->requested=true;h->failed=false;h->last_error=NULL;
    return true;
}

static void z8_hlg_c1_try(NikonZ9State *s)
{
    Z8HLGPipeline *h=&s->hlg_pipeline;
    if(!h->enabled || !h->requested || h->failed || !s->engine1543_active[1] ||
       s->engine1543_held[1] || !s->engine1518[0].config[0x30/4]) { return; }
    const char *failure="source_generation_changed";
    uint8_t *input[2]={NULL,NULL}, *out[3][2]={{NULL,NULL},{NULL,NULL},{NULL,NULL}};
    uint64_t input_size=UINT64_C(12384)*5504;
    if(!s->hlg_capture.last_output_hlg || s->engine151_capture_jobs!=h->source_job ||
       s->hlg_capture.source_frame!=h->source_frame ||
       memcmp(h->lut,s->engine1518[0].lut,sizeof(h->lut))) { goto done; }
    for(unsigned k=0;k<2;k++) {
        input[k]=g_try_malloc(input_size);
        failure="source_read";
        if(!input[k] || address_space_read(&address_space_memory,h->input[k],MEMTXATTRS_UNSPECIFIED,input[k],input_size)!=MEMTX_OK) { goto done; }
        char *hash=g_compute_checksum_for_data(G_CHECKSUM_SHA256,input[k],input_size);
        bool same=!strcmp(hash,h->input_sha256[k]);
        g_free(hash);
        failure="source_hash_mismatch";
        if(!same) { goto done; }
    }
    for(unsigned j=0;j<3;j++) {
        out[j][0]=g_try_malloc0(h->bytes[j][0]);out[j][1]=g_try_malloc0(h->bytes[j][1]);
        failure="output_allocation_or_sampling";
        if(!out[j][0] || !out[j][1] || !z8_hlg_c1_scale(input[0],input[1],8256,5504,12384,
                h->geometry[j],h->output[j][0x38/4],out[j][0],out[j][1])) { goto done; }
    }
    /* CPU cannot observe partial success. All destinations validated and all
     * six outputs built first; faults during a DMA remain partial failures. */
    if(s->producer_journal.enabled) {
        nj_unknown(&s->producer_journal,qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL));s->producer_journal.gap=true;
    }
    for(unsigned j=0;j<3;j++) for(unsigned k=0;k<2;k++) {
        failure="partial_dma_or_readback";
        if(address_space_write(&address_space_memory,h->target[j][k],MEMTXATTRS_UNSPECIFIED,out[j][k],h->bytes[j][k])!=MEMTX_OK ||
           !z8_hlg_hash_ram(s,h->target[j][k],h->bytes[j][k],h->output_sha256[j][k])) { goto done; }
        char *expected=g_compute_checksum_for_data(G_CHECKSUM_SHA256,out[j][k],h->bytes[j][k]);
        bool same=!strcmp(expected,h->output_sha256[j][k]);
        g_free(expected);
        if(!same) { goto done; }
    }
    h->jobs++;h->consumed151_job=h->source_job;h->owned=true;h->requested=false;
    /* Same photo subscriptions as native NV16 path; not a video ring STOP.
     * A1 boundaries, C1 output done and 1518 source done only after readback. */
    s->engine1542_pending[1]|=0x11;s->engine1542_pending[11]|=0x20;
    s->engine1518[0].pending|=1u<<20;
    raw_pipeline_irq_update(s);engine1518_update_irq(&s->engine1518[0]);
    fprintf(stderr,"Z8_HLG_C1 job=%" PRIu64 " source151=%" PRIu64 " frame=%" PRIu64
        " main=%" PRIx64 "/%" PRIx64 " outputs=8256x5504,320x212,1620x1080 bits=10; readback before IRQ, packing/filter hypotheses\n",
        h->jobs,h->source_job,h->source_frame,h->target[0][0],h->target[0][1]);
    failure=NULL;
done:
    for(unsigned k=0;k<2;k++) { g_free(input[k]); }
    for(unsigned j=0;j<3;j++) for(unsigned k=0;k<2;k++) { g_free(out[j][k]); }
    if(failure) { h->requested=false;h->failed=true;z8_hlg_c1_reject(s,failure); }
}

static bool z8_hlg_c1_source_locked(NikonZ9State *s, unsigned off, uint64_t value)
{
    Z8HLGPipeline *h=&s->hlg_pipeline;
    if(!h->requested && !h->owned) { return false; }
    if(off==8 || off==0xc || off==0x14 || off==0x18 || off==0x30) { return false; }
    return off>=0x220 || value!=h->source[off/4] || off==0xf4 || off==0xf8 || off==0xfc;
}

static bool z8_hlg_c1_target_locked(NikonZ9State *s, unsigned i, unsigned off, uint64_t value)
{
    Z8HLGPipeline *h=&s->hlg_pipeline;
    if(!h->requested && !h->owned) { return false; }
    const uint32_t *c=NULL;unsigned n=0;
    if(i==1) { if(off==0x18) { return false; } c=h->a;n=sizeof(h->a); }
    if(i==11) { if(off==4 || off==0x10) { return false; } c=h->c;n=sizeof(h->c); }
    if(i>=6 && i<=8) { if(off==0x18) { return false; } c=h->output[i-6];n=sizeof(h->output[0]); }
    if(i>=18 && i<=20) { c=h->geometry[i-18];n=sizeof(h->geometry[0]); }
    return c && (off>=n || value!=c[off/4]);
}

static bool get_z8_hlg_pipeline_model(Object *obj, Error **errp)
{ return NIKON_Z9_LAB(obj)->hlg_pipeline.enabled; }
static void set_z8_hlg_pipeline_model(Object *obj, bool value, Error **errp)
{
    NikonZ9State *s=NIKON_Z9_LAB(obj);
    if(s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp,"z8-capture-hlg-pipeline-model is immutable after realization");return;
    }
    s->hlg_pipeline.enabled=value;
}
static char *get_z8_hlg_pipeline_state(Object *obj, Error **errp)
{
    NikonZ9State *s=NIKON_Z9_LAB(obj);Z8HLGPipeline *h=&s->hlg_pipeline;
    GString *out=g_string_new(NULL);
    g_string_append_printf(out,"{\"enabled\":%s,\"requested\":%s,\"failed\":%s,\"owned\":%s,"
        "\"requests\":%" PRIu64 ",\"jobs\":%" PRIu64 ",\"rejects\":%" PRIu64 ",\"cancels\":%" PRIu64
        ",\"retired\":%" PRIu64 ",\"source_job\":%" PRIu64 ",\"source_frame\":%" PRIu64
        ",\"consumed151_job\":%" PRIu64 ",\"input_y\":%" PRIu64 ",\"input_uv\":%" PRIu64
        ",\"input_y_sha256\":\"%s\",\"input_uv_sha256\":\"%s\",\"lut_sha256\":\"%s\","
        "\"last_error\":\"%s\",\"source_pending\":%u,\"a1_pending\":%u,\"c1_pending\":%u,\"outputs\":[",
        h->enabled?"true":"false",h->requested?"true":"false",h->failed?"true":"false",h->owned?"true":"false",
        h->requests,h->jobs,h->rejects,h->cancels,h->retired,h->source_job,h->source_frame,h->consumed151_job,
        h->input[0],h->input[1],h->input_sha256[0],h->input_sha256[1],h->lut_sha256,h->last_error?h->last_error:"",
        s->engine1518[0].pending,s->engine1542_pending[1],s->engine1542_pending[11]);
    for(unsigned j=0;j<3;j++) {
        g_string_append_printf(out,"%s{\"y\":%" PRIu64 ",\"uv\":%" PRIu64
            ",\"y_bytes\":%" PRIu64 ",\"uv_bytes\":%" PRIu64 ",\"width\":%u,\"height\":%u,\"stride\":%u,"
            "\"bits\":10,\"chroma\":\"420\",\"y_sha256\":\"%s\",\"uv_sha256\":\"%s\"}",
            j?",":"",h->target[j][0],h->target[j][1],h->bytes[j][0],h->bytes[j][1],
            h->geometry[j][0x2c/4],h->geometry[j][0x30/4],h->output[j][0x38/4],h->output_sha256[j][0],h->output_sha256[j][1]);
    }
    g_string_append(out,"],\"admission_profile_observed\":true,\"profile_run\":\"heif-hlg-20261014-03\","
        "\"bit_order_verified\":false,\"uv_order_verified\":false,\"filter_verified\":false,"
        "\"lut_algorithm_verified\":false,\"quantization_verified\":false,\"internal_stream_packing_verified\":false,\"irq_timing_verified\":false,"
        "\"native_completion_verified\":false,\"video_ring_verified\":false,"
        "\"scope\":\"photo C1 only; exact native scalar/LUT profile with relocatable DMA; owned151 packed12 RAM to three packed10 outputs, nearest luma and paired-row chroma numerical hypothesis; no NV16 or file claim\"}");
    return g_string_free(out,false);
}
