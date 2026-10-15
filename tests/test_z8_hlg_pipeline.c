/* SPDX-License-Identifier: GPL-2.0-or-later */
#include <stdio.h>
#include <string.h>
#include "../qemu/nikon-z8-hlg-pipeline-pixels.h"
#define CHECK(v) do { if (!(v)) { fprintf(stderr,"line %d: %s\n",__LINE__,#v); return 1; } } while (0)
int main(void)
{
    uint32_t src[0x220/4], a[0x100/4], c[0x1000/4], b[3][0x100/4], f[3][0x100/4];
    memcpy(src,z8_hlg_c1_source,sizeof(src));memcpy(a,z8_hlg_c1_a1,sizeof(a));
    memcpy(c,z8_hlg_c1_control,sizeof(c));memcpy(b,z8_hlg_c1_output,sizeof(b));memcpy(f,z8_hlg_c1_geometry,sizeof(f));
    CHECK(z8_hlg_c1_profile(src,a,c,b,f));
    for(unsigned i=0;i<0x220/4;i++) {
        if(z8_hlg_c1_source_dynamic(i*4)) { continue; }
        src[i]^=1;CHECK(!z8_hlg_c1_profile(src,a,c,b,f));src[i]^=1;
    }
    for(unsigned i=0;i<0x100/4;i++) {
        if(i!=0x18/4) { a[i]^=1;CHECK(!z8_hlg_c1_profile(src,a,c,b,f));a[i]^=1; }
        for(unsigned j=0;j<3;j++) {
            if(!z8_hlg_c1_output_address(i*4)) {
                b[j][i]^=1;CHECK(!z8_hlg_c1_profile(src,a,c,b,f));b[j][i]^=1;
            }
            f[j][i]^=1;CHECK(!z8_hlg_c1_profile(src,a,c,b,f));f[j][i]^=1;
        }
    }
    for(unsigned i=0;i<0x1000/4;i++) {
        if(i==1 || i==4) { continue; }
        c[i]^=1;CHECK(!z8_hlg_c1_profile(src,a,c,b,f));c[i]^=1;
    }
    c[1]=0x101;CHECK(z8_hlg_c1_profile(src,a,c,b,f));
    c[1]=0x100;CHECK(!z8_hlg_c1_profile(src,a,c,b,f));
    c[1]=0x103;c[2]=0x302;CHECK(!z8_hlg_c1_profile(src,a,c,b,f)); /* video/ring tuple */
    c[2]=2;src[0x100/4]=0x12340000;src[0x104/4]=8;
    CHECK(z8_hlg_c1_profile(src,a,c,b,f)); /* addresses validated by DMA layer, not pinned */
    uint8_t iy[32]={0}, iu[32]={0}, oy[32]={0}, ou[16]={0};
    uint32_t small[0x100/4]={0};
    small[0x10/4]=small[0x14/4]=16384;small[0x18/4]=4;small[0x1c/4]=4;
    small[0x2c/4]=small[0x30/4]=4;
    for(unsigned y=0;y<4;y++) for(unsigned x=0;x<4;x++) {
        z8_hlg_store(iy+y*8,x,12,y*400+x*4+3);
        z8_hlg_store(iu+y*8,x,12,(x&1)?3000+y*8:100+y*8);
    }
    CHECK(z8_hlg_c1_scale(iy,iu,4,4,8,small,8,oy,ou));
    for(unsigned y=0;y<4;y++) for(unsigned x=0;x<4;x++) CHECK(z8_hlg_load(oy+y*8,x,10)==y*100+x+1);
    CHECK(z8_hlg_load(ou,0,10)==26 && z8_hlg_load(ou,1,10)==751);
    CHECK(z8_hlg_load(ou+8,0,10)==30 && z8_hlg_load(ou+8,1,10)==755);
    CHECK((oy[3]&0xc0)==0 && (oy[7]&0xc0)==0);
    small[0x20/4]=31;CHECK(!z8_hlg_c1_scale(iy,iu,4,4,8,small,8,oy,ou));
    small[0x20/4]=0;small[0x8/4]=16384;CHECK(!z8_hlg_c1_scale(iy,iu,4,4,8,small,8,oy,ou));
    small[0x8/4]=0;CHECK(!z8_hlg_c1_scale(iy,iu,4,4,5,small,8,oy,ou));
    puts("HLG C1 contracts passed; numerical/packing hypotheses only");return 0;
}
