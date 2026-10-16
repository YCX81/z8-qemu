/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Native P22 frame2: 40c26834 HOLD, all photo W1Cs consumed, then
 * 40c2683c config writes 103 without an intervening 100. This is a finite
 * completed photo receiver, not a live video ring. No pending/IRQ change. */
static bool z8_hlg_c1_can_rearm(NikonZ9State *s,unsigned j,uint64_t value)
{
    Z8HLGPipeline *h=&s->hlg_pipeline;
    return s->heif_c1_rearm_model && h->enabled && j==1 && value==0x103 &&
        s->engine1543_active[1] && s->engine1543_held[1] &&
        engine1543_capture_ready(s,1) && s->engine1542_config[11][1]==0x102 &&
        s->engine1543_latched[1][1]==0x101 && s->engine1543_latched[1][2]==2 &&
        s->engine1543_latched[1][3]==0 && s->engine1543_latched[1][4]==0x20 &&
        h->jobs && h->retired==h->jobs && !h->requested && !h->owned && !h->failed &&
        !s->engine1518[0].pending && !s->engine1542_pending[1] && !s->engine1542_pending[11] &&
        !s->capture_nv16_requested && !s->engine151_capture_pending &&
        s->hlg_capture.last_output_hlg && s->hlg_capture.plan.output_bits==12 &&
        h->consumed151_job!=UINT64_MAX && s->engine151_capture_jobs==h->consumed151_job+1 &&
        h->source_frame!=UINT64_MAX && s->hlg_capture.source_frame==h->source_frame+1 &&
        s->primary_d980_frames==s->hlg_capture.source_frame;
}
static bool get_z8_hlg_c1_rearm(Object *obj,Error **errp)
{ return NIKON_Z9_LAB(obj)->heif_c1_rearm_model; }
static void set_z8_hlg_c1_rearm(Object *obj,bool value,Error **errp)
{
    NikonZ9State *s=NIKON_Z9_LAB(obj);
    if (!object_dynamic_cast(obj,MACHINE_TYPE_NAME("nikon-z8-bringup")) || s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp,"z8-heif-c1-rearm-model requires Z8 creation; immutable");return;
    }
    s->heif_c1_rearm_model=value;
}
static char *get_z8_hlg_c1_rearm_state(Object *obj,Error **errp)
{
    NikonZ9State *s=NIKON_Z9_LAB(obj);
    return g_strdup_printf("{\"enabled\":%s,\"rearms\":%" PRIu64
        ",\"scope\":\"ACK-retired held HLG photo C1, fresh next151 frame only; no IRQ/pending writes or video ring\"}",
        s->heif_c1_rearm_model?"true":"false",s->heif_c1_rearms);
}
