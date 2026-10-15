/* SPDX-License-Identifier: GPL-2.0-or-later */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../qemu/nikon-sensor-tag.h"
#define CHECK(v) do { if(!(v)) { fprintf(stderr,"line %d\n",__LINE__);exit(1); } } while(0)
int main(void)
{
    uint8_t first[256*64]={0},second[sizeof(first)]={0};
    CHECK(nikon_sensor_tag_visible_copy(first,sizeof(first),128,64,256,12,1));
    CHECK(nikon_sensor_tag_visible_copy(second,sizeof(second),128,64,256,12,2));
    CHECK(memcmp(first,second,sizeof(first))!=0);
    for(unsigned row=0;row<64;row++)for(unsigned x=0;x<256;x++) {
        if(row<32 || row>=48 || x<96 || x>=192) { CHECK(first[row*256+x]==0 && second[row*256+x]==0); }
    }
    CHECK(!nikon_sensor_tag_visible_copy(first,sizeof(first),127,64,256,12,1));
    CHECK(!nikon_sensor_tag_visible_copy(first,sizeof(first),128,47,256,12,1));
    CHECK(nikon_sensor_tag_visible_copy(first,256*48,128,48,256,14,2));
    CHECK(!nikon_sensor_tag_visible_copy(first,sizeof(first),128,64,256,12,0));
    puts("Visible external sensor tag contracts passed");return 0;
}
