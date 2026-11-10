/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Original identity property definitions, included once at their original position. */
static bool get_engine153_inplace_identity_model(Object *obj, Error **errp)
{
    return NIKON_Z9_LAB(obj)->engine153_inplace_identity_model;
}

static void set_engine153_inplace_identity_model(Object *obj, bool value, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    if (s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp, "engine153-inplace-identity-model is immutable after realization"); return;
    }
    s->engine153_inplace_identity_model = value;
}

static char *get_engine153_inplace_identity_state(Object *obj, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    return g_strdup_printf("{\"enabled\":%s,\"jobs\":%" PRIu64 ",\"bytes\":%" PRIu64
        ",\"acks\":%" PRIu64 ",\"pending\":%u,\"source\":%" PRIu64
        ",\"target\":%" PRIu64 ",\"consumed_dma_frame\":%" PRIu64
        ",\"hsfc_input_enabled\":%s,\"source_kind\":%u,\"consumed_decoder_job\":%" PRIu64
        ",\"hsfc_frame\":%" PRIu64
        ",\"last_actual_ram_fnv1a64\":%" PRIu64
        ",\"physical_algorithm_verified\":false,\"scope\":\"diagnostic neutral NR convention; actual full RAM read/write in place; D8/pyramid/scratch roles unproved and untouched\"}",
        s->engine153_inplace_identity_model ? "true" : "false", s->engine153_jobs[1],
        s->engine153_bytes[1], s->engine153_acks[1], s->engine153_pending[1],
        s->engine153_last_source[1], s->engine153_last_target[1],
        s->engine153_consumed_dma_frame[1], s->z8_hsfc1538_identity_model ? "true" : "false",
        s->engine153_source_kind[1], s->engine153_consumed_decoder_job[1], s->engine153_hsfc_frame[1],
        s->engine153_identity_hash[1]);
}

static char *get_z8_hsfc_nr_state(Object *obj, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    GString *out = g_string_new(NULL);
    g_string_append_printf(out, "{\"enabled_banks\":[%s,%s],\"banks\":[",
        s->z8_hsfc1534_identity_model ? "true" : "false", s->z8_hsfc1538_identity_model ? "true" : "false");
    for (unsigned b = 0; b < 2; b++) {
        g_string_append_printf(out, "%s{\"bank\":%u,\"jobs\":%" PRIu64 ",\"bytes\":%" PRIu64
            ",\"acks\":%" PRIu64 ",\"pending\":%u,\"source\":%" PRIu64 ",\"target\":%" PRIu64
            ",\"source_kind\":%u,\"consumed_dma_frame\":%" PRIu64
            ",\"consumed_decoder_job\":%" PRIu64 ",\"hsfc_frame\":%" PRIu64
            ",\"last_actual_ram_fnv1a64\":%" PRIu64 "}",
            b ? "," : "", b, s->engine153_jobs[b], s->engine153_bytes[b], s->engine153_acks[b],
            s->engine153_pending[b], s->engine153_last_source[b], s->engine153_last_target[b],
            s->engine153_source_kind[b], s->engine153_consumed_dma_frame[b],
            s->engine153_consumed_decoder_job[b], s->engine153_hsfc_frame[b], s->engine153_identity_hash[b]);
    }
    g_string_append(out, "],\"physical_algorithm_verified\":false,\"native_lifetime_verified\":false,"
        "\"scope\":\"Neutral actual RAM processing; distinct banks and per-decoder-generation single-consumption records\"}");
    return g_string_free(out, false);
}

static bool get_z8_hsfc1538_identity_model(Object *obj, Error **errp)
{ return NIKON_Z9_LAB(obj)->z8_hsfc1538_identity_model; }

static void set_z8_hsfc1538_identity_model(Object *obj, bool value, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    if (!object_dynamic_cast(obj, MACHINE_TYPE_NAME("nikon-z8-bringup")) || s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp, "z8-hsfc1538-identity-model requires Z8 at machine creation"); return;
    }
    s->z8_hsfc1538_identity_model = value;
}

static bool get_z8_hsfc1534_identity_model(Object *obj, Error **errp)
{ return NIKON_Z9_LAB(obj)->z8_hsfc1534_identity_model; }

static void set_z8_hsfc1534_identity_model(Object *obj, bool value, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    if (!object_dynamic_cast(obj, MACHINE_TYPE_NAME("nikon-z8-bringup")) || s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp, "z8-hsfc1534-identity-model requires Z8 at machine creation"); return;
    }
    s->z8_hsfc1534_identity_model = value;
}
