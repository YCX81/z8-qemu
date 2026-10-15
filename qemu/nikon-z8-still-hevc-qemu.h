/* SPDX-License-Identifier: GPL-2.0-or-later
 * Included after NikonZ9State, dma_ram_range and unknown_access declarations.
 * Unique 16020000..16020fff dispatch; no MemoryRegion or Wave5 change.
 */
#ifndef NIKON_Z8_STILL_HEVC_QEMU_H
#define NIKON_Z8_STILL_HEVC_QEMU_H
#include "nikon-z8-still-hevc-backend.h"
static bool z8_still_hevc_access(NikonZ9State *s, hwaddr addr,
                                 uint64_t *value, unsigned size, bool write)
{
    Z8StillHEVC *h = &s->still_hevc;
    bool accepted = false;
    uint32_t result = 0;
    unsigned off;
    if (addr < 0x16020000 || addr >= 0x16021000) { return false; }
    off = addr - 0x16020000;
    bool irq_before=z8_hevc_irq(h);
    if (write && h->enabled && s->bootstrap_fixtures && size == 4 &&
        off == 0x100 && *value == 2 && !h->prepared && !h->pending && !h->errors) {
        Z8HEVCSpan spans[10];
        unsigned count = z8_hevc_spans(h,spans);
        for (unsigned i = 0; i < count; i++) {
            if (!dma_ram_range(s,spans[i].address,spans[i].bytes)) {
                z8_hevc_fail(h,"unbacked-ram-span");
                goto rejected;
            }
        }
    }
    uint32_t before=h->pending;
    if (write) {
        if(s->still_hevc_backend.enabled && s->bootstrap_fixtures && size==4 && off==0x100 && *value==1) {
            accepted=z8_hevc_backend_start(s);
            if(!accepted) { s->still_hevc_backend.error=h->reason; }
        } else {
            accepted = z8_hevc_write(h,s->bootstrap_fixtures,off,size,*value);
        }
        if(accepted && s->still_hevc_backend.enabled) { z8_hevc_backend_control(s,off,*value,before); }
    } else {
        accepted = size==4 && !(off&3) && s->bootstrap_fixtures && z8_hevc_backend_read(s,off,&result);
        if(!accepted) { accepted = z8_hevc_read(h,s->bootstrap_fixtures,off,size,&result); }
        if (accepted) { *value = result; }
    }
    if (!accepted) { goto rejected; }
    if(s->still_hevc_backend.enabled && s->still_hevc_backend.result_valid && !irq_before && z8_hevc_irq(h)) {
        s->still_hevc_backend.completion_irqs++;
    }
    if (h->enabled && s->still_hevc_irq) { qemu_set_irq(s->still_hevc_irq,z8_hevc_irq(h)); }
    if (write && (off == 0x7f8 || off == 0x7fc)) {
        fprintf(stderr,"Z9_1602_INIT addr=%" HWADDR_PRIx " value=%x\n",addr,(uint32_t)*value);
    }
    return true;
rejected:
    fprintf(stderr,"Z8_STILL_HEVC_REJECT off=%03x %s size=%u value=%" PRIx64
        " reason=%s initialized=%u reset_step=%u init_step=%u prepared=%u pending=%x errors=%x\n",
        off,write?"write":"read",size,*value,h->reason?h->reason:"none",
        h->initialized,h->reset_step,h->init_step,h->prepared,h->pending,h->errors);
    unknown_access(s,addr,*value,size,write);
    return true; /* Owned window: never fall through to another device. */
}

static bool get_z8_still_hevc_model(Object *obj, Error **errp)
{ return NIKON_Z9_LAB(obj)->still_hevc.enabled; }
static void set_z8_still_hevc_model(Object *obj, bool value, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    if (s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp,"z8-still-hevc-model is immutable after realization"); return;
    }
    s->still_hevc.enabled = value;
}
static char *get_z8_still_hevc_state(Object *obj, Error **errp)
{
    Z8StillHEVC *h = &NIKON_Z9_LAB(obj)->still_hevc;
    Z8HEVCBackend *b = &NIKON_Z9_LAB(obj)->still_hevc_backend;
    GString *out = g_string_new(NULL);
    g_string_append_printf(out,"{\"enabled\":%s,\"initialized\":%s,\"initializing\":%s,"
        "\"prepared\":%s,\"abort_ack\":%s,\"reset_step\":%u,\"init_step\":%u,"
        "\"writes\":%" PRIu64 ",\"rejects\":%" PRIu64 ",\"prepares\":%" PRIu64
        ",\"start_attempts\":%" PRIu64 ",\"aborts\":%" PRIu64 ",\"pending\":%u,\"errors\":%u,"
        "\"mask\":%u,\"error_mask\":%u,\"irq\":%s,\"last_reject\":\"%s\","
        "\"jobs\":%" PRIu64 ",\"dma_bytes\":%" PRIu64 ",\"completion_irqs\":%" PRIu64 ","
        "\"native_profile_observed\":%s,\"native_profile_evidence_sha256\":\"%s\","
        "\"irq_aggregation_verified\":false,"
        "\"scope\":\"P2a protocol; optional frozen-profile x265 backend has separate codec receipt\",\"registers\":{",
        h->enabled?"true":"false",h->initialized?"true":"false",h->initializing?"true":"false",
        h->prepared?"true":"false",h->abort_ack?"true":"false",h->reset_step,h->init_step,
        h->writes,h->rejects,h->prepares,h->start_attempts,h->aborts,h->pending,h->errors,
        z8_hevc_reg(h,0x128),z8_hevc_reg(h,0xfe8),z8_hevc_irq(h)?"true":"false",h->reason?h->reason:"",
        b->jobs,b->dma_bytes,b->completion_irqs,z8_hevc_native_profile(h)?"true":"false",
        z8_hevc_profile_index(h)==0?Z8_HEVC_RUN06_SHA256:z8_hevc_profile_index(h)==2?Z8_HEVC_RUN07_SHA256:"");
    bool comma = false;
    for (unsigned off = 0; off < 0x1000; off += 4) {
        if (!z8_hevc_seen(h,off) && !z8_hevc_reg(h,off)) { continue; }
        g_string_append_printf(out,"%s\"0x%03x\":{\"value\":%u,\"request_written\":%s}",
            comma?",":"",off,z8_hevc_reg(h,off),z8_hevc_seen(h,off)?"true":"false");
        comma = true;
    }
    g_string_append(out,"}}");
    return g_string_free(out,false);
}
#endif
