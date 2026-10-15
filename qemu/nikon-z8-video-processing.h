/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Adapted from WonderLantern shared146 video_processing_config_access.
 * Z8 native REC reaches global16018000 at40c37114 and reset bank8
 * bits23/24 at40c2d90c. Retain setup only: no NR processing, codec
 * picture, completion IRQ, firmware flag or queue entry is produced. */
static bool z8_processing_word(unsigned off)
{
    if (off & 3) { return false; }
    if (off <= 0x14 || off == 0x100) { return true; }
    if (off >= 0x120 && off <= 0x18c && off != 0x17c) { return true; }
    return off >= 0x200 && off < 0x300 && (off & 0x1f) <= 0x18;
}

/* Shared146 YUVCMP field masks, restricted to Z8 object1f4..1f6.
 * Source bytes are pulled only by a separately validated codec job. */
static uint32_t z8_plane_mask(unsigned off)
{
    if (off < 0x80) {
        switch (off) {
        case 0: case 0x10: return 1;
        case 0x20: case 0x28: return 0x2200000f;
        case 0x30: return 15;
        default: return 0;
        }
    }
    if (off < 0x1000 || off >= 0x2000) { return 0; }
    unsigned reg = off & 0x3ff;
    if (reg == 0 || reg == 4 || reg == 12) { return 3; }
    if (reg == 8) { return 0x7fff; }
    if (reg < 0x200) { return 0; }
    reg &= 0xff;
    switch (reg) {
    case 0: return 0x700f;
    case 0x18: case 0x28: return 0x3fffffff;
    case 0x1c: case 0x2c: return 0xffff;
    case 0x20: case 0x30: return 0xffffff;
    case 0x40: return 15;
    case 0x44: case 0x4c: return 0x3fff7fff;
    case 0x48: return 0x03ff07ff;
    case 0x64: return 0x003f0003;
    case 0x68: return 0xfff;
    case 0x7c: return 0x3ff;
    default: return reg >= 0x80 && reg < 0xc0 ? ((reg & 4) ? 15 : UINT32_MAX) : 0;
    }
}

static bool z8_plane_access(NikonZ9State *s, hwaddr addr, uint64_t *value,
                            unsigned size, bool write)
{
    Z8VideoProcessing *p = &s->z8_video_processing;
    if (!p->plane_reader || addr < 0x15660000 || addr >= 0x15662000) { return false; }
    unsigned off = addr - 0x15660000;
    if (off >= 0x1000 && (off & 0x3ff) == 0x20 && !write && size == 4) {
        *value = 0; p->plane_reads++; return true; /* no independently queued reader job */
    }
    uint64_t mask = z8_plane_mask(off);
    bool pair = size == 8 && !(off & 7) && (off & 0x3ff) >= 0x280 && (off & 0xff) < 0xc0;
    if (pair) { mask |= (uint64_t)z8_plane_mask(off + 4) << 32; }
    if ((off & 3) || !mask || (size != 4 && !pair) || (write && (*value & ~mask))) {
        unknown_access(s, addr, *value, size, write); if (!write) { *value = 0; } return true;
    }
    if (!write) {
        *value = p->plane_config[off / 4];
        if (pair) { *value |= (uint64_t)p->plane_config[off / 4 + 1] << 32; }
        p->plane_reads++; return true;
    }
    if (off == 0x10 && *value) {
        memset(p->plane_config, 0, sizeof(p->plane_config)); p->plane_resets++;
        s->z8_video_shared_reset_requests++;
    } else if (off == 0x28) { p->plane_config[off / 4] &= ~*value; }
    else {
        p->plane_config[off / 4] = *value;
        if (pair) { p->plane_config[off / 4 + 1] = *value >> 32; }
    }
    p->plane_writes++; return true;
}

static bool get_z8_plane_model(Object *obj, Error **errp)
{ return NIKON_Z9_LAB(obj)->z8_video_processing.plane_reader; }

static void set_z8_plane_model(Object *obj, bool value, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    if (s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp, "z8-video-plane-reader-model is immutable after realization"); return;
    }
    s->z8_video_processing.plane_reader = value;
}

static bool z8_processing_initialized(Z8VideoProcessing *p, unsigned port)
{
    if ((p->valid[port][0] & 0x1e) != 0x1e || !(p->valid[port][1] & 1)) { return false; }
    for (unsigned off = 0x120; off <= 0x18c; off += 4) {
        if (off == 0x17c) { continue; }
        unsigned word = off / 4;
        if (!(p->valid[port][word / 64] & (UINT64_C(1) << (word % 64)))) { return false; }
    }
    return true;
}

static bool z8_processing_access(NikonZ9State *s, hwaddr addr, uint64_t *value,
                                 unsigned size, bool write)
{
    Z8VideoProcessing *p = &s->z8_video_processing;
    if (!p->enabled) { return false; }
    if (write && (addr == 0x0d154248 || addr == 0x0d154348) && (*value & 0x1800000)) {
        if (size != 4 || *value & ~UINT64_C(0x1800000)) { goto invalid; }
        for (unsigned port = 0; port < 2; port++) {
            if ((*value & (0x800000u << port)) && p->ports[port][0]) { goto invalid; }
        }
        if (addr == 0x0d154248) { s->peripheral_release8 |= *value; }
        else {
            s->peripheral_release8 &= ~*value;
            for (unsigned port = 0; port < 2; port++) {
                if (!(*value & (0x800000u << port))) { continue; }
                memset(p->ports[port], 0, sizeof(p->ports[port]));
                memset(p->valid[port], 0, sizeof(p->valid[port]));
                p->resets++;
            }
        }
        return true;
    }
    bool global = addr >= 0x16018000 && addr < 0x16018010;
    unsigned port = addr >= 0x1601a000 ? 1 : 0;
    hwaddr base = global ? 0x16018000 : (port ? 0x1601a000 : 0x16019000);
    if (!global && !(addr >= base && addr < base + 0x300)) { return false; }
    unsigned off = addr - base, words = size / 4;
    if ((addr & 3) || !(size == 4 || (!global && size == 8 && off >= 0x100 &&
            z8_processing_word(off + 4))) || (!global && !z8_processing_word(off))) { goto invalid; }
    uint32_t *r = global ? p->global : p->ports[port];
    if (!write) {
        *value = (!global && off == 0x14) ? 0 : r[off / 4];
        if (size == 8) { *value |= (uint64_t)r[off / 4 + 1] << 32; }
        p->reads++; return true;
    }
    if (global) {
        if ((off == 0 || off == 4) ? *value > 1 : off == 8 ? *value != 0x111 : *value > UINT32_MAX) { goto invalid; }
        if (off == 12) { p->status_acks++; return true; }
        if (off != 4 && (p->ports[0][0] || p->ports[1][0])) { goto invalid; }
    } else {
        if (((off == 0 || off == 4) && *value > 1) ||
            (off == 8 && (*value & ~UINT64_C(0xff))) || (off == 12 && *value)) { goto invalid; }
        if (off == 0x14) { p->status_acks++; return true; }
        if (r[0] && !(off == 0 && !*value) &&
            !(off == 8 && !((r[2] ^ *value) & ~UINT64_C(1)))) { goto invalid; }
        if (off == 0 && *value && (!z8_processing_initialized(p, port) || p->global[2] != 0x111)) { goto invalid; }
    }
    for (unsigned i = 0; i < words; i++) {
        unsigned word = off / 4 + i;
        r[word] = *value >> (i * 32);
        if (!global) { p->valid[port][word / 64] |= UINT64_C(1) << (word % 64); }
    }
    p->writes++;
    if (global && off == 4) { z8_wave5_irq_update(s); }
    return true;
invalid:
    unknown_access(s, addr, *value, size, write);
    if (!write) { *value = 0; }
    return true;
}

static bool get_z8_processing_model(Object *obj, Error **errp)
{ return NIKON_Z9_LAB(obj)->z8_video_processing.enabled; }

static void set_z8_processing_model(Object *obj, bool value, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    if (s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp, "z8-video-processing-config-model is immutable after realization"); return;
    }
    s->z8_video_processing.enabled = value;
}

static char *get_z8_processing_state(Object *obj, Error **errp)
{
    Z8VideoProcessing *p = &NIKON_Z9_LAB(obj)->z8_video_processing;
    return g_strdup_printf("{\"enabled\":%s,\"reads\":%" PRIu64 ",\"writes\":%" PRIu64
        ",\"status_acks\":%" PRIu64 ",\"resets\":%" PRIu64 ",\"armed\":[%u,%u],"
        "\"global\":[%u,%u,%u,%u],\"pixel_bytes\":0,\"completion_events\":0,\"codec_verified\":false}",
        p->enabled ? "true" : "false", p->reads, p->writes, p->status_acks, p->resets,
        p->ports[0][0], p->ports[1][0], p->global[0], p->global[1], p->global[2], p->global[3]);
}
