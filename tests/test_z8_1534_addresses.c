/* SPDX-License-Identifier: GPL-2.0-or-later */
#include <stdlib.h>
#define CHECK(x) do { if (!(x)) { fprintf(stderr,"failed line %u: %s\n",__LINE__,#x);exit(1); } } while (0)
#include <stdio.h>
#include <string.h>
#include "nikon-z8-1534-addresses.h"
int main(int argc,char **argv)
{
    uint32_t r[84],c[84],off; uint8_t rp[0x7c0],cp[0x7c0]; Z8AddressArena a[2];
    CHECK(argc==2); FILE *f=fopen(argv[1],"rb"); CHECK(f);
    CHECK(fread(r,1,sizeof(r),f)==sizeof(r));CHECK(fread(c,1,sizeof(c),f)==sizeof(c));
    CHECK(fread(rp,1,sizeof(rp),f)==sizeof(rp));CHECK(fread(cp,1,sizeof(cp),f)==sizeof(cp));fclose(f);
    CHECK(z8_1534_addresses(r,c,rp,cp,a,&off));
    cp[0x790]^=1;CHECK(z8_1534_addresses(r,c,rp,cp,a,&off));
    cp[0x94]^=1;CHECK(!z8_1534_addresses(r,c,rp,cp,a,&off));cp[0x94]^=1;
    cp[0xb0]^=1;CHECK(!z8_1534_addresses(r,c,rp,cp,a,&off));cp[0xb0]^=1;
    cp[0xb4]^=0x10;CHECK(!z8_1534_addresses(r,c,rp,cp,a,&off));cp[0xb4]^=0x10;
    c[0xdc/4]+=256;CHECK(!z8_1534_addresses(r,c,rp,cp,a,&off));c[0xdc/4]-=256;
    c[0xd8/4]=0xf;CHECK(!z8_1534_addresses(r,c,rp,cp,a,&off));
    puts("native address descriptor positives/negatives passed");
}
