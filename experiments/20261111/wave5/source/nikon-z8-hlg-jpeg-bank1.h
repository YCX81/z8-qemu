/* SPDX-License-Identifier: GPL-2.0-or-later */
/* HX/P23: P22 frame2 40c0d2b8 ->15e21004 START. HLG owner4 is
 * independent of JPEG1 physical NB slot7. Only the observed A2 canvas
 * is admitted; I4 generation/hash/role/lease and DMA guards still own it.
 * Count/commit use the same original JPEG protocol as bank0. */
static const char *z8_hlg_bank1_profile(NikonZ9State *s,const Z8JpegSource *r,const uint32_t *c)
{
    if (!s->hlg_jpeg_bank1_model) { return "hlg_thumbnail_requires_bank0"; }
    if (!s->engine15e2_bank1_model || !s->hlg_thumbnail.enabled ||
        !s->hlg_thumbnail.valid || s->hlg_thumbnail.failed ||
        !r->live || !r->published || r->reserved ||
        s->engine1542_pending[2] || s->hlg_pipeline.owned || s->hlg_pipeline.requested ||
        s->hlg_pipeline.failed || s->hlg_pipeline.retired!=s->hlg_pipeline.jobs ||
        r->producer_job!=s->hlg_thumbnail.jobs || r->frame!=s->hlg_thumbnail.source_frame ||
        r->frame!=s->primary_d980_frames || s->hlg_thumbnail.source_job!=s->hlg_pipeline.jobs ||
        r->parent_151_job!=s->hlg_pipeline.consumed151_job) { return "hlg_bank1_source_not_retired_current"; }
    if (r->width!=160 || r->height!=106 || r->stride!=192 ||
        c[0x18/4]!=120 || c[0x1c/4]!=160 || c[0x410/4]!=192 || c[0x414/4]!=192 ||
        c[0x418/4]!=160 || c[0x41c/4]!=120 || c[0x400/4] ||
        !((c[0x404/4]==2 && !c[0x458/4]) || (!c[0x404/4] && c[0x458/4])) ||
        !jpeg_source_border(s,r,c)) { return "hlg_bank1_canvas_profile"; }
    return NULL;
}
static bool get_z8_hlg_bank1(Object *obj,Error **errp)
{ return NIKON_Z9_LAB(obj)->hlg_jpeg_bank1_model; }
static void set_z8_hlg_bank1(Object *obj,bool value,Error **errp)
{
    NikonZ9State *s=NIKON_Z9_LAB(obj);
    if (!object_dynamic_cast(obj,MACHINE_TYPE_NAME("nikon-z8-bringup")) || s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp,"z8-hlg-jpeg-bank1-model requires Z8 creation; immutable"); return;
    }
    s->hlg_jpeg_bank1_model=value;
}
static char *get_z8_hlg_bank1_state(Object *obj,Error **errp)
{
    return g_strdup_printf("{\"enabled\":%s,\"source_kind\":5,\"source_owner\":4,\"physical_slot\":7,"
        "\"scope\":\"current ACK-retired HLG A2 160x106 in CPU 160x120 canvas; I4 generations/roles/leases/IRQ retained\"}",
        NIKON_Z9_LAB(obj)->hlg_jpeg_bank1_model?"true":"false");
}
