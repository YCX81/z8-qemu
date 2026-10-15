/* SPDX-License-Identifier: GPL-2.0-or-later */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "hevc_probe_parse.h"
#define CHECK(v) do { if (!(v)) { fprintf(stderr,"line %d: %s\n",__LINE__,#v); exit(1); } } while (0)
static unsigned pos;
static uint8_t bits[512];
static void put(unsigned n, uint32_t v)
{ for (unsigned i=n;i;i--,pos++) { bits[pos/8] |= ((v>>(i-1))&1) << (7-pos%8); } }
static void ue(uint32_t v)
{ unsigned n=0; uint32_t x=v+1; while (x>>++n) {} put(n-1,0); put(n,v+1); }
int main(void)
{
    uint8_t nal[1024]={0,0,0,1,0x42,1}; unsigned n=6,zeros=0,type=0,prefix=0;
    ProbeSPS s={0};
    put(4,0);put(3,0);put(1,1);put(8,2);put(32,0x60000000);
    put(16,0x9000);put(32,0);put(8,120);ue(0);ue(1);ue(768);ue(2752);
    put(1,1);ue(0);ue(96);ue(0);ue(0);ue(2);ue(2);put(1,1);
    for (unsigned i=0;i<(pos+7)/8;i++) {
        if (zeros==2 && bits[i]<=3) { nal[n++]=3;zeros=0; }
        nal[n++]=bits[i];zeros=bits[i]==0?zeros+1:0;
    }
    CHECK(probe_nal_type(nal,n,&type,&prefix) && type==33 && prefix==4);
    CHECK(probe_sps(nal,n,&s));
    CHECK(s.profile_idc==2 && s.compatibility==0x60000000 && s.level_idc==120);
    CHECK(s.constraints==UINT64_C(0x900000000000));
    CHECK(s.chroma==1 && s.luma_depth==10 && s.chroma_depth==10);
    CHECK(s.width==768 && s.height==2752 && s.visible_width==576 && s.visible_height==2752);
    CHECK(!probe_sps(nal,7,&s));
    CHECK(!probe_sps(nal,n-2,&s));
    nal[4]=0xc2; CHECK(!probe_nal_type(nal,n,&type,&prefix)); nal[4]=0x42;
    nal[5]=0; CHECK(!probe_sps(nal,n,&s)); nal[5]=1;
    nal[4]=0x26; CHECK(probe_nal_type(nal,n,&type,&prefix) && type==19);
    CHECK(!probe_sps(nal,n,&s));
    CHECK(probe_nal_type(nal+1,n-1,&type,&prefix) && prefix==3);
    CHECK(!probe_nal_type(nal+2,n-2,&type,&prefix));
    puts("HEVC probe parser contracts passed");
    return 0;
}
