/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Explicit empty-target reset, not active NR cancellation. Original402f3084
 * asserts IDs43..4b independently of ongoing preview controls. Never retire
 * another device to make the old global input-idle predicate become true. */
static bool video_nr_port_empty(const NikonZ9State *s,unsigned port)
{
    for (unsigned i=0;i<192;i++) { if (s->video_processing_ports[port][i]) { return false; } }
    for (unsigned i=0;i<3;i++) { if (s->video_processing_valid[port][i]) { return false; } }
    return true;
}

/* Pending work is deliberately stronger than an armed/configured control.
 * The latter may belong to continuing preview and cannot prove NR ownership.
 * The target must have NO submitted words/validity, not merely control0=0. */
static uint32_t video_nr_empty_work_mask(const NikonZ9State *s)
{
    uint32_t mask=0;const Z8VideoData *d=s->z8_video_data;
    if (d && (d->packet_owned || d->frame_ready ||
        d->end_requested[0] || d->end_requested[1])) { mask|=1; }
    if (s->z8_video_primary_pending || s->z8_front_pending[0] || s->z8_front_pending[1]) { mask|=1; }
    for (unsigned ch=0;ch<6;ch++) { if (s->dma[ch][0] || s->dma[ch][2]) { mask|=2; } }
    if (s->wave5_busy || (s->wave5 && (s->wave5->busy || s->wave5->pending ||
        s->wave5->job || s->wave5->instances))) { mask|=4; }
    if ((s->engine151_pending[0]&8) || (s->engine1518[0].pending&0x08100000)) { mask|=8; }
    for (unsigned i=0;i<NB_SLOT_COUNT;i++) { if (s->output_owner.slots[i].held) { mask|=16; } }
    for (unsigned b=0;b<2;b++) {
        if (s->output_owner.readers[b].token) { mask|=16; }
        if (s->engine15e2_jpeg_requested[b] || s->engine15e2_jpeg_pending[b] || s->engine15e2_jpeg_core[b]) { mask|=32; }
    }
    for (unsigned i=0;i<13;i++) { if (s->engine1542_pending[i]) { mask|=64; } }
    if (s->z8_yuv_data) {
        if (s->z8_yuv_data->read_stage) { mask|=128; }
        for (unsigned i=0;i<G_N_ELEMENTS(s->z8_yuv_data->slots);i++) { if (s->z8_yuv_data->slots[i].owned) { mask|=128; } }
    }
    return mask;
}

static bool video_nr_empty_reset_access(NikonZ9State *s,hwaddr addr,uint64_t value,unsigned size)
{
    if (!s->video_nr_empty_reset_model || !s->video_nr_reset_model ||
        (addr!=0x0d154248 && addr!=0x0d154348) || !(value&UINT64_C(0x01800000))) { return false; }
    /* Malformed requests retain the original strict handler's rejection. */
    if (size!=4 || (value&~UINT64_C(0x01800000))) { return false; }
    bool release=addr==0x0d154248;
    uint32_t held=s->video_nr_empty_reset_held&value;
    if (release && !held) { return false; } /* Original fresh/ordinary release. */
    if (held && (!release || held!=value)) { goto invalid; }
    if (video_nr_empty_work_mask(s)) { goto invalid; }
    /* Preserve all already-supported boot/idle resets. Only the old global
     * non-idle rejection may enter this independently empty-target mode. */
    if (!release && video_processing_input_idle(s)) { return false; }
    for (unsigned p=0;p<2;p++) {
        if (!(value&(UINT32_C(0x00800000)<<p))) { continue; }
        /* Configured or armed ports never get this exception. */
        if (!video_nr_port_empty(s,p)) {
            if (held) { goto invalid; }
            return false;
        }
        if ((release ? s->video_nr_reset_releases[p] : s->video_nr_reset_asserts[p])==UINT64_MAX ||
            (release ? s->video_nr_empty_releases[p] : s->video_nr_empty_asserts[p])==UINT64_MAX) { goto invalid; }
    }
    if (!release && (s->peripheral_release8&value)!=value) { goto invalid; }
    for (unsigned p=0;p<2;p++) {
        if (!(value&(UINT32_C(0x00800000)<<p))) { continue; }
        if (release) { s->video_nr_empty_releases[p]++;s->video_nr_reset_releases[p]++; }
        else {
            memset(s->video_processing_ports[p],0,sizeof(s->video_processing_ports[p]));
            memset(s->video_processing_valid[p],0,sizeof(s->video_processing_valid[p]));
            s->video_nr_empty_asserts[p]++;s->video_nr_reset_asserts[p]++;
        }
    }
    if (release) { s->peripheral_release8|=value;s->video_nr_empty_reset_held&=~value; }
    else { s->peripheral_release8&=~value;s->video_nr_empty_reset_held|=value; }
    fprintf(stderr,"Z8_NR_EMPTY_RESET action=%s mask=%"PRIx64" legacy_idle=%u; empty target only, no DMA/IRQ completion\n",
        release?"release":"assert",value,video_processing_input_idle(s));
    return true;
invalid:
    if (s->video_nr_empty_rejects!=UINT64_MAX) { s->video_nr_empty_rejects++; }
    fprintf(stderr,"Z8_NR_EMPTY_RESET_REJECT mask=%"PRIx64" held=%x work=%x\n",value,s->video_nr_empty_reset_held,video_nr_empty_work_mask(s));
    unknown_access(s,addr,value,size,true);return true;
}

static bool get_video_nr_empty_reset_model(Object *obj,Error **errp)
{ return NIKON_Z9_LAB(obj)->video_nr_empty_reset_model; }
static void set_video_nr_empty_reset_model(Object *obj,bool value,Error **errp)
{
    NikonZ9State *s=NIKON_Z9_LAB(obj);
    if (s->image_ram.ram || s->image_ram.rom_device) { error_setg(errp,"video NR empty reset immutable after realization");return; }
    s->video_nr_empty_reset_model=value;
}
static char *get_video_nr_empty_reset_state(Object *obj,Error **errp)
{
    NikonZ9State *s=NIKON_Z9_LAB(obj);
    return g_strdup_printf("{\"enabled\":%s,\"held_mask\":%u,\"asserts\":[%"PRIu64",%"PRIu64"],"
        "\"releases\":[%"PRIu64",%"PRIu64"],\"rejects\":%"PRIu64",\"empty\":[%s,%s],\"work_mask\":%u,"
        "\"legacy_input_idle\":%s,\"pixel_bytes\":0,\"completion_events\":0,\"active_nr_reset_supported\":false}",
        s->video_nr_empty_reset_model?"true":"false",s->video_nr_empty_reset_held,
        s->video_nr_empty_asserts[0],s->video_nr_empty_asserts[1],s->video_nr_empty_releases[0],s->video_nr_empty_releases[1],
        s->video_nr_empty_rejects,video_nr_port_empty(s,0)?"true":"false",video_nr_port_empty(s,1)?"true":"false",
        video_nr_empty_work_mask(s),video_processing_input_idle(s)?"true":"false");
}
