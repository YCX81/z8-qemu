/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "nikon-z8-frame-period.h"

static bool hsfc_source_owned(NikonZ9State *s)
{
    return s->z8_front_mode[0] == 3 &&
           s->z8_hsfc152c_pending && s->engine152c_request_pending &&
           s->z8_d980_internal_waiting && (s->engine150c_active & 0x4000) &&
           !(s->engine150c_stop_requested & 0x4000) && !s->primary_reset_stage;
}

static bool hsfc_cadence_owned(NikonZ9State *s)
{
    return s->hsfc_ch13_cadence_model && hsfc_source_owned(s);
}

/* Called on every raw timer callback, even if output causes remain pending.
 * Non-HSFC/off uses the historical relative timer unchanged. */
static bool hsfc_cadence_schedule(NikonZ9State *s)
{
    uint64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL), period, elapsed;
    const char *reason = NULL;
    if (!hsfc_cadence_owned(s)) {
        s->hsfc_cadence_deadline = 0;
        timer_mod(s->raw_pipeline_timer, now + 33333333);
        return true;
    }
    if (!s->hsfc_ch13_commits) {
        reason = "uncommitted_ch13";
    } else if (!(s->event_control_1508[3] & (1u << 13))) {
        reason = "group3_bit13_disabled";
    } else if (!z8_ch13_period(s->event_active_1508[13][0],
                              s->event_active_1508[13][1],
                              s->hsfc_cadence_clock_hz, &period)) {
        reason = "invalid_ch13_or_clock";
    } else if (now > (uint64_t)INT64_MAX - period) {
        reason = "deadline_overflow";
    }
    if (reason) {
        s->hsfc_cadence_rejects++;
        fprintf(stderr, "Z8_HSFC_CADENCE_REJECT timer_origin=1 reason=%s ch13=%08x,%08x group3=%x\n",
                reason, s->event_active_1508[13][0], s->event_active_1508[13][1],
                s->event_control_1508[3]);
        /* This is a timer-side source failure, not an observed guest write. */
        unknown_access(s, 0x150833a4, 0, 4, false);
        return false;
    }
    if (!s->hsfc_cadence_deadline || s->hsfc_cadence_period != period) {
        s->hsfc_cadence_period = period;
        s->hsfc_cadence_deadline = now + period;
        s->hsfc_cadence_epochs++;
        timer_mod(s->raw_pipeline_timer, s->hsfc_cadence_deadline);
        return false;
    }
    if (now < s->hsfc_cadence_deadline) {
        timer_mod(s->raw_pipeline_timer, s->hsfc_cadence_deadline);
        return false;
    }
    if (!z8_ch13_deadline(now, s->hsfc_cadence_deadline, period,
                          &s->hsfc_cadence_deadline, &elapsed)) {
        s->hsfc_cadence_rejects++;
        fprintf(stderr, "Z8_HSFC_CADENCE_REJECT timer_origin=1 reason=deadline_overflow\n");
        unknown_access(s, 0x150833a4, 0, 4, false);
        return false;
    }
    s->hsfc_cadence_slots += elapsed;
    timer_mod(s->raw_pipeline_timer, s->hsfc_cadence_deadline);
    return true;
}

/* Preserve separate refill cause after the third completed output's W1C.
 * There is evidence for ordering, not for a whole sensor-period bubble. */
static void hsfc_cadence_refill_ack(NikonZ9State *s, unsigned i,
                                    uint32_t previous, uint32_t value)
{
    if (i == 10 && s->hsfc_refill_subphase_model && hsfc_source_owned(s) &&
        (previous & value & 1) &&
        s->raw_pipeline_row == 3 && !s->raw_pipeline_refill_notified) {
        s->raw_pipeline_refill_notified = true;
        s->engine1542_pending[10] |= 8;
        s->hsfc_cadence_refills++;
    }
}

static bool get_hsfc_refill_subphase_model(Object *obj, Error **errp)
{
    return NIKON_Z9_LAB(obj)->hsfc_refill_subphase_model;
}

static void set_hsfc_refill_subphase_model(Object *obj, bool value, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    if (!object_dynamic_cast(obj, MACHINE_TYPE_NAME("nikon-z8-bringup")) ||
        s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp, "hsfc-refill-subphase-model requires Z8 at creation");
        return;
    }
    s->hsfc_refill_subphase_model = value;
}

static bool get_hsfc_ch13_cadence_model(Object *obj, Error **errp)
{
    return NIKON_Z9_LAB(obj)->hsfc_ch13_cadence_model;
}

static void set_hsfc_ch13_cadence_model(Object *obj, bool value, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    if (!object_dynamic_cast(obj, MACHINE_TYPE_NAME("nikon-z8-bringup")) ||
        s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp, "hsfc-ch13-cadence-model requires Z8 at creation");
        return;
    }
    s->hsfc_ch13_cadence_model = value;
}

static void set_hsfc_cadence_clock(Object *obj, const char *value, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    if (s->image_ram.ram || s->image_ram.rom_device ||
        !value || strcmp(value, "72000000")) {
        error_setg(errp, "hsfc-ch13-clock-hz requires explicit 72000000 at creation");
        return;
    }
    s->hsfc_cadence_clock_hz = 72000000;
}

static char *get_hsfc_cadence_state(Object *obj, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    return g_strdup_printf("{\"enabled\":%s,\"owned\":%s,\"clock_hz_hypothesis\":%" PRIu64
        ",\"physical_timing_verified\":false,\"commits\":%" PRIu64
        ",\"ch13\":[%u,%u],\"group3\":%u,\"period_ns\":%" PRIu64
        ",\"next_deadline_ns\":%" PRIu64 ",\"epochs\":%" PRIu64
        ",\"slots\":%" PRIu64 ",\"frames\":%" PRIu64 ",\"skipped\":%" PRIu64
        ",\"refills\":%" PRIu64 ",\"rejects\":%" PRIu64 ",\"refill_enabled\":%s}",
        s->hsfc_ch13_cadence_model ? "true" : "false",
        hsfc_cadence_owned(s) ? "true" : "false", s->hsfc_cadence_clock_hz,
        s->hsfc_ch13_commits, s->event_active_1508[13][0], s->event_active_1508[13][1],
        s->event_control_1508[3], s->hsfc_cadence_period, s->hsfc_cadence_deadline,
        s->hsfc_cadence_epochs, s->hsfc_cadence_slots, s->hsfc_cadence_frames,
        s->hsfc_cadence_slots - s->hsfc_cadence_frames,
        s->hsfc_cadence_refills, s->hsfc_cadence_rejects,
        s->hsfc_refill_subphase_model ? "true" : "false");
}
