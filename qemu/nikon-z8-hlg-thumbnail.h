/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Finite photo A2 -> B4/F4 only; no C1/video-ring changes. */
/* Pure predicate diagnostics: same admission rules/order as before. */
static const char *z8_hlg_thumb_not_ready(NikonZ9State *s)
{
    Z8HLGThumbnail *t=&s->hlg_thumbnail;Z8HLGPipeline *p=&s->hlg_pipeline;
    const uint32_t *a=s->engine1542_config[2];
    if(!t->enabled) { return "disabled"; }
    if(s->engine1542_ram_input_valid!=0x3fff) { return "input_config_incomplete"; }
    if(!z8_hlg_thumb_profile(a,s->engine1542_config[7],s->engine1542_config[19])) { return "profile_mismatch"; }
    if(!p->jobs || p->failed || p->requested || p->owned) { return "c1_not_complete_or_retired"; }
    if(p->jobs==t->consumed_job) { return "c1_already_consumed"; }
    if(p->source_frame!=s->primary_d980_frames || p->consumed151_job!=s->engine151_capture_jobs) { return "source_generation_mismatch"; }
    if(z8_hlg_address(a,0x20)!=p->target[1][0] || z8_hlg_address(a,0x28)!=p->target[1][1]) { return "source_address_mismatch"; }
    return NULL;
}
static bool z8_hlg_thumb_ready(NikonZ9State *s)
{ return z8_hlg_thumb_not_ready(s)==NULL; }
static bool z8_hlg_thumb_fail(NikonZ9State *s,const char *reason)
{
    Z8HLGThumbnail *t=&s->hlg_thumbnail;
    Z8HLGThumbMismatch m={0};
    static const unsigned blocks[]={2,7,19};
    bool profile=z8_hlg_thumb_profile_check(s->engine1542_config[2],s->engine1542_config[7],s->engine1542_config[19],&m);
    fprintf(stderr,"Z8_HLG_A2_REJECT reason=%s valid=%x active=%d pending=%x profile_match=%d"
        " first_bad_mmio=%x actual=%x expected=%x c1_job=%" PRIu64 " consumed=%" PRIu64 "\n",
        reason,s->engine1542_ram_input_valid,s->engine1542_ram_input_active,s->engine1542_pending[2],profile,
        profile?0:engine1542_bases[blocks[m.bank]]+m.offset,m.actual,m.expected,
        s->hlg_pipeline.jobs,t->consumed_job);
    t->rejects++;t->last_error=reason;return false;
}
static bool z8_hlg_thumb_prepare(NikonZ9State *s)
{
    Z8HLGThumbnail *t=&s->hlg_thumbnail;Z8HLGPipeline *p=&s->hlg_pipeline;
    const uint32_t *a=s->engine1542_config[2], *b=s->engine1542_config[7];
    const char *reason=z8_hlg_thumb_not_ready(s);
    if(reason) { return z8_hlg_thumb_fail(s,reason); }
    if(s->engine15e2_jpeg_requested || s->engine15e2_jpeg_pending || s->engine15e2_jpeg_core) {
        return z8_hlg_thumb_fail(s,"jpeg_busy");
    }
    NBSpan spans[4]={{z8_hlg_address(a,0x20),448*212},{z8_hlg_address(a,0x28),448*106},
        {z8_hlg_address(b,0x20),192*106},{z8_hlg_address(b,0x28),192*106}};
    for(unsigned i=0;i<4;i++) {
        if(!z8_hlg_span(s,spans[i].pa,spans[i].bytes) ||
            (i>=2 && !nb_writable(&s->output_owner,spans[i]))) { return z8_hlg_thumb_fail(s,"dma_bounds_or_owner"); }
        for(unsigned k=0;k<i;k++) if(nb_overlap(spans[i],spans[k])) { return z8_hlg_thumb_fail(s,"dma_overlap"); }
        if(i>=2) for(unsigned j=0;j<3;j++) for(unsigned k=0;k<2;k++) {
            if(nb_overlap(spans[i],(NBSpan){p->target[j][k],p->bytes[j][k]})) {
                return z8_hlg_thumb_fail(s,"overwrites_c1_output");
            }
        }
    }
    memcpy(t->a,a,sizeof(t->a));memcpy(t->b,b,sizeof(t->b));memcpy(t->f,s->engine1542_config[19],sizeof(t->f));
    for(unsigned k=0;k<2;k++) {
        t->source[k]=spans[k].pa;t->target[k]=spans[k+2].pa;
        g_strlcpy(t->input_sha256[k],p->output_sha256[1][k],65);
    }
    t->source_job=p->jobs;t->source_frame=p->source_frame;t->requests++;
    t->valid=false;t->failed=false;t->last_error=NULL;memset(t->output_sha256,0,sizeof(t->output_sha256));
    return true;
}
static bool z8_hlg_thumb_run(NikonZ9State *s)
{
    Z8HLGThumbnail *t=&s->hlg_thumbnail;
    uint8_t *in[2]={NULL,NULL},*out[2]={NULL,NULL};
    const unsigned sizes[2]={448*212,448*106};const char *failure="allocation_or_source_read";
    for(unsigned k=0;k<2;k++) {
        in[k]=g_try_malloc(sizes[k]);out[k]=g_try_malloc(192*106);
        if(!in[k] || !out[k] || address_space_read(&address_space_memory,t->source[k],MEMTXATTRS_UNSPECIFIED,in[k],sizes[k])!=MEMTX_OK) { goto done; }
        char *sha=g_compute_checksum_for_data(G_CHECKSUM_SHA256,in[k],sizes[k]);
        bool same=!strcmp(sha,t->input_sha256[k]);g_free(sha);
        if(!same) { failure="source_hash_mismatch";goto done; }
    }
    z8_hlg_thumb_convert(in[0],in[1],out[0],out[1]);
    if(s->producer_journal.enabled) {
        nj_unknown(&s->producer_journal,qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL));s->producer_journal.gap=true;
    }
    for(unsigned k=0;k<2;k++) {
        failure="partial_dma_or_readback";
        if(address_space_write(&address_space_memory,t->target[k],MEMTXATTRS_UNSPECIFIED,out[k],192*106)!=MEMTX_OK ||
           !z8_hlg_hash_ram(s,t->target[k],192*106,t->output_sha256[k])) { goto done; }
        char *sha=g_compute_checksum_for_data(G_CHECKSUM_SHA256,out[k],192*106);
        bool same=!strcmp(sha,t->output_sha256[k]);g_free(sha);if(!same) { goto done; }
    }
    t->jobs++;t->consumed_job=t->source_job;t->valid=true;
    s->engine1542_ram_input_active=false;
    s->engine1542_pending[2]|=0x11;raw_pipeline_irq_update(s);
    fprintf(stderr,"Z8_HLG_THUMB job=%" PRIu64 " c1=%" PRIu64 " frame=%" PRIu64
        " y=%" PRIx64 " uv=%" PRIx64 " geometry=160x106 stride=192 bits=8 y_sha256=%s uv_sha256=%s\n",
        t->jobs,t->source_job,t->source_frame,t->target[0],t->target[1],t->output_sha256[0],t->output_sha256[1]);
    failure=NULL;
done:
    for(unsigned k=0;k<2;k++) { g_free(in[k]);g_free(out[k]); }
    if(failure) {
        t->failed=true;t->valid=false;s->engine1542_ram_input_active=false;
        return z8_hlg_thumb_fail(s,failure);
    }
    return true;
}

/* Dedicated JPEG owner4. The generic !last_output_hlg guard remains intact.
 * Only active pixels with this producer's readback hash are accepted; optional
 * 7-row top/bottom black borders must have been supplied by the original CPU. */
static bool z8_hlg_thumb_jpeg_owner(NikonZ9State *s)
{
    Z8HLGThumbnail *t=&s->hlg_thumbnail;const uint32_t *c=s->engine15e2_config[0];
    if(!t->enabled || !t->valid || t->failed || !t->jobs ||
       t->source_job!=s->hlg_pipeline.jobs || t->source_frame!=s->primary_d980_frames ||
       c[0x418/4]!=160 || (c[0x41c/4]!=106 && c[0x41c/4]!=120) ||
       c[0x410/4]!=192 || c[0x414/4]!=192) { return false; }
    unsigned height=c[0x41c/4],pad=(height-106)/2;uint64_t head=pad*192;
    uint64_t y=(uint64_t)c[0x430/4]<<4,uv=(uint64_t)c[0x434/4]<<4;
    if(t->target[0]<head || t->target[1]<head || y!=t->target[0]-head || uv!=t->target[1]-head ||
       !z8_hlg_span(s,y,(uint64_t)height*192) || !z8_hlg_span(s,uv,(uint64_t)height*192) ||
       nb_overlap((NBSpan){y,height*192},(NBSpan){uv,height*192})) { return false; }
    for(unsigned k=0;k<2;k++) {
        char hash[65];uint8_t row[192];
        if(!z8_hlg_hash_ram(s,t->target[k],192*106,hash) || strcmp(hash,t->output_sha256[k])) { return false; }
        for(unsigned r=0;r<height;r++) {
            if(r>=pad && r<pad+106) { continue; }
            if(address_space_read(&address_space_memory,(k?uv:y)+(uint64_t)r*192,MEMTXATTRS_UNSPECIFIED,row,192)!=MEMTX_OK) { return false; }
            for(unsigned x=0;x<160;x++) if(row[x]!=(k?128:0)) { return false; }
        }
    }
    return true;
}
static bool get_z8_hlg_thumbnail_model(Object *obj,Error **errp)
{ return NIKON_Z9_LAB(obj)->hlg_thumbnail.enabled; }
static void set_z8_hlg_thumbnail_model(Object *obj,bool value,Error **errp)
{
    NikonZ9State *s=NIKON_Z9_LAB(obj);
    if(s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp,"z8-capture-hlg-thumbnail-model is immutable after realization");return;
    }
    s->hlg_thumbnail.enabled=value;
}
static char *get_z8_hlg_thumbnail_state(Object *obj,Error **errp)
{
    NikonZ9State *s=NIKON_Z9_LAB(obj);Z8HLGThumbnail *t=&s->hlg_thumbnail;
    return g_strdup_printf("{\"enabled\":%s,\"valid\":%s,\"failed\":%s,\"jobs\":%" PRIu64
        ",\"requests\":%" PRIu64 ",\"rejects\":%" PRIu64 ",\"source_c1_job\":%" PRIu64
        ",\"source_frame\":%" PRIu64 ",\"consumed_c1_job\":%" PRIu64 ",\"jpeg_commits\":%" PRIu64
        ",\"source_y\":%" PRIu64 ",\"source_uv\":%" PRIu64 ",\"y\":%" PRIu64 ",\"uv\":%" PRIu64
        ",\"input_bytes\":142464,\"output_plane_bytes\":20352,\"width\":160,\"height\":106,\"stride\":192,"
        "\"input_y_sha256\":\"%s\",\"input_uv_sha256\":\"%s\",\"y_sha256\":\"%s\",\"uv_sha256\":\"%s\","
        "\"a2_pending\":%u,\"last_error\":\"%s\",\"jpeg_owner\":4,\"profile_run\":\"heif-hlg-20261014-04\","
        "\"bit_order_verified\":false,\"uv_order_verified\":false,\"quantization_verified\":false,"
        "\"filter_verified\":false,\"tone_mapping_verified\":false,\"irq_timing_verified\":false,"
        "\"scope\":\"C1 small packed10 actual RAM to finite A2 NV16 thumbnail; independent JPEG owner4; no host borders, video ring or native-file claim\"}",
        t->enabled?"true":"false",t->valid?"true":"false",t->failed?"true":"false",t->jobs,t->requests,t->rejects,
        t->source_job,t->source_frame,t->consumed_job,t->jpeg_commits,t->source[0],t->source[1],t->target[0],t->target[1],
        t->input_sha256[0],t->input_sha256[1],t->output_sha256[0],t->output_sha256[1],s->engine1542_pending[2],t->last_error?t->last_error:"");
}
