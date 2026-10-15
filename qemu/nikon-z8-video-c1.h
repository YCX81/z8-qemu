/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Z8 C3.11 functional video transport. Device/ownership design follows
 * WonderLantern shared146; the NZ8BAYR1 helper comes from the local HSFC
 * branch. Neither envelope nor Bayer/ISP arithmetic is Nikon's algorithm.
 * Actual packet RAM is the decoder's only input. No firmware queue, PC,
 * task flag, callback or media file is written by this model. */
#include "nikon-bayer-packet.h"

static bool z8_video_c1_address(NikonZ9State *s, uint64_t encoded,
                                uint64_t bytes, uint64_t *physical)
{
    if (encoded >= UINT64_C(0xc00000000) && encoded < UINT64_C(0x1000000000)) {
        encoded -= UINT64_C(0x400000000);
    }
    *physical = encoded;
    return bytes && dma_ram_range(s, encoded, bytes);
}

static void z8_video_c1_irq(NikonZ9State *s, unsigned port)
{
    uint32_t *c = port ? s->z8_video1530_config : s->z8_video152c_config;
    if (s->z8_video_c1.irq[port]) {
        qemu_set_irq(s->z8_video_c1.irq[port], c[0x14 / 4] == 1 &&
                     (s->z8_video_c1.pending[port] & c[0x18 / 4]));
    }
}

static void z8_video_c1_cancel(NikonZ9State *s, int reset_port)
{
    Z8VideoC1 *v = &s->z8_video_c1;
    if (!v->enabled) { return; }
    v->cancellations += v->packet_owned || v->frame_ready;
    v->packet_owned = v->frame_ready = v->ring_latched = false;
    v->failed = false;
    v->row = 0;
    v->input_bank = 0;
    v->source_group = 0;
    v->input_bank_written[0] = v->input_bank_written[1] = 0;
    v->fourth_written[0] = v->fourth_written[1] = 0;
    v->buffer_latched[0] = v->buffer_latched[1] = false;
    v->buffer_waiting[0] = v->buffer_waiting[1] = false;
    v->receiver_refill_notified = false;
    v->decoder_refill_notified = false;
    for (unsigned ch = 0; ch < 3; ch++) {
        g_free(s->raw_pipeline_cache[ch]); s->raw_pipeline_cache[ch] = NULL;
    }
    if (reset_port >= 0) {
        v->buffer_valid[reset_port][0] = v->buffer_valid[reset_port][1] = 0;
        v->buffer_written[reset_port][0] = v->buffer_written[reset_port][1] = 0;
        v->stop_cancelled[reset_port] += v->end_requested[reset_port];
        v->stream_proven[reset_port] = false;
        v->end_requested[reset_port] = v->end_ready[reset_port] = false;
        v->cancellations += !!v->pending[reset_port];
        v->pending[reset_port] = 0;
        z8_video_c1_irq(s, reset_port);
    }
}

static bool z8_video_c1_source(NikonZ9State *s)
{
    const uint32_t *g = s->z8_video_geometry_latched[0];
    unsigned width = g[4 / 4] >> 16, height = g[8 / 4] >> 16;
    if (!s->raw_pixels || !width || !height || ((width | height) & 1) ||
        width > 8280 || height > 5520 || (uint64_t)width * height > 48 * MiB ||
        g[0] != 1 || (g[4 / 4] & 0xffff) || (g[8 / 4] & 0xffff) || g[0x10 / 4] ||
        g[0x100 / 4] != (((width * 14 + 7) / 8 + 31) & ~31u) ||
        g[0x104 / 4] != 1 || g[0x108 / 4] != 1 || g[0x10c / 4] ||
        g[0x110 / 4] != 1 || g[0x114 / 4] || g[0x118 / 4] != 3 || g[0x11c / 4]) {
        return false;
    }
    for (unsigned row = 0; row < 4; row++) {
        if (g[(0x120 + row * 8) / 4] != 0xe0000000 || g[(0x124 + row * 8) / 4]) {
            return false;
        }
    }
    return true;
}

static bool z8_video_c1_receiver(NikonZ9State *s, unsigned bank, uint64_t outputs[4])
{
    const uint32_t *c = s->z8_video152c_config, *d = s->z8_video15ac_config;
    const uint32_t *g = s->z8_video_geometry_latched[0];
    unsigned amount = c[0x550 / 4];
    if (!z8_video_c1_source(s) || c[8 / 4] != 1 || c[0x14 / 4] != 1 ||
        c[0x404 / 4] || d[0x38 / 4] != 1 ||
        c[0x52c / 4] || c[0x530 / 4] || c[0x534 / 4] || c[0x544 / 4] ||
        c[0x100 / 4] != 1 || c[0x104 / 4] != 1 || c[0x108 / 4] > 1 || bank > 1 ||
        c[0x114 / 4] != 3 || c[0x548 / 4] != g[4 / 4] >> 16 ||
        c[0x54c / 4] != g[8 / 4] >> 16 ||
        d[0x14 / 4] != c[0x548 / 4] || d[0x1c / 4] != c[0x54c / 4] ||
        amount < 512 || amount > 128 * MiB || d[0x44 / 4] != amount || d[0x48 / 4] != 14) {
        return false;
    }
    for (unsigned row = 0; row < 4; row++) {
        unsigned off = (0x120 + bank * 0x20 + row * 8) / 4;
        uint64_t encoded = c[off] | (uint64_t)c[off + 1] << 32;
        if ((encoded & 31) || !z8_video_c1_address(s, encoded, amount, &outputs[row])) { return false; }
        for (unsigned j = 0; j < row; j++) {
            if (outputs[row] < outputs[j] + amount && outputs[j] < outputs[row] + amount) { return false; }
        }
    }
    return true;
}

static bool z8_video_c1_buffer_bank(NikonZ9State *s, unsigned port)
{
    Z8VideoC1 *v = &s->z8_video_c1;
    const uint32_t *c = port ? s->z8_video1530_config : s->z8_video152c_config;
    unsigned bank = c[0x108 / 4];
    if (bank > 1) { return false; }
    if (v->buffer_latched[port] && !v->buffer_waiting[port]) { return true; }
    if (v->buffer_valid[port][bank] != 0xff ||
        (v->buffer_waiting[port] && (bank == v->buffer_bank[port] ||
         v->buffer_written[port][bank] != 0xff))) { return false; }
    uint64_t addresses[4];
    if (!port) {
        if (!z8_video_c1_receiver(s,bank,addresses)) { return false; }
        if (v->buffer_latched[0] && (c[0x548/4] != v->width || c[0x54c/4] != v->height ||
            c[0x550/4] != v->capacity)) { return false; }
        v->width = c[0x548/4]; v->height = c[0x54c/4];
        v->stride = s->z8_video_geometry_latched[0][0x100/4]; v->capacity = c[0x550/4];
    } else {
        if (c[0x548/4] != v->width || c[0x54c/4] != v->height ||
            c[0x534/4] != v->capacity || c[0x114/4] != 3) { return false; }
        for (unsigned row = 0; row < 4; row++) {
            unsigned off = (0x120 + bank*0x20 + row*8)/4;
            uint64_t encoded = c[off] | (uint64_t)c[off+1] << 32;
            if ((encoded & 31) || !z8_video_c1_address(s,encoded,v->capacity,&addresses[row])) { return false; }
            for (unsigned j = 0; j < row; j++) {
                if (addresses[row] < addresses[j]+v->capacity && addresses[j] < addresses[row]+v->capacity) { return false; }
            }
        }
    }
    memcpy(v->buffer_physical[port],addresses,sizeof(addresses));
    v->buffer_bank[port] = bank;
    v->buffer_written[port][bank] = 0;
    v->buffer_latched[port] = true;
    v->buffer_waiting[port] = false;
    if (!port) { v->receiver_refill_notified = false; }
    else { v->decoder_refill_notified = false; }
    return true;
}

static uint8_t *z8_video_c1_pack(NikonZ9State *s, unsigned width,
                                unsigned height, unsigned stride)
{
    const NikonRawPixels *input = s->raw_pixels;
    uint8_t *raw = g_malloc0((size_t)stride * height);
    for (unsigned y = 0; y < height; y++) {
        unsigned sy = (uint64_t)(y / 2) * (input->height / 2) / (height / 2) * 2 + (y & 1);
        for (unsigned x = 0; x < width; x++) {
            unsigned sx = (uint64_t)(x / 2) * (input->width / 2) / (width / 2) * 2 + (x & 1);
            unsigned value = MIN(16383, ((uint64_t)input->samples[(size_t)sy * input->width + sx] *
                                        16383 + input->white / 2) / input->white);
            unsigned at = x * 14 / 8, shift = (x * 14) & 7;
            uint32_t packed = value << shift;
            raw[(size_t)y * stride + at] |= packed;
            raw[(size_t)y * stride + at + 1] |= packed >> 8;
            if (shift > 2) { raw[(size_t)y * stride + at + 2] |= packed >> 16; }
        }
    }
    return raw;
}

static void z8_video_c1_decode(NikonZ9State *s)
{
    Z8VideoC1 *v = &s->z8_video_c1;
    const uint32_t *c = s->z8_video1530_config;
    if (!v->packet_owned || v->frame_ready || v->pending[1] || c[8 / 4] != 1 ||
        c[0x404 / 4] || c[0x14 / 4] != 1 || s->z8_video15b0_config[0x38 / 4] != 1) { return; }
    if (!z8_video_c1_buffer_bank(s,1)) { return; }
    uint64_t physical = v->buffer_physical[1][v->packet_row];
    if (c[0x548 / 4] != v->width || c[0x54c / 4] != v->height ||
        c[0x534 / 4] != v->capacity ||
        !dma_ram_range(s,physical,v->packet_bytes) || physical != v->packet_address) {
        v->rejects++; v->failed = true; return;
    }
    size_t raw_bytes = (size_t)v->stride * v->height;
    uint8_t *packet = g_malloc(v->packet_bytes), *raw = g_malloc(raw_bytes);
    NBPInfo info;
    bool ok = address_space_read(&address_space_memory, physical, MEMTXATTRS_UNSPECIFIED,
                                 packet, v->packet_bytes) == MEMTX_OK &&
        nbp_decode(packet, v->packet_bytes, raw, raw_bytes, &info) &&
        info.meta.frame_id == v->epoch && info.meta.virtual_ns == v->captured_ns &&
        info.meta.width == v->width && info.meta.height == v->height &&
        info.meta.stride == v->stride && info.meta.bits == 14;
    g_free(packet);
    if (!ok) { g_free(raw); v->rejects++; v->failed = true; return; }
    uint8_t *samples = g_malloc((size_t)v->width * v->height * 2);
    for (unsigned y = 0; y < v->height; y++) {
        for (unsigned x = 0; x < v->width; x++) {
            unsigned at = x * 14 / 8, shift = (x * 14) & 7;
            const uint8_t *p = raw + (size_t)y * v->stride + at;
            uint32_t value = p[0] | (uint32_t)p[1] << 8;
            if (shift > 2) { value |= (uint32_t)p[2] << 16; }
            stw_le_p(samples + ((size_t)y * v->width + x) * 2, (value >> shift) & 16383);
        }
    }
    g_free(raw);
    /* Native REC requests8280x4688. This explicit video input bound does
     * not change the original16Mi-sample photo/fixture constructor. */
    NikonRawPixels *pixels = nikon_raw_pixels_new_bounded(samples, v->width, v->height,
                                                        0, 16383, 8280, 5520, 48 * MiB);
    g_free(v->decoded_sha256);
    v->decoded_sha256 = g_compute_checksum_for_data(G_CHECKSUM_SHA256, samples,
                                                   (size_t)v->width * v->height * 2);
    g_free(samples);
    if (!pixels) { v->rejects++; v->failed = true; return; }
    nikon_raw_pixels_free(v->pixels);
    v->pixels = pixels;
    for (unsigned ch = 0; ch < 3; ch++) {
        g_free(s->raw_pipeline_cache[ch]); s->raw_pipeline_cache[ch] = NULL;
    }
    v->decoded++;
    v->decoded_samples += (uint64_t)v->width * v->height;
    v->packet_owned = false;
    v->frame_ready = true;
    v->stream_proven[1] = true;
    if (v->packet_row == 3) { v->buffer_waiting[1] = true; }
    /* Continuous Z8 decoder client42260001 uses DATA0 and REFILL4.
     * A whole decoded frame earns DATA0/1, not the separate STOP END2. */
    v->pending[1] |= 3;
    z8_video_c1_irq(s, 1);
}

static void z8_video_c1_tick(NikonZ9State *s)
{
    Z8VideoC1 *v = &s->z8_video_c1;
    if (!v->enabled || v->failed) { return; }
    z8_video_c1_decode(s);
    if (!s->event_timing_active || s->primary_reset_stage ||
        !(s->engine150c_active & 0x4000) || (s->engine150c_stop_requested & 0x4000) ||
        (s->engine_150c_init[0x18 / 4] & 0x4000) ||
        s->z8_video152c_config[8/4] != 1 || s->z8_video152c_config[0x14/4] != 1 ||
        s->z8_video152c_config[0x404/4] || s->z8_video15ac_config[0x38/4] != 1 ||
        v->end_requested[0] || v->end_requested[1] ||
        v->packet_owned || v->frame_ready || v->pending[0] || v->pending[1]) { return; }
    if (!z8_video_c1_buffer_bank(s,0)) { return; }
    const uint64_t *outputs = v->buffer_physical[0];
    uint8_t *raw = z8_video_c1_pack(s, v->width, v->height, v->stride);
    uint8_t *packet = g_malloc0(v->capacity);
    NBPMeta meta = {v->width, v->height, v->stride, 14,
                   v->epoch + 1, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL)};
    size_t bytes;
    bool ok = nbp_encode(raw, (size_t)v->stride * v->height, &meta, packet, v->capacity, &bytes) &&
        address_space_write(&address_space_memory, outputs[v->row], MEMTXATTRS_UNSPECIFIED,
                            packet, v->capacity) == MEMTX_OK;
    g_free(raw); g_free(packet);
    if (!ok) { v->rejects++; v->failed = true; return; }
    v->epoch = meta.frame_id; v->captured_ns = meta.virtual_ns;
    v->packet_bytes = bytes; v->packet_address = outputs[v->row];
    v->packet_row = v->row;
    /* C3.11's actual client 40e8df34 subscribes13: bits0/1 deliver
     * data flags20/10000. Bit2 is STOP END and is not a frame delivery. */
    v->packet_owned = true; v->packets++; v->pending[0] |= 3;
    v->stream_proven[0] = true;
    z8_video_c1_irq(s, 0);
    if (v->packets <= 8) {
        fprintf(stderr, "Z8_VIDEO_C1_PACKET epoch=%" PRIu64 " row=%u address=%" PRIx64
                " bytes=%zu width=%u height=%u bits=14\n", v->epoch, v->row,
                v->packet_address, bytes, v->width, v->height);
    }
}

static void z8_video_c1_end(NikonZ9State *s, unsigned port)
{
    Z8VideoC1 *v = &s->z8_video_c1;
    if (v->end_requested[port] && !v->end_ready[port] && !v->pending[port]) {
        /* Original 40cbe9e0 clears its old software flag before issuing
         * STOP and waiting for a fresh END. Completed data causes must
         * retire first; an inactive/unproven stream earns no completion. */
        v->pending[port] = 4;
        v->end_ready[port] = true;
        v->stop_ends[port]++;
    }
    z8_video_c1_irq(s,port);
}

static bool z8_video_c1_access(NikonZ9State *s, hwaddr addr, uint64_t *value,
                              unsigned size, bool write)
{
    Z8VideoC1 *v = &s->z8_video_c1;
    if (!v->enabled || size != 4) { return false; }
    unsigned port = addr >= 0x15300000, base = port ? 0x15300000 : 0x152c0000;
    if (addr < base || addr >= base + 0x55c) { return false; }
    unsigned off = addr - base;
    uint32_t *c = port ? s->z8_video1530_config : s->z8_video152c_config;
    if (!write && (off == 0x10 || off == 0x1c)) {
        *value = off == 0x10 ? v->pending[port] & c[0x18 / 4] : v->pending[port];
        return true;
    }
    if (write && off == 0x1c && !(*value & ~0x7ffu)) {
        uint32_t old = v->pending[port];
        if (v->pending[port] & *value & 4) {
            if (v->end_ready[port]) {
                v->stop_acks[port]++;
                v->end_ready[port] = v->end_requested[port] = false;
            }
        }
        v->pending[port] &= ~*value;
        if (port) { v->decoder_refill_acks += !!(old & *value & 0x10); }
        else { v->receiver_refill_acks += !!(old & *value & 0x10); }
        if ((old & 3) && !(v->pending[port] & 3)) {
            v->acks[port]++;
            bool *notified = port ? &v->decoder_refill_notified : &v->receiver_refill_notified;
            if (v->packet_row == 2 && !*notified &&
                c[8/4] == 1 && !c[0x404/4] && !v->end_requested[port]) {
                *notified = true;
                if (port) { v->decoder_refills++; } else { v->receiver_refills++; }
                v->pending[port] |= 0x10;
            }
        }
        z8_video_c1_end(s,port); return true;
    }
    if (write && off == 8 && *value == 0) {
        bool active = c[off / 4] == 1;
        c[off / 4] = 0;
        if (active) {
            if (v->stream_proven[port] && !c[0x404/4] && c[0x14/4] && (c[0x18/4] & 4)) {
                v->stop_requests[port]++;
                v->end_requested[port] = true;
                v->stream_proven[port] = false;
            }
            z8_video_c1_cancel(s,-1);
            z8_video_c1_end(s,port);
        }
        return true;
    }
    if (write && off == 8 && *value == 1) {
        if (v->end_requested[port] || v->end_ready[port]) {
            unknown_access(s,addr,*value,size,true); return true;
        }
        if (!c[off / 4]) { v->stream_proven[port] = false; }
        c[off / 4] = 1;
        return true;
    }
    if (write && (off == 0x14 || off == 0x18)) {
        unsigned mask = off == 0x14 ? 1 : 0x7ff;
        if (!(*value & ~mask)) {
            c[off / 4] = *value; z8_video_c1_irq(s, port); return true;
        }
    }
    if (write && off >= 0x120 && off < 0x160 && !(off & 3)) {
        uint64_t mask = off & 4 ? 15 : UINT32_MAX;
        if (*value & ~mask) { unknown_access(s,addr,*value,size,true); return true; }
        unsigned bank = (off - 0x120)/0x20, bit = ((off - 0x120)&0x1f)/4;
        c[off/4] = *value;
        v->buffer_valid[port][bank] |= 1u << bit;
        v->buffer_written[port][bank] |= 1u << bit;
        return true;
    }
    return false;
}

static bool get_z8_video_c1_model(Object *obj, Error **errp)
{ return NIKON_Z9_LAB(obj)->z8_video_c1.enabled; }

static bool z8_video_c1_refill(NikonZ9State *s)
{
    Z8VideoC1 *v = &s->z8_video_c1;
    const uint32_t *c = s->engine1542_config[11];
    unsigned bank = c[8 / 4] & 1;
    if (bank == v->input_bank || v->input_bank_written[bank] != 0xffff) { return false; }
    for (unsigned row = 0; row < 4; row++) {
        for (unsigned word = 0; word < 4; word++) {
            if (c[(0x200 + bank * 0x200 + row * 0x80) / 4 + word] !=
                s->engine1543_latched[1][(0x200 + row * 0x80) / 4 + word]) { return false; }
        }
    }
    /* Native C1 replenishes its internal input bank while the C0 output
     * table remains programmed. Reuse needs that complete fresh request
     * and all previous output ACKs (checked by raw_pipeline_tick). Partial
     * replacement of output spans must never be treated as a retained table. */
    /* In the observed C1 video route, the stopped C0 selector remains
     * 302 while the original C1 transaction selects303 and supplies the
     * complete next output table. Require that explicitly paired bank. */
    unsigned output_bank = v->fourth_output ? bank : s->engine1542_config[10][8 / 4] & 1;
    const uint64_t pairs = UINT64_C(0xfff0fff0fff0fff0);
    uint64_t written = s->raw_pipeline_bank_written[output_bank] & pairs;
    if ((written && written != pairs) ||
        (output_bank != s->raw_pipeline_bank && written != pairs)) { return false; }
    if (v->fourth_output && v->fourth_written[output_bank] != 0xffff) { return false; }
    if (written == pairs || v->fourth_output) {
        memcpy(&s->engine1543_latched[0][(0x200 + output_bank * 0x200) / 4],
               &s->engine1542_config[10][(0x200 + output_bank * 0x200) / 4], 0x200);
        s->raw_pipeline_bank_written[output_bank] = 0;
        s->raw_pipeline_bank = output_bank;
        v->fourth_written[output_bank] = 0;
    }
    v->input_bank = bank;
    v->input_bank_written[bank] = 0;
    v->input_bank_refills++;
    s->raw_pipeline_wait_bank = false;
    return true;
}

static bool z8_video_c1_isp_ready(NikonZ9State *s)
{
    const uint32_t *c = s->engine151_latched[0];
    const uint32_t *src = s->engine1518[0].config;
    const uint32_t *a = s->engine1542_config[1];
    const uint32_t *headers = s->engine1543_latched[1];
    unsigned width = a[0x4c / 4], height = a[0x50 / 4], pitch = a[0x38 / 4];
    return s->z8_video_c1.enabled && s->z8_video_c1.frame_ready && s->z8_video_c1.pixels &&
        s->event_timing_active && !s->primary_reset_stage &&
        (s->engine150c_active & 0x4000) && !(s->engine_150c_init[0x18 / 4] & 0x4000) &&
        s->engine151_active[0] && !s->engine151_hold[0] &&
        s->engine1543_active[1] && !s->engine1543_held[1] &&
        width && height && width <= 4096 && height <= 2160 && !((width | height) & 1) &&
        width * 9 == height * 16 && pitch == ((width * 3 + 255) & ~255u) && a[0x3c / 4] == pitch &&
        (a[4 / 4] & 0x200) && a[0x10 / 4] == 1 &&
        c[0x200 / 4] == 1 && c[0x204 / 4] == 1 && !c[0x208 / 4] && !c[0x20c / 4] &&
        !c[0x210 / 4] && c[0x214 / 4] == 1 && c[0x218 / 4] == 1 &&
        c[0x21c / 4] == 5 && c[0x220 / 4] == 2 &&
        c[0x400 / 4] == (width | height << 16) && c[0x450 / 4] == c[0x400 / 4] &&
        c[0x430 / 4] == width * 3 && c[0x434 / 4] == width * 3 &&
        c[0x480 / 4] == pitch && c[0x484 / 4] == pitch &&
        c[0x740 / 4] == headers[0x200 / 4] && c[0x744 / 4] == headers[0x204 / 4] &&
        c[0x9c0 / 4] == headers[0x208 / 4] && c[0x9c4 / 4] == headers[0x20c / 4] &&
        !c[0x9a0 / 4] && !c[0x9a4 / 4] &&
        src[0xb0 / 4] == width * 3 && src[0xb4 / 4] == width * 3 &&
        src[0xbc / 4] == 1 && src[0xc0 / 4] == 1 && src[0xc4 / 4] == 0x22 && src[0xd4 / 4] == 0x22;
}

static void set_z8_video_c1_model(Object *obj, bool value, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    if (s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp, "z8-video-c1-model is immutable after realization"); return;
    }
    s->z8_video_c1.enabled = value;
}

static bool get_z8_video_c1_primary_model(Object *obj, Error **errp)
{ return NIKON_Z9_LAB(obj)->z8_video_c1.primary_boundaries; }

static void set_z8_video_c1_primary_model(Object *obj, bool value, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    if (s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp, "z8-video-c1-primary-boundaries-model is immutable after realization"); return;
    }
    s->z8_video_c1.primary_boundaries = value;
}

static char *get_z8_video_c1_state(Object *obj, Error **errp)
{
    Z8VideoC1 *v = &NIKON_Z9_LAB(obj)->z8_video_c1;
    return g_strdup_printf("{\"enabled\":%s,\"packets\":%" PRIu64 ",\"decoded\":%" PRIu64
        ",\"decoded_samples\":%" PRIu64 ",\"output_frames\":%" PRIu64 ",\"output_bytes\":%" PRIu64
        ",\"epoch\":%" PRIu64 ",\"consumed_epoch\":%" PRIu64 ",\"captured_ns\":%" PRIu64
        ",\"packet_address\":%" PRIu64 ",\"packet_bytes\":%" PRIu64
        ",\"receiver_acks\":%" PRIu64 ",\"decoder_acks\":%" PRIu64
        ",\"cancellations\":%" PRIu64 ",\"rejects\":%" PRIu64
        ",\"packet_owned\":%s,\"frame_ready\":%s,\"failed\":%s,\"row\":%u,"
        "\"receiver_pending\":%u,\"decoder_pending\":%u,\"input_bits\":14,"
        "\"decoded_sha256\":\"%s\",\"input_bank\":%u,\"input_bank_written\":[%u,%u],"
        "\"input_bank_refills\":%" PRIu64 ",\"isp_ready\":%s,"
        "\"primary_boundaries\":%s,\"primary_pending\":%u,\"primary_frames\":%" PRIu64
        ",\"primary_input_acks\":%" PRIu64 ",\"primary_output_acks\":%" PRIu64
        ",\"primary_cancelled\":%" PRIu64 ","
        "\"source_end_model\":%s,\"source_group\":%u,\"source_ends\":%" PRIu64
        ",\"source_end_acks\":%" PRIu64 ",\"source_end_cancelled\":%" PRIu64 ","
        "\"fourth_output_model\":%s,\"fourth_frames\":%" PRIu64 ",\"fourth_rejects\":%" PRIu64
        ",\"fourth_epoch\":%" PRIu64 ",\"fourth_alias\":[%" PRIu64 ",%" PRIu64 "],"
        "\"fourth_physical\":[%" PRIu64 ",%" PRIu64 "],\"fourth_packet_bytes\":[%" PRIu64 ",%" PRIu64 "],"
        "\"fourth_dimensions\":[%u,%u],\"fourth_written\":[%u,%u],"
        "\"stop_requests\":[%" PRIu64 ",%" PRIu64 "],\"stop_ends\":[%" PRIu64 ",%" PRIu64 "],"
        "\"stop_acks\":[%" PRIu64 ",%" PRIu64 "],\"stop_cancelled\":[%" PRIu64 ",%" PRIu64 "],"
        "\"buffer_banks\":[%u,%u],\"buffer_waiting\":[%s,%s],"
        "\"receiver_refills\":%" PRIu64 ",\"receiver_refill_acks\":%" PRIu64 ","
        "\"decoder_refills\":%" PRIu64 ",\"decoder_refill_acks\":%" PRIu64 ","
        "\"recording_verified\":false,\"nikon_codec_verified\":false}",
        v->enabled ? "true" : "false", v->packets, v->decoded, v->decoded_samples,
        v->output_frames, v->output_bytes, v->epoch, v->consumed_epoch, v->captured_ns,
        v->packet_address, v->packet_bytes, v->acks[0], v->acks[1], v->cancellations, v->rejects,
        v->packet_owned ? "true" : "false", v->frame_ready ? "true" : "false", v->failed ? "true" : "false",
        v->row, v->pending[0], v->pending[1], v->decoded_sha256 ? v->decoded_sha256 : "",
        v->input_bank, v->input_bank_written[0], v->input_bank_written[1], v->input_bank_refills,
        z8_video_c1_isp_ready(NIKON_Z9_LAB(obj)) ? "true" : "false",
        v->primary_boundaries ? "true" : "false", v->primary_pending, v->primary_frames,
        v->primary_input_acks, v->primary_output_acks, v->primary_cancelled,
        v->source_end ? "true" : "false", v->source_group, v->source_ends,
        v->source_end_acks, v->source_end_cancelled,
        v->fourth_output ? "true" : "false",v->fourth_frames,v->fourth_rejects,v->fourth_epoch,
        v->fourth_alias[0],v->fourth_alias[1],v->fourth_physical[0],v->fourth_physical[1],
        v->fourth_packet_bytes[0],v->fourth_packet_bytes[1],v->fourth_width,v->fourth_height,
        v->fourth_written[0],v->fourth_written[1],v->stop_requests[0],v->stop_requests[1],
        v->stop_ends[0],v->stop_ends[1],v->stop_acks[0],v->stop_acks[1],
        v->stop_cancelled[0],v->stop_cancelled[1],v->buffer_bank[0],v->buffer_bank[1],
        v->buffer_waiting[0] ? "true" : "false",v->buffer_waiting[1] ? "true" : "false",
        v->receiver_refills,v->receiver_refill_acks,v->decoder_refills,v->decoder_refill_acks);
}

static bool get_z8_video_c1_source_end(Object *obj, Error **errp)
{ return NIKON_Z9_LAB(obj)->z8_video_c1.source_end; }

static void set_z8_video_c1_source_end(Object *obj, bool value, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    if (s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp, "z8-video-c1-source-end-model is immutable after realization"); return;
    }
    s->z8_video_c1.source_end = value;
}
