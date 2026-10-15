/* SPDX-License-Identifier: GPL-2.0-or-later */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../qemu/nikon-z8-1534-reference.h"
#define CHECK(x) do { if (!(x)) { fprintf(stderr,"%d %s\n",__LINE__,#x);exit(1); } } while (0)
int main(void)
{
    uint32_t a[84]={0},b[84]={0},off;
    uint8_t p[Z8_153_PROGRAM_BYTES]={0},q[Z8_153_PROGRAM_BYTES]={0};
    a[0x48/4]=0x2c97d00;a[0x4c/4]=10;b[0x48/4]=0x2c97500;b[0x4c/4]=10;
    a[0x40/4]=b[0x40/4]=11;
    CHECK(z8_1534_same_control(a,b,&off));CHECK(z8_1534_same_program(p,q,&off));
    for(unsigned i=0;i<84;i++) {
        b[i]^=1;CHECK(!z8_1534_same_control(a,b,&off));CHECK(off==4*i);b[i]^=1;
    }
    for(unsigned i=0;i<sizeof(p);i++) {
        q[i]=1;CHECK(!z8_1534_same_program(p,q,&off));CHECK(off==i);q[i]=0;
    }
    CHECK(z8_1534_next_frame(1,2,0));CHECK(!z8_1534_next_frame(0,1,0));
    CHECK(!z8_1534_next_frame(1,1,0));CHECK(!z8_1534_next_frame(1,3,0));
    CHECK(!z8_1534_next_frame(1,2,2));CHECK(!z8_1534_next_frame(UINT64_MAX,0,0));
    CHECK(!z8_1534_next_frame(1,2,3));
    puts("1534 reference contracts passed");return 0;
}
