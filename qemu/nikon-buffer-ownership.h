/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Model output ownership until native W1C/reset. No allocator/pool claim.
 * Adapted invariants from WonderLantern z9-qemu c6dbfc4 shared146.
 * Only D980/151/1540 instrumented DMA writers participate; CPU/other DMA
 * writes and native history queues remain outside this contract. */
#ifndef NIKON_BUFFER_OWNERSHIP_H
#define NIKON_BUFFER_OWNERSHIP_H
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
typedef struct NBSpan { uint64_t pa, bytes; } NBSpan;
typedef struct NBSlot {
    bool held, complete;
    unsigned count;
    uint64_t job, frame;
    NBSpan spans[3];
} NBSlot;
typedef struct NBOwner {
    bool enabled;
    NBSlot slots[2]; /* 151 outputs; 1540 output. */
    uint64_t reserved, completed, acknowledged, cancelled, rejected;
} NBOwner;
static inline bool nb_valid(NBSpan a)
{ return a.bytes && a.bytes <= (UINT64_C(128)<<20) && a.pa <= UINT64_MAX-a.bytes; }
static inline bool nb_overlap(NBSpan a, NBSpan b)
{ return a.pa < b.pa+b.bytes && b.pa < a.pa+a.bytes; }
static inline bool nb_writable(const NBOwner *o, NBSpan a)
{
    if (!o->enabled) { return true; }
    if (!nb_valid(a)) { return false; }
    for (unsigned i=0;i<2;i++) {
        if (!o->slots[i].held) { continue; }
        for (unsigned k=0;k<o->slots[i].count;k++) {
            if (nb_overlap(a,o->slots[i].spans[k])) { return false; }
        }
    }
    return true;
}
static inline bool nb_reserve(NBOwner *o, unsigned slot, uint64_t job,
                              uint64_t frame, const NBSpan *spans, unsigned n)
{
    if (!o->enabled) { return true; }
    if (slot>=2 || !job || !frame || !spans || !n || n>3 ||
        o->reserved==UINT64_MAX || o->slots[slot].held || job<=o->slots[slot].job) {
        return false;
    }
    for (unsigned i=0;i<n;i++) {
        if (!nb_writable(o,spans[i])) { return false; }
        for (unsigned k=0;k<i;k++) { if (nb_overlap(spans[i],spans[k])) { return false; } }
    }
    NBSlot *s=&o->slots[slot];
    *s=(NBSlot){.held=true,.count=n,.job=job,.frame=frame};
    memcpy(s->spans,spans,n*sizeof(*spans));o->reserved++;
    return true;
}
static inline bool nb_complete(NBOwner *o, unsigned slot, uint64_t job)
{
    if (!o->enabled) { return true; }
    if (slot>=2 || !o->slots[slot].held || o->slots[slot].complete ||
        job!=o->slots[slot].job || o->completed==UINT64_MAX) { return false; }
    o->slots[slot].complete=true;o->completed++;return true;
}
static inline bool nb_ack(NBOwner *o, unsigned slot, uint64_t job)
{
    if (!o->enabled) { return true; }
    if (slot>=2 || !o->slots[slot].held || !o->slots[slot].complete ||
        job!=o->slots[slot].job || o->acknowledged==UINT64_MAX) { return false; }
    o->slots[slot].held=false;o->acknowledged++;return true;
}
static inline void nb_cancel(NBOwner *o, unsigned slot)
{
    if (o->enabled && slot<2 && o->slots[slot].held) {
        o->slots[slot].held=false;
        if (o->cancelled!=UINT64_MAX) { o->cancelled++; }
    }
}
#endif
