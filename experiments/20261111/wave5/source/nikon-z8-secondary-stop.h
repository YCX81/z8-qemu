/* SPDX-License-Identifier: GPL-2.0-or-later */
/* P22: 4115faf8 stops output groups then clears both source controls.
 * 40c2b3a8 W1C -> 41151ed4 translates global1 bit18 to OS bit18.
 * Only an actual, drained status-source transaction produces this IRQ.
 * No pixel completion, guest flag write, or physical latency claim. */
static void z8_secondary_stop_group(NikonZ9State *s, unsigned group)
{
    if (!s->heif_secondary_stop_model) { return; }
    if (!group) { s->heif_secondary_stop_armed=false; return; }
    if ((s->event_group_latched[1]&2) && (s->event_control_1508[1]&1) &&
        (s->event_control_1508[3]&2) && s->event_secondary_timing_active &&
        !(s->event_global1_pending&0x40000)) {
        s->heif_secondary_stop_armed=true;
    }
}
static void z8_secondary_stop_control(NikonZ9State *s, unsigned group,
                                     bool clear, uint32_t previous, uint32_t value)
{
    if (!s->heif_secondary_stop_model || group!=1) { return; }
    if (!clear && (value&1)) { s->heif_secondary_stop_armed=false; return; }
    if (clear && (previous&value&1) && s->heif_secondary_stop_armed &&
        !s->event_secondary_timing_active && !(s->event_output_group_running&2) &&
        !(s->event_global1_pending&0x40000)) {
        s->heif_secondary_stop_armed=false;
        s->event_global1_pending|=0x40000;
        s->heif_secondary_stop_notifications++;
        event_global1_update_irq(s);
        fprintf(stderr,"Z8_1508_SECONDARY_STOP cpu=%d pc=%" PRIx64
            " pending=%x notifications=%" PRIu64 "; drained status source, no pixels\n",
            current_cpu?current_cpu->cpu_index:-1,current_cpu?ARM_CPU(current_cpu)->env.pc:0,
            s->event_global1_pending,s->heif_secondary_stop_notifications);
    }
}
static bool get_z8_secondary_stop(Object *obj,Error **errp)
{ return NIKON_Z9_LAB(obj)->heif_secondary_stop_model; }
static void set_z8_secondary_stop(Object *obj,bool value,Error **errp)
{
    NikonZ9State *s=NIKON_Z9_LAB(obj);
    if (!object_dynamic_cast(obj,MACHINE_TYPE_NAME("nikon-z8-bringup")) || s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp,"z8-heif-secondary-stop-model requires Z8 creation; immutable"); return;
    }
    s->heif_secondary_stop_model=value;
}
static char *get_z8_secondary_stop_state(Object *obj,Error **errp)
{
    NikonZ9State *s=NIKON_Z9_LAB(obj);
    return g_strdup_printf("{\"enabled\":%s,\"armed\":%s,\"notifications\":%" PRIu64
        ",\"physical_latency_verified\":false,\"pixel_bytes\":0}",
        s->heif_secondary_stop_model?"true":"false",s->heif_secondary_stop_armed?"true":"false",
        s->heif_secondary_stop_notifications);
}
