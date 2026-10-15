/* SPDX-License-Identifier: GPL-2.0-or-later */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../qemu/nikon-z8-still-hevc.h"
#define CHECK(x) do { if (!(x)) { fprintf(stderr,"line %d: %s\n",__LINE__,#x); exit(1); } } while (0)

static void initialize(Z8StillHEVC *h)
{
    unsigned i;
    h->enabled = true;
    for (i = 0; i < sizeof(z8_hevc_reset)/sizeof(z8_hevc_reset[0]); i++) {
        CHECK(z8_hevc_write(h, true, z8_hevc_reset[i].off, 4, z8_hevc_reset[i].value));
    }
    for (i = 0; i < sizeof(z8_hevc_init)/sizeof(z8_hevc_init[0]); i++) {
        CHECK(z8_hevc_write(h, true, z8_hevc_init[i].off, 4, z8_hevc_init[i].value));
    }
    CHECK(h->initialized && !h->prepared);
}

static void replay(const char *path)
{
    Z8StillHEVC h = {0}, copy;
    Z8HEVCSpan spans[10];
    unsigned off, value;
    uint32_t result = 99;
    FILE *f = fopen(path,"r");
    CHECK(f != NULL); h.enabled = true;
    while (fscanf(f,"%x %x",&off,&value) == 2) {
        CHECK(z8_hevc_write(&h,true,off,4,value));
    }
    CHECK(feof(f)); fclose(f);
    CHECK(z8_hevc_spans(&h,spans) == (z8_hevc_reg(&h,0x200) == 5 ? 10u : 4u));
    /* Every explicitly submitted word is required for this first request. */
    for (off = 0; off < 0x1000; off += 4) {
        if (!z8_hevc_seen(&h,off)) { continue; }
        copy = h;
        copy.seen[(off / 4) / 64] &= ~(UINT64_C(1) << ((off / 4) % 64));
        CHECK(!z8_hevc_write(&copy,true,0x100,4,2));
    }
    copy = h; copy.pending = 0x20000;
    CHECK(!z8_hevc_write(&copy,true,0x100,4,2));
    CHECK(z8_hevc_write(&copy,true,0x120,4,0x20000));
    CHECK(z8_hevc_write(&copy,true,0x100,4,2));
    CHECK(z8_hevc_write(&h,true,0x100,4,2));
    CHECK(h.prepared && h.prepares == 1 && !z8_hevc_irq(&h));
    CHECK(z8_hevc_read(&h,true,0x100,4,&result) && result == 0);
    CHECK(!z8_hevc_write(&h,true,0x100,4,2));
    CHECK(!z8_hevc_write(&h,true,0x100,4,1));
    CHECK(!z8_hevc_write(&h,true,0x100,4,1));
    CHECK(h.start_attempts == 2 && !strcmp(h.reason,"start-unimplemented"));
    CHECK(!z8_hevc_write(&h,true,0xf00,4,0x70000000));
    CHECK(!strcmp(h.reason,"prepared-immutable"));
    initialize(&h);
    CHECK(!h.prepared && !z8_hevc_spans(&h,spans));
}
static void auxiliary_profiles(void)
{
    for(unsigned small=0;small<2;small++) {
        Z8StillHEVC h={0};initialize(&h);
        for(unsigned i=0;i<sizeof(z8_hevc_run07)/sizeof(z8_hevc_run07[0]);i++) {
            unsigned off=z8_hevc_run07[i].off;
            CHECK(z8_hevc_write(&h,true,off,4,z8_hevc_aux_value(off,z8_hevc_run07[i].value,small)));
        }
        CHECK(z8_hevc_write(&h,true,0x100,4,2));
        CHECK(z8_hevc_profile_index(&h)==(small?1:2));
        CHECK(z8_hevc_native_profile(&h)==!small);
        for(unsigned i=0;i<sizeof(z8_hevc_run07)/sizeof(z8_hevc_run07[0]);i++) {
            Z8StillHEVC changed=h;changed.reg[z8_hevc_run07[i].off/4]^=1;
            CHECK(z8_hevc_profile_index(&changed)==-1);
            unsigned bit=z8_hevc_run07[i].off/4;changed=h;
            changed.seen[bit/64]&=~(UINT64_C(1)<<(bit%64));
            CHECK(z8_hevc_profile_index(&changed)==-1);
        }
        h.seen[0]|=1;CHECK(z8_hevc_profile_index(&h)==-1);
    }
}

int main(int argc, char **argv)
{
    auxiliary_profiles();
    if (argc == 2) { replay(argv[1]); }
    Z8StillHEVC h = {0}; uint32_t v = 123;
    CHECK(!z8_hevc_native_profile(&h));
    initialize(&h);
    for (unsigned i=0;i<sizeof(z8_hevc_run06)/sizeof(z8_hevc_run06[0]);i++) {
        CHECK(z8_hevc_write(&h,true,z8_hevc_run06[i].off,4,z8_hevc_run06[i].value));
    }
    CHECK(!z8_hevc_native_profile(&h)); /* Full profile still requires prepare. */
    CHECK(z8_hevc_write(&h,true,0x100,4,2));
    CHECK(z8_hevc_native_profile(&h));
    for (unsigned i=0;i<sizeof(z8_hevc_run06)/sizeof(z8_hevc_run06[0]);i++) {
        unsigned off=z8_hevc_run06[i].off;
        Z8StillHEVC changed=h;changed.reg[off/4]^=1;
        CHECK(!z8_hevc_native_profile(&changed));
        changed=h;changed.seen[(off/4)/64]&=~(UINT64_C(1)<<((off/4)%64));
        CHECK(!z8_hevc_native_profile(&changed));
    }
    h.seen[0]|=1; CHECK(!z8_hevc_native_profile(&h)); /* Unobserved extra field. */
    memset(&h,0,sizeof(h));
    CHECK(z8_hevc_write(&h, true, 0x7f8, 4, 0x544d3234));
    CHECK(z8_hevc_write(&h, true, 0x7fc, 4, 0x36332020));
    CHECK(!z8_hevc_write(&h, false, 0x7f8, 4, 0x544d3234));
    CHECK(!z8_hevc_write(&h, true, 0x128, 4, 0));
    CHECK(!z8_hevc_read(&h, true, 0x004, 4, &v) && v == 123);
    h.enabled = true;
    CHECK(!z8_hevc_write(&h, true, 0x128, 2, 0));
    CHECK(!z8_hevc_write(&h, true, 0x129, 4, 0));
    CHECK(!z8_hevc_write(&h, true, 0x084, 4, 0));
    initialize(&h);
    CHECK(!z8_hevc_write(&h, true, 0x100, 4, 2)); /* defaults != request */
    CHECK(!z8_hevc_write(&h, true, 0x100, 4, 1));
    CHECK(!z8_hevc_write(&h, true, 0x100, 4, 1));
    CHECK(h.start_attempts == 2 && !h.prepared);
    CHECK(!z8_hevc_write(&h, true, 0xf00, 4, 1)); /* input alignment */
    CHECK(!z8_hevc_write(&h, true, 0xf04, 4, 16));
    CHECK(!z8_hevc_write(&h, true, 0x310, 4, 0x15802041));
    CHECK(!z8_hevc_write(&h, true, 0xe60, 4, 0x3000));
    CHECK(!z8_hevc_write(&h, true, 0x008, 4, 1));
    CHECK(!z8_hevc_write(&h, true, 0x100, 4, 0x80000000));
    CHECK(!z8_hevc_read(&h, true, 0x900, 4, &v));
    CHECK(!z8_hevc_read(&h, true, 0xf60, 4, &v));
    CHECK(!z8_hevc_read(&h, true, 0x110, 4, &v));
    CHECK(z8_hevc_read(&h, true, 0x004, 4, &v) && v == 0);
    CHECK(!z8_hevc_irq(&h));
    /* Unit-only abstract pending injection. No QOM/MMIO guest injection exists. */
    h.pending = 0x60000; h.errors = 0x20000000;
    CHECK(z8_hevc_write(&h, true, 0x128, 4, 0x1060055));
    CHECK(z8_hevc_write(&h, true, 0xfe8, 4, 0x60000770));
    CHECK(z8_hevc_irq(&h));
    CHECK(!z8_hevc_write(&h, true, 0x100, 4, 2));
    CHECK(!strcmp(h.reason, "pending-unacked"));
    CHECK(z8_hevc_write(&h, true, 0x120, 4, 0));
    CHECK(h.pending == 0x60000);
    CHECK(z8_hevc_write(&h, true, 0x120, 4, 0x40000));
    CHECK(h.pending == 0x20000 && z8_hevc_irq(&h));
    CHECK(z8_hevc_write(&h, true, 0xfe0, 4, 0x20000000));
    CHECK(z8_hevc_write(&h, true, 0x128, 4, 0));
    CHECK(!z8_hevc_irq(&h) && h.pending == 0x20000);
    CHECK(z8_hevc_write(&h, true, 0xfe8, 4, 0));
    CHECK(z8_hevc_write(&h, true, 0x008, 4, 0));
    CHECK(z8_hevc_write(&h, true, 0xff4, 4, 1));
    CHECK(z8_hevc_read(&h, true, 0xfe4, 4, &v) && v == 1);
    CHECK(!h.initialized && !h.prepared && h.pending == 0x20000);
    initialize(&h);
    CHECK(h.pending == 0 && h.errors == 0 && !z8_hevc_irq(&h));
    puts("Still HEVC protocol contracts passed");
    return 0;
}
