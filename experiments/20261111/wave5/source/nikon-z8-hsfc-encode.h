/* SPDX-License-Identifier: GPL-2.0-or-later */
/* C120 simulation U16 source -> F0. Native metadata/file acceptance separate. */
static bool z8_hsfc_pending_overlap(NikonZ9State *s, uint64_t target,
                                    uint64_t capacity, uint32_t encoder_base)
{
    if (s->z8_video_data && (s->z8_video_data->packet_owned || s->z8_video_data->frame_ready ||
        s->z8_front_pending[0] || s->z8_front_pending[1]) &&
        graphics_spans_overlap(target, capacity, s->z8_video_data->packet_address, s->z8_video_data->capacity)) { return true; }
    if (s->z8_video_data && s->z8_video_data->output_frames &&
        ((s->engine151_pending[0] & 8) || (s->engine1542_pending[10] & 1) ||
         (s->engine1518[0].pending & 0x08100000))) {
        for (unsigned i = 0; i < 3; i++) {
            uint64_t n = (uint64_t)s->raw_pipeline_stride[i] * s->raw_pipeline_height[i];
            if (graphics_spans_overlap(target, capacity, s->raw_pipeline_y[i], n) ||
                graphics_spans_overlap(target, capacity, s->raw_pipeline_uv[i], n / (s->raw_pipeline_uv_half[i] ? 2 : 1))) { return true; }
        }
    }
    if (s->z8_yuv_data) {
        for (unsigned i = 0; i < G_N_ELEMENTS(s->z8_yuv_data->slots); i++) {
            const Z8YuvSlot *slot = &s->z8_yuv_data->slots[i];
            if (!slot->owned) { continue; }
            for (unsigned p = 0; p < 2; p++) {
                if (graphics_spans_overlap(target, capacity, slot->physical[p], slot->capacity[p])) { return true; }
            }
        }
    }
    if (encoder_base != 0x15400000 && s->engine1540_frame_pending &&
        target < s->engine1540_frame_target + s->engine1540_frame_capacity &&
        s->engine1540_frame_target < target + capacity) { return true; }
    return encoder_base != 0x153c0000 && s->z8_hsfc153c_pending &&
        target < s->z8_hsfc153c_target + s->z8_hsfc153c_capacity &&
        s->z8_hsfc153c_target < target + capacity;
}

static Z8EncodedRecord *z8_hsfc_lossless_encode(NikonZ9State *s,
    const uint32_t *p, const uint32_t *descriptors, uint32_t mode,
    uint32_t pending, uint32_t encoder_base, uint64_t encoder_job)
{
    Z8EncodedRecord *result = NULL;
    unsigned owner_slot = encoder_base == 0x15400000 ? NB_SLOT_1540 : NB_SLOT_153C;
    bool owner_reserved = false;
    uint8_t *input = NULL, *encoded = NULL;
    char *input_sha = NULL, *output_sha = NULL, *readback_sha = NULL;
    size_t written = 0;
    uint64_t source = p[0x400 / 4] | (uint64_t)p[0x404 / 4] << 32;
    uint64_t target = p[0x420 / 4] | (uint64_t)p[0x424 / 4] << 32;
    /* RAW58 observed the original D0=1 compression path. RAW57's mode2,
     * D4=0 request is fixed-length transfer and MUST NOT produce F0 here:
     * its native caller ignores entropy length and suppresses MakerNote96.
     * D4 is not allocation capacity. Capacity below is RAW58's actual
     * record+50 RAW reservation. The native runner must re-check this bound
     * against each current allocation before START; it is a limited profile. */
    const uint32_t input_bytes = 8288u * 2756u, capacity = 68712960u;
    if ((encoder_base != 0x15400000 && encoder_base != 0x153c0000) ||
        !s->output_owner.enabled || !s->z8_hsfc1540_lossless_model || s->producer_journal.enabled ||
        !s->engine1540_lossless14_model || pending ||
        !encoder_job || s->z8_hsfc1540_jobs == UINT64_MAX ||
        p[0x40 / 4] != 0x10000 || mode != 0 || p[0x48 / 4] ||
        p[0x4c / 4] != 4140 || p[0x50 / 4] != 2756 ||
        p[0x408 / 4] != 8288 || p[0x428 / 4] != 8288 || p[0xd4 / 4] != input_bytes ||
        p[0x54 / 4] != 0x11 || p[0x58 / 4] != 2048 || p[0x1a0 / 4] ||
        p[0x184 / 4] || p[0x188 / 4] || p[0x18c / 4] || p[0x190 / 4] ||
        p[0x1d8 / 4] || p[0x1dc / 4] || ((source | target) & 31) ||
        !dma_ram_range(s, source, input_bytes) || !dma_ram_range(s, target, capacity) ||
        (source < target + capacity && target < source + input_bytes) ||
        z8_hsfc_pending_overlap(s, target, capacity, encoder_base)) { goto done; }
    const Z8EncodedRecord *slot = &s->z8_hsfc1540_records[s->z8_hsfc1540_jobs % Z8_HSFC_RECORDS];
    if (slot->valid && ((s->engine1540_frame_pending && slot->encoder_base == 0x15400000 &&
                       slot->target == s->engine1540_frame_target) ||
                      (s->z8_hsfc153c_pending && slot->encoder_base == 0x153c0000 &&
                       slot->target == s->z8_hsfc153c_target))) { goto done; }
    static const uint32_t codes[15] = {0x3e00,0x3f00,0x3d00,0x3800,0x3000,
        0x2000,0x1000,0,0x1800,0x2800,0x3400,0x3a00,0x3c00,0x3f80,0x3fc0};
    static const uint32_t lengths[15] = {6,7,6,5,4,3,3,2,3,3,4,5,6,8,8};
    for (unsigned i = 0; i < 15; i++) {
        if (p[0x5c / 4 + i] != codes[i] || p[0x98 / 4 + i] != lengths[i]) { goto done; }
    }
    for (unsigned i = 0; i < 10; i++) {
        unsigned lane = i % 5;
        if (descriptors[i] != (lane == 0 ? 2 : lane == 1 ? 0xff : 0)) { goto done; }
    }
    Z8ISPRecord *parent = NULL;
    for (unsigned i = 0; i < Z8_HSFC_RECORDS; i++) {
        Z8ISPRecord *r = &s->z8_hsfc151_records[i];
        const Z8DecodeRecord *d = &s->z8_hsfc1530_records[i];
        const Z8HSFCFrameRecord *f = &s->z8_hsfc152c_records[i];
        if ((d->valid && target < d->target + d->output_bytes && d->target < target + capacity) ||
            (f->valid && target < f->physical + f->packet_bytes && f->physical < target + capacity)) { goto done; }
        if (!r->valid && !r->preview_valid) { continue; }
        uint64_t pa[3] = {r->raw_target, r->y, r->uv};
        uint64_t n[3] = {r->raw_bytes, r->plane_bytes, r->plane_bytes};
        for (unsigned k = 0; k < 3; k++) {
            if (k == 0 ? !r->valid : !r->preview_valid) { continue; }
            if (target < pa[k] + n[k] && pa[k] < target + capacity) { goto done; }
        }
        if (r->valid && r->raw_target == source) {
            if (parent || !r->acknowledged || r->encoded || r->raw_bytes != input_bytes) { goto done; }
            parent = r;
        }
    }
    if (!parent) { goto done; }
    input = g_try_malloc(input_bytes); encoded = g_try_malloc(capacity);
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
    /* HSFC_OWNER_RESERVE: only this bank/job may cancel its acquired lease. */
    NBSpan output_span = {target, written};
    if (!nb_reserve(&s->output_owner, owner_slot, encoder_job, parent->frame_id, &output_span, 1)) { goto done; }
    owner_reserved = true;
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
    /* HSFC_OWNER_COMPLETE: full encoded RAM readback precedes publication. */
    if (!nb_complete(&s->output_owner, owner_slot, encoder_job)) { goto done; }
    Z8EncodedRecord *r = &s->z8_hsfc1540_records[s->z8_hsfc1540_jobs % Z8_HSFC_RECORDS];
    memset(r, 0, sizeof(*r));
    r->job = ++s->z8_hsfc1540_jobs; r->encoder_job = encoder_job; r->isp_job = parent->job;
    r->frame_id = parent->frame_id; r->captured_ns = parent->captured_ns;
    r->completed_ns = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    r->source = source; r->target = target; r->input_bytes = input_bytes;
    r->output_bytes = written; r->capacity = capacity;
    r->encoder_base = encoder_base; r->output_fnv64 = fnv;
    g_strlcpy(r->source_sha256, input_sha, sizeof(r->source_sha256));
    g_strlcpy(r->output_sha256, readback_sha, sizeof(r->output_sha256));
    r->valid = true; parent->encoded = true;
    fprintf(stderr, "%s job=%" PRIu64 " frame=%" PRIu64
        " isp_job=%" PRIu64 " source=%" PRIx64 " target=%" PRIx64
        " bytes=%zu sha256=%s; actual U16 source, F0 DMA and full readback before IRQ\n",
        encoder_base == 0x15400000 ? "Z8_HSFC1540_F0" : "Z8_HSFC153C_F0",
        r->job, r->frame_id, r->isp_job, source, target, written, readback_sha);
    result = r;
done:
    /* HSFC_OWNER_CANCEL: no failed pre-reserve request can cancel another job. */
    if (!result && owner_reserved) { nb_cancel(&s->output_owner, owner_slot); }
    g_free(input); g_free(encoded); g_free(input_sha); g_free(output_sha); g_free(readback_sha);
    return result;
}

static bool z8_hsfc1540_encode(NikonZ9State *s)
{
    Z8EncodedRecord *r = z8_hsfc_lossless_encode(s, s->engine1540_parameters,
        s->engine1540_descriptors, s->engine_pair_init[1][2],
        s->engine1540_frame_pending, 0x15400000, s->engine1540_frame_jobs + 1);
    if (!r) { return false; }
    s->engine1540_frame_jobs++; s->engine1540_frame_bytes += r->output_bytes;
    s->engine1540_frame_frame = r->frame_id;
    s->engine1540_frame_source = r->source; s->engine1540_frame_target = r->target;
    s->engine1540_frame_capacity = r->capacity;
    s->engine1540_hsfc_record_job = r->job;
    s->engine1540_frame_hash = s->engine1540_frame_readback = r->output_fnv64;
    s->engine1540_frame_result_bytes = r->output_bytes;
    s->engine1540_frame_pending = 0x100;
    engine1540_frame_update_irq(s);
    return true;
}

static Z8EncodedRecord *z8_hsfc_ack_record(NikonZ9State *s, uint32_t encoder_base,
                                         uint64_t encoder_job, uint64_t record_job)
{
    Z8EncodedRecord *found = NULL;
    for (unsigned i = 0; i < Z8_HSFC_RECORDS; i++) {
        Z8EncodedRecord *r = &s->z8_hsfc1540_records[i];
        if (r->job != record_job || r->encoder_base != encoder_base || r->encoder_job != encoder_job) { continue; }
        if (found || !r->valid || r->acknowledged) { return NULL; }
        found = r;
    }
    return found;
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
        g_string_append_printf(out, "%s{\"job\":%" PRIu64 ",\"encoder_job\":%" PRIu64
            ",\"valid\":%s,\"acknowledged\":%s,\"isp_job\":%" PRIu64
            ",\"frame_id\":%" PRIu64 ",\"captured_ns\":%" PRIu64 ",\"completed_ns\":%" PRIu64
            ",\"source\":%" PRIu64 ",\"target\":%" PRIu64 ",\"input_bytes\":%u,\"output_bytes\":%u,\"capacity\":%u,\"encoder_base\":%u,"
            "\"source_sha256\":\"%s\",\"output_sha256\":\"%s\"}",
            comma ? "," : "", r->job, r->encoder_job, r->valid ? "true" : "false",
            r->acknowledged ? "true" : "false", r->isp_job,
            r->frame_id, r->captured_ns, r->completed_ns, r->source, r->target,
            r->input_bytes, r->output_bytes, r->capacity, r->encoder_base, r->source_sha256, r->output_sha256);
        comma = true;
    }
    g_string_append(out, "],\"width\":4140,\"height\":2756,\"bits\":14,\"predictors\":[2048,2048,2048,2048],"
        "\"encoding\":\"nikon-f0-lossless14\",\"hash_basis\":\"actual source RAM and full encoded target readback\","
        "\"output_ownership_connected\":true,\"native_nef_saved\":false,\"raw_precapture_verified\":false,\"physical_codec_verified\":false}");
    return g_string_free(out, false);
}
