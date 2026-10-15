/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Explicit NZ8BAYR1 paired simulation decoder. Not Nikon's codec.
 * RAW39 supplies a native 4140x2756, 6240-byte-stride output request. This
 * model declares packed12 LE plus rounded 14->12 rescaling as its convention.
 * The conversion loses precision; it is not described as lossless decoding.
 * Historical pixels are read solely from actual guest RAM, never restored.
 */
static void z8_hsfc1530_irq_update(NikonZ9State *s)
{
    if (s->z8_hsfc1530_irq) {
        qemu_set_irq(s->z8_hsfc1530_irq, s->z8_hsfc1530_decode_model &&
            s->engine1530_config[0x14 / 4] == 1 &&
            (s->engine1530_config[0x1c / 4] & s->engine1530_config[0x18 / 4]));
    }
}

static bool z8_hsfc1530_profile(NikonZ9State *s)
{
    const uint32_t *c = s->engine1530_config, *d = s->engine15b0_config;
    if (!s->z8_hsfc1530_decode_model || s->z8_hsfc1530_active || c[0x1c / 4] ||
        c[0x404 / 4] || c[8 / 4] || s->z8_hsfc1530_jobs == UINT64_MAX) { return false; }
    /* Exactly the observed single-buffer profile. Addresses/masks are
     * independently checked below; an unknown nonzero word stays strict. */
    for (unsigned i = 0; i < ARRAY_SIZE(s->engine1530_config); i++) {
        uint32_t expected = 0;
        switch (i * 4) {
        case 0x18: case 0x120: case 0x124: case 0x220: case 0x224: continue;
        case 0x14: case 0x104: case 0x52c: expected = 1; break;
        case 0x504: case 0x518: expected = 0xff; break;
        case 0x510: case 0x524: case 0x530: expected = 2; break;
        case 0x534: expected = 0x414800; break;
        case 0x544: expected = 6240; break;
        case 0x548: expected = 4140; break;
        case 0x54c: expected = 2756; break;
        }
        if (c[i] != expected) { return false; }
    }
    for (unsigned i = 0; i < ARRAY_SIZE(s->engine15b0_config); i++) {
        uint32_t expected = 0;
        switch (i * 4) {
        case 0x2c: expected = 0x83; break;
        case 0x38: case 0x3c: case 0x4c: expected = 1; break;
        case 0x84: expected = 0x102c0ac4; break;
        case 0xa0: expected = 0xfc0; break;
        case 0xa4: expected = 0x48f4; break;
        case 0xa8: expected = 0x49cd; break;
        case 0xac: expected = 0x3f7f; break;
        }
        if (d[i] != expected) { return false; }
    }
    return true;
}

static bool z8_hsfc1530_decode(NikonZ9State *s)
{
    if (!z8_hsfc1530_profile(s)) { return false; }
    const uint32_t *c = s->engine1530_config;
    uint64_t source = c[0x120 / 4] | (uint64_t)c[0x124 / 4] << 32;
    uint64_t target = c[0x220 / 4] | (uint64_t)c[0x224 / 4] << 32;
    const size_t output_bytes = 6240u * 2756u;
    if ((source | target) & 31 || !dma_ram_range(s, source, 0x414800) ||
        !dma_ram_range(s, target, output_bytes) ||
        (source < target + output_bytes && target < source + 0x414800)) { return false; }
    const Z8HSFCFrameRecord *parent = NULL;
    for (unsigned i = 0; i < Z8_HSFC_RECORDS; i++) {
        const Z8HSFCFrameRecord *r = &s->z8_hsfc152c_records[i];
        if (!r->valid) { continue; }
        /* Do not damage another retained encoded image. */
        if (r->physical < target + output_bytes && target < r->physical + r->packet_bytes) { return false; }
        if (r->physical == source) {
            if (parent) { return false; }
            parent = r;
        }
    }
    if (!parent || !parent->packet_bytes || parent->packet_bytes > 0x414800) { return false; }
    uint8_t *packet = g_malloc(parent->packet_bytes), *raw = NULL, *output = NULL;
    char *hash = NULL;
    bool success = false;
    NBPInfo info;
    if (address_space_read(&address_space_memory, source, MEMTXATTRS_UNSPECIFIED,
                            packet, parent->packet_bytes) != MEMTX_OK) { goto done; }
    hash = g_compute_checksum_for_data(G_CHECKSUM_SHA256, packet, parent->packet_bytes);
    if (strcmp(hash, parent->packet_sha256) || !nbp_header(packet, parent->packet_bytes, &info) ||
        info.packet_bytes != parent->packet_bytes || info.meta.frame_id != parent->id ||
        info.meta.virtual_ns != parent->captured_ns || info.meta.width != 4140 ||
        info.meta.height != 2756 || info.meta.stride != 7264 || info.meta.bits != 14 ||
        info.raw_bytes != 7264u * 2756u || info.raw_crc != parent->raw_crc) { goto done; }
    g_clear_pointer(&hash, g_free);
    raw = g_malloc(info.raw_bytes);
    if (!nbp_decode(packet, parent->packet_bytes, raw, info.raw_bytes, &info)) { goto done; }
    hash = g_compute_checksum_for_data(G_CHECKSUM_SHA256, raw, info.raw_bytes);
    if (strcmp(hash, parent->raw_sha256)) { goto done; }
    g_clear_pointer(&hash, g_free);
    output = g_malloc0(output_bytes);
    for (unsigned y = 0; y < 2756; y++) {
        const uint8_t *row = raw + (size_t)y * 7264;
        uint8_t *out = output + (size_t)y * 6240;
        for (unsigned x = 0; x < 4140; x += 2) {
            unsigned a = (engine151_capture_sample(row, x, 14) * 4095u + 8191u) / 16383u;
            unsigned b = (engine151_capture_sample(row, x + 1, 14) * 4095u + 8191u) / 16383u;
            out[x * 3 / 2] = a;
            out[x * 3 / 2 + 1] = (a >> 8) | ((b & 15) << 4);
            out[x * 3 / 2 + 2] = b >> 4;
        }
    }
    /* Retire overlapping receipts before writing. Metadata contains no
     * archived pixels, and failed writes must not leave valid old claims. */
    for (unsigned i = 0; i < Z8_HSFC_RECORDS; i++) {
        Z8DecodeRecord *r = &s->z8_hsfc1530_records[i];
        if (r->valid && target < r->target + r->output_bytes && r->target < target + output_bytes) { r->valid = false; }
    }
    if (address_space_write(&address_space_memory, target, MEMTXATTRS_UNSPECIFIED,
                            output, output_bytes) != MEMTX_OK) { goto done; }
    Z8DecodeRecord *r = &s->z8_hsfc1530_records[s->z8_hsfc1530_jobs % Z8_HSFC_RECORDS];
    memset(r, 0, sizeof(*r));
    r->job = ++s->z8_hsfc1530_jobs;
    r->frame_id = parent->id;
    r->captured_ns = parent->captured_ns;
    r->completed_ns = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    r->source = source; r->target = target;
    r->packet_bytes = parent->packet_bytes; r->output_bytes = output_bytes;
    g_strlcpy(r->packet_sha256, parent->packet_sha256, sizeof(r->packet_sha256));
    hash = g_compute_checksum_for_data(G_CHECKSUM_SHA256, output, output_bytes);
    g_strlcpy(r->output_sha256, hash, sizeof(r->output_sha256));
    r->valid = true;
    s->z8_hsfc1530_bytes += output_bytes;
    s->z8_hsfc1530_active = false;
    /* The observed request contains one source and one destination. Both
     * the frame and the complete job finish only after the actual DMA.
     * ISR40c30c88 -> 40cbe060 maps bit1 to worker flag1 and bit2 to flag4.
     * Native one-shot40cbded8..dee4 waits for flag4. Continuous mode instead
     * waits for a software stop request; its protocol is not modeled here. */
    s->engine1530_config[0x1c / 4] |= 6;
    fprintf(stderr, "Z8_HSFC1530_DECODE job=%" PRIu64 " frame=%" PRIu64
        " captured_ns=%" PRIu64 " source=%" PRIx64 " target=%" PRIx64
        " bytes=%zu sha256=%s; explicit packed12 simulation conversion\n",
        r->job, r->frame_id, r->captured_ns, source, target, output_bytes, r->output_sha256);
    success = true;
done:
    g_free(hash); g_free(output); g_free(raw); g_free(packet);
    return success;
}

static bool get_z8_hsfc1530_decode_model(Object *obj, Error **errp)
{ return NIKON_Z9_LAB(obj)->z8_hsfc1530_decode_model; }

static void set_z8_hsfc1530_decode_model(Object *obj, bool value, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    if (!object_dynamic_cast(obj, MACHINE_TYPE_NAME("nikon-z8-bringup")) || s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp, "z8-hsfc1530-decode-model requires Z8 at machine creation"); return;
    }
    s->z8_hsfc1530_decode_model = value;
}

static char *get_z8_hsfc1530_decode_state(Object *obj, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    GString *out = g_string_new(NULL);
    g_string_append_printf(out, "{\"enabled\":%s,\"active\":%s,\"pending\":%u,\"jobs\":%" PRIu64
        ",\"bytes\":%" PRIu64 ",\"acks\":%" PRIu64 ",\"stops\":%" PRIu64
        ",\"completion_acks\":%" PRIu64 ",\"cancelled\":%" PRIu64 ",\"rejects\":%" PRIu64 ",\"records\":[",
        s->z8_hsfc1530_decode_model ? "true" : "false", s->z8_hsfc1530_active ? "true" : "false",
        s->engine1530_config[0x1c / 4], s->z8_hsfc1530_jobs, s->z8_hsfc1530_bytes, s->z8_hsfc1530_acks,
        s->z8_hsfc1530_stops, s->z8_hsfc1530_completion_acks, s->z8_hsfc1530_cancelled, s->z8_hsfc1530_rejects);
    bool comma = false;
    for (unsigned i = 0; i < Z8_HSFC_RECORDS; i++) {
        const Z8DecodeRecord *r = &s->z8_hsfc1530_records[i];
        if (!r->job) { continue; }
        g_string_append_printf(out, "%s{\"job\":%" PRIu64 ",\"valid\":%s,\"frame_id\":%" PRIu64
            ",\"captured_ns\":%" PRIu64 ",\"completed_ns\":%" PRIu64 ",\"source\":%" PRIu64
            ",\"target\":%" PRIu64 ",\"packet_bytes\":%u,\"output_bytes\":%u,\"width\":4140,"
            "\"height\":2756,\"stride\":6240,\"input_bits\":14,\"output_bits\":12,"
            "\"packet_sha256\":\"%s\",\"output_sha256\":\"%s\",\"nr_consumed\":%s,\"nr_bank\":%u}",
            comma ? "," : "", r->job, r->valid ? "true" : "false", r->frame_id, r->captured_ns,
            r->completed_ns, r->source, r->target, r->packet_bytes, r->output_bytes, r->packet_sha256, r->output_sha256,
            r->nr_consumed ? "true" : "false", r->nr_bank);
        comma = true;
    }
    g_string_append(out, "],\"physical_codec_verified\":false,\"native_lifetime_verified\":false,"
        "\"precision_preserved\":false,\"raw_precapture_verified\":false,"
        "\"scope\":\"Actual retained guest RAM packets decoded to packed12 with explicit rounded14-to12 conversion; not Nikon codec\"}");
    return g_string_free(out, false);
}
