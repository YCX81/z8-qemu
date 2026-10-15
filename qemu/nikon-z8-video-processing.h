/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Adapted from WonderLantern/zr-qemu f9cc5d1, with Z8 upstream-owner
 * checks. Retained initialization only: no NR/DSP arithmetic or data/IRQ
 * completion. Z8 402f2f44/50 reaches reset IDs4a/4b, bank8 bits23/24. */
static bool video_processing_word(unsigned off)
{
    if (off&3) { return false; }
    if (off<=0x14 || off==0x100) { return true; }
    if (off>=0x120 && off<=0x18c && off!=0x17c) { return true; }
    return off>=0x200 && off<0x300 && (off&0x1f)<=0x18;
}
static bool video_processing_input_idle(NikonZ9State *s)
{
    Z8VideoData *d=s->z8_video_data;
    return !(s->engine150c_active&0xc000) &&
        !(d && (d->packet_owned || d->frame_ready || s->z8_front_pending[0] || s->z8_front_pending[1] || d->end_requested[0] || d->end_requested[1])) &&
        !s->engine152c_config[0][2] && !s->engine1530_config[2] &&
        !(s->engine151_pending[0]&8) && !(s->engine1518[0].pending&0x08100000);
}
static bool video_processing_port_initialized(NikonZ9State *s,unsigned port)
{
    /* Initial control, mode and the whole coefficient generation must be
     * submitted before arming. Slot payload addresses remain opaque until
     * a separately implemented actual DMA request consumes them. */
    if ((s->video_processing_valid[port][0]&UINT64_C(0x1e))!=UINT64_C(0x1e) ||
        !(s->video_processing_valid[port][1]&1)) { return false; }
    for (unsigned off=0x120;off<=0x18c;off+=4) {
        if (off==0x17c) { continue; }
        unsigned word=off/4;
        if (!(s->video_processing_valid[port][word/64]&(UINT64_C(1)<<(word%64)))) { return false; }
    }
    return true;
}
static bool video_processing_config_access(NikonZ9State *s,hwaddr addr,
                                          uint64_t *value,unsigned size,bool write)
{
    if (!s->video_processing_config_model) { return false; }
    bool global=addr>=0x16018000 && addr<0x16018010;
    unsigned port=addr>=0x1601a000 ? 1 : 0;
    hwaddr base=global ? 0x16018000 : (port ? 0x1601a000 : 0x16019000);
    if (!global && !(addr>=base && addr<base+0x300)) { return false; }
    unsigned off=addr-base,words=size/4;
    bool width_ok=size==4 || (!global && size==8 && off>=0x100 && video_processing_word(off+4));
    if ((addr&3) || !width_ok || (!global && !video_processing_word(off))) { goto invalid; }
    uint32_t *r=global ? s->video_processing_global : s->video_processing_ports[port];
    if (!write) {
        /* Aggregate status is owned by actual enabled producer completion.
         * Unimplemented raw-processing ports still have no pending work. */
        *value=(!global && off==0x14) ? 0 : r[off/4];
        if (size==8) { *value|=(uint64_t)r[off/4+1]<<32; }
        s->video_processing_reads++;return true;
    }
    /* Original ISR40e36ccc/40e36d3c acquires/releases the shared interrupt
     * reference through global+4 while data DMA can still be owned. This
     * control and aggregate acknowledge cannot replace DMA configuration. */
    /* Configure an unarmed target while upstream owns its source.
     * These ports still have no processing/DMA implementation. Retained
     * configuration must not retire any source packet, decoder or ISP owner. */
    bool target_setup = s->video_initial_reset_model &&
        (global ? (!s->video_processing_ports[0][0] && !s->video_processing_ports[1][0]) :
            (!s->video_processing_ports[port][0] || (off == 0 && !*value) ||
             (off == 8 && !((s->video_processing_ports[port][2] ^ *value) & ~UINT64_C(1)))));
    if (!(global && (off==4 || off==12)) && !target_setup && !video_processing_input_idle(s)) { goto invalid; }
    if (global) {
        if ((off==0 || off==4) ? *value>1 : off==8 ? *value!=0x111 : *value>UINT32_MAX) { goto invalid; }
        if (off==12) { s->video_processing_status_acks++;return true; }
    } else {
        if ((off==0 || off==4) && *value>1) { goto invalid; }
        if (off==8 && (*value&~UINT64_C(0xff))) { goto invalid; }
        if (off==12 && *value) { goto invalid; }
        if (off==0x14) { s->video_processing_status_acks++;return true; }
        /* Original40e3700c toggles bit0 through read/modify/write even
         * while the port is armed. Retain that mask; no pending producer
         * exists here, so it cannot itself complete a frame. */
        if (r[0] && !(off==0 && !*value) && !(off==8 && !((r[2]^*value)&~UINT64_C(1)))) { goto invalid; }
        if (off==0 && *value && (!video_processing_port_initialized(s,port) ||
                s->video_processing_global[2]!=0x111)) { goto invalid; }
    }
    for (unsigned i=0;i<words;i++) {
        unsigned word=off/4+i;r[word]=*value>>(i*32);
        if (!global) { s->video_processing_valid[port][word/64]|=UINT64_C(1)<<(word%64); }
    }
    s->video_processing_writes++;return true;
invalid:
    unknown_access(s,addr,*value,size,write);if (!write) { *value=0; }return true;
}
static bool get_video_processing_config_model(Object *obj, Error **errp)
{ return NIKON_Z9_LAB(obj)->video_processing_config_model; }
static void set_video_processing_config_model(Object *obj,bool value,Error **errp)
{
    NikonZ9State *s=NIKON_Z9_LAB(obj);
    if (s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp,"video processing config immutable after realization");return;
    }
    s->video_processing_config_model=value;
}
static char *get_video_processing_config_state(Object *obj,Error **errp)
{
    NikonZ9State *s=NIKON_Z9_LAB(obj);
    GString *g=g_string_new(NULL);
    g_string_append_printf(g,"{\"enabled\":%s,\"writes\":%" PRIu64 ",\"reads\":%" PRIu64
        ",\"status_acks\":%" PRIu64 ",\"global\":[%u,%u,%u,%u],\"ports\":[",
        s->video_processing_config_model ? "true" : "false",s->video_processing_writes,
        s->video_processing_reads,s->video_processing_status_acks,s->video_processing_global[0],
        s->video_processing_global[1],s->video_processing_global[2],s->video_processing_global[3]);
    for (unsigned p=0;p<2;p++) {
        g_string_append_printf(g,"%s{\"base\":%u,\"initialized\":%s,\"words\":[",
            p ? "," : "",0x16019000+p*0x1000,video_processing_port_initialized(s,p) ? "true" : "false");
        for (unsigned i=0;i<192;i++) { g_string_append_printf(g,"%s%u",i ? "," : "",s->video_processing_ports[p][i]); }
        g_string_append(g,"]}");
    }
    g_string_append(g,"],\"pixel_bytes\":0,\"completion_events\":0,\"physical_algorithm_verified\":false,"
        "\"scope\":\"retained original global/two-port/eight-slot initialization; aggregate VPU completion owned by explicit codec model; no raw processing, DMA or shader result\"}");
    return g_string_free(g,false);
}

/* Both original tables: IDf -> bank0/bit15. Native494's release
 * precedes any implemented work for this target. Keep only that unit's
 * empty reset latch; no other reset/packet/IRQ or pixel ownership changes.
 * Its physical identity is unmeasured; revisit its owner when adding work. */
static bool video_reset15_access(NikonZ9State *s, hwaddr addr, uint64_t value, unsigned size)
{
    if (!s->video_initial_reset_model || (addr != 0x0d154240 && addr != 0x0d154340) ||
            !(value & 0x8000)) { return false; }
    if (size != 4 || value != 0x8000) {
        unknown_access(s, addr, value, size, true); return true;
    }
    s->video_reset15_released = addr == 0x0d154240;
    if (s->video_reset15_released) { s->video_reset15_releases++; }
    else { s->video_reset15_asserts++; }
    return true;
}
static bool video_nr_fresh_release(NikonZ9State *s, hwaddr addr, uint64_t value, unsigned size)
{
    if (!s->video_initial_reset_model || addr != 0x0d154248 || size != 4 ||
            !value || (value & ~UINT64_C(0x01800000))) { return false; }
    for (unsigned p = 0; p < 2; p++) {
        uint32_t bit = UINT32_C(0x00800000) << p;
        if (!(value & bit)) { continue; }
        if ((s->peripheral_release8 & bit) || s->video_processing_ports[p][0] ||
                video_processing_port_initialized(s, p)) { return false; }
    }
    return true;
}
static bool video_nr_reset_access(NikonZ9State *s, hwaddr addr,
                                  uint64_t value, unsigned size)
{
    if (!s->video_nr_reset_model || (addr!=0x0d154248 && addr!=0x0d154348) ||
        !(value&UINT64_C(0x01800000))) { return false; }
    bool fresh = video_nr_fresh_release(s, addr, value, size);
    bool target_active = s->video_initial_reset_model &&
        (((value & 0x800000) && s->video_processing_ports[0][0]) ||
         ((value & 0x1000000) && s->video_processing_ports[1][0]));
    if (size!=4 || !value || (value&~UINT64_C(0x01800000)) || target_active ||
        (!fresh && !video_processing_input_idle(s))) {
        unknown_access(s,addr,value,size,true);return true;
    }
    bool release=addr==0x0d154248;
    for (unsigned i=0;i<2;i++) {
        if (!(value&(UINT32_C(0x00800000)<<i))) { continue; }
        if (release) {
            s->video_nr_reset_releases[i]++;
            if (fresh) { s->video_nr_initial_releases[i]++; }
        } else { s->video_nr_reset_asserts[i]++; }
    }
    if (release) { s->peripheral_release8|=value; } else { s->peripheral_release8&=~value; }
    return true;
}
static bool get_video_initial_reset_model(Object *obj, Error **errp)
{ return NIKON_Z9_LAB(obj)->video_initial_reset_model; }
static void set_video_initial_reset_model(Object *obj, bool value, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    if (s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp, "video initial reset mode is immutable after realization"); return;
    }
    s->video_initial_reset_model = value;
}
static char *get_video_initial_reset_state(Object *obj, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    return g_strdup_printf("{\"enabled\":%s,\"initial_nr_releases\":[%" PRIu64 ",%" PRIu64
        "],\"reset15_released\":%s,\"reset15_asserts\":%" PRIu64 ",\"reset15_releases\":%" PRIu64
        ",\"pixel_bytes\":0,\"completion_events\":0,\"physical_unit_identity_verified\":false,"
        "\"scope\":\"unarmed NR initialization with preserved upstream ownership; observed empty unit15 reset; no processing/DMA/IRQ result\"}",
        s->video_initial_reset_model ? "true" : "false", s->video_nr_initial_releases[0], s->video_nr_initial_releases[1],
        s->video_reset15_released ? "true" : "false", s->video_reset15_asserts, s->video_reset15_releases);
}
static bool get_video_nr_reset_model(Object *obj, Error **errp)
{ return NIKON_Z9_LAB(obj)->video_nr_reset_model; }
static void set_video_nr_reset_model(Object *obj, bool value, Error **errp)
{
    NikonZ9State *s=NIKON_Z9_LAB(obj);
    if (s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp,"video NR reset immutable after realization");return;
    }
    s->video_nr_reset_model=value;
}
static char *get_video_nr_reset_state(Object *obj, Error **errp)
{
    NikonZ9State *s=NIKON_Z9_LAB(obj);
    return g_strdup_printf("{\"enabled\":%s,\"asserts\":[%" PRIu64 ",%" PRIu64
        "],\"releases\":[%" PRIu64 ",%" PRIu64 "],\"release_latch\":%u,"
        "\"pixel_bytes\":0,\"completion_events\":0,\"physical_nr_verified\":false,"
        "\"scope\":\"original reset IDs4a/4b empty lifecycle after source/backend/ISP owners retire; not NR computation\"}",
        s->video_nr_reset_model ? "true" : "false",s->video_nr_reset_asserts[0],s->video_nr_reset_asserts[1],
        s->video_nr_reset_releases[0],s->video_nr_reset_releases[1],s->peripheral_release8);
}

