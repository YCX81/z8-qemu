/* SPDX-License-Identifier: GPL-2.0-or-later */
/* HX native hx-p23-01 request5: C/F/S unchanged; R+50/+58 and B+8/+10
 * select current C1 output2. Only f00/f08 differ from run07. The small
 * pair is the observed C1 output1 plus the unchanged 40c977a4 builder;
 * its request remains a static candidate until separately observed.
 * Finite frame2 profiles, not arbitrary address relocation. No guest patch.
 */
static int z8_hevc_photo2_profile(NikonZ9State *s)
{
    Z8HLGPipeline *p=&s->hlg_pipeline;
    Z8HEVCBackend *b=&s->still_hevc_backend;
    Z8StillHEVC *h=&s->still_hevc;
    unsigned geometry=z8_hevc_reg(h,0x310);
    bool small=geometry==0x00d40140;
    unsigned index=small?1:2;
    uint64_t y=small?UINT64_C(0x8d9101e00):UINT64_C(0x8d9124c00);
    uint64_t uv=small?UINT64_C(0x8d9119100):UINT64_C(0x8d9362800);
    if (!s->still_hevc_photo2_model || !s->heif_c1_rearm_model ||
        (geometry!=0x04380654 && !small) || p->jobs!=2 || p->source_job!=2 ||
        p->source_frame!=2 || p->retired!=2 || p->owned || p->requested || p->failed ||
        b->consumed_jobs[0]!=2 || b->jobs<4 ||
        p->target[index][0]!=y || p->target[index][1]!=uv) { return -1; }
    Z8HEVCWrite expected[sizeof(z8_hevc_run07)/sizeof(z8_hevc_run07[0])];
    for (unsigned i=0;i<sizeof(expected)/sizeof(expected[0]);i++) {
        expected[i]=z8_hevc_run07[i];
        expected[i].value=z8_hevc_aux_value(expected[i].off,expected[i].value,small);
        if (expected[i].off==0xf00) { expected[i].value=(uint32_t)y; }
        if (expected[i].off==0xf08) { expected[i].value=(uint32_t)uv; }
    }
    return z8_hevc_match_profile(h,expected,sizeof(expected)/sizeof(expected[0]),false)?index:-1;
}
static bool get_z8_still_hevc_photo2(Object *obj,Error **errp)
{ return NIKON_Z9_LAB(obj)->still_hevc_photo2_model; }
static void set_z8_still_hevc_photo2(Object *obj,bool value,Error **errp)
{
    NikonZ9State *s=NIKON_Z9_LAB(obj);
    if (!object_dynamic_cast(obj,MACHINE_TYPE_NAME("nikon-z8-bringup")) || s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp,"z8-still-hevc-photo2-model requires Z8 creation; immutable");return;
    }
    s->still_hevc_photo2_model=value;
}
