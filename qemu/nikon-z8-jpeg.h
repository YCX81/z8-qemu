/* SPDX-License-Identifier: GPL-2.0-or-later */
static void engine15e2_jpeg_irq_update(NikonZ9State *s, unsigned b)
{
    if (s->engine15e2_jpeg_irq[b]) {
        qemu_set_irq(s->engine15e2_jpeg_irq[b],
            !!(s->engine15e2_jpeg_pending[b] & s->engine15e2_config[b][0x484 / 4]));
    }
}



/* The original CPU can add symmetric NV16 black bars around our actual
 * A2/B4 output. Bind the submitted source to this completed producer and
 * validate the visible border bytes; never create a RAM/FIFO alias. */
static int engine15e2_owned_source(NikonZ9State *s, unsigned b)
{
    if (s->engine15e2_bank1_model) { return jpeg_source_owner(jpeg_request_source(s,b)); }
    int owner = capture_nv16_jpeg_owner(s, jpeg_request_words(s,b));
    if (owner >= 0) { return owner; }
    /* Original517 reads the same retained small RAM after its first commit.
     * This is another actual codec job, not another sensor generation. */
    if (s->engine15e2_preflight_model && s->capture_nv16_model && s->capture_nv16_jobs) {
        const uint32_t *p = jpeg_request_words(s,b);
        for (unsigned j = 0; j < 3; j++) {
            if (p[0x418 / 4] == s->capture_nv16_w[j] && p[0x41c / 4] == s->capture_nv16_h[j] &&
                p[0x410 / 4] == s->capture_nv16_stride[j] &&
                ((uint64_t)p[0x430 / 4] << 4) == s->capture_nv16_y[j] &&
                ((uint64_t)p[0x434 / 4] << 4) == s->capture_nv16_uv[j]) { return j; }
        }
    }
    if (!s->engine15e2_preflight_model || !s->capture_thumbnail_model ||
        !s->capture_thumbnail_jobs || s->capture_thumbnail_failed) { return -1; }
    const uint32_t *c = jpeg_request_words(s,b);
    unsigned w = c[0x418 / 4], h = c[0x41c / 4], stride = c[0x410 / 4];
    unsigned active_h = s->capture_thumbnail_h;
    if (w != s->capture_thumbnail_w || h < active_h || h - active_h > 32 ||
        ((h - active_h) & 1) || stride != s->capture_thumbnail_stride ||
        c[0x414 / 4] != stride || stride < w || !w || !active_h) { return -1; }
    size_t pad = (h - active_h) / 2, head = pad * stride, plane = (size_t)h * stride;
    uint64_t y = (uint64_t)c[0x430 / 4] << 4, uv = (uint64_t)c[0x434 / 4] << 4;
    if (s->capture_thumbnail_y < head || s->capture_thumbnail_uv < head ||
        y != s->capture_thumbnail_y - head || uv != s->capture_thumbnail_uv - head ||
        !plane || plane > 4 * MiB || !dma_ram_range(s,y,plane) || !dma_ram_range(s,uv,plane)) { return -1; }
    if (pad) {
        uint8_t *row = g_malloc(stride);
        for (unsigned p = 0; p < 2; p++) {
            for (unsigned r = 0; r < h; r++) {
                if (r >= pad && r < pad + active_h) { continue; }
                uint64_t address = (p ? uv : y) + (uint64_t)r * stride;
                if (address_space_read(&address_space_memory,address,MEMTXATTRS_UNSPECIFIED,row,stride) != MEMTX_OK) {
                    g_free(row); return -1;
                }
                for (unsigned x = 0; x < w; x++) {
                    if (row[x] != (p ? 128 : 0)) { g_free(row); return -1; }
                }
            }
        }
        g_free(row);
    }
    return 3;
}

static uint64_t engine15e2_source_generation(NikonZ9State *s, int owner)
{ return owner == 3 ? s->capture_thumbnail_jobs : s->capture_nv16_jobs; }

static bool engine15e2_jpeg_source_ready(NikonZ9State *s, unsigned b)
{
    if (s->engine15e2_bank1_model) {
        Z8JpegSource *r=jpeg_request_source(s,b);
        return r && r->live && r->published;
    }
    const uint32_t *c = jpeg_request_words(s,b);
    return engine15e2_owned_source(s, b) >= 0 || (s->engine151_capture_jobs &&
        s->engine15e2_jpeg_consumed_job != s->engine151_capture_jobs &&
        c[0x418 / 4] == s->engine151_capture_width &&
        c[0x41c / 4] == s->engine151_capture_height &&
        c[0x410 / 4] == s->engine151_capture_stride &&
        ((uint64_t)c[0x430 / 4] << 4) == s->engine151_capture_y &&
        ((uint64_t)c[0x434 / 4] << 4) == s->engine151_capture_uv);
}

/* Original packet+c sets bit1 and clears hardware458 despite a nonzero
 * software allocation. Accept only an actual fresh owned small/mid producer.
 * No allocator/firmware addresses or host-injected completion are used. */
static bool engine15e2_limit_disabled_request(NikonZ9State *s, unsigned b)
{
    const uint32_t *c = jpeg_request_words(s,b);
    int owner = engine15e2_owned_source(s, b);
    return s->engine15e2_limit_disabled_model &&
        c[0x404 / 4] == 2 && c[0x458 / 4] == 0 && (owner == 1 || owner == 2 || owner == 3);
}

static bool engine15e2_jpeg_request(NikonZ9State *s, unsigned b)
{
    const uint32_t *c = jpeg_request_words(s,b);
    unsigned w = c[0x418 / 4], h = c[0x41c / 4], stride = c[0x410 / 4];
    uint64_t y = (uint64_t)c[0x430 / 4] << 4;
    uint64_t uv = (uint64_t)c[0x434 / 4] << 4;
    uint64_t out = (uint64_t)c[0x440 / 4] << 4;
    uint64_t plane = (uint64_t)stride * h, capacity = (uint64_t)c[0x458 / 4] * 512;
    bool no_limit = engine15e2_limit_disabled_request(s, b);
    unsigned scale = no_limit ? (c[0x400 / 4] >> 4) & 3 : 0;
    /* With the hardware limit disabled, only the starting target byte is
     * checked here. Validate the COMPLETE ACTUAL stream before any write;
     * a temporary codec ceiling is not a DMA allocation or writable span. */
    bool count_only = no_limit && s->engine15e2_preflight_model;
    int owner = engine15e2_owned_source(s, b);
    if (count_only && owner < 0) { return false; }
    /* Count passes may repeat with other quantizers. Every pass reads the
     * actual RAM and encodes anew; pending request/core/IRQ gates below
     * still prohibit overlapping jobs or an invented completion. */
    uint64_t target_span = no_limit ? 1 : capacity;
    if ((!s->engine15e2_bank1_model && !s->engine15e2_deferred_input_model && !engine15e2_jpeg_source_ready(s, b)) ||
        s->engine15e2_jpeg_requested[b] || s->engine15e2_jpeg_pending[b] || s->engine15e2_jpeg_core[b] ||
        c[0] != 0x31 || c[0x0c / 4] != 0x14 || c[0x10 / 4] != 0x3c ||
        c[0x14 / 4] || c[0x24 / 4] != 4 || c[0x28 / 4] != 0x70f ||
        (no_limit ? (c[0x400 / 4] != 0 && c[0x400 / 4] != 0x20) : c[0x400 / 4]) || (!no_limit && c[0x404 / 4]) || c[0x408 / 4] != 0x10000 ||
        c[0x450 / 4] || c[0x454 / 4] ||
        !w || !h || (w & 1) || stride < w || stride > 16384 ||
        c[0x414 / 4] != stride || c[0x1c / 4] != (w >> scale) || c[0x18 / 4] != (h >> scale) ||
        !(w >> scale) || !(h >> scale) ||
        !plane || plane > 64 * MiB || (!no_limit && !capacity) || capacity > 128 * MiB ||
        y < UINT64_C(0x800000000) || y > UINT64_C(0x900000000) - plane ||
        uv < UINT64_C(0x800000000) || uv > UINT64_C(0x900000000) - plane ||
        (!count_only && (out < UINT64_C(0x800000000) || out > UINT64_C(0x900000000) - target_span)) ||
        !dma_ram_range(s, y, plane) || !dma_ram_range(s, uv, plane) ||
        (!count_only && (!dma_ram_range(s, out, target_span) ||
        (out < y + plane && y < out + target_span) ||
        (out < uv + plane && uv < out + target_span)))) { return false; }
    for (unsigned word = 0x100 / 4; word < 0x180 / 4; word++) {
        uint32_t q = c[word];
        for (unsigned shift = 0; shift < 32; shift += 8) {
            if (!((q >> shift) & 255)) { return false; }
        }
    }
    return true;
}

/* Diagnostic only: called AFTER rejection. Never selects a producer, reserves
 * a lease or changes the existing request predicate. Keep all observed words
 * in the log even when an earlier source check is the first failing guard. */
static const char *engine15e2_start_reject_reason(NikonZ9State *s, unsigned b, unsigned size)
{
    if (size != 4) { return "access_width"; }
    if (!s->engine15e2_config_model) { return "config_model_disabled"; }
    if (!s->engine15e2_jpeg_model) { return "jpeg_model_disabled"; }
    if (b && !s->engine15e2_bank1_model) { return "bank1_model_disabled"; }
    if (s->engine15e2_bank1_model && s->jpeg_request[b].failure &&
        strcmp(s->jpeg_request[b].failure,"request_profile_or_busy")) { return s->jpeg_request[b].failure; }
    const uint32_t *c=jpeg_request_words(s,b);
    bool no_limit=engine15e2_limit_disabled_request(s,b);
    bool count_only=no_limit && s->engine15e2_preflight_model;
    unsigned w=c[0x418/4],h=c[0x41c/4],stride=c[0x410/4];
    unsigned scale=no_limit?(c[0x400/4]>>4)&3:0;
    uint64_t plane=(uint64_t)stride*h,capacity=(uint64_t)c[0x458/4]*512;
    uint64_t y=(uint64_t)c[0x430/4]<<4,uv=(uint64_t)c[0x434/4]<<4,out=(uint64_t)c[0x440/4]<<4;
    uint64_t span=no_limit?1:capacity;
    if (count_only && engine15e2_owned_source(s,b)<0) { return "count_source_unmatched"; }
    if (!s->engine15e2_bank1_model && !s->engine15e2_deferred_input_model && !engine15e2_jpeg_source_ready(s,b)) { return "source_unmatched"; }
    if (s->engine15e2_jpeg_requested[b] || s->engine15e2_jpeg_pending[b] || s->engine15e2_jpeg_core[b]) { return "bank_busy"; }
    if (c[0]!=0x31 || c[0xc/4]!=0x14 || c[0x10/4]!=0x3c || c[0x14/4] || c[0x24/4]!=4 || c[0x28/4]!=0x70f) { return "unsupported_core_configuration"; }
    if ((no_limit ? (c[0x400/4]!=0 && c[0x400/4]!=0x20) : c[0x400/4]) ||
        (!no_limit && c[0x404/4]) || c[0x408/4]!=0x10000 || c[0x450/4] || c[0x454/4]) { return "unsupported_wrapper_mode"; }
    if (!w || !h || (w&1) || stride<w || stride>16384 || c[0x414/4]!=stride ||
        c[0x1c/4]!=(w>>scale) || c[0x18/4]!=(h>>scale) || !(w>>scale) || !(h>>scale)) { return "geometry_stride_or_scale"; }
    if (!plane || plane>64*MiB) { return "input_plane_size"; }
    if (!no_limit && !capacity) { return "zero_capacity_without_supported_limit_mode"; }
    if (capacity>128*MiB) { return "output_capacity_limit"; }
    if (y<UINT64_C(0x800000000) || y>UINT64_C(0x900000000)-plane ||
        uv<UINT64_C(0x800000000) || uv>UINT64_C(0x900000000)-plane ||
        (!count_only && (out<UINT64_C(0x800000000) || out>UINT64_C(0x900000000)-span)) ||
        !dma_ram_range(s,y,plane) || !dma_ram_range(s,uv,plane) ||
        (!count_only && !dma_ram_range(s,out,span))) { return "ram_span"; }
    if (!count_only && ((out<y+plane && y<out+span) || (out<uv+plane && uv<out+span))) { return "source_output_overlap"; }
    for (unsigned word=0x100/4;word<0x180/4;word++) {
        for (unsigned shift=0;shift<32;shift+=8) { if (!((c[word]>>shift)&255)) { return "zero_quantizer"; } }
    }
    return "request_or_source_contract";
}

static void engine15e2_start_reject_log(NikonZ9State *s, unsigned b, unsigned size)
{
    const uint32_t *c=jpeg_request_words(s,b);
    bool enabled=s->engine15e2_config_model && s->engine15e2_jpeg_model && (!b || s->engine15e2_bank1_model);
    fprintf(stderr,"Z9_15E2_REJECT bank=%u reason=%s off=4 size=%u source_ready=%u no_limit_accepted=%u"
        " capacity=%" PRIu64 " capacity_zero=%u w000=%x w400=%x w404=%x w408=%x w458=%x"
        " width=%u height=%u stride=%u y=%x uv=%x out=%x\n",
        b,engine15e2_start_reject_reason(s,b,size),size,enabled && engine15e2_jpeg_source_ready(s,b),
        enabled && engine15e2_limit_disabled_request(s,b),(uint64_t)c[0x458/4]*512,!c[0x458/4],
        c[0],c[0x400/4],c[0x404/4],c[0x408/4],c[0x458/4],c[0x418/4],c[0x41c/4],c[0x410/4],
        c[0x430/4],c[0x434/4],c[0x440/4]);
}

static bool engine15e2_jpeg_pixels(NikonZ9State *s, unsigned b)
{
    const uint32_t *c = jpeg_request_words(s,b);
    unsigned w = c[0x418 / 4], h = c[0x41c / 4], stride = c[0x410 / 4];
    uint64_t y = (uint64_t)c[0x430 / 4] << 4;
    uint64_t uv = (uint64_t)c[0x434 / 4] << 4;
    uint64_t out = (uint64_t)c[0x440 / 4] << 4;
    size_t plane = (size_t)stride * h, capacity = (size_t)c[0x458 / 4] * 512;
    bool no_limit = engine15e2_limit_disabled_request(s, b);
    bool count_only = no_limit && s->engine15e2_preflight_model;
    size_t temporary_ceiling = no_limit ? 128 * MiB : capacity;
    unsigned scale = no_limit ? (c[0x400 / 4] >> 4) & 3 : 0;
    if (!engine15e2_jpeg_source_ready(s, b)) { return false; }
    int capture_owner = engine15e2_owned_source(s, b);
    uint8_t quantizers[2][64];
    for (unsigned t = 0; t < 2; t++) {
        for (unsigned at = 0; at < 64; at++) {
            quantizers[t][at] = c[(0x100 + t * 64) / 4 + at / 4] >> (24 - (at & 3) * 8);
        }
    }
    uint8_t *input = g_malloc(plane * 2);
    if (address_space_read(&address_space_memory, y, MEMTXATTRS_UNSPECIFIED, input, plane) != MEMTX_OK ||
        address_space_read(&address_space_memory, uv, MEMTXATTRS_UNSPECIFIED, input + plane, plane) != MEMTX_OK) {
        g_free(input); return false;
    }
    Z8JpegRequest *request=&s->jpeg_request[b];
    Z8JpegSource *source=s->engine15e2_bank1_model?jpeg_request_source(s,b):NULL;
    if (s->engine15e2_bank1_model) {
        if (!source || !jpeg_source_hash_valid(s,source) || !jpeg_source_matches(s,source,c) ||
            !nb_read_acquire(&s->output_owner,b,request->token,(NBSpan){y,plane},(NBSpan){uv,plane})) {
            g_free(input);jpeg_bank_failed(s,b,"source_overwritten_or_unbound");return false;
        }
        /* Hash the copied active bytes too: verification is of the actual
         * codec input, not a second RAM read which could race CPU writes. */
        size_t head=(size_t)(h-source->height)/2*stride,n=(size_t)source->height*stride;
        char *ys=g_compute_checksum_for_data(G_CHECKSUM_SHA256,input+head,n);
        char *us=g_compute_checksum_for_data(G_CHECKSUM_SHA256,input+plane+head,n);
        bool valid=!strcmp(ys,source->y_sha)&&!strcmp(us,source->uv_sha);
        g_free(ys);g_free(us);
        for (unsigned p=0;p<2 && valid;p++) {
            for (unsigned row=0;row<h && valid;row++) {
                if (row>=head/stride && row<head/stride+source->height) { continue; }
                for (unsigned x=0;x<w;x++) {
                    if (input[(size_t)p*plane+(size_t)row*stride+x]!=(p?128:0)) { valid=false;break; }
                }
            }
        }
        if (!valid) { g_free(input);jpeg_bank_failed(s,b,"copied_source_hash");return false; }
    }
    unsigned char *encoded = NULL; size_t bytes = 0;
    bool ok = no_limit ? wonderlantern_nv16_jpeg_scaled(input, plane, input + plane, plane,
        w, h, stride, quantizers, temporary_ceiling, scale, &encoded, &bytes) :
        wonderlantern_nv16_jpeg(input, plane, input + plane, plane,
        w, h, stride, quantizers, temporary_ceiling, &encoded, &bytes);
    g_free(input);
    if (!ok) { return false; }
    /* Atomic admission: do not write partial JPEG or issue an IRQ when the
     * actual stream crosses a sparse hole, MMIO, a RAM edge or source plane. */
    if (!bytes || bytes > temporary_ceiling || (!count_only && (!dma_ram_range(s, out, bytes) ||
        (out < y + plane && y < out + bytes) ||
        (out < uv + plane && uv < out + bytes)))) { free(encoded); return false; }
    if (s->engine15e2_bank1_model) {
        unsigned slot=b?NB_SLOT_JPEG1:NB_SLOT_JPEG0;
        uint64_t unfinished=0;
        for (unsigned i=0;i<NB_SLOT_COUNT;i++) {
            unfinished+=s->output_owner.slots[i].held && !s->output_owner.slots[i].complete;
        }
        /* A deferred consumer can run inside151's publication callback before
         * the parent calls nb_complete. Do not consume its last counter slot. */
        if (!count_only && s->output_owner.completed>UINT64_MAX-unfinished-(!request->lease)) { free(encoded);return false; }
        if ((!count_only && s->output_owner.completed==UINT64_MAX) || s->engine15e2_jpeg_jobs[b]==UINT64_MAX || s->engine15e2_jpeg_bytes[b]>UINT64_MAX-bytes ||
            s->engine15e2_jpeg_input_bytes[b]>UINT64_MAX-plane*2 ||
            (no_limit && s->engine15e2_limit_disabled_jobs[b]==UINT64_MAX) ||
            (count_only && (s->engine15e2_count_jobs[b]==UINT64_MAX || source->roles[request->role].counts==UINT64_MAX))) { free(encoded);return false; }
        if (!count_only && !request->lease) {
            NBSpan span={out,bytes};
            if (!nb_reserve(&s->output_owner,slot,request->token,source->frame,&span,1)) { free(encoded);return false; }
            request->lease=true;
        }
        /* The output lease is ours; test all other source readers directly.
         * Other held outputs were checked by nb_reserve before any write. */
        if (!count_only) {
            for (unsigned i=0;i<Z8_JPEG_SOURCES;i++) {
                Z8JpegSource *r=&s->jpeg_sources.entries[i];
                if (r->readers && jpeg_source_overlap(r,(NBSpan){out,bytes})) { free(encoded);return false; }
            }
            for (unsigned i=0;i<Z8_JPEG_SOURCES;i++) {
                Z8JpegSource *r=&s->jpeg_sources.entries[i];
                if (r->published && jpeg_source_overlap(r,(NBSpan){out,bytes})) { r->live=false; }
            }
        }
    }
    if (!count_only && address_space_write(&address_space_memory, out, MEMTXATTRS_UNSPECIFIED, encoded, bytes) != MEMTX_OK) {
        free(encoded); return false;
    }
    free(encoded);
    if (s->engine15e2_bank1_model) {
        Z8JpegRole *role=&source->roles[request->role];
        if (!count_only && !nb_complete(&s->output_owner,b?NB_SLOT_JPEG1:NB_SLOT_JPEG0,request->token)) { return false; }
        if (count_only) { role->counts++; }
        else { role->committed=true;role->committed_count=role->counts;role->claim=0; }
        source->readers&=~(1u<<b);nb_read_release(&s->output_owner,b,request->token);
    }
    s->engine15e2_jpeg_jobs[b]++; s->engine15e2_jpeg_bytes[b] += bytes;
    if (no_limit) {
        s->engine15e2_limit_disabled_jobs[b]++;
        s->engine15e2_encoded_width[b] = w >> scale;
        s->engine15e2_encoded_height[b] = h >> scale;
        s->engine15e2_encoded_scale[b] = scale;
    }
    s->engine15e2_jpeg_input_bytes[b] += plane * 2;
    s->engine15e2_last_count_only[b] = count_only;
    if (count_only) {
        s->engine15e2_count_consumed[capture_owner] = source ? MAX(s->engine15e2_count_consumed[capture_owner],source->producer_job) : engine15e2_source_generation(s,capture_owner);
        s->engine15e2_count_jobs[b]++;
    } else if (capture_owner == 3) {
        s->engine15e2_thumbnail_consumed = source ? MAX(s->engine15e2_thumbnail_consumed,source->producer_job) : s->capture_thumbnail_jobs;
    } else if (capture_owner >= 0) {
        s->capture_nv16_jpeg_consumed[capture_owner] = source ? MAX(s->capture_nv16_jpeg_consumed[capture_owner],source->producer_job) : s->capture_nv16_jobs;
    } else { s->engine15e2_jpeg_consumed_job = source ? MAX(s->engine15e2_jpeg_consumed_job,source->parent_151_job) : s->engine151_capture_jobs; }
    s->engine15e2_jpeg_output[b] = out; s->engine15e2_jpeg_last_bytes[b] = bytes;
    s->engine15e2_jpeg_y[b] = y; s->engine15e2_jpeg_uv[b] = uv;
    s->engine15e2_jpeg_capacity[b] = capacity; s->engine15e2_jpeg_width[b] = w;
    s->engine15e2_jpeg_height[b] = h; s->engine15e2_jpeg_stride[b] = stride;
    s->engine15e2_jpeg_requested[b] = false; s->engine15e2_jpeg_result_valid[b] = true;
    s->engine15e2_config[b][4 / 4] = 0;
    /* Original ISR maps raw bit27/core2c bit31 to flag1, bit15 to flag20000,
     * bit14 to flag40000. Its combined60001 subscription is a functional
     * completion convention, asserted only after real encoded RAM output.
     * 470 returns actual input lines under a provisional units convention. */
    s->engine15e2_jpeg_core[b] = 0x80000000;
    s->engine15e2_jpeg_pending[b] = 0x0800c000;
    if (s->engine15e2_bank1_model) {
        fprintf(stderr,"Z9_15E2_BANK_COMPLETE bank=%u token=%" PRIu64 " generation=%" PRIu64
            " frame=%" PRIu64 " parent151=%" PRIu64 " decoder=%" PRIu64 " source_kind=%u kind=%u index=%u count=%u\n",
            b,request->token,request->generation,request->frame,request->parent_151_job,request->decoder_job,
            request->source_kind,request->producer_kind,request->producer_index,count_only);
    }
    fprintf(stderr, "Z9_15E2_JPEG y=%" PRIx64 " uv=%" PRIx64 " out=%" PRIx64
        " bytes=%zu capacity=%zu geometry=%ux%u stride=%u; baseline JPEG approximation\n",
        y, uv, out, bytes, capacity, w, h, stride);
    return true;
}


static void engine15e2_deferred_input_notify(NikonZ9State *s)
{
    if (s->engine15e2_bank1_model) { jpeg_sources_refresh151(s); }
    for (unsigned b = 0; b < (s->engine15e2_bank1_model ? 2u : 1u); b++) {

    if (!s->engine15e2_deferred_input_model || !s->engine15e2_jpeg_requested[b] || s->engine15e2_deferred_failed[b]) { continue; }
    if (s->engine15e2_bank1_model) {
        Z8JpegSource *r=jpeg_request_source(s,b);
        if (!r || !r->live) { jpeg_bank_failed(s,b,"source_intent_invalidated");continue; }
    }
    if (s->engine15e2_config[b][0x400 / 4] != 0x4000 || s->engine15e2_config[b][0x404 / 4] != 0x4000) { continue; }
    if (s->engine15e2_bank1_model && s->engine15e2_deferred_notifications[b]==UINT64_MAX) {
        jpeg_bank_failed(s,b,"notification_counter_overflow");continue;
    }
    s->engine15e2_deferred_notifications[b]++;
    if (!engine15e2_jpeg_source_ready(s, b)) { continue; }
    if (!engine15e2_jpeg_pixels(s, b)) {
        /* Codec/capacity failure does not invent an original error cause.
         * Retain busy, report failure, and wait for explicit future modeling. */
        s->engine15e2_deferred_failed[b] = true;
        if (s->engine15e2_bank1_model) { jpeg_bank_failed(s,b,s->jpeg_request[b].failure?s->jpeg_request[b].failure:"codec_or_output_contract"); }
        fprintf(stderr, "Z9_15E2_DEFERRED_FAILED; no new output/completion\n");
        continue;
    }
    engine15e2_jpeg_irq_update(s, b);
    }
}

static bool get_engine15e2_deferred_input_model(Object *obj, Error **errp)
{ return NIKON_Z9_LAB(obj)->engine15e2_deferred_input_model; }

static void set_engine15e2_deferred_input_model(Object *obj, bool value, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    if (s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp, "engine15e2-deferred-input-model is immutable after realization"); return;
    }
    s->engine15e2_deferred_input_model = value;
}

static char *get_engine15e2_deferred_input_state(Object *obj, Error **errp)
{
    const unsigned b = 0;
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    bool waiting = s->engine15e2_deferred_input_model && s->engine15e2_jpeg_requested[b] &&
        s->engine15e2_config[b][0x400 / 4] == 0x4000 &&
        s->engine15e2_config[b][0x404 / 4] == 0x4000 && !engine15e2_jpeg_source_ready(s, b);
    return g_strdup_printf("{\"enabled\":%s,\"waiting_for_source\":%s,\"failed\":%s,"
        "\"waits\":%" PRIu64 ",\"producer_notifications\":%" PRIu64 ","
        "\"physical_timing_verified\":false,\"native_file_saved\":false,"
        "\"scope\":\"bounded immutable JPEG request; only actual fresh matching151 RAM permits encoding; missing producer remains busy\"}",
        s->engine15e2_deferred_input_model ? "true" : "false", waiting ? "true" : "false",
        s->engine15e2_deferred_failed[b] ? "true" : "false", s->engine15e2_deferred_waits[b],
        s->engine15e2_deferred_notifications[b]);
}

static bool engine15e2_jpeg_read(NikonZ9State *s, unsigned b, unsigned off,
                                unsigned size, uint64_t *value)
{
    /* Original encode ISR copies474 into opaque packet84 when packet+c/d
     * is nonzero. Opt-in functional units: real completed stream bytes;
     * physical counter units are unproved. Not a guessed success constant. */
    if (off == 0x474 && s->engine15e2_result474_model && (b == 0 || s->engine15e2_bank1_model)) {
        if (size != 4 || !s->engine15e2_jpeg_result_valid[b] ||
            s->engine15e2_jpeg_capacity[b] != 0) {
            unknown_access(s, 0x15e20000 + b * 0x1000 + off, 0, size, false);
            *value = 0; return true;
        }
        *value = s->engine15e2_jpeg_last_bytes[b]; return true;
    }
    if (!s->engine15e2_jpeg_model || (b != 0 && !s->engine15e2_bank1_model) ||
        (off != 0x20 && off != 0x2c && off != 0x470 && off != 0x480)) { return false; }
    if (size != 4 || ((off == 0x20 || off == 0x470) && !s->engine15e2_jpeg_result_valid[b])) {
        unknown_access(s, 0x15e20000 + b * 0x1000 + off, 0, size, false); *value = 0; return true;
    }
    *value = off == 0x480 ? s->engine15e2_jpeg_pending[b] :
             off == 0x2c ? s->engine15e2_jpeg_core[b] :
             off == 0x20 ? s->engine15e2_jpeg_last_bytes[b] : s->engine15e2_jpeg_height[b];
    return true;
}

static bool engine15e2_jpeg_write(NikonZ9State *s, unsigned b, unsigned off,
                                 uint64_t value, unsigned size)
{
    if (!s->engine15e2_jpeg_model || (b != 0 && !s->engine15e2_bank1_model)) { return false; }
    if (s->engine15e2_bank1_model && s->engine15e2_deferred_failed[b] && (off==0x400 || off==0x404)) { goto invalid; }
    if (off == 0x480 || off == 0x2c) {
        if (size != 4) { goto invalid; }
        if (off == 0x480) {
            if (value & ~s->engine15e2_jpeg_pending[b]) { goto invalid; }
            if (s->engine15e2_bank1_model &&
                ((value && s->engine15e2_jpeg_acks[b]==UINT64_MAX) ||
                 !jpeg_bank_retire(s,b,s->engine15e2_jpeg_pending[b]&~value,s->engine15e2_jpeg_core[b]))) { goto invalid; }
            if (value) { s->engine15e2_jpeg_acks[b]++; }
            s->engine15e2_jpeg_pending[b] &= ~value;
        } else {
            /* Original ISR writes ~issued_core to clear its bit31. */
            if (!s->engine15e2_jpeg_core[b] || value != (~s->engine15e2_jpeg_core[b] & UINT32_MAX)) { goto invalid; }
            if (s->engine15e2_bank1_model && (s->engine15e2_jpeg_core_acks[b]==UINT64_MAX ||
                !jpeg_bank_retire(s,b,s->engine15e2_jpeg_pending[b],0))) { goto invalid; }
            s->engine15e2_jpeg_core[b] = 0; s->engine15e2_jpeg_core_acks[b]++;
        }
        engine15e2_jpeg_irq_update(s, b); return true;
    }
    if (off == 4 && value == 1) {
        if (s->engine15e2_bank1_model) { s->jpeg_request[b].failure=NULL; }
        if (size != 4 || !engine15e2_jpeg_request(s, b)) {
            if (s->engine15e2_bank1_model) { s->jpeg_request[b].failure="request_profile_or_busy"; } goto invalid;
        }
        if (s->engine15e2_bank1_model && !jpeg_bank_bind(s,b)) { goto invalid; }
        if (s->engine15e2_deferred_input_model && !engine15e2_jpeg_source_ready(s, b)) {
            s->engine15e2_deferred_waits[b]++;
        }
        s->engine15e2_deferred_failed[b] = false;
        s->engine15e2_jpeg_requested[b] = true; s->engine15e2_jpeg_result_valid[b] = false;
        s->engine15e2_config[b][1] = 1; s->engine15e2_jpeg_requests[b]++;
        if (s->engine15e2_bank1_model) {
            const Z8JpegRequest *q=&s->jpeg_request[b];
            fprintf(stderr,"Z9_15E2_BANK_START bank=%u token=%" PRIu64 " generation=%" PRIu64
                " frame=%" PRIu64 " parent151=%" PRIu64 " decoder=%" PRIu64 " source_kind=%u intent_wait=%u\n",
                b,q->token,q->generation,q->frame,q->parent_151_job,q->decoder_job,q->source_kind,
                !engine15e2_jpeg_source_ready(s,b));
        }
        return true;
    }
    if (off == 4 && (value == 0x80000000 || value == 0x80000001)) {
        if (size != 4 || s->engine15e2_jpeg_requested[b] || s->engine15e2_jpeg_pending[b] || s->engine15e2_jpeg_core[b]) { goto invalid; }
        s->engine15e2_jpeg_result_valid[b] = false;
        if (s->engine15e2_bank1_model) { memset(&s->jpeg_request[b],0,sizeof(s->jpeg_request[b]));s->engine15e2_deferred_failed[b]=false; }
        s->engine15e2_config[b][1] = 0; s->engine15e2_idle_resets[b]++;
        return true;
    }
    if ((off == 0x400 || off == 0x404) &&
        (value == 0x4000 || (engine15e2_limit_disabled_request(s, b) &&
            ((off == 0x404 && value == 0x4002) || (off == 0x400 && value == 0x4020))))) {
        bool no_limit = engine15e2_limit_disabled_request(s, b);
        uint32_t scale_bits = s->engine15e2_config[b][0x400 / 4] & 0x30;
        uint32_t initial = no_limit ? (off == 0x404 ? 2 : scale_bits) : 0;
        if (size != 4 || !s->engine15e2_jpeg_requested[b] ||
            value != (initial | 0x4000) || s->engine15e2_config[b][off / 4] != initial ||
            (off == 0x404 && s->engine15e2_config[b][0x400 / 4] != (0x4000 | (no_limit ? scale_bits : 0)))) { goto invalid; }
        if (off == 0x404 &&
            !(s->engine15e2_deferred_input_model && !engine15e2_jpeg_source_ready(s, b)) &&
            !engine15e2_jpeg_pixels(s, b)) {
            if (s->engine15e2_bank1_model) { jpeg_bank_failed(s,b,s->jpeg_request[b].failure?s->jpeg_request[b].failure:"codec_or_output_contract"); }
            goto invalid;
        }
        s->engine15e2_config[b][off / 4] = value;
        engine15e2_jpeg_irq_update(s, b); return true;
    }
    return false;
invalid:
    if (off == 4 && value == 1) {
        engine15e2_start_reject_log(s,b,size);
    } else if (s->engine15e2_bank1_model) {
        fprintf(stderr,"Z9_15E2_REJECT bank=%u off=%x generation=%" PRIu64 " reason=%s\n",b,off,
            s->jpeg_request[b].generation,s->jpeg_request[b].failure?s->jpeg_request[b].failure:"register_protocol");
    }
    unknown_access(s, 0x15e20000 + b * 0x1000 + off, value, size, true); return true;
}


static bool get_engine15e2_preflight_model(Object *obj, Error **errp)
{ return NIKON_Z9_LAB(obj)->engine15e2_preflight_model; }

static void set_engine15e2_preflight_model(Object *obj, bool value, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    if (s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp, "engine15e2-preflight-model is immutable after realization"); return;
    }
    s->engine15e2_preflight_model = value;
}

static char *get_engine15e2_preflight_state(Object *obj, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    return g_strdup_printf("{\"enabled\":%s,\"last_count_only\":%s,\"count_jobs\":%" PRIu64
        ",\"count_dma_bytes\":0,\"thumbnail_committed_generation\":%" PRIu64
        ",\"physical_counter_units_verified\":false,"
        "\"scope\":\"owned count/commit JPEG lifetime; count mode measures actual encoding without output DMA; original CPU thumbnail borders checked\"}",
        s->engine15e2_preflight_model ? "true" : "false", s->engine15e2_last_count_only[0] ? "true" : "false",
        s->engine15e2_count_jobs[0], s->engine15e2_thumbnail_consumed);
}

static bool get_engine15e2_result474_model(Object *obj, Error **errp)
{ return NIKON_Z9_LAB(obj)->engine15e2_result474_model; }

static void set_engine15e2_result474_model(Object *obj, bool value, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    if (s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp, "model must be selected before realization"); return;
    }
    s->engine15e2_result474_model = value;
}

static bool get_engine15e2_limit_disabled_model(Object *obj, Error **errp)
{ return NIKON_Z9_LAB(obj)->engine15e2_limit_disabled_model; }

static void set_engine15e2_limit_disabled_model(Object *obj, bool value, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    if (s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp, "engine15e2-limit-disabled-model is immutable after realization"); return;
    }
    s->engine15e2_limit_disabled_model = value;
}

static char *get_engine15e2_limit_disabled_state(Object *obj, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    return g_strdup_printf("{\"enabled\":%s,\"jobs\":%" PRIu64
        ",\"temporary_codec_ceiling\":134217728,\"encoded_width\":%u,\"encoded_height\":%u,\"scale_shift\":%u,\"physical_algorithm_verified\":false,"
        "\"native_file_saved\":false,\"scope\":\"actual fresh owned small/mid RAM, original limit-disable mode2; only complete actual stream written after RAM/nonoverlap validation; temporary ceiling is not DMA allocation\"}",
        s->engine15e2_limit_disabled_model ? "true" : "false", s->engine15e2_limit_disabled_jobs[0],
        s->engine15e2_encoded_width[0], s->engine15e2_encoded_height[0], s->engine15e2_encoded_scale[0]);
}

static bool get_engine15e2_jpeg_model(Object *obj, Error **errp)
{
    return NIKON_Z9_LAB(obj)->engine15e2_jpeg_model;
}

static void set_engine15e2_jpeg_model(Object *obj, bool value, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    if (s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp, "engine15e2-jpeg-model is immutable after realization"); return;
    }
    s->engine15e2_jpeg_model = value;
}

static char *get_engine15e2_jpeg_state(Object *obj, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    return g_strdup_printf("{\"enabled\":%s,\"requested\":%s,\"result_valid\":%s,"
        "\"requests\":%" PRIu64 ",\"jobs\":%" PRIu64 ",\"bytes\":%" PRIu64
        ",\"input_bytes\":%" PRIu64 ",\"acks\":%" PRIu64 ",\"core_acks\":%" PRIu64
        ",\"consumed151job\":%" PRIu64 ",\"pending\":%u,\"core\":%u,"
        "\"output\":%" PRIu64 ",\"last_bytes\":%" PRIu64 ",\"capacity\":%" PRIu64
        ",\"y\":%" PRIu64 ",\"uv\":%" PRIu64 ",\"width\":%u,\"height\":%u,\"stride\":%u,"
        "\"physical_algorithm_verified\":false,\"scope\":\"actual generated151 RAM -> baseline libjpeg whole-stream; original cause subscriptions; 470=input lines and physical header/units/timing provisional; no file-save proof\"}",
        s->engine15e2_jpeg_model ? "true" : "false", s->engine15e2_jpeg_requested[0] ? "true" : "false",
        s->engine15e2_jpeg_result_valid[0] ? "true" : "false", s->engine15e2_jpeg_requests[0],
        s->engine15e2_jpeg_jobs[0], s->engine15e2_jpeg_bytes[0], s->engine15e2_jpeg_input_bytes[0],
        s->engine15e2_jpeg_acks[0], s->engine15e2_jpeg_core_acks[0], s->engine15e2_jpeg_consumed_job,
        s->engine15e2_jpeg_pending[0], s->engine15e2_jpeg_core[0], s->engine15e2_jpeg_output[0],
        s->engine15e2_jpeg_last_bytes[0], s->engine15e2_jpeg_capacity[0], s->engine15e2_jpeg_y[0],
        s->engine15e2_jpeg_uv[0], s->engine15e2_jpeg_width[0], s->engine15e2_jpeg_height[0], s->engine15e2_jpeg_stride[0]);
}


static bool get_engine15e2_bank1_model(Object *obj, Error **errp)
{ return NIKON_Z9_LAB(obj)->engine15e2_bank1_model; }
static void set_engine15e2_bank1_model(Object *obj, bool value, Error **errp)
{
    NikonZ9State *s=NIKON_Z9_LAB(obj);
    if (!object_dynamic_cast(obj,MACHINE_TYPE_NAME("nikon-z8-bringup")) || s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp,"engine15e2-bank1-jpeg-model requires Z8 at creation");return;
    }
    s->engine15e2_bank1_model=value;
}
static char *get_engine15e2_bank_state(Object *obj, Error **errp)
{
    NikonZ9State *s=NIKON_Z9_LAB(obj);GString *out=g_string_new(NULL);
    g_string_append_printf(out,"{\"enabled\":%s,\"banks\":[",s->engine15e2_bank1_model?"true":"false");
    for (unsigned b=0;b<2;b++) {
        const Z8JpegRequest *q=&s->jpeg_request[b];
        g_string_append_printf(out,"%s{\"bank\":%u,\"base\":%u,\"intid\":%u,\"ownership_slot\":%u,"
            "\"requested\":%s,\"pending\":%u,\"core\":%u,\"result_valid\":%s,\"failed\":%s,"
            "\"requests\":%" PRIu64 ",\"jobs\":%" PRIu64 ",\"bytes\":%" PRIu64 ",\"input_bytes\":%" PRIu64
            ",\"count_jobs\":%" PRIu64 ",\"acks\":%" PRIu64 ",\"core_acks\":%" PRIu64
            ",\"deferred_waits\":%" PRIu64 ",\"notifications\":%" PRIu64 ",\"output\":%" PRIu64
            ",\"result_bytes\":%" PRIu64 ",\"capacity\":%" PRIu64 ",\"generation\":%" PRIu64
            ",\"job_token\":%" PRIu64 ",\"frame\":%" PRIu64 ",\"source_kind\":%u,\"producer_kind\":%u,\"producer_index\":%u,"
            "\"parent_151_job\":%" PRIu64 ",\"decoder_job\":%" PRIu64 ",\"parent_generation\":%" PRIu64
            ",\"lease\":%s,\"count_only\":%s,\"failure\":\"%s\",\"request\":[",
            b?",":"",b,0x15e20000+b*0x1000,121+b,b?NB_SLOT_JPEG1:NB_SLOT_JPEG0,
            s->engine15e2_jpeg_requested[b]?"true":"false",s->engine15e2_jpeg_pending[b],s->engine15e2_jpeg_core[b],
            s->engine15e2_jpeg_result_valid[b]?"true":"false",s->engine15e2_deferred_failed[b]?"true":"false",
            s->engine15e2_jpeg_requests[b],s->engine15e2_jpeg_jobs[b],s->engine15e2_jpeg_bytes[b],s->engine15e2_jpeg_input_bytes[b],
            s->engine15e2_count_jobs[b],s->engine15e2_jpeg_acks[b],s->engine15e2_jpeg_core_acks[b],s->engine15e2_deferred_waits[b],
            s->engine15e2_deferred_notifications[b],s->engine15e2_jpeg_output[b],s->engine15e2_jpeg_last_bytes[b],s->engine15e2_jpeg_capacity[b],
            q->generation,q->token,q->frame,q->source_kind,q->producer_kind,q->producer_index,q->parent_151_job,q->decoder_job,q->parent_generation,
            q->lease?"true":"false",q->count_only?"true":"false",q->failure?q->failure:"");
        for (unsigned w=0;w<0x500/4;w++) { g_string_append_printf(out,"%s%u",w?",":"",q->words[w]); }
        g_string_append(out,"]}");
    }
    g_string_append_printf(out,"],\"generation_serial\":%" PRIu64 ",\"source_failure\":\"%s\",\"sources\":[",
        s->jpeg_sources.serial,s->jpeg_sources.failure?s->jpeg_sources.failure:"");
    bool comma=false;
    for (unsigned i=0;i<Z8_JPEG_SOURCES;i++) {
        const Z8JpegSource *r=&s->jpeg_sources.entries[i];if (!r->generation) { continue; }
        g_string_append_printf(out,"%s{\"generation\":%" PRIu64 ",\"kind\":%u,\"index\":%u,\"source_kind\":%u,"
            "\"reserved\":%s,\"published\":%s,\"live\":%s,\"readers\":%u,\"producer_job\":%" PRIu64
            ",\"parent_generation\":%" PRIu64 ",\"parent_151_job\":%" PRIu64 ",\"frame\":%" PRIu64
            ",\"decoder_job\":%" PRIu64 ",\"isp_record_job\":%" PRIu64 ",\"captured_ns\":%" PRIu64
            ",\"y\":%" PRIu64 ",\"uv\":%" PRIu64 ",\"width\":%u,\"height\":%u,\"stride\":%u,"
            "\"format\":\"NV16-8\",\"y_sha256\":\"%s\",\"uv_sha256\":\"%s\","
            "\"parent_y_sha256\":\"%s\",\"parent_uv_sha256\":\"%s\",\"roles\":[",
            comma?",":"",r->generation,r->kind,r->index,r->source_kind,r->reserved?"true":"false",r->published?"true":"false",
            r->live?"true":"false",r->readers,r->producer_job,r->parent_generation,r->parent_151_job,r->frame,r->decoder_job,
            r->isp_record_job,r->captured_ns,r->y,r->uv,r->width,r->height,r->stride,r->y_sha,r->uv_sha,r->parent_y_sha,r->parent_uv_sha);
        bool role_comma=false;
        for (unsigned j=0;j<Z8_JPEG_ROLES;j++) {
            const Z8JpegRole *role=&r->roles[j];if (!role->used) { continue; }
            g_string_append_printf(out,"%s{\"signature\":\"%s\",\"counts\":%" PRIu64
                ",\"committed\":%s,\"committed_count\":%" PRIu64 ",\"claim_bank_plus1\":%u}",
                role_comma?",":"",role->signature,role->counts,role->committed?"true":"false",role->committed_count,role->claim);
            role_comma=true;
        }
        g_string_append(out,"]}");comma=true;
    }
    g_string_append(out,"],\"scope\":\"model producer generations and instrumented DMA leases; CPU/other DMA, native writer lifetime and physical codec unverified\"}");
    return g_string_free(out,false);
}
