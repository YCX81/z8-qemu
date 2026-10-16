/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef NIKON_Z8_1534_ADDRESSES_H
#define NIKON_Z8_1534_ADDRESSES_H
#include <stdint.h>
#include <stdbool.h>
/* MAIN 40c28310..40c28694, corroborated by SB6 and H P20 frame2.
 * 12 effective 0x70 descriptors after 0x90 header. Only seven packed
 * 36-bit address pairs are relocatable. Bytes >=0x5d0 are receipt-only.
 * Restrict to the observed adjacent bank allocations: 0xef0000 and
 * 0xa00000. Preserve every relative address and all alias relationships.
 * These are containing allocation envelopes, NOT inferred pixel layouts.
 * Caller validates both whole envelopes against RAM and NB ownership.
 */
typedef struct Z8AddressArena { uint64_t base,bytes; } Z8AddressArena;
static inline uint32_t z8_a32(const uint8_t *p)
{ return p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24); }
static inline uint64_t z8_a36(const uint8_t *p)
{ return z8_a32(p)|((uint64_t)(z8_a32(p+4)&15)<<32); }
static inline bool z8_1534_addresses(const uint32_t *r,const uint32_t *c,
    const uint8_t *rp,const uint8_t *cp,Z8AddressArena a[2],uint32_t *off)
{
    uint64_t rb=r[0xdc/4]|((uint64_t)r[0xd8/4]<<32);
    uint64_t cb=c[0xdc/4]|((uint64_t)c[0xd8/4]<<32);
    uint64_t scratch=UINT64_MAX;
    *off=0x40;
    if(r[0x40/4]!=11 || c[0x40/4]!=11 || rb<UINT64_C(0x800000000) ||
       rb>UINT64_C(0x900000000)-0x1de0000 || (rb&511) || cb!=rb+0xef0000) return false;
    a[0]=(Z8AddressArena){cb,0xef0000};
    for(unsigned i=0;i<84;i++) {
        uint32_t wanted=r[i];
        if(i==0x48/4) { if(r[i]!=0x02c97d00) return false; wanted=0x02c97500; }
        if(i==0x4c/4 && r[i]!=0xa) return false;
        if(i==0xd8/4) wanted=cb>>32;
        if(i==0xdc/4) wanted=cb;
        if(i==0x100/4) wanted=(cb+0xcc7c00)>>32;
        if(i==0x104/4) wanted=cb+0xcc7c00;
        if(c[i]!=wanted) { *off=i*4;return false; }
    }
    if((r[0x104/4]|((uint64_t)r[0x100/4]<<32))!=rb+0xcc7c00) return false;
    for(unsigned b=0;b<12;b++) for(unsigned j=8;j<=0x38;j+=8) {
        uint64_t addr=z8_a36(rp+0x90+0x70*b+j);
        if(addr && !(addr>=rb && addr<rb+0xef0000) && addr<scratch) scratch=addr;
    }
    if(scratch==UINT64_MAX || (scratch&255) || scratch<UINT64_C(0x800000000) ||
       scratch>UINT64_C(0x900000000)-0x1400000) return false;
    a[1]=(Z8AddressArena){scratch+0xa00000,0xa00000};
    if(a[0].base<a[1].base+a[1].bytes && a[1].base<a[0].base+a[0].bytes) return false;
    for(unsigned n=0;n<0x5d0;n+=4) {
        unsigned rel=n>=0x90?(n-0x90)%0x70:0x70;
        if(n>=0x90 && rel>=8 && rel<=0x38 && !(rel&7)) {
            uint64_t old=z8_a36(rp+n),now=z8_a36(cp+n),wanted=0;
            *off=n;
            if(old) {
                unsigned pool=old>=rb && old<rb+0xef0000?0:1;
                uint64_t base=pool?scratch:rb,bytes=pool?0xa00000:0xef0000;
                if((old&255) || old<base || old>base+bytes-256) return false;
                wanted=a[pool].base+(old-base);
                if((now&255) || now<a[pool].base || now>a[pool].base+bytes-256) return false;
            }
            if(now!=wanted || ((z8_a32(rp+n+4)^z8_a32(cp+n+4))&~15u)) return false;
            n+=4;
        } else if(z8_a32(rp+n)!=z8_a32(cp+n)) { *off=n;return false; }
    }
    *off=UINT32_MAX;return true;
}
#endif
