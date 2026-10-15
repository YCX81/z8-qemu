/* SPDX-License-Identifier: GPL-2.0-or-later */
/* C3.11 object180/base153c uses the same original setters/callback as
 * object181/base1540, but registers its OWN INTID130. Never alias pending,
 * parameters, valid length, acknowledgement or IRQ state between them.
 * The initial U16/F0 profile is bounded to RAW58; native bank0 admission
 * and each actual allocation must still be observed before acceptance. */
static void z8_hsfc153c_update_irq(NikonZ9State *s)
{
    if (s->z8_hsfc153c_irq) {
        qemu_set_irq(s->z8_hsfc153c_irq, s->z8_hsfc153c_lossless_model &&
            (s->z8_hsfc153c_pending & s->z8_hsfc153c_subscription));
    }
}

static bool z8_hsfc153c_read(NikonZ9State *s, hwaddr addr, unsigned size,
                            uint64_t *value)
{
    if (!s->z8_hsfc153c_lossless_model || addr < 0x153c0000 || addr >= 0x153c042c) {
        return false;
    }
    unsigned off = addr - 0x153c0000, i, normalized;
    uint32_t mask;
    if (off == 0 || off == 4) { return false; } /* existing boot/reset shadow */
    if (size != 4 || (off & 3)) { goto invalid; }
    if (off == 0x14) { *value = s->z8_hsfc153c_interface; return true; }
    if (off == 0x20) { *value = s->z8_hsfc153c_subscription; return true; }
    if (off == 0x2c) { *value = s->z8_hsfc153c_pending; return true; }
    if (off == 0x194 || off == 0x198 || off == 0x19c) {
        if (!s->z8_hsfc153c_jobs || s->z8_hsfc153c_parameters[0x184 / 4] ||
            s->z8_hsfc153c_parameters[0x188 / 4] || s->z8_hsfc153c_parameters[0x18c / 4] ||
            s->z8_hsfc153c_parameters[0x190 / 4]) { goto invalid; }
        *value = off == 0x198 ? s->z8_hsfc153c_result_bytes : 0;
        return true;
    }
    if (engine1540_descriptor_address(s, 0x15400000 + off, &i, &mask)) {
        if (!mask) { goto invalid; }
        *value = s->z8_hsfc153c_descriptors[i]; return true;
    }
    if (engine1540_parameter_address(s, 0x15400000 + off, &normalized, &mask) && mask) {
        *value = off == 0x44 ? s->engine_pair_init[0][2] : s->z8_hsfc153c_parameters[off / 4];
        return true;
    }
invalid:
    unknown_access(s, addr, 0, size, false); *value = 0; return true;
}

static bool z8_hsfc153c_write(NikonZ9State *s, hwaddr addr, uint64_t value,
                             unsigned size)
{
    if (!s->z8_hsfc153c_lossless_model || addr < 0x153c0000 || addr >= 0x153c042c) {
        return false;
    }
    unsigned off = addr - 0x153c0000, i, normalized;
    uint32_t mask;
    if (off == 0 || off == 4) { return false; }
    if (size != 4 || (off & 3) || value > UINT32_MAX) { goto invalid; }
    if (off == 0x20) {
        s->z8_hsfc153c_subscription = value; z8_hsfc153c_update_irq(s); return true;
    }
    if (off == 0x2c) {
        if (s->z8_hsfc153c_pending & value) {
            Z8EncodedRecord *r = z8_hsfc_ack_record(s, 0x153c0000,
                s->z8_hsfc153c_jobs, s->z8_hsfc153c_record_job);
            if (!r) { goto invalid; }
            /* HSFC_OWNER_ACK:153c never retires1540 or a JPEG owner. */
            if (!nb_ack(&s->output_owner, NB_SLOT_153C, s->z8_hsfc153c_jobs)) { goto invalid; }
            r->acknowledged = true;
            s->z8_hsfc153c_acks++;
        }
        s->z8_hsfc153c_pending &= ~value; z8_hsfc153c_update_irq(s); return true;
    }
    if (s->z8_hsfc153c_pending) { goto invalid; }
    if (off == 0x14) {
        if (value > 1) { goto invalid; }
        s->z8_hsfc153c_interface = value; return true;
    }
    if (off == 8) {
        if (value != 1) { goto invalid; }
        Z8EncodedRecord *r = z8_hsfc_lossless_encode(s, s->z8_hsfc153c_parameters,
            s->z8_hsfc153c_descriptors, s->engine_pair_init[0][2],
            s->z8_hsfc153c_pending, 0x153c0000, s->z8_hsfc153c_jobs + 1);
        if (!r) { goto invalid; }
        s->z8_hsfc153c_jobs++; s->z8_hsfc153c_bytes += r->output_bytes;
        s->z8_hsfc153c_result_bytes = r->output_bytes;
        s->z8_hsfc153c_target = r->target; s->z8_hsfc153c_capacity = r->capacity;
        s->z8_hsfc153c_record_job = r->job;
        s->z8_hsfc153c_pending = 0x100; z8_hsfc153c_update_irq(s);
        return true;
    }
    if (engine1540_descriptor_address(s, 0x15400000 + off, &i, &mask)) {
        if (!mask || (value & ~(uint64_t)mask) || ((i == 0 || i == 5) && value == 1)) { goto invalid; }
        s->z8_hsfc153c_descriptors[i] = value; s->z8_hsfc153c_descriptor_writes++;
        return true;
    }
    if (engine1540_parameter_address(s, 0x15400000 + off, &normalized, &mask) &&
        mask && !(value & ~(uint64_t)mask)) {
        if (off == 0x44) { s->engine_pair_init[0][2] = value; }
        s->z8_hsfc153c_parameters[off / 4] = value; s->z8_hsfc153c_parameter_writes++;
        return true;
    }
invalid:
    unknown_access(s, addr, value, size, true); return true;
}

static bool get_z8_hsfc153c_model(Object *obj, Error **errp)
{ return NIKON_Z9_LAB(obj)->z8_hsfc153c_lossless_model; }

static void set_z8_hsfc153c_model(Object *obj, bool value, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    if (!object_dynamic_cast(obj, MACHINE_TYPE_NAME("nikon-z8-bringup")) ||
        s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp, "select z8-hsfc153c-lossless-model at Z8 realization"); return;
    }
    s->z8_hsfc153c_lossless_model = value;
}

static char *get_z8_hsfc153c_state(Object *obj, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj); GString *out = g_string_new(NULL);
    g_string_append_printf(out, "{\"enabled\":%s,\"base\":356253696,\"irq_intid\":304,"
        "\"jobs\":%" PRIu64 ",\"bytes\":%" PRIu64 ",\"acks\":%" PRIu64
        ",\"pending\":%u,\"last_result_bytes\":%u,\"interface\":%u,\"subscription\":%u,"
        "\"parameter_writes\":%" PRIu64 ",\"descriptor_writes\":%" PRIu64 ",\"fields\":[",
        s->z8_hsfc153c_lossless_model ? "true" : "false", s->z8_hsfc153c_jobs,
        s->z8_hsfc153c_bytes, s->z8_hsfc153c_acks, s->z8_hsfc153c_pending,
        s->z8_hsfc153c_result_bytes, s->z8_hsfc153c_interface, s->z8_hsfc153c_subscription,
        s->z8_hsfc153c_parameter_writes, s->z8_hsfc153c_descriptor_writes);
    for (unsigned i = 0; i < ARRAY_SIZE(s->z8_hsfc153c_parameters); i++) {
        g_string_append_printf(out, "%s%u", i ? "," : "",
            i == 0x44 / 4 ? s->engine_pair_init[0][2] : s->z8_hsfc153c_parameters[i]);
    }
    g_string_append(out, "],\"descriptors\":[");
    for (unsigned i = 0; i < ARRAY_SIZE(s->z8_hsfc153c_descriptors); i++) {
        g_string_append_printf(out, "%s%u", i ? "," : "", s->z8_hsfc153c_descriptors[i]);
    }
    g_string_append(out, "],\"output_ownership_connected\":true,\"ownership_slot\":5,\"physical_codec_verified\":false,\"native_bank_verified\":false,"
        "\"scope\":\"independent153c state and IRQ130; bounded shared historical U16/F0 transaction; native profile/file validation required\"}");
    return g_string_free(out, false);
}
