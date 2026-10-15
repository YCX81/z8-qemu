/* SPDX-License-Identifier: GPL-2.0-or-later */
#include <stdio.h>
#include <stdlib.h>
#include "../qemu/nikon-buffer-ownership.h"
#define CHECK(x) do { if (!(x)) { fprintf(stderr,"failed line%d\n",__LINE__);exit(1); } } while(0)
int main(void)
{
    NBOwner o={.enabled=true};NBSpan a={0x10000,4096},b={0x20000,4096},raw={0x30000,4096};
    CHECK(NB_SLOT_153C==5 && NB_SLOT_JPEG0==6 && NB_SLOT_JPEG1==7 && NB_SLOT_COUNT==8);
    for(unsigned i=2;i<5;i++) { CHECK(!nb_reserve(&o,i,1,13,&a,1)); }
    CHECK(nb_reserve(&o,NB_SLOT_153C,1,14,&raw,1));CHECK(nb_complete(&o,NB_SLOT_153C,1));
    CHECK(nb_reserve(&o,NB_SLOT_JPEG0,1,13,&a,1));CHECK(nb_reserve(&o,NB_SLOT_JPEG1,1,14,&b,1));
    CHECK(!nb_ack(&o,NB_SLOT_JPEG1,1));CHECK(nb_complete(&o,NB_SLOT_JPEG0,1));CHECK(nb_complete(&o,NB_SLOT_JPEG1,1));
    CHECK(!nb_writable(&o,a) && !nb_writable(&o,b));
    CHECK(nb_ack(&o,NB_SLOT_JPEG0,1));CHECK(!o.slots[6].held && o.slots[7].held && o.slots[5].held);
    CHECK(!nb_ack(&o,NB_SLOT_JPEG0,1));CHECK(!nb_reserve(&o,6,2,15,&b,1));
    CHECK(nb_ack(&o,7,1));CHECK(o.slots[5].held && o.acknowledged==2);
    CHECK(nb_read_acquire(&o,0,2,a,b));CHECK(!nb_writable(&o,a));
    CHECK(!nb_reserve(&o,7,2,15,&a,1));nb_read_release(&o,0,1);CHECK(!nb_writable(&o,a));
    nb_read_release(&o,0,2);CHECK(nb_writable(&o,a));
    CHECK(nb_reserve(&o,7,2,15,&a,1));nb_cancel(&o,7);CHECK(o.slots[5].held && o.cancelled==1);
    CHECK(!nb_reserve(&o,7,2,16,&a,1));CHECK(nb_ack(&o,5,1));
    puts("independent JPEG slots and read leases passed");return 0;
}
