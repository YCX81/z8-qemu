/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Observed Z8 mode0 channel4/5 rectangle read from its YUV aperture.
 * Actual WLYV RAM is decoded before any destination write. This does not
 * emulate Nikon YUV compression or introduce a generic mode0 DMA fallback. */
static bool z8_yuv_dma(NikonZ9State *s,unsigned ch,uint64_t request,hwaddr reg)
{
    uint32_t *r=s->dma[ch];Z8YuvData *d=s->z8_yuv_data;
    uint64_t src=r[0x30/4]|(uint64_t)r[0x34/4]<<32;
    if (!s->z8_yuv_dma_model || !d || ch<4 || r[0x14/4]!=0 ||
        src<UINT64_C(0x200000000) || src>=UINT64_C(0x240000000)) { return false; }
    if (r[2]) { d->dma_rejects++;unknown_access(s,reg,request,4,true);return true; }
    unsigned stage=1;
    r[0]=r[2]=0;dma_irq_update(s,ch);
    uint64_t dst=r[0x38/4]|(uint64_t)r[0x3c/4]<<32;
    /* Original40ca6298/62c8 uses descriptor halfword0/1 to scale the
     * source/destination row offsets. 40c19024/34/4c writes source pitch,
     * destination pitch, then copy width. Linear copies hid this distinction. */
    unsigned width=r[0x24/4]?r[0x24/4]:32768,rows=r[0x28/4]?r[0x28/4]:4096;
    unsigned ss=r[0x1c/4],ds=r[0x20/4];
    uint64_t span=(uint64_t)(rows-1)*ss+width,target=(uint64_t)(rows-1)*ds+width;
    if (request!=1 || !width || width>32768 || rows>4096 || ss<width || ds<width ||
        ((width|ss|ds|src|dst)&3) || (uint64_t)width*rows>64*MiB ||
        !dma_ram_range(s,dst,target)) { goto invalid; }
    const uint32_t *v=s->z8_video1566_config;
    stage=2;
    unsigned matches=0,plane=0,capacity=0,raw_bytes=0,frame_width=0;uint64_t physical=0,offset=0;
    for (unsigned p=0;p<2;p++) {
        unsigned channel=0x1000+p*0x400;
        if (v[channel/4]!=1 || v[(channel+4)/4]!=1) { continue; }
        for (unsigned bank=0;bank<2;bank++) {
            unsigned base=channel+0x200+bank*0x100,n=v[(base+0x18)/4];
            if (!n || n>64*MiB || v[(base+0x20)/4]!=ss) { continue; }
            for (unsigned row=0;row<4;row++) {
                unsigned at=(base+0x80+row*8)/4;
                uint64_t alias=v[at]|(uint64_t)v[at+1]<<32;
                if (!(v[(base+0x40)/4]&(1u<<row)) || src<alias || src-alias>=n || span>n-(src-alias)) { continue; }
                matches++;physical=v[at+8]|(uint64_t)v[at+9]<<32;offset=src-alias;
                plane=p;capacity=v[(base+0x28)/4];raw_bytes=n;frame_width=v[(base+0x1c)/4];
            }
        }
    }
    if (matches!=1 || capacity<48 || capacity>32*MiB || !dma_ram_range(s,physical,capacity) ||
        graphics_spans_overlap(physical,capacity,dst,target)) { goto invalid; }
    /* DMA is a read of the currently mapped RAM, including retained frames
     * after encoder consumption or reset. It does not acquire an encoder
     * job from that bounded ownership journal. Validate self-described bytes. */
    stage=3;
    if (!frame_width || frame_width>4096 || ss<frame_width || raw_bytes%ss || raw_bytes/ss>2160) { goto invalid; }
    uint8_t h[48];
    stage=4;
    if (address_space_read(&address_space_memory,physical,MEMTXATTRS_UNSPECIFIED,h,sizeof(h))!=MEMTX_OK ||
        memcmp(h,"WLYV0001",8) || ldl_le_p(h+8)!=frame_width || ldl_le_p(h+12)!=raw_bytes/ss ||
        ldl_le_p(h+16)!=ss || ldl_le_p(h+20)!=plane || !ldq_le_p(h+24) ||
        ldl_le_p(h+32)!=raw_bytes || ldl_le_p(h+44)) { goto invalid; }
    unsigned amount=ldl_le_p(h+36);uint32_t expected_crc=ldl_le_p(h+40);
    if (!amount || amount>capacity-48) { goto invalid; }
    unsigned total=48+amount;uint8_t *packet=g_malloc(total),*pixels;
    stage=5;
    memcpy(packet,h,48);
    if (address_space_read(&address_space_memory,physical+48,MEMTXATTRS_UNSPECIFIED,packet+48,amount)!=MEMTX_OK) { g_free(packet);goto invalid; }
    if (d->dma_cache_pixels[plane] && d->dma_cache_bytes[plane]==total && !memcmp(d->dma_cache_packet[plane],packet,total)) {
        pixels=d->dma_cache_pixels[plane];g_free(packet);d->dma_cache_hits++;
    } else {
        pixels=g_malloc(raw_bytes);uLongf length=raw_bytes;
        if (uncompress(pixels,&length,packet+48,amount)!=Z_OK || length!=raw_bytes || (uint32_t)crc32(0,pixels,raw_bytes)!=expected_crc) {
            g_free(packet);g_free(pixels);goto invalid;
        }
        g_free(d->dma_cache_packet[plane]);g_free(d->dma_cache_pixels[plane]);
        d->dma_cache_packet[plane]=packet;d->dma_cache_pixels[plane]=pixels;d->dma_cache_bytes[plane]=total;
    }
    for (unsigned row=0;row<rows;row++) {
        stage=6;
        if (address_space_write(&address_space_memory,dst+(uint64_t)row*ds,MEMTXATTRS_UNSPECIFIED,
                pixels+offset+(uint64_t)row*ss,width)!=MEMTX_OK) { goto invalid; }
    }
    d->dma_jobs++;d->dma_bytes+=(uint64_t)width*rows;d->dma_last_epoch=ldq_le_p(h+24);
    r[0x10/4]=2;r[2]|=1;dma_irq_update(s,ch);
    fprintf(stderr,"Z8_YUV_DMA job=%"PRIu64" epoch=%"PRIu64" plane=%u source=%"PRIx64" packet=%"PRIx64" destination=%"PRIx64" width=%u rows=%u bytes=%"PRIu64"\n",
        d->dma_jobs,d->dma_last_epoch,plane,src,physical,dst,width,rows,(uint64_t)width*rows);
    return true;
invalid:
    fprintf(stderr,"Z8_YUV_DMA_REJECT stage=%u ch=%u source=%"PRIx64" destination=%"PRIx64" width=%u rows=%u strides=%u:%u\n",stage,ch,src,dst,width,rows,ss,ds);
    d->dma_rejects++;unknown_access(s,reg,request,4,true);return true;
}
