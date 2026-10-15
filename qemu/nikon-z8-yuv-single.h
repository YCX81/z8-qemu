/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Original40cb84cc ->40c27244, observed after recording EOS and YUV DMA.
 * Only observed A1 0x3801 NV12 -> B2/B3/B4 NV16 and A2 0x1001 NV16 -> B4
 * admitted. Nearest-neighbour scaling is diagnostic, not Nikon filtering.
 * Synchronous snapshot/commit: no completion before all six RAM writes. */
static bool get_z8_yuv_single_model(Object *obj,Error **errp)
{
    Z8YuvData *d=NIKON_Z9_LAB(obj)->z8_yuv_data;return d && d->single_enabled;
}
static void set_z8_yuv_single_model(Object *obj,bool value,Error **errp)
{
    NikonZ9State *s=NIKON_Z9_LAB(obj);
    if (!object_dynamic_cast(obj,MACHINE_TYPE_NAME("nikon-z8-bringup")) || s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp,"z8-yuv-single-model requires Z8 at startup");return;
    }
    if (!s->z8_yuv_data) { s->z8_yuv_data=g_new0(Z8YuvData,1); }
    s->z8_yuv_data->single_enabled=value;
}

static bool z8_yuv_single_start(NikonZ9State *s,unsigned input,uint64_t request)
{
    Z8YuvData *d=s->z8_yuv_data;
    if (!d || !d->single_enabled) { return false; }
    bool second=input==2;
    uint32_t *a=s->engine1542_config[input];
    unsigned width=a[0x4c/4],height=a[0x50/4];
    unsigned channels=second?1:3,count=2+2*channels;
    uint64_t address[8],length[8];unsigned widths[3],heights[3],strides[3];
    uint8_t *data[8]={NULL};bool ok=false;
    /* Exact format/control values from protocol-a1-observe01. Unrelated
     * A1 modes, streaming/ring owners and non-integer ratios stay strict. */
    if (request!=(second?0x1001:0x3801) || (a[1]&~3u)!=(second?0x1000:0x3800) ||
        a[2]!=(second?0x10000000:0x78000000) || a[3]!=(second?0x71000010:0x31000000) || a[4]!=1 ||
        !width || !height || width>4096 || height>2160 || width%(second?8:12) || height%(second?4:6) ||
        a[0x38/4]!=width || a[0x3c/4]!=width || s->engine1542_pending[input] ||
        s->engine1543_active[0] || s->engine1543_active[1] || s->engine1543_active[2]) { goto done; }
    address[0]=a[8]|(uint64_t)a[9]<<32;address[1]=a[10]|(uint64_t)a[11]<<32;
    length[0]=(uint64_t)width*height;length[1]=length[0]/(second?1:2);
    for (unsigned ch=0;ch<channels;ch++) {
        unsigned lane=second?4:2+ch;
        const uint32_t *b=s->engine1542_config[3+lane],*f=s->engine1542_config[15+lane];
        unsigned factor=second?4:ch==0?2:ch==1?1:6,w=f[0x2c/4],h=f[0x30/4],pitch=b[0x38/4];
        if (b[3]!=0x41000010 || b[4]!=1 || !w || !h || w*factor!=width || h*factor!=height ||
            pitch<w || pitch>8192 || (pitch&1) || b[0x3c/4]!=pitch ||
            f[0x18/4]!=width || f[0x1c/4]!=height) { goto done; }
        widths[ch]=w;heights[ch]=h;strides[ch]=pitch;
        for (unsigned p=0;p<2;p++) {
            unsigned j=2+2*ch+p;
            address[j]=b[8+2*p]|(uint64_t)b[9+2*p]<<32;length[j]=(uint64_t)pitch*h;
        }
    }
    for (unsigned i=0;i<count;i++) {
        if ((address[i]&15) || !dma_ram_range(s,address[i],length[i])) { goto done; }
        for (unsigned j=0;j<i;j++) {
            if (graphics_spans_overlap(address[i],length[i],address[j],length[j])) { goto done; }
        }
    }
    for (unsigned p=0;p<2;p++) {
        data[p]=g_malloc(length[p]);
        if (address_space_read(&address_space_memory,address[p],MEMTXATTRS_UNSPECIFIED,data[p],length[p])!=MEMTX_OK) { goto done; }
    }
    for (unsigned ch=0;ch<channels;ch++) {
        unsigned w=widths[ch],h=heights[ch],factor=width/w;
        for (unsigned p=0;p<2;p++) {
            unsigned j=2+2*ch+p;data[j]=g_malloc((size_t)w*h);
            for (unsigned r=0;r<h;r++) { for (unsigned x=0;x<w;x++) {
                unsigned sy=p&&!second?r*factor/2:r*factor,sx=p?(x/2)*factor*2+(x&1):x*factor;
                data[j][r*w+x]=data[p][sy*width+sx];
            } }
        }
    }
    for (unsigned i=2;i<count;i++) {
        unsigned ch=(i-2)/2;
        for (unsigned r=0;r<heights[ch];r++) {
            if (address_space_write(&address_space_memory,address[i]+(uint64_t)r*strides[ch],MEMTXATTRS_UNSPECIFIED,
                data[i]+r*widths[ch],widths[ch])!=MEMTX_OK) { goto done; }
        }
    }
    d->single_jobs++;d->single_input_bytes+=length[0]+length[1];
    for (unsigned ch=0;ch<channels;ch++) { d->single_output_bytes+=(uint64_t)2*widths[ch]*heights[ch]; }
    a[1]=request;s->engine1542_pending[input]|=0x10;raw_pipeline_irq_update(s);ok=true;
    /* Original40cb7154 consumes A1 bit4 as the single-job wait flag1. */
    fprintf(stderr,"Z8_YUV_SINGLE job=%"PRIu64" input=A%u source=%"PRIx64":%"PRIx64" size=%ux%u pending=10; diagnostic scaling\n",
        d->single_jobs,input,address[0],address[1],width,height);
done:
    for (unsigned i=0;i<8;i++) { g_free(data[i]); }
    if (!ok) { d->single_rejects++;unknown_access(s,0x15421004+input*0x100,request,4,true); }
    return true;
}
