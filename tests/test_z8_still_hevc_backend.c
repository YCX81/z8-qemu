/* SPDX-License-Identifier: GPL-2.0-or-later */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../qemu/nikon-z8-still-hevc-pixels.h"
#define CHECK(x) do { if (!(x)) { fprintf(stderr,"line %d %s\n",__LINE__,#x);exit(1); } } while (0)
int main(void)
{
    CHECK(!strcmp(z8_hevc_vui_range(0x1d800,0x91209),"full"));
    CHECK(!z8_hevc_vui_range(0x15800,0x91209)); /* native full-range bit cleared */
    CHECK(!z8_hevc_vui_range(0x1d800,0x91009)); /* transfer changed */
    CHECK(!z8_hevc_vui_range(0xd800,0x91209)); /* colour description absent */
    uint8_t y[4*8]={0},uv[2*8]={0};uint16_t tile[12];
    for(unsigned row=0;row<4;row++)for(unsigned x=0;x<6;x++)z8_hlg_store(y+row*8,x,10,row*100+x);
    for(unsigned row=0;row<2;row++)for(unsigned x=0;x<6;x++)z8_hlg_store(uv+row*8,x,10,row*100+x+400);
    CHECK(z8_hevc_tile(y,uv,6,4,8,4,2,1,1,tile,12));
    const uint16_t want[12]={204,205,205,205,304,305,305,305,504,504,505,505};
    CHECK(!memcmp(tile,want,sizeof(want)));
    CHECK(!z8_hevc_tile(y,uv,6,4,7,4,2,1,1,tile,12));
    CHECK(!z8_hevc_tile(y,uv,6,4,8,4,2,1,1,tile,11));
    unsigned counts[4]={0};
    for(unsigned slot=0;slot<24;slot++) {
        unsigned source=z8_hevc_slot_source(slot);
        CHECK(source<22);CHECK(z8_hevc_slot(source)==slot || slot==11 || slot==23);
        counts[slot%4]++;
    }
    for(unsigned i=0;i<4;i++)CHECK(counts[i]==6);
    uint8_t nal[]={0,0,0,1,0x28,1,0x80,0x12,0x34},before[sizeof(nal)];
    memcpy(before,nal,sizeof(nal));CHECK(!z8_hevc_transform_single(nal,sizeof(nal),2));
    CHECK(!memcmp(before,nal,sizeof(nal)));CHECK(z8_hevc_transform_single(nal,sizeof(nal),1));
    CHECK(nal[4]==0x26);CHECK(!memcmp(nal,before,4));CHECK(!memcmp(nal+5,before+5,sizeof(nal)-5));
    CHECK(!z8_hevc_transform_single(nal,sizeof(nal),1));
    nal[4]=0x28;nal[5]=2;CHECK(!z8_hevc_transform_single(nal,sizeof(nal),1));nal[5]=1;
    CHECK(z8_hevc_transform_single(nal,sizeof(nal),1));
    uint8_t stream[64]={0},table[32]={0};Z8HEVCLane l={stream,table,64,32,0,0};
    CHECK(z8_hevc_lane_add(&l,19,nal,sizeof(nal)));CHECK(l.used==sizeof(nal) && l.count==1);
    CHECK(table[0]==19 && z8_hevc_le32(table+4)==sizeof(nal) && z8_hevc_le32(table+8)==0);
    CHECK(z8_hevc_lane_add(&l,19,nal,sizeof(nal)));CHECK(z8_hevc_le32(table+24)==sizeof(nal));
    CHECK(!z8_hevc_lane_add(&l,19,nal,sizeof(nal)));CHECK(l.count==2);
    l.count=0;l.used=60;CHECK(!z8_hevc_lane_add(&l,19,nal,sizeof(nal)));CHECK(l.used==60);
    l.used=0;CHECK(!z8_hevc_lane_add(&l,20,nal,sizeof(nal)));
    CHECK(z8_hevc_kib_limit(4096,4));CHECK(!z8_hevc_kib_limit(4097,4));
    CHECK(!z8_hevc_kib_limit(1,0));CHECK(!z8_hevc_kib_limit(1,0x1000000));
    CHECK(z8_hevc_completion_mask(4)==0x60000);
    CHECK(z8_hevc_completion_mask(1)==0x20000);
    CHECK(z8_hevc_completion_mask(2)==0);
    const unsigned shapes[][3]={{320,212,448},{1620,1080,2176}};
    for(unsigned n=0;n<2;n++) {
        unsigned w=shapes[n][0],h=shapes[n][1],stride=shapes[n][2];
        uint8_t *py=calloc(h,stride),*puv=calloc(h/2,stride);
        uint16_t *decoded=malloc((size_t)w*h*3);
        CHECK(py && puv && decoded);
        for(unsigned r=0;r<h;r++)for(unsigned x=0;x<w;x++)z8_hlg_store(py+(size_t)r*stride,x,10,(r*3+x)&1023);
        for(unsigned r=0;r<h/2;r++)for(unsigned x=0;x<w;x++)z8_hlg_store(puv+(size_t)r*stride,x,10,(r*7+x)&1023);
        CHECK(z8_hevc_tile(py,puv,w,h,stride,w,h,0,0,decoded,(size_t)w*h*3/2));
        for(unsigned r=0;r<h;r++)for(unsigned x=0;x<w;x++)CHECK(decoded[(size_t)r*w+x]==((r*3+x)&1023));
        for(unsigned r=0;r<h/2;r++)for(unsigned x=0;x<w/2;x++) {
            CHECK(decoded[(size_t)w*h+(size_t)r*w/2+x]==((r*7+x*2)&1023));
            CHECK(decoded[(size_t)w*h*5/4+(size_t)r*w/2+x]==((r*7+x*2+1)&1023));
        }
        CHECK(!z8_hevc_tile(py,puv,w,h,stride,w,h,1,0,decoded,(size_t)w*h*3/2));
        free(py);free(puv);free(decoded);
    }
    puts("Still HEVC backend helper contracts passed");return 0;
}
