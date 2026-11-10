/* SPDX-License-Identifier: GPL-2.0-or-later
 *
 * Optional Z8 audio input path for the lab model. Default off; every property
 * is fixed at machine creation. Behaviour with all of them off is unchanged.
 *
 * Mechanism ported (rewritten for this tree) from rdwing-z8-qemu, Copyright
 * (C) 2026 RdWing, GPL-2.0-or-later: qemu/nikon-expeed-dsp-boot.h lpcm_*,
 * dsp_boot_mailbox_access channel 3, and qemu/nikon-z8-lab.c serial5_pcm_*,
 * core1b_boot_*. See docs/native-mcu.md there.
 *
 * What this is NOT: no on-chip 1b core instruction is executed and no silicon
 * bit layout is measured. The FIFO word layout (stereo LE32, left first, sample
 * left-aligned) and the width conversion (keep the top bytes) are declared
 * functional assumptions. PCM bytes reach RAM only through an explicit block
 * from the host, before the original RX DMA completion it belongs to; with no
 * block there is no byte, no success and no IRQ.
 */
#define Z8_LPCM_MAILBOX 0x1b802000
#define Z8_LPCM_TX_DELAY_NS 1000000
#define Z8_LPCM_WORK_BYTES 0x2f000

static void z8_lpcm_irq_update(NikonZ9State *s)
{
    if (s->lpcm_rx_irq) { qemu_set_irq(s->lpcm_rx_irq, s->lpcm_rx_pending); }
    if (s->lpcm_tx_irq) { qemu_set_irq(s->lpcm_tx_irq, s->lpcm_tx_pending); }
}

static void z8_lpcm_reset(NikonZ9State *s)
{
    if (s->lpcm_tx_timer) { timer_del(s->lpcm_tx_timer); }
    if (s->core1b_boot_timer) { timer_del(s->core1b_boot_timer); }
    s->lpcm_tx_scheduled = s->lpcm_tx_pending = s->lpcm_rx_pending = false;
    s->lpcm_booted = s->lpcm_configured = s->lpcm_reply_ready = false;
    memset(s->lpcm_claims, 0, sizeof(s->lpcm_claims));
    z8_lpcm_irq_update(s);
}

static void z8_core1b_boot_response(void *opaque)
{
    NikonZ9State *s = opaque;
    uint32_t status = 0;
    if (s->core1b_config[0] || s->core1b_config[2] || s->core1b_config[8]) { return; }
    if (address_space_read(&address_space_memory, UINT64_C(0xa00080000),
                           MEMTXATTRS_UNSPECIFIED, &status, 4) != MEMTX_OK || status) {
        return;
    }
    status = cpu_to_le32(0x53535353);
    if (address_space_write(&address_space_memory, UINT64_C(0xa00080000),
                            MEMTXATTRS_UNSPECIFIED, &status, 4) == MEMTX_OK) {
        s->core1b_boot_responses++;
        s->lpcm_booted = true;
        fprintf(stderr, "Z8_LPCM_BOOT_RESPONSE ns=%" PRId64 "; boot ABI only\n",
                qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL));
    }
}

static void z8_core1b_boot_written(NikonZ9State *s, unsigned off)
{
    if (!s->lpcm_host_conversion_model) { return; }
    if (s->core1b_config[0] || s->core1b_config[2] || s->core1b_config[8]) {
        z8_lpcm_reset(s);
        return;
    }
    if (off != 0x2c || s->core1b_config[0x2c / 4] != 0x10 ||
        timer_pending(s->core1b_boot_timer)) {
        return;
    }
    uint8_t program[0x3e00];
    unsigned nonzero = 0;
    if (address_space_read(&address_space_memory, UINT64_C(0xa00000000),
                           MEMTXATTRS_UNSPECIFIED, program, sizeof(program)) != MEMTX_OK) {
        return;
    }
    for (unsigned i = 0; i < sizeof(program); i++) { nonzero += program[i] != 0; }
    if (nonzero < 1024) { return; } /* nothing uploaded: no boot result */
    s->core1b_boot_requests++;
    fprintf(stderr, "Z8_LPCM_BOOT request=%" PRIu64 " program_nonzero=%u; functional ABI, no ISA execution\n",
            s->core1b_boot_requests, nonzero);
    timer_mod(s->core1b_boot_timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + 1000000);
}

static bool z8_lpcm_address(NikonZ9State *s, unsigned direction, uint32_t local,
                            uint32_t bytes, uint64_t *physical)
{
    if (!s->bus_window_fixture || !bytes) { return false; }
    for (unsigned entry = direction * 2; entry < 32; entry += 4) {
        unsigned bank = !!(s->bus_windows[10][0][entry][0] & 0x10);
        uint64_t *w = s->bus_windows[10][bank][entry];
        uint32_t start = w[0] & 0xffffffc0, span = -(uint32_t)(w[1] & 0xffffffc0);
        if (local >= start && (uint64_t)local - start < span &&
            bytes <= span - ((uint64_t)local - start)) {
            *physical = (((bank ? UINT64_C(0x800000000) : UINT64_C(0xc00000000)) +
                          (w[2] & UINT64_C(0xfffffffc0))) & UINT64_C(0xfffffffc0)) + local - start;
            if (s->lpcm_ram_aperture_fixture && entry < 4 && w[2] == 0 &&
                w[1] == (UINT64_C(1) << 36) - MACHINE(s)->ram_size) {
                *physical = UINT64_C(0xa00000000) + local - start;
            }
            return dma_ram_range(s, *physical, bytes);
        }
    }
    return false;
}

/* Words: [2,1,mode,in,out] init, [2,3,in_bytes,out_bytes] convert, [2,2] end. */
static bool z8_lpcm_submit(NikonZ9State *s, const uint32_t *w, uint32_t mask)
{
    if (mask != 0x1ff || w[0] != 2 || s->lpcm_reply_ready || s->lpcm_rx_pending) { return false; }
    unsigned used = w[1] == 1 ? 5 : w[1] == 2 ? 2 : w[1] == 3 ? 4 : 9;
    if (used == 9) { return false; }
    for (unsigned i = used; i < 9; i++) {
        if (w[i]) { return false; }
    }
    uint64_t src, dst;
    if (w[1] == 1) {
        if (s->lpcm_configured || w[2] < 1 || w[2] > 4 ||
            !z8_lpcm_address(s, 0, w[3], Z8_LPCM_WORK_BYTES, &src) ||
            !z8_lpcm_address(s, 1, w[4], Z8_LPCM_WORK_BYTES, &dst) ||
            (src < dst + Z8_LPCM_WORK_BYTES && dst < src + Z8_LPCM_WORK_BYTES)) {
            return false;
        }
        s->lpcm_mode = w[2];
        s->lpcm_input = w[3];
        s->lpcm_output = w[4];
        s->lpcm_configured = true;
    } else if (w[1] == 2) {
        if (!s->lpcm_configured) { return false; }
        s->lpcm_configured = false;
    } else {
        unsigned ib = s->lpcm_mode == 1 || s->lpcm_mode == 3 ? 4 : 3;
        unsigned ob = s->lpcm_mode == 1 ? 3 : s->lpcm_mode == 2 ? 4 : 2;
        if (!s->lpcm_configured || !w[2] || w[2] > Z8_LPCM_WORK_BYTES || w[2] % ib ||
            w[3] != w[2] / ib * ob || w[3] > Z8_LPCM_WORK_BYTES ||
            !z8_lpcm_address(s, 0, s->lpcm_input, w[2], &src) ||
            !z8_lpcm_address(s, 1, s->lpcm_output, w[3], &dst) ||
            (src < dst + w[3] && dst < src + w[2])) {
            return false;
        }
        uint8_t *input = g_malloc(w[2]), *output = g_malloc0(w[3]);
        bool ok = address_space_read(&address_space_memory, src, MEMTXATTRS_UNSPECIFIED,
                                     input, w[2]) == MEMTX_OK;
        if (ok) {
            for (unsigned i = 0, j = 0; i < w[2]; i += ib, j += ob) {
                if (ob < ib) {
                    memcpy(output + j, input + i + ib - ob, ob);
                } else {
                    memcpy(output + j + ob - ib, input + i, ib);
                }
            }
            ok = address_space_write(&address_space_memory, dst, MEMTXATTRS_UNSPECIFIED,
                                     output, w[3]) == MEMTX_OK;
        }
        g_free(input);
        g_free(output);
        if (!ok) { return false; }
        s->lpcm_jobs++;
        s->lpcm_input_bytes += w[2];
        s->lpcm_output_bytes += w[3];
    }
    memset(s->lpcm_reply, 0, sizeof(s->lpcm_reply));
    s->lpcm_reply[0] = 2;
    s->lpcm_reply[1] = w[1];
    if (w[1] == 3) { s->lpcm_reply[2] = w[2]; }
    s->lpcm_reply_ready = true;
    fprintf(stderr, "Z8_LPCM_JOB command=%u mode=%u words=%08x,%08x,%08x,%08x,%08x jobs=%" PRIu64 "\n",
            w[1], s->lpcm_mode, w[0], w[1], w[2], w[3], w[4], s->lpcm_jobs);
    return true;
}

static void z8_lpcm_try_reply(NikonZ9State *s)
{
    if (!s->lpcm_reply_ready || s->lpcm_rx_pending || s->lpcm_tx_scheduled ||
        s->lpcm_tx_pending || s->lpcm_claims[s->lpcm_tx_slot] || s->lpcm_claims[0]) {
        return;
    }
    memset(s->lpcm_rx, 0, sizeof(s->lpcm_rx));
    memcpy(s->lpcm_rx, s->lpcm_reply, sizeof(s->lpcm_reply));
    s->lpcm_rx_pending = true;
    s->lpcm_reply_ready = false;
    s->lpcm_rx_replies++;
    z8_lpcm_irq_update(s);
}

static void z8_lpcm_tx_complete(void *opaque)
{
    NikonZ9State *s = opaque;
    if (!s->lpcm_tx_scheduled) { return; }
    s->lpcm_tx_scheduled = false;
    s->lpcm_tx_pending = true;
    z8_lpcm_irq_update(s);
}

static bool z8_lpcm_mailbox_access(NikonZ9State *s, hwaddr addr, uint64_t *value,
                                   unsigned size, bool write)
{
    if (!s->lpcm_host_conversion_model ||
        addr < Z8_LPCM_MAILBOX || addr >= Z8_LPCM_MAILBOX + 0x904) {
        return false;
    }
    unsigned off = addr - Z8_LPCM_MAILBOX;
    if (size != 4 || (addr & 3) || (write && *value > UINT32_MAX)) { goto invalid; }
    if (off == 0x84 && !write) { *value = s->lpcm_rx_pending ? 0x100 : 0; return true; }
    if (off == 0x80 && !write) {
        *value = s->lpcm_tx_pending ? 0x100 + s->lpcm_tx_slot * 0x80 : 0;
        return true;
    }
    if (off >= 0x80 && off < 0xa0 && !write) { *value = 0; return true; }
    if (off == 0x900 && !write) {
        uint32_t busy = s->lpcm_rx_pending ? 1 : 0;
        for (unsigned i = 0; i < 8; i++) {
            if (s->lpcm_claims[i]) { busy |= 1U << i; }
        }
        *value = busy;
        return true;
    }
    if (off < 0x100 || off >= 0x500) { goto invalid; }
    unsigned slot = (off - 0x100) / 0x80, reg = (off - 0x100) % 0x80;
    if (reg >= 0x40) {
        unsigned word = (reg - 0x40) / 4;
        if (write) {
            if (slot == 0 && s->lpcm_rx_pending) { goto invalid; }
            s->lpcm_tx[slot][word] = *value;
            s->lpcm_tx_written[slot] |= 1U << word;
        } else {
            *value = slot == 0 && s->lpcm_rx_pending ? s->lpcm_rx[word] : s->lpcm_tx[slot][word];
        }
        return true;
    }
    if (!write) {
        switch (reg) {
        case 0x00: *value = s->lpcm_claims[slot]; break;
        case 0x04: *value = s->lpcm_lengths[slot]; break;
        case 0x10: *value = s->lpcm_destinations[slot]; break;
        case 0x38:
            *value = s->lpcm_tx_pending && slot == s->lpcm_tx_slot ? s->lpcm_destinations[slot] : 0;
            break;
        default: goto invalid;
        }
        return true;
    }
    switch (reg) {
    case 0x00:
        if (*value > UINT16_MAX || (slot == 0 && s->lpcm_rx_pending) ||
            (slot == s->lpcm_tx_slot && (s->lpcm_tx_scheduled || s->lpcm_tx_pending))) {
            goto invalid;
        }
        if (*value && !s->lpcm_claims[slot]) {
            memset(s->lpcm_tx[slot], 0, sizeof(s->lpcm_tx[slot]));
            s->lpcm_tx_written[slot] = 0;
        }
        s->lpcm_claims[slot] = *value;
        break;
    case 0x04:
        if (*value > 7) { goto invalid; }
        s->lpcm_lengths[slot] = *value;
        break;
    case 0x10:
        if (*value > UINT16_MAX) { goto invalid; }
        s->lpcm_destinations[slot] = *value;
        break;
    case 0x14:
        if (*value != 0 && *value != 2) { goto invalid; }
        if (slot == 0 && *value == 2 && s->lpcm_rx_pending) {
            s->lpcm_rx_pending = false;
            s->lpcm_rx_acks++;
        }
        break;
    case 0x34:
        if (*value & ~UINT64_C(0xffff)) { goto invalid; }
        if (s->lpcm_tx_pending && slot == s->lpcm_tx_slot && (*value & s->lpcm_destinations[slot])) {
            s->lpcm_tx_pending = false;
            s->lpcm_tx_acks++;
        }
        break;
    case 0x08:
        if (*value != 1 || !s->lpcm_booted || s->lpcm_tx_scheduled || s->lpcm_tx_pending ||
            !s->lpcm_claims[slot] || s->lpcm_lengths[slot] != 3 ||
            s->lpcm_destinations[slot] != 0x10 || !s->lpcm_tx_written[slot] ||
            (s->lpcm_tx_written[slot] & (s->lpcm_tx_written[slot] + 1))) {
            goto invalid;
        }
        if (!z8_lpcm_submit(s, s->lpcm_tx[slot], s->lpcm_tx_written[slot])) {
            s->lpcm_rejects++;
            goto invalid;
        }
        s->lpcm_tx_slot = slot;
        s->lpcm_tx_requests++;
        s->lpcm_tx_scheduled = true;
        timer_mod(s->lpcm_tx_timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + Z8_LPCM_TX_DELAY_NS);
        break;
    default:
        goto invalid;
    }
    z8_lpcm_try_reply(s);
    z8_lpcm_irq_update(s);
    return true;
invalid:
    unknown_access(s, addr, *value, size, write);
    if (!write) { *value = 0; }
    return true;
}

static char *get_lpcm_host_state(Object *obj, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    return g_strdup_printf("{\"enabled\":%s,\"ram_aperture_fixture\":%s,\"boot_requests\":%" PRIu64
        ",\"boot_responses\":%" PRIu64 ",\"booted\":%s,\"configured\":%s,\"mode\":%u,\"input_bus\":%u,\"output_bus\":%u,"
        "\"jobs\":%" PRIu64 ",\"input_bytes\":%" PRIu64 ",\"output_bytes\":%" PRIu64 ",\"rejects\":%" PRIu64
        ",\"tx_requests\":%" PRIu64 ",\"tx_acks\":%" PRIu64 ",\"rx_replies\":%" PRIu64 ",\"rx_acks\":%" PRIu64
        ",\"tx_pending\":%s,\"rx_pending\":%s,\"original_core_instructions_executed\":false,"
        "\"physical_packing_verified\":false}",
        s->lpcm_host_conversion_model ? "true" : "false", s->lpcm_ram_aperture_fixture ? "true" : "false",
        s->core1b_boot_requests, s->core1b_boot_responses, s->lpcm_booted ? "true" : "false",
        s->lpcm_configured ? "true" : "false", s->lpcm_mode, s->lpcm_input, s->lpcm_output,
        s->lpcm_jobs, s->lpcm_input_bytes, s->lpcm_output_bytes, s->lpcm_rejects,
        s->lpcm_tx_requests, s->lpcm_tx_acks, s->lpcm_rx_replies, s->lpcm_rx_acks,
        s->lpcm_tx_pending ? "true" : "false", s->lpcm_rx_pending ? "true" : "false");
}

/* Pending original RX descriptor for this serial5 channel, or -1. Requests are
 * 25+4*channel+phase (observed ch10=25, ch11=26; both banks read one FIFO). */
static int z8_serial5_pcm_request(NikonZ9State *s, unsigned channel, uint64_t *physical)
{
    if (channel >= 5 || !s->bootstrap_fixtures || !s->bus_window_fixture || !s->serial_fixture ||
        !s->serial5_audio_config_model || !(s->serial5_config[channel][0] & 0x1000)) {
        return -1;
    }
    int found = -1;
    for (unsigned ch = 0; ch < 24; ch++) {
        uint32_t *r = s->peripheral24[ch];
        unsigned request = 25 + channel * 4 + s->serial5_pcm_phase[channel];
        if (!(r[0] & 0x80000000) || ((r[0] >> 24) & 0x3f) != request ||
            r[4] != 0x0e200138 + channel * 0x100) {
            continue;
        }
        unsigned words = (r[0] & 0xffff) + 1;
        uint64_t target;
        if (found >= 0 || (r[0] & 0x40ff0000) || (r[1] & 0x70000) ||
            (r[1] & ~0x001f0000u) != 0x2ae00000 || (r[2] & ~0x404u) || r[3] ||
            !(s->peripheral_global[ch / 8] & 0x80000000) || (words & 1) ||
            !pdma_ram_address(s, 1, r[6], words * 4, &target) || (target & 3)) {
            return -1;
        }
        for (unsigned other = 0; other < 24; other++) {
            uint32_t *q = s->peripheral24[other];
            uint64_t address;
            unsigned bytes = ((q[0] & 0xffff) + 1) * 4;
            if (other == ch || !(q[0] & 0x80000000) ||
                q[4] < 0x0e200100 || q[4] >= 0x0e200600 || (q[4] & 255) != 0x38) {
                continue;
            }
            if (pdma_ram_address(s, 1, q[6], bytes, &address) &&
                target < address + bytes && address < target + words * 4) {
                return -1;
            }
        }
        found = ch;
        *physical = target;
    }
    return found;
}

/* channel:phase:local:word-count:file-offset, all hex. Bytes come from the
 * declared serial5-pcm-file at that offset. Short source = refused, no IRQ. */
static void set_serial5_pcm_input(Object *obj, const char *value, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    unsigned channel, phase;
    uint32_t local, words;
    uint64_t offset, target;
    char extra;
    if (strspn(value, "0123456789abcdefABCDEF:") != strlen(value) ||
        sscanf(value, "%1u:%1u:%8" SCNx32 ":%8" SCNx32 ":%16" SCNx64 "%c",
               &channel, &phase, &local, &words, &offset, &extra) != 5 ||
        channel >= 5 || phase > 1) {
        error_setg(errp, "PCM input requires channel:phase:local:word-count:file-offset hex");
        return;
    }
    int ch = z8_serial5_pcm_request(s, channel, &target);
    if (ch < 0 || phase != s->serial5_pcm_phase[channel] ||
        local != s->peripheral24[ch][6] || words != (s->peripheral24[ch][0] & 0xffff) + 1) {
        error_setg(errp, "PCM input does not match one enabled mapped pending RX descriptor");
        return;
    }
    if (!s->serial5_pcm_data || offset > s->serial5_pcm_bytes ||
        (uint64_t)words * 4 > s->serial5_pcm_bytes - offset) {
        error_setg(errp, "PCM source is missing or too short; no completion issued");
        return;
    }
    if (address_space_write(&address_space_memory, target, MEMTXATTRS_UNSPECIFIED,
                            s->serial5_pcm_data + offset, words * 4) != MEMTX_OK) {
        error_setg(errp, "PCM RAM write failed; no completion issued");
        return;
    }
    s->serial5_pcm_blocks[channel]++;
    s->serial5_pcm_words[channel] += words;
    s->serial5_pcm_irqs[channel] += !!(s->peripheral24[ch][1] & 0x80000);
    s->serial5_pcm_phase[channel] ^= 1;
    /* Bytes precede device success; the original ISR owns callback, ack and refill. */
    pdma_progress(s, ch, 0);
}

static char *get_serial5_pcm_state(Object *obj, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    GString *out = g_string_new(NULL);
    g_string_append_printf(out, "{\"source_bytes\":%" PRIu64 ",\"source_sha256\":\"%s\",\"channels\":[",
                           (uint64_t)s->serial5_pcm_bytes, s->serial5_pcm_sha256 ? s->serial5_pcm_sha256 : "");
    for (unsigned i = 0; i < 5; i++) {
        uint64_t physical = 0;
        int ch = z8_serial5_pcm_request(s, i, &physical);
        g_string_append_printf(out, "%s{\"channel\":%u,\"phase\":%u,\"ready\":%s,\"dma_channel\":%d,"
            "\"local\":%u,\"words\":%u,\"physical\":%" PRIu64 ",\"blocks\":%" PRIu64
            ",\"received_words\":%" PRIu64 ",\"irq_assertions\":%" PRIu64 "}",
            i ? "," : "", i, s->serial5_pcm_phase[i], ch >= 0 ? "true" : "false", ch,
            ch >= 0 ? s->peripheral24[ch][6] : 0, ch >= 0 ? (s->peripheral24[ch][0] & 0xffff) + 1 : 0,
            ch >= 0 ? physical : 0, s->serial5_pcm_blocks[i], s->serial5_pcm_words[i], s->serial5_pcm_irqs[i]);
    }
    g_string_append(out, "],\"physical_clock_verified\":false,\"physical_packing_verified\":false}");
    return g_string_free(out, false);
}

static bool z8_audio_immutable(NikonZ9State *s, Error **errp)
{
    if (s->z8_audio_realized) {
        error_setg(errp, "Z8 audio properties are fixed at machine creation");
        return true;
    }
    return false;
}
static bool get_lpcm_ram_aperture(Object *obj, Error **errp)
{ return NIKON_Z9_LAB(obj)->lpcm_ram_aperture_fixture; }
static void set_lpcm_ram_aperture(Object *obj, bool value, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    if (!z8_audio_immutable(s, errp)) { s->lpcm_ram_aperture_fixture = value; }
}
static bool get_lpcm_host_conversion(Object *obj, Error **errp)
{ return NIKON_Z9_LAB(obj)->lpcm_host_conversion_model; }
static void set_lpcm_host_conversion(Object *obj, bool value, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    if (!z8_audio_immutable(s, errp)) { s->lpcm_host_conversion_model = value; }
}
static bool get_serial5_audio_config(Object *obj, Error **errp)
{ return NIKON_Z9_LAB(obj)->serial5_audio_config_model; }
static void set_serial5_audio_config(Object *obj, bool value, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    if (!z8_audio_immutable(s, errp)) { s->serial5_audio_config_model = value; }
}
static char *get_serial5_pcm_file(Object *obj, Error **errp)
{ return g_strdup(NIKON_Z9_LAB(obj)->serial5_pcm_path ? NIKON_Z9_LAB(obj)->serial5_pcm_path : ""); }
static void set_serial5_pcm_file(Object *obj, const char *value, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    if (!z8_audio_immutable(s, errp)) {
        g_free(s->serial5_pcm_path);
        s->serial5_pcm_path = g_strdup(value);
    }
}

/* Called once from machine init, before the GIC user wiring is finished. */
static void z8_audio_realize(NikonZ9State *s, DeviceState *gic)
{
    bool any = s->lpcm_ram_aperture_fixture || s->lpcm_host_conversion_model ||
               s->serial5_audio_config_model || s->serial5_pcm_path;
    s->z8_audio_realized = true;
    if (!any) { return; }
    if (!object_dynamic_cast(OBJECT(s), MACHINE_TYPE_NAME("nikon-z8-bringup"))) {
        error_report("Z8 audio properties require the Z8 machine");
        exit(1);
    }
    if (!s->bootstrap_fixtures || !s->bus_window_fixture || !s->lpcm_ram_aperture_fixture) {
        error_report("Z8 audio requires bootstrap, bus windows and lpcm-ram-aperture-fixture");
        exit(1);
    }
    if (s->serial5_pcm_path && !s->serial5_audio_config_model) {
        error_report("serial5-pcm-file requires serial5-audio-config-model");
        exit(1);
    }
    if (s->serial5_pcm_path) {
        GError *error = NULL;
        gsize bytes = 0;
        gchar *data = NULL;
        if (!g_file_get_contents(s->serial5_pcm_path, &data, &bytes, &error)) {
            error_report("serial5-pcm-file: %s", error->message);
            exit(1);
        }
        s->serial5_pcm_data = (uint8_t *)data;
        s->serial5_pcm_bytes = bytes;
        s->serial5_pcm_sha256 = g_compute_checksum_for_data(G_CHECKSUM_SHA256, (guchar *)data, bytes);
    }
    if (s->lpcm_host_conversion_model) {
        s->core1b_boot_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, z8_core1b_boot_response, s);
        s->lpcm_tx_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, z8_lpcm_tx_complete, s);
        s->lpcm_rx_irq = qdev_get_gpio_in(gic, 87 - 32);
        s->lpcm_tx_irq = qdev_get_gpio_in(gic, 86 - 32);
    }
}
