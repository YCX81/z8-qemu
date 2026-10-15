/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Explicit functional internal Bayer -> native receiver RAM model.
 * NZ8BAYR1 is an authored lossless simulation transport, NOT Nikon's codec.
 * Only the current source frame uses host scratch; retained images exist
 * solely in the guest's actual output RAM. Metadata never restores pixels.
 */
static void z8_hsfc152c_irq_update(NikonZ9State *s)
{
    if (s->z8_hsfc152c_irq) {
        qemu_set_irq(s->z8_hsfc152c_irq,
            s->z8_hsfc152c_pixels_model && s->engine152c_config[0][0x14 / 4] == 1 &&
            (s->z8_hsfc152c_pixel_pending & s->engine152c_config[0][0x18 / 4]));
    }
}

static void z8_hsfc152c_pixels_begin(NikonZ9State *s)
{
    s->z8_hsfc152c_bank = s->z8_hsfc152c_slot = 0;
    s->z8_hsfc152c_bank_written[0] = s->z8_hsfc152c_bank_written[1] = 0;
    s->z8_hsfc152c_refill_notified = s->z8_hsfc152c_waiting_bank = false;
    s->z8_hsfc152c_pixel_failed = false;
}

static void z8_hsfc152c_pixels_reset(NikonZ9State *s)
{
    s->z8_hsfc152c_pixel_cancelled += !!s->z8_hsfc152c_pixel_pending;
    s->z8_hsfc152c_pixel_pending = 0;
    z8_hsfc152c_pixels_begin(s);
    /* Completed RAM is not cleared or restored by a peripheral reset. */
    z8_hsfc152c_irq_update(s);
}

static void z8_hsfc152c_pixels_ack(NikonZ9State *s, uint32_t value)
{
    uint32_t previous = s->z8_hsfc152c_pixel_pending;
    s->z8_hsfc152c_pixel_pending &= ~value;
    bool data_acked = (previous & 3) && !(s->z8_hsfc152c_pixel_pending & 3);
    s->z8_hsfc152c_pixel_acks += data_acked;
    s->z8_hsfc152c_end_acks += !!(previous & value & 4);
    s->z8_hsfc152c_refill_acks += !!(previous & value & 0x10);
    /* Separate watermark after three complete packets, before slot four.
     * This allows original FFA4 handling to populate the other bank. */
    if (data_acked && s->z8_hsfc152c_pending && s->z8_hsfc152c_slot == 3 &&
        !s->z8_hsfc152c_refill_notified) {
        s->z8_hsfc152c_refill_notified = true;
        s->z8_hsfc152c_refills++;
        s->z8_hsfc152c_pixel_pending |= 0x10;
    }
    z8_hsfc152c_irq_update(s);
}

static void z8_hsfc152c_shadow_write(NikonZ9State *s, unsigned bank, unsigned off)
{
    if (!bank && off >= 0x120 && off < 0x160 && s->z8_hsfc152c_pending) {
        unsigned target_bank = (off - 0x120) / 0x20;
        if (target_bank != s->z8_hsfc152c_bank) {
            s->z8_hsfc152c_bank_written[target_bank] |= 1u << ((off & 0x1f) / 4);
        }
    }
    if (!bank && (off == 0x14 || off == 0x18)) { z8_hsfc152c_irq_update(s); }
}

static bool z8_hsfc152c_next_bank(NikonZ9State *s)
{
    const uint32_t *c = s->engine152c_config[0];
    unsigned bank = c[0x108 / 4] & 1;
    uint64_t encoded[4], physical[4];
    if (bank == s->z8_hsfc152c_bank || s->z8_hsfc152c_bank_written[bank] != 0xff) {
        s->z8_hsfc152c_waiting_bank = true;
        return false;
    }
    if (c[0x548 / 4] != 4140 || c[0x54c / 4] != 2756 || c[0x550 / 4] != 0x414800 ||
        c[0x114 / 4] != 3) { goto invalid; }
    for (unsigned i = 0; i < 4; i++) {
        unsigned off = 0x120 + bank * 0x20 + i * 8;
        encoded[i] = c[off / 4] | (uint64_t)c[off / 4 + 1] << 32;
        if ((encoded[i] & 511) || encoded[i] < UINT64_C(0xc00000000) ||
            encoded[i] >= UINT64_C(0xd00000000)) { goto invalid; }
        physical[i] = encoded[i] - UINT64_C(0x400000000);
        if (!dma_ram_range(s, physical[i], 0x414800)) { goto invalid; }
        for (unsigned j = 0; j < i; j++) {
            if (!(physical[i] + 0x414800 <= physical[j] || physical[j] + 0x414800 <= physical[i])) { goto invalid; }
        }
    }
    memcpy(s->z8_hsfc152c_encoded, encoded, sizeof(encoded));
    memcpy(s->z8_hsfc152c_physical, physical, sizeof(physical));
    s->z8_hsfc152c_bank_written[0] = s->z8_hsfc152c_bank_written[1] = 0;
    s->z8_hsfc152c_bank = bank;
    s->z8_hsfc152c_slot = 0;
    s->z8_hsfc152c_refill_notified = s->z8_hsfc152c_waiting_bank = false;
    return true;
invalid:
    s->z8_hsfc152c_pixel_failed = true;
    s->z8_hsfc152c_rejects++;
    unknown_access(s, 0x152c0108, bank, 4, true);
    return false;
}

static void z8_hsfc152c_frame(NikonZ9State *s)
{
    const size_t raw_bytes = 7264u * 2756u, capacity = 0x414800;
    const uint32_t *c = s->engine152c_config[0];
    if (!s->z8_hsfc152c_pixels_model || !s->z8_hsfc152c_pending ||
        !s->engine152c_request_pending || s->z8_hsfc152c_pixel_failed ||
        s->z8_hsfc152c_pixel_pending || c[8 / 4] != 1 || c[0x14 / 4] != 1 || c[0x404 / 4] ||
        !s->raw_pixels || !s->event_timing_active || s->primary_reset_stage ||
        !s->z8_d980_internal_waiting || !(s->engine150c_active & 0x4000) ||
        (s->engine150c_stop_requested & 0x4000) || (s->engine_150c_init[0x18 / 4] & 0x4000)) {
        return;
    }
    if (s->z8_hsfc152c_slot == 4 && !z8_hsfc152c_next_bank(s)) { return; }
    if (!z8_d980_internal_profile(s, s->primary_d980_latched) ||
        s->primary_d980_latched[1] != 0x102c0000 || s->primary_d980_latched[2] != 0x0ac40002 ||
        s->z8_hsfc152c_frames >= UINT32_MAX) { goto invalid; }
    uint64_t id = s->z8_hsfc152c_frames + 1;
    uint64_t when = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    unsigned slot = s->z8_hsfc152c_slot;
    uint64_t physical = s->z8_hsfc152c_physical[slot];
    if (!dma_ram_range(s, physical, capacity)) { goto invalid; }
    if (!s->z8_hsfc152c_raw_cache) {
        s->z8_hsfc152c_raw_cache = primary_d980_pack14(s->raw_pixels, 4140, 2756, 7264);
    }
    if (!nikon_sensor_tag_apply(s->z8_hsfc152c_raw_cache, raw_bytes, 4140, 2756, 7264, 14, id)) {
        goto invalid;
    }
    NBPMeta meta = {4140, 2756, 7264, 14, id, when};
    NBPInfo info;
    size_t packet_bytes = 0;
    uint8_t *packet = g_malloc(capacity);
    if (!nbp_encode(s->z8_hsfc152c_raw_cache, raw_bytes, &meta, packet, capacity, &packet_bytes) ||
        !nbp_header(packet, NBP_HEADER_BYTES, &info)) {
        g_free(packet);
        goto invalid;
    }
    /* A new actual write invalidates overlapping old producer metadata.
     * It never obtains old pixels from these records or from a host archive. */
    for (unsigned i = 0; i < Z8_HSFC_RECORDS; i++) {
        Z8HSFCFrameRecord *old = &s->z8_hsfc152c_records[i];
        if (old->valid && physical < old->physical + old->packet_bytes &&
            old->physical < physical + packet_bytes) { old->valid = false; }
    }
    if (address_space_write(&address_space_memory, physical, MEMTXATTRS_UNSPECIFIED,
                            packet, packet_bytes) != MEMTX_OK) {
        g_free(packet);
        goto invalid;
    }
    Z8HSFCFrameRecord *record = &s->z8_hsfc152c_records[s->z8_hsfc152c_frames % Z8_HSFC_RECORDS];
    s->z8_hsfc152c_record_evictions += record->id != 0;
    memset(record, 0, sizeof(*record));
    record->id = id;
    record->captured_ns = when;
    record->completed_ns = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    record->encoded = s->z8_hsfc152c_encoded[slot];
    record->physical = physical;
    record->packet_bytes = packet_bytes;
    record->raw_crc = info.raw_crc;
    char *hash = g_compute_checksum_for_data(G_CHECKSUM_SHA256, packet, packet_bytes);
    g_strlcpy(record->packet_sha256, hash, sizeof(record->packet_sha256));
    g_free(hash);
    hash = g_compute_checksum_for_data(G_CHECKSUM_SHA256, s->z8_hsfc152c_raw_cache, raw_bytes);
    g_strlcpy(record->raw_sha256, hash, sizeof(record->raw_sha256));
    g_free(hash);
    g_free(packet);
    record->valid = true;
    s->z8_hsfc152c_frames++;
    s->z8_hsfc152c_pixel_bytes += packet_bytes;
    s->z8_hsfc152c_raw_bytes += raw_bytes;
    s->z8_hsfc152c_slot++;
    s->z8_hsfc152c_waiting_bank = s->z8_hsfc152c_slot == 4;
    /* Original bit0 gives B498/20 before EFD8 pop -> job+e0. Bit1 gives
     * B498/10000. Both follow complete actual data in this functional model. */
    s->z8_hsfc152c_pixel_pending = 3;
    z8_hsfc152c_irq_update(s);
    fprintf(stderr, "Z8_HSFC152C_FRAME id=%" PRIu64 " captured_ns=%" PRIu64
        " physical=%" PRIx64 " bytes=%zu raw_bytes=%zu bank=%u slot=%u packet_sha256=%s"
        "; private lossless simulation codec, not Nikon bitstream\n",
        id, when, physical, packet_bytes, raw_bytes, s->z8_hsfc152c_bank, slot, record->packet_sha256);
    return;
invalid:
    s->z8_hsfc152c_pixel_failed = true;
    s->z8_hsfc152c_rejects++;
    unknown_access(s, 0x152c0008, 1, 4, true);
}

static bool get_z8_hsfc152c_pixels_model(Object *obj, Error **errp)
{
    return NIKON_Z9_LAB(obj)->z8_hsfc152c_pixels_model;
}

static void set_z8_hsfc152c_pixels_model(Object *obj, bool value, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    if (!object_dynamic_cast(obj, MACHINE_TYPE_NAME("nikon-z8-bringup")) ||
        s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp, "z8-hsfc152c-pixels-model requires Z8 at machine creation");
        return;
    }
    s->z8_hsfc152c_pixels_model = value;
}

static char *get_z8_hsfc152c_pixels_state(Object *obj, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    GString *out = g_string_new(NULL);
    g_string_append_printf(out, "{\"enabled\":%s,\"pending\":%u,\"frames\":%" PRIu64
        ",\"pixel_bytes\":%" PRIu64 ",\"raw_bytes\":%" PRIu64 ",\"acks\":%" PRIu64
        ",\"cancelled\":%" PRIu64 ",\"rejects\":%" PRIu64
        ",\"ends\":%" PRIu64 ",\"end_acks\":%" PRIu64 ",\"last_end_ns\":%" PRIu64
        ",\"refill_requests\":%" PRIu64 ",\"refill_acks\":%" PRIu64
        ",\"record_evictions\":%" PRIu64 ",\"bank\":%u,\"next_slot\":%u,"
        "\"waiting_for_bank\":%s,\"failed\":%s,\"records\":[",
        s->z8_hsfc152c_pixels_model ? "true" : "false", s->z8_hsfc152c_pixel_pending,
        s->z8_hsfc152c_frames, s->z8_hsfc152c_pixel_bytes, s->z8_hsfc152c_raw_bytes,
        s->z8_hsfc152c_pixel_acks, s->z8_hsfc152c_pixel_cancelled, s->z8_hsfc152c_rejects,
        s->z8_hsfc152c_ends, s->z8_hsfc152c_end_acks, s->z8_hsfc152c_last_end_ns,
        s->z8_hsfc152c_refills, s->z8_hsfc152c_refill_acks, s->z8_hsfc152c_record_evictions,
        s->z8_hsfc152c_bank, s->z8_hsfc152c_slot,
        s->z8_hsfc152c_waiting_bank ? "true" : "false", s->z8_hsfc152c_pixel_failed ? "true" : "false");
    bool comma = false;
    for (unsigned i = 0; i < Z8_HSFC_RECORDS; i++) {
        const Z8HSFCFrameRecord *r = &s->z8_hsfc152c_records[i];
        if (!r->id) { continue; }
        g_string_append_printf(out, "%s{\"id\":%" PRIu64 ",\"valid\":%s,"
            "\"captured_ns\":%" PRIu64 ",\"completed_ns\":%" PRIu64
            ",\"encoded\":%" PRIu64 ",\"physical\":%" PRIu64 ",\"packet_bytes\":%u,"
            "\"raw_crc\":%u,\"packet_sha256\":\"%s\",\"raw_sha256\":\"%s\"}",
            comma ? "," : "", r->id, r->valid ? "true" : "false", r->captured_ns,
            r->completed_ns, r->encoded, r->physical, r->packet_bytes, r->raw_crc,
            r->packet_sha256, r->raw_sha256);
        comma = true;
    }
    g_string_append(out, "],\"physical_codec_verified\":false,\"native_lifetime_verified\":false,"
        "\"raw_precapture_verified\":false,\"scope\":\"Actual guest RAM NZ8BAYR1 packets from authored Bayer input; paired functional codec, not Nikon compression\"}");
    return g_string_free(out, false);
}
