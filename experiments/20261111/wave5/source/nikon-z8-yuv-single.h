/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Original40cb84cc ->40c27244, observed after recording EOS and YUV DMA.
 * Only observed A1 0x3801 NV12 -> B5/B3/B4 NV16 and A2 0x1001 NV16 -> B4
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

static bool z8_yuv_single_job(NikonZ9State *s,unsigned input,uint64_t request,bool execute)
{
    Z8YuvData *d=s->z8_yuv_data;
    if (!d || !d->single_enabled) { return false; }
    bool second=input==2;
    uint32_t *a=s->engine1542_config[input];
    unsigned width=a[0x4c/4],height=a[0x50/4];
    unsigned channels=second?1:3,count=2+2*channels;
    uint64_t address[8],length[8];unsigned widths[3],heights[3],strides[3];
    uint8_t *data[8]={NULL};bool ok=false;
    Z8JpegSource *video_parent=NULL;const char *video_skip=NULL;
    char parent_y[65]={0},parent_uv[65]={0};uint64_t video_generation[3]={0};
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
        /*40e85f98..5fe0 and40c26234/26288/262dc bind B3/B4/B5.
         * A1+8 routes3..6 (0x78), but +4 enables only3..5 (0x38).
         * Preserve processing order: half-size, full-size, sixth-size. */
        unsigned lane=second?4:ch==0?5:ch==1?3:4;
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
    if (!execute) { return true; }
    for (unsigned p=0;p<2;p++) {
        data[p]=g_malloc(length[p]);
        if (address_space_read(&address_space_memory,address[p],MEMTXATTRS_UNSPECIFIED,data[p],length[p])!=MEMTX_OK) { goto done; }
    }
    if (s->engine15e2_video_source_model) {
        if (d->single_jobs==UINT64_MAX) { goto done; }
        char *ys=g_compute_checksum_for_data(G_CHECKSUM_SHA256,data[0],length[0]);
        char *us=g_compute_checksum_for_data(G_CHECKSUM_SHA256,data[1],length[1]);
        g_strlcpy(parent_y,ys,65);g_strlcpy(parent_uv,us,65);g_free(ys);g_free(us);
        if (second) {
            video_parent=jpeg_source_tuple(s,Z8_JPEG_VIDEO_RESIZE,4,address[0],address[1],width,height,width);
            if (!video_parent || !jpeg_source_hash_valid(s,video_parent) ||
                strcmp(video_parent->y_sha,parent_y) || strcmp(video_parent->uv_sha,parent_uv)) {
                video_parent=NULL;video_skip="a2_parent_or_copied_hash";
            }
        } else if (!d->dma_last_epoch) { video_skip="missing_dma_epoch"; }
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
    if (s->engine15e2_video_source_model) {
        NBSpan outputs[6];
        for (unsigned i=2;i<count;i++) { outputs[i-2]=(NBSpan){address[i],length[i]}; }
        if (!jpeg_sources_write_begin(s,outputs,count-2)) {
            fprintf(stderr,"Z8_VIDEO_SOURCE_REJECT reason=output_lease_or_source_reader input=A%u\n",input);
            goto done;
        }
    }
    for (unsigned i=2;i<count;i++) {
        unsigned ch=(i-2)/2;
        for (unsigned r=0;r<heights[ch];r++) {
            if (address_space_write(&address_space_memory,address[i]+(uint64_t)r*strides[ch],MEMTXATTRS_UNSPECIFIED,
                data[i]+r*widths[ch],widths[ch])!=MEMTX_OK) { goto done; }
        }
    }
    s->z8_yuv_single_owner[input - 1] = true;
    d->single_jobs++;d->single_input_bytes+=length[0]+length[1];
    for (unsigned ch=0;ch<channels;ch++) { d->single_output_bytes+=(uint64_t)2*widths[ch]*heights[ch]; }
    if (s->engine15e2_video_source_model) {
        /* Preserve B4/B5 publication order, then publish full-size B3.
         * All siblings bind the actual A1 job/epoch/copied NV12 parent SHA;
         * B3 is an NV12->NV16 output, not an alias of an encoder input. */
        for (unsigned pub=0;pub<(second?1:3);pub++) {
            unsigned ch=second?0:pub==0?2:pub==1?0:1,j=2+2*ch,lane=second||pub==0?4:pub==1?5:3;
            const char *skip=video_skip;
            if (!skip) {
                Z8JpegSource *r=jpeg_source_reserve(s,second?Z8_JPEG_VIDEO_THUMB:Z8_JPEG_VIDEO_RESIZE,
                    lane,d->single_jobs,address[j],address[j+1],widths[ch],heights[ch],strides[ch],video_parent);
                if (r) {
                    r->source_kind=2;r->frame=second?video_parent->frame:d->dma_last_epoch;
                    g_strlcpy(r->parent_y_sha,parent_y,65);g_strlcpy(r->parent_uv_sha,parent_uv,65);
                    if (jpeg_source_publish_video(s,r,data[j],data[j+1])) { video_generation[ch]=r->generation; }
                    else { r->live=false;r->reserved=false;skip="publication_hash_or_parent"; }
                } else { skip=s->jpeg_sources.failure; }
            }
            if (skip) { fprintf(stderr,"Z8_VIDEO_SOURCE_SKIP input=A%u job=%"PRIu64" lane=%u reason=%s\n",input,d->single_jobs,lane,skip); }
        }
        for (unsigned lane_ch=0;lane_ch<channels;lane_ch++) {
            unsigned lane=second?4:lane_ch==0?5:lane_ch==1?3:4,k=2+2*lane_ch;
            fprintf(stderr,"Z8_VIDEO_SOURCE_OUTPUT input=A%u job=%"PRIu64" lane=%u y=%"PRIx64" uv=%"PRIx64
                " width=%u height=%u stride=%u generation=%"PRIu64"\n",input,d->single_jobs,lane,
                address[k],address[k+1],widths[lane_ch],heights[lane_ch],strides[lane_ch],video_generation[lane_ch]);
        }
    }
    a[1]=request;s->engine1542_pending[input]|=0x10;raw_pipeline_irq_update(s);ok=true;
    /* Original40cb7154 consumes A1 bit4 as the single-job wait flag1. */
    fprintf(stderr,"Z8_YUV_SINGLE job=%"PRIu64" input=A%u source=%"PRIx64":%"PRIx64" size=%ux%u pending=10; diagnostic scaling\n",
        d->single_jobs,input,address[0],address[1],width,height);
done:
    for (unsigned i=0;i<8;i++) { g_free(data[i]); }
    if (!execute) { return false; }
    if (!ok) { d->single_rejects++;unknown_access(s,0x15421004+input*0x100,request,4,true); }
    return true;
}

static bool z8_yuv_single_matches(NikonZ9State *s, unsigned input, uint64_t request)
{
    return z8_yuv_single_job(s, input, request, false);
}

static bool z8_yuv_single_start(NikonZ9State *s, unsigned input, uint64_t request)
{
    return z8_yuv_single_job(s, input, request, true);
}
