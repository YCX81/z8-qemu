/* SPDX-License-Identifier: GPL-2.0-or-later */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../qemu/nikon-z8-heif-menu-stop.h"
#define CHECK(x) do { if (!(x)) { fprintf(stderr,"line %d: %s\n",__LINE__,#x); exit(1); } } while (0)
static Z8HeifMenuStopInput ready(void)
{
    Z8HeifMenuStopInput i={0};
    i.active=i.latched=0x77f7f;i.data_pending=0x4000000;
    i.frames=i.hlg_frame=i.hlg_jobs=1;i.source_ready=true;
    return i;
}
int main(void)
{
    Z8HeifMenuStop s={0},before;Z8HeifMenuStopInput i=ready();
    CHECK(z8_heif_menu_stop_reason(&s,&i,0x400e,4));
    s.enabled=true;
    CHECK(!z8_heif_menu_stop_reason(&s,&i,0x400e,4));
    for (unsigned width=1;width<=8;width*=2) {
        if(width!=4)CHECK(z8_heif_menu_stop_reason(&s,&i,0x400e,width));
    }
    for(unsigned bit=0;bit<32;bit++)CHECK(z8_heif_menu_stop_reason(&s,&i,0x400e^(1u<<bit),4));
    before=s;
#define BAD(field,value) do { Z8HeifMenuStopInput b=i;b.field=value;CHECK(z8_heif_menu_stop_reason(&s,&b,0x400e,4));CHECK(!memcmp(&s,&before,sizeof(s))); } while(0)
    BAD(active,0x400c);BAD(latched,0xe);BAD(hold,2);BAD(conflicts,0x4000);
    BAD(stop_pending,0x2000000);BAD(reset_stage,1);BAD(data_pending,0);
    BAD(frames,0);BAD(hlg_frame,2);BAD(hlg_jobs,0);BAD(source_ready,false);
    BAD(armed,true);BAD(prepared,true);BAD(downstream_owned,true);
    z8_heif_menu_stop_begin(&s,&i);
    CHECK(s.owned==0x400e && s.requests==1 && s.source_frame==1 && !s.kicked);
    CHECK(z8_heif_menu_stop_reason(&s,&i,0x400e,4));
    CHECK(!z8_heif_menu_stop_kick_valid(&s,0xe,0x45));
    CHECK(z8_heif_menu_stop_kick_valid(&s,0x400e,0x8045));
    z8_heif_menu_stop_kicked(&s);
    CHECK(!z8_heif_menu_stop_kick_valid(&s,0x400e,0x8045));
    z8_heif_menu_stop_retire(&s,2);CHECK(s.owned==0x400c && s.acks==1);
    z8_heif_menu_stop_retire(&s,0x400c);CHECK(!s.owned && !s.kicked && s.acks==2);
    z8_heif_menu_stop_begin(&s,&i);z8_heif_menu_stop_cancel(&s);
    CHECK(!s.owned && s.reset_cancels==1);
    CHECK(i.data_pending==0x4000000); /* No API has a mutable DMA/IRQ input. */
    puts("HEIF menu stop contracts passed");return 0;
}
