/* SPDX-License-Identifier: GPL-2.0-or-later
 * Exact first native main-image request, run06, including allocation addresses.
 * first-fault-state SHA256: 274db49ac0a56223bc5fb2fd9ce1ebe6da7dd3e507f92bbc49b785cd99a854ac
 * capture-trace SHA256: 1db2cdae6c7f77747b74790d3fe5a1af9e98fae92bcb0fa5bff7fa28035fd515
 * run07 adds an exact preview profile. Small-image candidate below is static;
 * other sizes, QPs, allocations and retry profiles are NOT accepted matches.
 * Included by nikon-z8-still-hevc.h after its protocol helpers.
 */
#ifndef NIKON_Z8_STILL_HEVC_PROFILE_H
#define NIKON_Z8_STILL_HEVC_PROFILE_H
#define Z8_HEVC_RUN06_SHA256 "274db49ac0a56223bc5fb2fd9ce1ebe6da7dd3e507f92bbc49b785cd99a854ac"
static const Z8HEVCWrite z8_hevc_run06[] = {
    {0x080,0x000003db},{0x128,0x01060055},{0x200,0x00000005},{0x204,0x00000014},
    {0x300,0x0000050a},{0x310,0x15802040},{0x318,0x00000278},{0x370,0x00000480},
    {0x390,0x0000080d},{0x394,0x00000004},{0x3f0,0x00000000},{0x400,0x0001d800},
    {0x404,0x00000000},{0x408,0x00091209},{0x40c,0x00000000},{0x410,0x00000000},
    {0x414,0x00000000},{0x704,0x0000000c},{0x740,0x00000000},{0x744,0x00000000},
    {0x748,0x00000000},{0x74c,0x00000000},{0x780,0x00000000},{0x7a0,0x00000000},
    {0xe40,0x8fac85e0},{0xe48,0x8fbc85e0},{0xe50,0x8fcc85e0},{0xe58,0x8fdc85e0},
    {0xe60,0x01000000},{0xe64,0x00000000},{0xe90,0xa15591b0},{0xe98,0xa15592b0},
    {0xea0,0xa15593b0},{0xea8,0xa15594b0},{0xeb0,0x00001000},{0xeb8,0x00000000},
    {0xf00,0xb5c76600},{0xf04,0x00000008},{0xf08,0xb963e600},{0xf0c,0x00000008},
    {0xf10,0x00002b00},{0xf20,0x0ac00300},{0xf44,0x00000008},{0xf50,0x0000ff90},
    {0xf54,0x00000aa6},{0xfe8,0x60000770},{0xff0,0x80f000f0}
};
#define Z8_HEVC_RUN07_SHA256 "341ab2ba7d0ba91ec8d92464fc33eeea0825cb6f86d9bdcea32d6752723d58d4"
/* Exact second request MMIO. The first-fault R and S driver copies corroborate
 * this tuple; per-PC observations for this request still await run08. */
static const Z8HEVCWrite z8_hevc_run07[] = {
    {0x080,0x5b},{0x128,0x01020001},{0x200,1},{0x204,0x14},
    {0x300,0x50a},{0x310,0x04380654},{0x318,0x278},{0x370,0x480},
    {0x390,0x80d},{0x394,4},{0x3f0,0},{0x400,0x1d800},
    {0x404,0},{0x408,0x91209},{0x40c,0},{0x410,0},{0x414,0},
    {0x704,12},{0x740,0},{0x744,0},{0x748,0},{0x74c,0},{0x780,0},{0x7a0,0},
    {0xe40,0x8fec85e0},{0xe60,0x200000},{0xe64,0},{0xe90,0xa15591b0},
    {0xeb0,0x1000},{0xeb8,0},{0xf00,0xd8c8f000},{0xf04,8},
    {0xf08,0xd8eccc00},{0xf0c,8},{0xf10,2176},{0xf20,0},
    {0xf44,8},{0xf50,0x7fc},{0xf54,0},{0xfe8,0x20000770},{0xff0,0x80f000f0}
};
/* Finite 320x212 candidate, never native_observed. Pool table a029d0418:
 * small offset0/cap2MiB vs preview offset0x4000000/cap2MiB. Source addresses
 * are C1 output1 from run07; 40c98b08..14 gives capacity/1024-4. NORM initial
 * QP12 follows a029d1a00[1] via 40eb7944/40eb7bb0. No quality retry allowed. */
static inline uint32_t z8_hevc_aux_value(unsigned off,uint32_t value,bool small)
{
    if(!small) { return value; }
    switch(off) {
    case 0x310:return 0x00d40140;
    case 0x318:return 0x23c;
    case 0xe40:return 0x8fac85e0;
    case 0xf00:return 0xd8c6c200;
    case 0xf08:return 0xd8c83500;
    case 0xf10:return 448;
    default:return value;
    }
}
static inline bool z8_hevc_match_profile(const Z8StillHEVC *h,const Z8HEVCWrite *profile,unsigned count,bool small)
{
    uint64_t expected[16]={0};
    if (!h->enabled || !h->initialized || !h->prepared) { return false; }
    for (unsigned i=0;i<count;i++) {
        unsigned off=profile[i].off,bit=off/4;
        expected[bit/64]|=UINT64_C(1)<<(bit%64);
        if (z8_hevc_reg(h,off)!=z8_hevc_aux_value(off,profile[i].value,small)) { return false; }
    }
    return memcmp(expected,h->seen,sizeof(expected))==0;
}
static inline int z8_hevc_profile_index(const Z8StillHEVC *h)
{
    if(z8_hevc_match_profile(h,z8_hevc_run06,sizeof(z8_hevc_run06)/sizeof(z8_hevc_run06[0]),false)) { return 0; }
    if(z8_hevc_match_profile(h,z8_hevc_run07,sizeof(z8_hevc_run07)/sizeof(z8_hevc_run07[0]),false)) { return 2; }
    if(z8_hevc_match_profile(h,z8_hevc_run07,sizeof(z8_hevc_run07)/sizeof(z8_hevc_run07[0]),true)) { return 1; }
    return -1;
}
static inline bool z8_hevc_native_profile(const Z8StillHEVC *h)
{ int index=z8_hevc_profile_index(h);return index==0 || index==2; }
#endif
