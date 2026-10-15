/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef NIKON_Z8_HEIF_MENU_STOP_H
#define NIKON_Z8_HEIF_MENU_STOP_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define Z8_HEIF_MENU_STOP_MASK UINT32_C(0x400e)
#define Z8_HEIF_MENU_STOP_KICK UINT32_C(0x8045)
#define Z8_HEIF_MENU_STOP_CAUSE UINT32_C(0x0200d000)

typedef struct Z8HeifMenuStop {
    bool enabled, kicked;
    uint32_t owned;
    uint64_t requests, kicks, acks, reset_cancels, rejects;
    uint64_t source_frame, source_hlg_job;
    char source_sha256[65];
    const char *last_reject;
} Z8HeifMenuStop;

typedef struct Z8HeifMenuStopInput {
    uint32_t active, latched, hold, conflicts, stop_pending, reset_stage, data_pending;
    uint64_t frames, hlg_frame, hlg_jobs;
    bool source_ready, armed, prepared, downstream_owned;
} Z8HeifMenuStopInput;

/* Native run13 request; dimensions are not a control-stop discriminator.
 * Already-issued RAM DMA is synchronous. A held completed generation and
 * matching acknowledged HLG151 consumer prove this producer can quiesce.
 * No API here can clear data pending or publish an image completion. */
static inline const char *z8_heif_menu_stop_reason(const Z8HeifMenuStop *s,
    const Z8HeifMenuStopInput *i, uint64_t value, unsigned size)
{
    if (!s->enabled) { return "disabled"; }
    if (size != 4 || value != Z8_HEIF_MENU_STOP_MASK) { return "command"; }
    if (s->owned || s->kicked) { return "owned"; }
    if ((i->active & value) != value || (i->latched & value) != value) { return "inactive-or-unlatched"; }
    if (i->reset_stage || ((i->hold | i->conflicts) & value) ||
        (i->stop_pending & Z8_HEIF_MENU_STOP_CAUSE)) { return "overlapping-control"; }
    if (i->data_pending != UINT32_C(0x04000000) || !i->frames ||
        !i->hlg_jobs || i->hlg_frame != i->frames || !i->source_ready) { return "source-generation"; }
    if (i->armed || i->prepared || i->downstream_owned) { return "in-flight"; }
    return NULL;
}

static inline void z8_heif_menu_stop_begin(Z8HeifMenuStop *s,const Z8HeifMenuStopInput *i)
{
    s->owned=Z8_HEIF_MENU_STOP_MASK;s->kicked=false;s->requests++;
    s->source_frame=i->frames;s->source_hlg_job=i->hlg_jobs;s->last_reject=NULL;
}
static inline bool z8_heif_menu_stop_kick_valid(const Z8HeifMenuStop *s,uint32_t engines,uint64_t value)
{
    return !(engines & s->owned) || (!s->kicked && s->owned==Z8_HEIF_MENU_STOP_MASK &&
        engines==Z8_HEIF_MENU_STOP_MASK && value==Z8_HEIF_MENU_STOP_KICK);
}
static inline void z8_heif_menu_stop_kicked(Z8HeifMenuStop *s)
{ s->kicked=true;s->kicks++; }
static inline void z8_heif_menu_stop_retire(Z8HeifMenuStop *s,uint32_t engines)
{
    if (engines & s->owned) { s->acks++;s->owned &= ~engines; }
    if (!s->owned) { s->kicked=false; }
}
static inline void z8_heif_menu_stop_cancel(Z8HeifMenuStop *s)
{
    s->reset_cancels += !!s->owned;s->owned=0;s->kicked=false;
}
#endif
