/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Model output ownership until native W1C/reset. No allocator/pool claim.
 * Adapted invariants from WonderLantern z9-qemu c6dbfc4 shared146.
 * Instrumented D980/151/1540/153c and frontend/video DMA writers participate;
 * CPU/other DMA writes and native history queues remain outside this contract. */
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
/* Stable output ledger slots:2..4 stay reserved,153c=5,JPEG0/1=6/7.
 * JPEG input source_owner0..4 is a separate namespace, including HEIF input4. */
enum { NB_SLOT_151 = 0, NB_SLOT_1540 = 1, NB_SLOT_153C = 5, NB_SLOT_JPEG0 = 6, NB_SLOT_JPEG1 = 7, NB_SLOT_COUNT = 8 };
static inline bool nb_slot_valid(unsigned slot)
{ return slot == NB_SLOT_151 || slot == NB_SLOT_1540 || slot == NB_SLOT_153C || slot == NB_SLOT_JPEG0 || slot == NB_SLOT_JPEG1; }
typedef struct NBOwner {
    bool enabled;
    NBSlot slots[NB_SLOT_COUNT];
    struct { uint64_t token; NBSpan planes[2]; } readers[2];
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
        if (!o->readers[i].token) { continue; }
        for (unsigned k=0;k<2;k++) {
            if (nb_overlap(a,o->readers[i].planes[k])) { return false; }
        }
    }
    for (unsigned i=0;i<NB_SLOT_COUNT;i++) {
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
    if (!nb_slot_valid(slot) || !job || !frame || !spans || !n || n>3 ||
        o->reserved==UINT64_MAX || o->completed==UINT64_MAX || o->slots[slot].held || job<=o->slots[slot].job) {
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
    if (!nb_slot_valid(slot) || !o->slots[slot].held || o->slots[slot].complete ||
        job!=o->slots[slot].job || o->completed==UINT64_MAX) { return false; }
    o->slots[slot].complete=true;o->completed++;return true;
}
static inline bool nb_ack(NBOwner *o, unsigned slot, uint64_t job)
{
    if (!o->enabled) { return true; }
    if (!nb_slot_valid(slot) || !o->slots[slot].held || !o->slots[slot].complete ||
        job!=o->slots[slot].job || o->acknowledged==UINT64_MAX) { return false; }
    o->slots[slot].held=false;o->acknowledged++;return true;
}
static inline void nb_cancel(NBOwner *o, unsigned slot)
{
    if (o->enabled && nb_slot_valid(slot) && o->slots[slot].held) {
        o->slots[slot].held=false;
        if (o->cancelled!=UINT64_MAX) { o->cancelled++; }
    }
}
/* Read leases belong to a frozen JPEG request, never a sensor-frame counter.
 * They protect only instrumented DMA; the consumer must also verify RAM SHA. */
static inline bool nb_read_acquire(NBOwner *o, unsigned bank, uint64_t token, NBSpan y, NBSpan uv)
{
    if (!o->enabled || bank >= 2 || !token || !nb_valid(y) || !nb_valid(uv) ||
        (o->readers[bank].token && o->readers[bank].token != token)) { return false; }
    o->readers[bank].token=token; o->readers[bank].planes[0]=y; o->readers[bank].planes[1]=uv;
    return true;
}
static inline void nb_read_release(NBOwner *o, unsigned bank, uint64_t token)
{
    if (bank < 2 && o->readers[bank].token == token) { memset(&o->readers[bank],0,sizeof(o->readers[bank])); }
}
#endif
