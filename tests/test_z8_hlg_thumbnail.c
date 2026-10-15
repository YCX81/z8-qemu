/* SPDX-License-Identifier: GPL-2.0-or-later */
#include <stdio.h>
#include <string.h>
#include "../qemu/nikon-z8-hlg-thumbnail-pixels.h"
#define CHECK(v) do { if (!(v)) { fprintf(stderr,"line %d: %s\n",__LINE__,#v); return 1; } } while (0)
int main(void)
{
    uint32_t a[64],b[64],f[64];
    memcpy(a,z8_hlg_thumb_a,sizeof(a));memcpy(b,z8_hlg_thumb_b,sizeof(b));memcpy(f,z8_hlg_thumb_f,sizeof(f));
    CHECK(z8_hlg_thumb_profile(a,b,f));
    Z8HLGThumbMismatch mismatch;
    b[0xc/4]=0x01000200;
    CHECK(!z8_hlg_thumb_profile_check(a,b,f,&mismatch));
    CHECK(mismatch.bank==1 && mismatch.offset==0xc && mismatch.actual==0x01000200 && mismatch.expected==0x41000010);
    b[0xc/4]=0x41000010;
    for(unsigned i=0;i<64;i++) {
        if(!z8_hlg_thumb_address(i*4)) {
            a[i]^=1;CHECK(!z8_hlg_thumb_profile(a,b,f));a[i]^=1;
            b[i]^=1;CHECK(!z8_hlg_thumb_profile(a,b,f));b[i]^=1;
        }
        f[i]^=1;CHECK(!z8_hlg_thumb_profile(a,b,f));f[i]^=1;
    }
    uint8_t iy[448*212]={0},iu[448*106]={0},oy[192*106],ou[192*106];
    for(unsigned y=0;y<212;y++) for(unsigned x=0;x<320;x++) z8_hlg_store(iy+y*448,x,10,(x+7*y)%1024);
    for(unsigned y=0;y<106;y++) for(unsigned x=0;x<320;x++) z8_hlg_store(iu+y*448,x,10,(x&1)?900-y-x:100+y+x);
    z8_hlg_thumb_convert(iy,iu,oy,ou);
    for(unsigned y=0;y<106;y++) for(unsigned x=0;x<160;x++) {
        unsigned v=((2*x+14*y)%1024+2)/4;
        CHECK(oy[y*192+x]==(v>255?255:v));
        unsigned sx=(x/2)*4+x%2;
        CHECK(ou[y*192+x]==((x&1)?(902-y-sx)/4:(102+y+sx)/4));
    }
    CHECK(oy[191]==0 && ou[191]==128);
    CHECK(z8_hlg_thumb_quantize(1023)==255 && z8_hlg_thumb_quantize(6)==2);
    puts("HLG thumbnail contracts passed; packing and quantization hypotheses");return 0;
}
