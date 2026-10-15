/* SPDX-License-Identifier: GPL-2.0-or-later */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <glib.h>
#include "../qemu/nikon-z8-still-hevc-pixels.h"
#include "../qemu/nikon-z8-still-hevc-codec.h"
#define CHECK(x) do { if (!(x)) { fprintf(stderr,"line %d %s\n",__LINE__,#x);exit(1); } } while (0)
int main(void)
{
    uint8_t image[]={0,0,0,1,0x28,1,0x80};Z8HEVCEncoded e={0};
    x265_nal bad={19,sizeof(image),image};CHECK(!z8_hevc_collect(&e,&bad,1));
    x265_nal duplicate[2]={{20,sizeof(image),image},{20,sizeof(image),image}};
    CHECK(!z8_hevc_collect(&e,duplicate,2));z8_hevc_encoded_free(&e);
    image[4]=0x40;duplicate[0].type=duplicate[1].type=32;
    CHECK(!z8_hevc_collect(&e,duplicate,2));z8_hevc_encoded_free(&e);
    const x265_api *api=x265_api_get(10);
    CHECK(api && api->bit_depth==10 && (size_t)api->sizeof_param==sizeof(x265_param) && (size_t)api->sizeof_picture==sizeof(x265_picture));
    const unsigned shapes[][2]={{768,2752},{1620,1080},{320,212}};
    for(unsigned n=0;n<3;n++) {
    unsigned width=shapes[n][0],height=shapes[n][1];
    size_t plane=(size_t)width*height;uint16_t *pixels=g_new(uint16_t,plane*3/2);Z8HEVCTileReceipt receipt={0};
    for(size_t i=0;i<plane*3/2;i++) { pixels[i]=(i*7+i/width*11)&1023; }
    const char *error=n?z8_hevc_encode_image(api,pixels,width,height,0,0x1d800,0x91209,&e,&receipt):z8_hevc_encode_tile(api,pixels,0,0x1d800,0x91209,&e,&receipt);
    if(error) { fprintf(stderr,"codec reason: %s\n",error); }
    CHECK(!error && e.vcl==1 && e.image[4]==0x26 && e.image[5]==1);
    CHECK(e.sps.luma_depth==10 && e.sps.chroma==1 && (e.sps.profile_idc==2 || e.sps.profile_idc==4));
    CHECK(strcmp(receipt.before_sha256,receipt.after_sha256) && receipt.bytes==e.image_bytes);
    CHECK(e.sps.visible_width==width && e.sps.visible_height==height);
    printf("Real x265 still codec contracts passed profile=%u raw_type=20 emitted_type=19 bytes=%zu\n",e.sps.profile_idc,e.image_bytes);
    z8_hevc_encoded_free(&e);g_free(pixels);
    }
    return 0;
}
