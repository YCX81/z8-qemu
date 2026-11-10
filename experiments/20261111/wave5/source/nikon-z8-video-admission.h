/* SPDX-License-Identifier: GPL-2.0-or-later */
/* START admission uses the native program, not the later1518 source gate.
 * Original order: C1 ->151 START ->152 ->1518 gates ->frontends ->150c.
 * Runtime gates remain in z8_video_isp_tick/z8_video_data_receive. */
static const char *z8_video_isp_start_reject(NikonZ9State *s)
{
    const uint32_t *c = s->engine151_config[0], *src = s->engine1518[0].config;
    const uint32_t *a = s->engine1542_config[1], *h = s->engine1543_latched[1];
    unsigned w = a[0x4c/4], height = a[0x50/4], pitch = a[0x38/4];
    if (!(s->z8_video_interface_model)) { return "video:interface"; }
    if (!(s->z8_video_c1_owner)) { return "video:c1_owner"; }
    if (!(s->engine1543_active[1])) { return "video:c1_active"; }
    if (!(!s->engine1543_held[1])) { return "video:c1_held"; }
    if (!((a[1] & 0x200))) { return "video:a1_enable"; }
    if (!(w)) { return "video:width_zero"; }
    if (!(height)) { return "video:height_zero"; }
    if (!(!((w|height)&1))) { return "video:odd_geometry"; }
    if (!(w <= 4096)) { return "video:width_limit"; }
    if (!(height <= 2160)) { return "video:height_limit"; }
    if (!(w*9 == height*16)) { return "video:aspect"; }
    if (!(pitch == ((w*3+255)&~255u))) { return "video:pitch"; }
    if (!(a[0x3c/4] == pitch)) { return "video:a1_uv_pitch"; }
    if (!(src[0xb0/4] == w*3)) { return "video:source_y_pitch"; }
    if (!(src[0xb4/4] == w*3)) { return "video:source_uv_pitch"; }
    if (!(src[0xbc/4] == 1)) { return "video:source_bc"; }
    if (!(src[0xc0/4] == 1)) { return "video:source_c0"; }
    if (!(src[0xc4/4] == 0x22)) { return "video:source_c4"; }
    if (!(src[0xd4/4] == 0x22)) { return "video:source_d4"; }
    if (!((h[2]&~1u) == 0x302)) { return "video:c1_header"; }
    if (!(h[4] == 0x29)) { return "video:c1_mask"; }
    if (!(c[0x200/4] == 1)) { return "video:w200"; }
    if (!(c[0x204/4] == 1)) { return "video:w204"; }
    if (!(!c[0x208/4])) { return "video:w208"; }
    if (!(!c[0x20c/4])) { return "video:w20c"; }
    if (!(!c[0x210/4])) { return "video:w210"; }
    if (!(c[0x214/4] == 1)) { return "video:w214"; }
    if (!(c[0x218/4] == 1)) { return "video:w218"; }
    if (!(c[0x21c/4] == 5)) { return "video:w21c"; }
    if (!(c[0x220/4] == 2)) { return "video:w220"; }
    if (!(c[0x400/4] == (w|(height<<16)))) { return "video:input_geometry"; }
    if (!(c[0x450/4] == c[0x400/4])) { return "video:output_geometry"; }
    if (!(c[0x430/4] == w*3)) { return "video:input_y_pitch"; }
    if (!(c[0x434/4] == w*3)) { return "video:input_uv_pitch"; }
    if (!(c[0x480/4] == pitch)) { return "video:output_y_pitch"; }
    if (!(c[0x484/4] == pitch)) { return "video:output_uv_pitch"; }
    if (!(c[0x740/4] == h[0x200/4])) { return "video:y_low"; }
    if (!(c[0x744/4] == h[0x204/4])) { return "video:y_high"; }
    if (!(c[0x9c0/4] == h[0x208/4])) { return "video:uv_low"; }
    if (!(c[0x9c4/4] == h[0x20c/4])) { return "video:uv_high"; }
    return NULL;
}

static void engine151_start_log(NikonZ9State *s, unsigned i, const char *reason)
{
    const uint32_t *c = s->engine151_config[i], *src = s->engine1518[0].config;
    const uint32_t *h = s->engine1543_latched[1], *a = s->engine1542_config[1];
    fprintf(stderr, "Z9_ENGINE151_%s cpu=%d pc=0x%" PRIx64
        " base=0x%x instance=%u reason=%s w200=%x w204=%x w21c=%x w220=%x"
        " w400=%x w430=%x w480=%x w740=%x w9c0=%x c1h=%x/%x a4=%x"
        " src_b0=%x src_gate=%x c1_owner=%u isp0_owner=%u\n",
        reason ? "REJECT" : "START", current_cpu ? current_cpu->cpu_index : -1,
        current_cpu ? ARM_CPU(current_cpu)->env.pc : 0, 0x15100000+i*0x40000, i,
        reason ? reason : "accepted", c[0x200/4], c[0x204/4], c[0x21c/4], c[0x220/4],
        c[0x400/4], c[0x430/4], c[0x480/4], c[0x740/4], c[0x9c0/4], h[2], h[4], a[1],
        src[0xb0/4], src[0x30/4], s->z8_video_c1_owner, s->z8_video_isp_owner);
}
