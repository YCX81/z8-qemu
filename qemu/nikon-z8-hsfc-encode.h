/* SPDX-License-Identifier: GPL-2.0-or-later */
/* C120 simulation U16 source -> F0. Native metadata/file acceptance separate. */
static bool z8_hsfc1540_encode(NikonZ9State *s)
{
    const uint32_t *p = s->engine1540_parameters;
    uint64_t source = p[0x400 / 4] | (uint64_t)p[0x404 / 4] << 32;
    uint64_t target = p[0x420 / 4] | (uint64_t)p[0x424 / 4] << 32;
    /* RAW58 observed the original D0=1 compression path. RAW57's mode2,
     * D4=0 request is fixed-length transfer and MUST NOT produce F0 here:
     * its native caller ignores entropy length and suppresses MakerNote96.
     * D4 is not allocation capacity. Capacity below is RAW58's actual
     * record+50 RAW reservation. The native runner must re-check this bound
     * against each current allocation before START; it is a limited profile. */
    const uint32_t input_bytes = 8288u * 2756u, capacity = 68712960u;
    if (!s->z8_hsfc1540_lossless_model || s->producer_journal.enabled ||
        !s->engine1540_lossless14_model || s->engine1540_frame_pending ||
        s->z8_hsfc1540_jobs == UINT64_MAX ||
        p[0x40 / 4] != 0x10000 || s->engine_pair_init[1][2] != 0 || p[0x48 / 4] ||
        p[0x4c / 4] != 4140 || p[0x50 / 4] != 2756 ||
        p[0x408 / 4] != 8288 || p[0x428 / 4] != 8288 || p[0xd4 / 4] != input_bytes ||
        p[0x54 / 4] != 0x11 || p[0x58 / 4] != 2048 || p[0x1a0 / 4] ||
        p[0x184 / 4] || p[0x188 / 4] || p[0x18c / 4] || p[0x190 / 4] ||
        p[0x1d8 / 4] || p[0x1dc / 4] || ((source | target) & 31) ||
        !dma_ram_range(s, source, input_bytes) || !dma_ram_range(s, target, capacity) ||
        (source < target + capacity && target < source + input_bytes)) { return false; }
    static const uint32_t codes[15] = {0x3e00,0x3f00,0x3d00,0x3800,0x3000,
        0x2000,0x1000,0,0x1800,0x2800,0x3400,0x3a00,0x3c00,0x3f80,0x3fc0};
    static const uint32_t lengths[15] = {6,7,6,5,4,3,3,2,3,3,4,5,6,8,8};
    for (unsigned i = 0; i < 15; i++) {
        if (p[0x5c / 4 + i] != codes[i] || p[0x98 / 4 + i] != lengths[i]) { return false; }
    }
    for (unsigned i = 0; i < ARRAY_SIZE(s->engine1540_descriptors); i++) {
        unsigned lane = i % 5;
        if (s->engine1540_descriptors[i] != (lane == 0 ? 2 : lane == 1 ? 0xff : 0)) { return false; }
    }
    Z8ISPRecord *parent = NULL;
    for (unsigned i = 0; i < Z8_HSFC_RECORDS; i++) {
        Z8ISPRecord *r = &s->z8_hsfc151_records[i];
        const Z8DecodeRecord *d = &s->z8_hsfc1530_records[i];
        const Z8HSFCFrameRecord *f = &s->z8_hsfc152c_records[i];
        if ((d->valid && target < d->target + d->output_bytes && d->target < target + capacity) ||
            (f->valid && target < f->physical + f->packet_bytes && f->physical < target + capacity)) { return false; }
        if (!r->valid && !r->preview_valid) { continue; }
        uint64_t pa[3] = {r->raw_target, r->y, r->uv};
        uint64_t n[3] = {r->raw_bytes, r->plane_bytes, r->plane_bytes};
        for (unsigned k = 0; k < 3; k++) {
            if (k == 0 ? !r->valid : !r->preview_valid) { continue; }
            if (target < pa[k] + n[k] && pa[k] < target + capacity) { return false; }
        }
        if (r->valid && r->raw_target == source) {
            if (parent || !r->acknowledged || r->encoded || r->raw_bytes != input_bytes) { return false; }
            parent = r;
        }
    }
    if (!parent) { return false; }
    bool success = false;
    uint8_t *input = g_try_malloc(input_bytes), *encoded = g_try_malloc(capacity);
    char *input_sha = NULL, *output_sha = NULL, *readback_sha = NULL;
    size_t written = 0;
    const uint16_t seeds[4] = {2048, 2048, 2048, 2048};
    if (!input || !encoded || address_space_read(&address_space_memory, source,
        MEMTXATTRS_UNSPECIFIED, input, input_bytes) != MEMTX_OK) { goto done; }
    input_sha = g_compute_checksum_for_data(G_CHECKSUM_SHA256, input, input_bytes);
    if (strcmp(input_sha, parent->raw_sha256) ||
        !nikon_z8_lossless14_u16_encode(input, input_bytes, 4140, 2756, 8288,
                                      seeds, encoded, capacity, &written)) { goto done; }
    output_sha = g_compute_checksum_for_data(G_CHECKSUM_SHA256, encoded, written);
    uint64_t fnv = UINT64_C(14695981039346656037);
    for (size_t i = 0; i < written; i++) { fnv = (fnv ^ encoded[i]) * UINT64_C(1099511628211); }
    /* Reused allocation retires old receipts before any new DMA. No pixels
     * are kept in these bounded records or restored from an older frame. */
    for (unsigned i = 0; i < Z8_HSFC_RECORDS; i++) {
        Z8EncodedRecord *r = &s->z8_hsfc1540_records[i];
        if (r->valid && target < r->target + r->output_bytes && r->target < target + capacity) { r->valid = false; }
    }
    if (address_space_write(&address_space_memory, target, MEMTXATTRS_UNSPECIFIED, encoded, written) != MEMTX_OK ||
        address_space_read(&address_space_memory, target, MEMTXATTRS_UNSPECIFIED, encoded, written) != MEMTX_OK) { goto done; }
    readback_sha = g_compute_checksum_for_data(G_CHECKSUM_SHA256, encoded, written);
    if (strcmp(output_sha, readback_sha)) { goto done; }
    Z8EncodedRecord *r = &s->z8_hsfc1540_records[s->z8_hsfc1540_jobs % Z8_HSFC_RECORDS];
    memset(r, 0, sizeof(*r));
    r->job = ++s->z8_hsfc1540_jobs; r->isp_job = parent->job;
    r->frame_id = parent->frame_id; r->captured_ns = parent->captured_ns;
    r->completed_ns = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    r->source = source; r->target = target; r->input_bytes = input_bytes;
    r->output_bytes = written; r->capacity = capacity;
    g_strlcpy(r->source_sha256, input_sha, sizeof(r->source_sha256));
    g_strlcpy(r->output_sha256, readback_sha, sizeof(r->output_sha256));
    r->valid = true; parent->encoded = true;
    s->engine1540_frame_jobs++; s->engine1540_frame_bytes += written;
    s->engine1540_frame_frame = parent->frame_id;
    s->engine1540_frame_source = source; s->engine1540_frame_target = target;
    s->engine1540_frame_hash = fnv; s->engine1540_frame_readback = fnv;
    s->engine1540_frame_result_bytes = written;
    s->engine1540_frame_pending = 0x100;
    engine1540_frame_update_irq(s);
    fprintf(stderr, "Z8_HSFC1540_F0 job=%" PRIu64 " frame=%" PRIu64
        " isp_job=%" PRIu64 " source=%" PRIx64 " target=%" PRIx64
        " bytes=%zu sha256=%s; actual U16 source, F0 DMA and full readback before IRQ\n",
        r->job, r->frame_id, r->isp_job, source, target, written, readback_sha);
    success = true;
done:
    g_free(input); g_free(encoded); g_free(input_sha); g_free(output_sha); g_free(readback_sha);
    return success;
}

static bool get_z8_hsfc1540_model(Object *obj, Error **errp)
{ return NIKON_Z9_LAB(obj)->z8_hsfc1540_lossless_model; }

static void set_z8_hsfc1540_model(Object *obj, bool value, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    if (!object_dynamic_cast(obj, MACHINE_TYPE_NAME("nikon-z8-bringup")) || s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp, "select z8-hsfc1540-lossless-model at Z8 realization"); return;
    }
    s->z8_hsfc1540_lossless_model = value;
}

static char *get_z8_hsfc1540_state(Object *obj, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    GString *out = g_string_new(NULL);
    g_string_append_printf(out, "{\"enabled\":%s,\"jobs\":%" PRIu64 ",\"records\":[",
        s->z8_hsfc1540_lossless_model ? "true" : "false", s->z8_hsfc1540_jobs);
    bool comma = false;
    for (unsigned i = 0; i < Z8_HSFC_RECORDS; i++) {
        const Z8EncodedRecord *r = &s->z8_hsfc1540_records[i];
        if (!r->job) { continue; }
        g_string_append_printf(out, "%s{\"job\":%" PRIu64 ",\"valid\":%s,\"isp_job\":%" PRIu64
            ",\"frame_id\":%" PRIu64 ",\"captured_ns\":%" PRIu64 ",\"completed_ns\":%" PRIu64
            ",\"source\":%" PRIu64 ",\"target\":%" PRIu64 ",\"input_bytes\":%u,\"output_bytes\":%u,\"capacity\":%u,"
            "\"source_sha256\":\"%s\",\"output_sha256\":\"%s\"}",
            comma ? "," : "", r->job, r->valid ? "true" : "false", r->isp_job,
            r->frame_id, r->captured_ns, r->completed_ns, r->source, r->target,
            r->input_bytes, r->output_bytes, r->capacity, r->source_sha256, r->output_sha256);
        comma = true;
    }
    g_string_append(out, "],\"width\":4140,\"height\":2756,\"bits\":14,\"predictors\":[2048,2048,2048,2048],"
        "\"encoding\":\"nikon-f0-lossless14\",\"hash_basis\":\"actual source RAM and full encoded target readback\","
        "\"native_nef_saved\":false,\"raw_precapture_verified\":false,\"physical_codec_verified\":false}");
    return g_string_free(out, false);
}
