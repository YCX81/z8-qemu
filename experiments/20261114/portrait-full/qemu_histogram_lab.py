# SPDX-License-Identifier: GPL-2.0-or-later
"""Emulation-only patch for qemu/nikon-z9-lab.c used to show the live histogram.

Applies, in order: HISTDIAG diagnostics; c0=0 constant-paint path fill;
statistics/1508 diagnostics; forced 1508 group0 on video restart (workaround);
310=1 statistics completion without data (workaround); a port of WonderLantern
z8-qemu's RSZ E histogram unit (engine1542 instance 14 at 0x15430100, bins at
0x15c00000; https://github.com/WonderLantern/z8-qemu, GPL-2.0-or-later) with
synthetic bins; instance-14 write logging. Usage: python3 qemu_histogram_lab.py
<copy-of-nikon-z9-lab.c>. Not a hardware model; do not merge into qemu/.
"""
import sys,re
p=sys.argv[1];s=open(p).read()
DUMP=r'''
static void hist_diag(NikonZ9State *s, const char *where)
{
    Z9Graphics *g = &s->graphics;
    fprintf(stderr, "HISTDIAG %s hdr=%08x p0=%08x img=%d path=%d init=%d edges=%u "
            "46_60=%x 46_70=%x 47_c0=%x 47_c1=%x 47_df=%x 47_e0=%x 47_e1=%x 47_72=%x 47_73=%x "
            "48_21=%x 48_23=%x 48_20=%x 48_22=%x 48_4b1=%x 48_4b3=%x 48_4b4=%x 48_4b7=%x 48_4b8=%x "
            "48_4c0=%x 48_4c2=%x 48_4c3=%x 48_4c4=%x 48_610=%x 48_611=%x 48_620=%x 48_630=%x 48_640=%x 48_650=%x 48_350=%x "
            "780=%x,%x,%x,%x,%x,%x,%x,%x ctl=%x,%x geo=%x,%x color_ready=%d\n",
            where, g->header, g->payload[0], g->image_pending, g->path_pending, g->image_initialized,
            g->path_edge_count, g->config46[0x60], g->config46[0x70], g->config47[0xc0], g->config47[0xc1],
            g->config47[0xdf], g->config47[0xe0], g->config47[0xe1], g->config47[0x72], g->config47[0x73],
            g->config48[0x21], g->config48[0x23], g->config48[0x20], g->config48[0x22], g->config48[0x4b1],
            g->config48[0x4b3], g->config48[0x4b4], g->config48[0x4b7], g->config48[0x4b8],
            g->config48[0x4c0], g->config48[0x4c2], g->config48[0x4c3], g->config48[0x4c4],
            g->config48[0x610], g->config48[0x611], g->config48[0x620], g->config48[0x630],
            g->config48[0x640], g->config48[0x650], g->config48[0x350],
            g->config48[0x780], g->config48[0x781], g->config48[0x782], g->config48[0x783],
            g->config48[0x784], g->config48[0x785], g->config48[0x786], g->config48[0x787],
            g->image_control[0], g->image_control[1], g->image_geometry[0], g->image_geometry[1],
            graphics_color_ready(g));
}
'''
anchor='static bool graphics_commit(NikonZ9State *s)\n{'
assert s.count(anchor)==1
s=s.replace(anchor,DUMP+anchor)
old='''                if (!graphics_init46(r, v) &&
                    !(s->graphics_path_model && ((r == 0x60 && !(v & ~0xeU) && (v & 0xcU) != 0xcU) ||
                      (r == 0x70 && v == 0x3c && g->path_jobs &&
                       !g->path_pending && !g->image_pending)))) {
                    return false;'''
assert s.count(old)==1
s=s.replace(old,old.replace('return false;','hist_diag(s, "init46"); return false;'))
old2='''            if ((g->image_pending && g->path_pending) ||
                (g->image_pending && !graphics_render_image(s)) ||
                (g->path_pending && !graphics_render_path(s))) {
                return false;'''
assert s.count(old2)==1
s=s.replace(old2,old2.replace('return false;','hist_diag(s, "render4a"); return false;'))
# reject in graphics_consume header filter
old3='''              ((word & 0xfffff000) == 0x49000000 && count))) {
            return false;'''
assert s.count(old3)==1
s=s.replace(old3,old3.replace('return false;','fprintf(stderr, "HISTDIAG header-filter word=%08x'+chr(92)+'n", word); return false;'))
open(p,'w').write(s);print('patched')
# Model extension: histogram backdrop uses constant paint with c0=0 and a nonzero packed color.
s=open(p).read()
old4="(g->config47[0xc0] != 0x10000 && !zero_mode) || g->config48[0x610] != 8 ||"
assert s.count(old4)==1
s=s.replace(old4,"(g->config47[0xc0] != 0x10000 && g->config47[0xc0] != 0 && !zero_mode) || g->config48[0x610] != 8 ||")
open(p,'w').write(s);print('extended c0=0')
# Diagnostics for the live-view restart after enabling the histogram.
s=open(p).read()
old5="""        s->statistics_rejects++;
        statistics_update(s);
        return;"""
assert s.count(old5)==1
s=s.replace(old5,r"""        s->statistics_rejects++;
        if (s->statistics_rejects <= 6) {
            fprintf(stderr, "HISTDIAG stats-reject n=%" PRIu64 " 310=%x 314=%x 340=%x 344=%x 348=%x 34c=%x 3a8=%x enc=%" PRIx64 " bank=%u\n",
                    s->statistics_rejects, r[0x310/4], r[0x314/4], r[0x340/4], r[0x344/4], r[0x348/4], r[0x34c/4], r[0x3a8/4], encoded, bank);
        }
        statistics_update(s);
        return;""")
old6="""    s->event_timing_active = active;
    if (active && s->primary_d980_prepared) {"""
assert s.count(old6)==1
s=s.replace(old6,r"""    s->event_timing_active = active;
    fprintf(stderr, "HISTDIAG event-timing active=%d c0=%x c3=%x\n", active, s->event_control_1508[0], s->event_control_1508[3]);
    if (active && s->primary_d980_prepared) {""")
open(p,'w').write(s);print('diag2 added')
# Emulation-only workaround: after the DISP-triggered restart the firmware never
# re-enables 1508 group0, so no frame timing reaches the video pipeline.
s=open(p).read()
old7="""        if (value & 0x4000) {
            s->primary_video_active = video;
            s->primary_video_aggregate = video && aggregate;
        }"""
assert s.count(old7)==1
s=s.replace(old7,old7+r"""
        if (video && aggregate && !s->event_timing_active && !(s->event_control_1508[0] & 1)) {
            s->event_control_1508[0] |= 1;
            fprintf(stderr, "HISTHACK forced 1508 group0 on video aggregate start\n");
            event_timing_update(s);
        }""")
open(p,'w').write(s);print('hack added')
# Emulation-only: accept the 310=1 statistics profile used with the live histogram.
# No data is written; only the completion edge is produced so the GUI can proceed.
s=open(p).read()
old8="""    if (r[0x310 / 4] != 3 || r[0x314 / 4] != 1 || r[0x340 / 4] != 1 ||"""
assert s.count(old8)==1
s=s.replace(old8,r"""    if (r[0x310 / 4] == 1) {
        s->statistics_frames++;
        s->statistics_pending = 0x20;
        s->statistics_events++;
        if (s->statistics_frames <= 3 || !(s->statistics_frames % 300)) {
            fprintf(stderr, "HISTHACK stats310=1 completion frame=%" PRIu64 " (no data)\n", s->statistics_frames);
        }
        engine150c_stop_irq_update(s);
        return;
    }
""" + old8)
open(p,'w').write(s);print('stats hack added')
# Port of WonderLantern z8-qemu's RSZ E histogram unit (engine1542 instance 14,
# 0x15430100; results at 0x15c00000). Bins here are synthetic, not image-derived.
s=open(p).read()
anchor_mask="""static uint32_t engine1542_mask(unsigned i, unsigned off)
{"""
assert s.count(anchor_mask)==1
s=s.replace(anchor_mask,r"""static uint32_t hist_e_bins[3][256];
static bool hist_e_bins_ready;
static uint32_t hist_e_mask(unsigned off)
{
    switch (off) {
    case 0x00: return 1;
    case 0x04: return 0xf3;
    case 0x08: return 0xf;
    case 0x0c: return 0x7f7f;
    case 0x10: return 0xfff;
    case 0x20: case 0x24: case 0x28: case 0x2c: return 0x07ff07ff;
    case 0x30: return 0x7ff;
    case 0x40: return 0x1fff1fff;
    case 0x44: return 0x1fff;
    default: return 0;
    }
}
static void hist_e_tick(NikonZ9State *s)
{
    static uint64_t done;
    if (!(s->engine1542_config[14][0] & 1) || (s->engine1542_pending[14] & 1)) {
        return;
    }
    if (!hist_e_bins_ready) {
        for (unsigned v = 0; v < 256; v++) {
            double x = (v - 110.0) / 55.0, y = (v - 200.0) / 18.0;
            uint32_t n = (uint32_t)(900.0 * exp(-x * x) + 500.0 * exp(-y * y) + 40.0);
            hist_e_bins[0][v] = hist_e_bins[1][v] = hist_e_bins[2][v] = n;
        }
        hist_e_bins_ready = true;
    }
    s->engine1542_pending[14] |= 1;
    raw_pipeline_irq_update(s);
    if (++done <= 3 || !(done % 300)) {
        fprintf(stderr, "HISTHACK E-histogram completion n=%" PRIu64 " ctl=%x src=%x (synthetic bins)\n",
                done, s->engine1542_config[14][0], s->engine1542_config[14][1]);
    }
}
""" + anchor_mask + """
    if (i == 14 && hist_e_mask(off)) {
        return hist_e_mask(off);
    }""")
anchor_read="""    if (engine1542_read(s, addr, size, &engine1518_value)) {
        return engine1518_value;
    }"""
assert s.count(anchor_read)==1
s=s.replace(anchor_read,"""    if (size == 4 && !(addr & 3) && addr >= 0x15c00000 && addr < 0x15c00c00) {
        unsigned word = (addr - 0x15c00000) / 4;
        return hist_e_bins[word / 256][word % 256];
    }
"""+anchor_read)
anchor_tick="""    z8_video_data_receive(s);
    /* Explicit counterfactual: one primary bit2 input"""
assert s.count(anchor_tick)==1
s=s.replace(anchor_tick,"""    z8_video_data_receive(s);
    hist_e_tick(s);
    /* Explicit counterfactual: one primary bit2 input""")
anchor_fwd="static void event_timing_expire(void *opaque)\n"
assert s.count(anchor_fwd)==1
s=s.replace(anchor_fwd,"static void hist_e_tick(NikonZ9State *s);\n"+anchor_fwd)
open(p,'w').write(s);print('E histogram port added')
# Log every instance-14 configuration write (first 200).
s=open(p).read()
a="""    s->engine1542_config[i][off / 4] = value;
    if (s->engine1542_ram_input_model && i == 2) {"""
assert s.count(a)==1
s=s.replace(a,r"""    if (i == 14) {
        static unsigned logged;
        if (logged++ < 200) {
            fprintf(stderr, "HISTDIAG E-write off=%x value=%" PRIx64 " pc=%" PRIx64 "\n", off, value,
                    current_cpu ? (uint64_t)ARM_CPU(current_cpu)->env.pc : 0);
        }
    }
"""+a)
open(p,'w').write(s);print('E write log added')
