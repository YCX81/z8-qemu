/* SPDX-License-Identifier: GPL-2.0-or-later */
/* B3/F3 -> paired internal-port/RAM transport. Native 40c75374 checks each
 * selected address pair at +10/+30 and +58/+78; 40c754d4 submits the banks.
 * The NZ8YUV1/zlib payload is an explicit simulation convention, not recovered
 * Nikon plane compression or physical bus topology. No unbacked port is RAM. */
#include "nikon-video-plane.h"

typedef struct Z8FourthWrite {
    uint64_t alias[2], physical[2];
    size_t capacity[2], bytes[2];
    uint8_t *packet[2];
    unsigned width, height, stride;
} Z8FourthWrite;

static void z8_fourth_free(Z8FourthWrite *w)
{
    for (unsigned i = 0; i < 2; i++) { g_free(w->packet[i]); w->packet[i] = NULL; }
}

static bool z8_fourth_mapping(NikonZ9State *s, uint64_t alias, unsigned plane,
                             unsigned width, unsigned rows, unsigned stride,
                             uint64_t *physical, size_t *capacity)
{
    if (alias < UINT64_C(0x200000000) || alias >= UINT64_C(0x230000000) ||
        (alias - UINT64_C(0x200000000)) % (48 * MiB)) { return false; }
    const uint32_t *c = s->z8_video1564_config;
    unsigned control = (0x1000 + plane * 0x400) / 4;
    if (c[control] != 1 || c[control + 3] != 3) { return false; }
    unsigned matches = 0;
    for (unsigned bank = 0; bank < 2; bank++) {
        unsigned index = bank * 2 + plane;
        unsigned off = (0x1200 + bank * 0x100 + plane * 0x400) / 4;
        if (c[off + 0x18 / 4] != (uint64_t)stride * rows ||
            c[off + 0x1c / 4] != stride || c[off + 0x20 / 4] != stride ||
            c[off + 0x44 / 4] != (width | rows << 16)) { continue; }
        for (unsigned slot = 0; slot < 4; slot++) {
            if (!(c[off + 0x40 / 4] & (1u << slot)) ||
                s->z8_video1564_addresses[index][slot] != alias) { continue; }
            uint64_t target = s->z8_video1564_addresses[index][slot + 4];
            uint64_t amount = c[off + 0x28 / 4];
            if ((target & 15) || amount < 512 || amount > 64 * MiB ||
                !dma_ram_range(s,target,amount)) { return false; }
            *physical = target; *capacity = amount; matches++;
        }
    }
    return matches == 1;
}

static bool z8_fourth_prepare(NikonZ9State *s, unsigned bank, unsigned row,
                              const uint64_t ys[3], const uint64_t uvs[3],
                              const unsigned heights[3], const unsigned strides[3],
                              const bool halves[3], Z8FourthWrite *write)
{
    const uint32_t *b = s->engine1542_config[6], *f = s->engine1542_config[18];
    const uint32_t *ring = s->engine1543_latched[0];
    write->width = f[0x2c / 4]; write->height = f[0x30 / 4]; write->stride = b[0x38 / 4];
    if (b[0x10 / 4] != 1 || (b[0xc / 4] & 0x10) || !write->width || !write->height ||
        ((write->width | write->height | write->stride) & 1) ||
        write->width > 4096 || write->height > 2160 || write->stride < write->width ||
        write->stride > 8192 || b[0x3c / 4] != write->stride) { return false; }
    uint64_t all_addresses[8], all_sizes[8];
    for (unsigned ch = 0; ch < 3; ch++) {
        all_addresses[2*ch] = ys[ch]; all_addresses[2*ch+1] = uvs[ch];
        all_sizes[2*ch] = (uint64_t)strides[ch] * heights[ch];
        all_sizes[2*ch+1] = all_sizes[2*ch] / (halves[ch] ? 2 : 1);
    }
    for (unsigned plane = 0; plane < 2; plane++) {
        unsigned off = (0x240 + bank * 0x200 + row * 0x80 + plane * 8) / 4;
        write->alias[plane] = ring[off] | (uint64_t)ring[off+1] << 32;
        if (!z8_fourth_mapping(s,write->alias[plane],plane,write->width,
                              write->height / (plane ? 2 : 1),write->stride,
                              &write->physical[plane],&write->capacity[plane])) { return false; }
        all_addresses[6+plane] = write->physical[plane];
        all_sizes[6+plane] = write->capacity[plane];
    }
    for (unsigned i = 0; i < 8; i++) {
        for (unsigned j = 0; j < i; j++) {
            if (all_addresses[i] < all_addresses[j]+all_sizes[j] &&
                all_addresses[j] < all_addresses[i]+all_sizes[i]) { return false; }
        }
        if (all_addresses[i] < s->z8_video_c1.packet_address+s->z8_video_c1.capacity &&
            s->z8_video_c1.packet_address < all_addresses[i]+all_sizes[i]) { return false; }
    }
    uint8_t *nv12 = nikon_raw_pixels_yuv(s->z8_video_c1.pixels,write->width,write->height,write->stride,true);
    if (!nv12) { return false; }
    bool valid = true;
    for (unsigned plane = 0; plane < 2; plane++) {
        NVPInfo info = { .width=write->width, .height=write->height/(plane ? 2 : 1),
                        .stride=write->stride, .plane=plane, .epoch=s->z8_video_c1.epoch,
                        .virtual_ns=s->z8_video_c1.captured_ns };
        info.raw_bytes = (uint64_t)info.stride * info.height;
        write->packet[plane] = g_malloc0(write->capacity[plane]);
        const uint8_t *pixels = nv12 + (plane ? (size_t)write->stride * write->height : 0);
        if (!nvp_encode(pixels,&info,write->packet[plane],write->capacity[plane],&write->bytes[plane])) {
            valid = false; break;
        }
    }
    g_free(nv12);
    if (!valid) { z8_fourth_free(write); }
    return valid;
}

static bool z8_fourth_commit(NikonZ9State *s, Z8FourthWrite *write)
{
    for (unsigned plane = 0; plane < 2; plane++) {
        if (address_space_write(&address_space_memory,write->physical[plane],MEMTXATTRS_UNSPECIFIED,
                                write->packet[plane],write->capacity[plane]) != MEMTX_OK) { return false; }
    }
    Z8VideoC1 *v = &s->z8_video_c1;
    v->fourth_frames++;
    for (unsigned plane = 0; plane < 2; plane++) {
        v->fourth_alias[plane] = write->alias[plane];
        v->fourth_physical[plane] = write->physical[plane];
        v->fourth_packet_bytes[plane] = write->bytes[plane];
    }
    v->fourth_epoch = v->epoch;
    v->fourth_width = write->width; v->fourth_height = write->height;
    if (v->fourth_frames <= 8) {
        fprintf(stderr,"Z8_VIDEO_FOURTH epoch=%" PRIu64 " aliases=%" PRIx64 ":%" PRIx64
                " ram=%" PRIx64 ":%" PRIx64 " dimensions=%ux%u; private NZ8YUV1 transport\n",
                v->epoch,write->alias[0],write->alias[1],write->physical[0],write->physical[1],write->width,write->height);
    }
    return true;
}

static bool get_z8_video_fourth(Object *obj, Error **errp)
{ return NIKON_Z9_LAB(obj)->z8_video_c1.fourth_output; }

static void set_z8_video_fourth(Object *obj, bool value, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    if (s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp,"z8-video-c1-fourth-output-model is immutable after realization"); return;
    }
    s->z8_video_c1.fourth_output = value;
}
