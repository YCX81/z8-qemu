/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Optional Z8 YUV aperture endpoint. Decode actual WLYV packet RAM for an
 * owned generation; never substitute the host source or a preview image. */
static int z8_wave5_yuv_snapshot(WlWave5State *w,WlWave5Instance *i,WlWave5Job *job)
{
    NikonZ9State *s=w->board;uint32_t *r=w->regs;Z8YuvData *d=s->z8_yuv_data;
    if (!s->z8_yuv_data_model || w->fio_words[9]!=8 || r[0x148/4]>=0x40000000) { return -1; }
    if (!d) { return 0; }
    d->read_stage=1;
    unsigned format=r[0x158/4],endian=(format>>6)&15;
    if (i->depth!=8 || (format&~0x3ffu) || (format&31)!=2 || (endian!=0 && endian!=15) ||
        (r[0x154/4]>>16)!=i->width || (r[0x154/4]&0xffff)!=i->width) { goto invalid; }
    uint64_t logical[2]={UINT64_C(0x200000000)|r[0x148/4],UINT64_C(0x200000000)|r[0x14c/4]};
    uint64_t physical[2]={0,0};
    unsigned bases[2]={0,0};
    /* Producer and consumer have separate virtual aliases. Original Z8
     * maps consumer200000000 to the producer20c000000 backing reservation.
     * Join them by both actual physical planes, never by the alias alone. */
    const uint32_t *v=yuvcmp_domain_words(s, 1);
    for (unsigned p=0;p<2;p++) {
        for (unsigned bank=0;bank<2;bank++) { for (unsigned n=0;n<4;n++) {
            unsigned base=0x1200+p*0x400+bank*0x100,off=(base+0x80+n*8)/4;
            if ((v[off]|(uint64_t)v[off+1]<<32)==logical[p]) { physical[p]=v[off+8]|(uint64_t)v[off+9]<<32;bases[p]=base; }
        } }
    }
    Z8YuvSlot *slot=NULL;
    d->read_stage=2;
    for (unsigned n=0;n<G_N_ELEMENTS(d->slots);n++) { if (d->slots[n].owned && d->slots[n].physical[0]==physical[0] && d->slots[n].physical[1]==physical[1]) { slot=&d->slots[n];break; } }
    if (!slot || slot->width!=i->width || slot->height!=i->height) { goto invalid; }
    uint8_t *planes[2]={NULL,NULL};bool ok=false;
    for (unsigned p=0;p<2;p++) {
        d->read_stage=3+p*10;
        unsigned ch=0x1000+p*0x400,base=bases[p];
        if (v[ch/4]!=1 || v[(ch+4)/4]!=1 || v[(base+0x1c)/4]!=i->width ||
            v[(base+0x20)/4]!=slot->stride || v[(base+0x28)/4]<slot->capacity[p]) { goto done; }
        bool matched=false;
        d->read_stage=4+p*10;
        for (unsigned n=0;n<4;n++) {
            unsigned off=(base+0x80+n*8)/4;
            uint64_t a=v[off]|(uint64_t)v[off+1]<<32,b=v[off+8]|(uint64_t)v[off+9]<<32;
            if (a==logical[p] && b==slot->physical[p]) { matched=true; }
        }
        if (!matched || graphics_spans_overlap(slot->physical[p],slot->capacity[p],i->work_physical,i->work_capacity) ||
            graphics_spans_overlap(slot->physical[p],slot->capacity[p],job->output,job->capacity)) {
            fprintf(stderr,"Z8_YUV_MAP_REJECT matched=%d plane=%u logical=%"PRIx64" physical=%"PRIx64" capacity=%u work=%"PRIx64":%u output=%"PRIx64":%u\n",matched,p,logical[p],slot->physical[p],slot->capacity[p],i->work_physical,i->work_capacity,job->output,job->capacity);goto done;
        }
        uint8_t h[48];
        d->read_stage=5+p*10;
        if (address_space_read(&address_space_memory,slot->physical[p],MEMTXATTRS_UNSPECIFIED,h,sizeof(h))!=MEMTX_OK) { goto done; }
        unsigned n=slot->stride*(slot->height>>p),amount=ldl_le_p(h+36);
        if (z8_packet_kind(h, sizeof(h), true) != Z8_PACKET_WLYV || ldl_le_p(h+8)!=slot->width || ldl_le_p(h+12)!=(slot->height>>p) ||
            ldl_le_p(h+16)!=slot->stride || ldl_le_p(h+20)!=p || ldq_le_p(h+24)!=slot->epoch ||
            ldl_le_p(h+32)!=n || ldl_le_p(h+44) || !amount || amount>slot->capacity[p]-48) { goto done; }
        g_autofree uint8_t *packet=g_malloc(amount);planes[p]=g_malloc(n);uLongf length=n;
        d->read_stage=6+p*10;
        MemTxResult read_result=address_space_read(&address_space_memory,slot->physical[p]+48,MEMTXATTRS_UNSPECIFIED,packet,amount);
        int decoded=read_result==MEMTX_OK?uncompress(planes[p],&length,packet,amount):Z_DATA_ERROR;
        uint32_t actual_crc=crc32(0,planes[p],n),expected_crc=ldl_le_p(h+40);
        if (decoded!=Z_OK || length!=n || actual_crc!=expected_crc) {
            fprintf(stderr,"Z8_YUV_PACKET_REJECT plane=%u physical=%"PRIx64" read=%d decoded=%d length=%lu expected=%u crc=%x expected_crc=%x\n",p,slot->physical[p],read_result,decoded,(unsigned long)length,n,actual_crc,expected_crc);goto done;
        }
    }
    unsigned ybytes=i->width*i->height;
    job->input_bytes=ybytes*3/2;job->input=g_malloc(job->input_bytes);memcpy(job->input,planes[0],ybytes);
    for (unsigned n=0;n<ybytes/4;n++) { job->input[ybytes+n]=planes[1][n*2];job->input[ybytes+ybytes/4+n]=planes[1][n*2+1]; }
    job->strides[0]=i->width;job->strides[1]=job->strides[2]=i->width/2;
    job->input_crc=crc32(0,job->input,job->input_bytes);slot->owned=false;d->reads++;d->read_stage=0;ok=true;
done:
    g_free(planes[0]);g_free(planes[1]);if (ok) { return 1; }
invalid:
    d->rejects++;return 0;
}
