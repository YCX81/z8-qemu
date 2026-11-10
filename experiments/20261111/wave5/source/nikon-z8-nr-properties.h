/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Exact NR property definitions, included once at their original location. */
static bool get_z8_bank0_d980_model(Object *obj, Error **errp)
{ return NIKON_Z9_LAB(obj)->z8_bank0_d980_identity_model; }
static void set_z8_bank0_d980_model(Object *obj, bool value, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    if (!object_dynamic_cast(obj, MACHINE_TYPE_NAME("nikon-z8-bringup")) ||
        s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp, "z8-1534-bank0-d980-identity-model requires Z8 at creation and is immutable");
        return;
    }
    s->z8_bank0_d980_identity_model = value;
}
static char *get_z8_bank0_d980_state(Object *obj, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    return g_strdup_printf("{\"enabled\":%s,\"physical_algorithm_verified\":false,"
        "\"scope\":\"bank0 ordinary D980 neutral identity; pyramid/scratch unsupported\","
        "\"consumed_dma_frames\":[%" PRIu64 ",%" PRIu64 "],"
        "\"pending\":[%u,%u]}", s->z8_bank0_d980_identity_model ? "true" : "false",
        s->engine153_consumed_dma_frame[0], s->engine153_consumed_dma_frame[1],
        s->engine153_pending[0], s->engine153_pending[1]);
}

static bool get_z8_153_ordinary_retained_start(Object *obj, Error **errp)
{ return NIKON_Z9_LAB(obj)->z8_153_ordinary_retained_start_model; }
static void set_z8_153_ordinary_retained_start(Object *obj, bool value, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    if (!object_dynamic_cast(obj, MACHINE_TYPE_NAME("nikon-z8-bringup")) ||
        s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp, "z8-153-ordinary-retained-start-model requires Z8 at creation and is immutable");
        return;
    }
    s->z8_153_ordinary_retained_start_model = value;
}
static char *get_z8_153_ordinary_retained_start_state(Object *obj, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    return g_strdup_printf("{\"enabled\":%s,\"physical_algorithm_verified\":false,"
        "\"scope\":\"ordinary D980 retained START after ACK retirement; new unconsumed generation; other guards unchanged\","
        "\"jobs\":[%" PRIu64 ",%" PRIu64 "],\"acks\":[%" PRIu64 ",%" PRIu64 "],"
        "\"pending\":[%u,%u],\"retained_start\":[%u,%u]}",
        s->z8_153_ordinary_retained_start_model ? "true" : "false",
        s->engine153_jobs[0], s->engine153_jobs[1],
        s->engine153_acks[0], s->engine153_acks[1],
        s->engine153_pending[0], s->engine153_pending[1],
        s->engine153_config[0][1], s->engine153_config[1][1]);
}

static bool get_engine153_frame_pass_model(Object *obj, Error **errp)
{
    return NIKON_Z9_LAB(obj)->engine153_frame_pass_model;
}

static void set_engine153_frame_pass_model(Object *obj, bool value, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    if (s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp, "engine153-frame-pass-model is immutable after realization"); return;
    }
    s->engine153_frame_pass_model = value;
}

static char *get_engine153_frame_pass_state(Object *obj, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    GString *out = g_string_new(NULL);
    g_string_append_printf(out, "{\"enabled\":%s,\"physical_algorithm_verified\":false,\"scope\":\"neutral full-frame copy; internal multiscale processing and scratch unsupported; statusbit0 and mask20 hypotheses\",\"banks\":[",
        s->engine153_frame_pass_model ? "true" : "false");
    for (unsigned b = 0; b < 2; b++) {
        g_string_append_printf(out, "%s{\"jobs\":%" PRIu64 ",\"bytes\":%" PRIu64
            ",\"pending\":%u,\"acks\":%" PRIu64 ",\"source\":%" PRIu64
            ",\"target\":%" PRIu64 ",\"consumed_dma_frame\":%" PRIu64 "}",
            b ? "," : "", s->engine153_jobs[b], s->engine153_bytes[b], s->engine153_pending[b],
            s->engine153_acks[b], s->engine153_last_source[b], s->engine153_last_target[b],
            s->engine153_consumed_dma_frame[b]);
    }
    g_string_append(out, "]}"); return g_string_free(out, false);
}
