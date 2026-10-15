/* SPDX-License-Identifier: GPL-2.0-or-later */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../qemu/nikon-z8-hlg-pixels.h"
#define CHECK(v) do { if (!(v)) { fprintf(stderr,"line %d: %s\n",__LINE__,#v); return 1; } } while (0)
int main(void)
{
    uint32_t c[0x7000/4] = {0}; Z8HLGPlan p;
    c[0x200/4]=0x10000; c[0x218/4]=1; c[0x21c/4]=1; c[0x220/4]=19;
    c[0x400/4]=(16<<16)|64; c[0x450/4]=(16<<16)|64;
    c[0x430/4]=96; c[0x480/4]=c[0x484/4]=128; c[0x45c/4]=5;
    CHECK(z8_hlg_plan(c,12,&p)); CHECK(p.output_bits==10 && p.uv_height==8 && p.plane_y==2048 && p.plane_uv==1024);
    for (unsigned mode=0; mode<8; mode++) {
        c[0x45c/4]=mode;
        if(mode==2) c[0x480/4]=c[0x484/4]=96;
        else c[0x480/4]=c[0x484/4]=128;
        CHECK(z8_hlg_plan(c,12,&p)==(mode==2 || mode==5));
    }
    c[0x45c/4]=2; c[0x480/4]=c[0x484/4]=96;
    CHECK(z8_hlg_plan(c,12,&p)); CHECK(p.output_bits==12 && p.uv_height==16);
    const unsigned offsets[]={0x200,0x204,0x60,0x64,0x218,0x21c,0x220,0x480,0x484};
    for(unsigned i=0;i<sizeof(offsets)/sizeof(offsets[0]);i++) {
        unsigned o=offsets[i]/4; uint32_t old=c[o]; c[o]^=1;
        CHECK(!z8_hlg_plan(c,12,&p)); c[o]=old;
    }
    c[0x454/4]=2; CHECK(!z8_hlg_plan(c,12,&p)); c[0x454/4]=0;
    c[0x450/4]=0; CHECK(!z8_hlg_plan(c,12,&p));
    c[0x400/4]=(5520u<<16)|8280; c[0x450/4]=(5504u<<16)|8256;
    c[0x454/4]=(8<<16)|12; c[0x430/4]=12448;
    c[0x480/4]=c[0x484/4]=12384;
    CHECK(z8_hlg_plan(c,12,&p)); CHECK(p.plane_y>64u*1024*1024);
    CHECK(!z8_hlg_plan(c,16,&p));
    uint8_t row[8]={0};
    z8_hlg_store(row,0,10,1); z8_hlg_store(row,1,10,2); z8_hlg_store(row,2,10,1023);
    CHECK(row[0]==1 && row[1]==8 && row[2]==240 && row[3]==63);
    z8_hlg_store(row,3,10,513); CHECK(row[4]==1 && row[5]==2);
    const unsigned expected[]={1,2,1023,513};
    for(unsigned i=0;i<4;i++) CHECK(z8_hlg_load(row,i,10)==expected[i]);
    memset(row,0,sizeof(row)); z8_hlg_store(row,0,12,0xabc); z8_hlg_store(row,1,12,0x123);
    CHECK(row[0]==0xbc && row[1]==0x3a && row[2]==0x12);
    CHECK(z8_hlg_load(row,0,12)==0xabc && z8_hlg_load(row,1,12)==0x123);
    puts("HLG pixel contracts passed (packing hypothesis only)"); return 0;
}
