/* SPDX-License-Identifier: GPL-2.0-or-later */
#include <stdio.h>
#include <stdlib.h>
#include "../qemu/nikon-buffer-ownership.h"
#define CHECK(x) do { if (!(x)) { fprintf(stderr,"failed line %d\n",__LINE__); exit(1); } } while (0)
int main(void) {
    NBOwner o={.enabled=true};
    NBSpan a={0x10000,0x800},b={0x20000,0x1000};
    CHECK(NB_SLOT_153C==5);
    CHECK(nb_reserve(&o,NB_SLOT_1540,1,13,&a,1));
    CHECK(nb_complete(&o,NB_SLOT_1540,1));
    CHECK(!nb_reserve(&o,NB_SLOT_153C,1,14,&a,1));
    CHECK(o.slots[NB_SLOT_1540].held);
    CHECK(nb_reserve(&o,NB_SLOT_153C,1,14,&b,1));
    CHECK(!nb_ack(&o,NB_SLOT_153C,1));
    CHECK(nb_complete(&o,NB_SLOT_153C,1));
    for(unsigned i=2;i<5;i++){CHECK(!nb_reserve(&o,i,1,14,&b,1));CHECK(!nb_ack(&o,i,1));}
    CHECK(!nb_writable(&o,(NBSpan){b.pa+1,1}));
    CHECK(nb_ack(&o,NB_SLOT_1540,1));
    CHECK(o.slots[NB_SLOT_153C].held && o.acknowledged==1);
    CHECK(!nb_ack(&o,NB_SLOT_1540,1));
    CHECK(nb_ack(&o,NB_SLOT_153C,1));
    CHECK(o.acknowledged==2 && nb_writable(&o,b));
    CHECK(!nb_reserve(&o,NB_SLOT_153C,1,15,&b,1));
    CHECK(nb_reserve(&o,NB_SLOT_153C,2,15,&b,1));
    nb_cancel(&o,NB_SLOT_153C);CHECK(o.cancelled==1 && !o.slots[NB_SLOT_1540].held);
    puts("independent HSFC ownership checks passed");
}
