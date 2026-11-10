/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Included immediately before engine150c_stop_write. This owns only the
 * observed photo 400e held/reset route, never video F04F or a direct drain. */
static bool z8_heif_menu_stop_request(NikonZ9State *s, hwaddr addr,
                                      uint64_t value, unsigned size)
{
    Z8HeifMenuStop *m=&s->heif_menu_stop;
    if (!m->enabled || addr != 0x150c000c ||
        (value != Z8_HEIF_MENU_STOP_MASK && !(value & m->owned))) { return false; }
    Z8HeifMenuStopInput i={
        .active=s->engine150c_active,.latched=s->engine150c_latched,
        .hold=s->engine_150c_init[0x18/4],.reset_stage=s->primary_reset_stage,
        .conflicts=s->engine150c_stop_requested | s->engine150c_stop_done |
            s->engine150c_direct_stop_done | s->engine150c_stop_reset_asserted |
            s->engine150c_stop_reset_released | s->z8_primary_f04f_owned |
            s->engine150c_idle_stop_requested | s->primary_idle_direct_done,
        .stop_pending=s->engine150c_stop_pending,.data_pending=s->primary_d980_pending,
        .frames=s->primary_d980_frames,.hlg_frame=s->hlg_capture.source_frame,
        .hlg_jobs=s->hlg_capture.jobs,
        .source_ready=s->primary_d980_cache && s->hlg_capture.enabled &&
            s->hlg_capture.last_output_hlg && !s->engine151_capture_pending &&
            !(s->engine151_pending[0] & 2),
        .armed=s->primary_d980_single_armed,.prepared=s->primary_d980_prepared,
        .downstream_owned=s->hlg_pipeline.requested || s->hlg_pipeline.owned,
    };
    const char *reason=z8_heif_menu_stop_reason(m,&i,value,size);
    if (reason) {
        m->last_reject=reason;m->rejects++;
        fprintf(stderr,"Z8_HEIF_MENU_STOP reject=%s value=%" PRIx64
                " active=%x latched=%x hold=%x conflicts=%x reset=%u data_pending=%x"
                " frame=%" PRIu64 " hlg_frame=%" PRIu64 " hlg_jobs=%" PRIu64
                " source_ready=%u last_hlg=%u capture_pending=%u armed=%u prepared=%u source_owned=%u\n",
                reason,value,i.active,i.latched,i.hold,i.conflicts,i.reset_stage,i.data_pending,
                i.frames,i.hlg_frame,i.hlg_jobs,(unsigned)i.source_ready,
                (unsigned)s->hlg_capture.last_output_hlg,(unsigned)s->engine151_capture_pending,
                (unsigned)i.armed,(unsigned)i.prepared,(unsigned)i.downstream_owned);
        unknown_access(s,addr,value,size,true);return true;
    }
    z8_heif_menu_stop_begin(m,&i);
    g_strlcpy(m->source_sha256,s->hlg_capture.source_sha256,sizeof(m->source_sha256));
    s->engine150c_stop_requested |= Z8_HEIF_MENU_STOP_MASK;
    s->engine150c_stop_requests++;
    primary_empty_end_update(s);
    fprintf(stderr,"Z8_HEIF_MENU_STOP request=%" PRIu64 " frame=%" PRIu64
            " hlg_job=%" PRIu64 " mask=400e; await original hold/kick; data_pending=%x preserved\n",
            m->requests,m->source_frame,m->source_hlg_job,s->primary_d980_pending);
    return true;
}

static bool get_z8_heif_menu_stop_model(Object *obj, Error **errp)
{ return NIKON_Z9_LAB(obj)->heif_menu_stop.enabled; }
static void set_z8_heif_menu_stop_model(Object *obj,bool value,Error **errp)
{
    NikonZ9State *s=NIKON_Z9_LAB(obj);
    if (!object_dynamic_cast(obj,MACHINE_TYPE_NAME("nikon-z8-bringup")) ||
        s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp,"z8-heif-menu-stop-model requires Z8 at creation and is immutable");return;
    }
    s->heif_menu_stop.enabled=value;
}
static char *get_z8_heif_menu_stop_state(Object *obj,Error **errp)
{
    NikonZ9State *s=NIKON_Z9_LAB(obj);Z8HeifMenuStop *m=&s->heif_menu_stop;
    return g_strdup_printf("{\"enabled\":%s,\"owned_mask\":%u,\"kicked\":%s,\"requests\":%" PRIu64
        ",\"kicks\":%" PRIu64 ",\"acks\":%" PRIu64 ",\"reset_cancels\":%" PRIu64
        ",\"rejects\":%" PRIu64 ",\"source_frame\":%" PRIu64 ",\"source_hlg_job\":%" PRIu64
        ",\"source_sha256\":\"%s\",\"data_pending\":%u,\"last_reject\":\"%s\","
        "\"irq_timing_verified\":false,\"native_retirement_observed\":false,"
        "\"scope\":\"run13 400e request; static hold400e/kick8045/group2/reset contract; no data pending clear or image completion\"}",
        m->enabled?"true":"false",m->owned,m->kicked?"true":"false",m->requests,m->kicks,m->acks,
        m->reset_cancels,m->rejects,m->source_frame,m->source_hlg_job,m->source_sha256,
        s->primary_d980_pending,m->last_reject?m->last_reject:"");
}
