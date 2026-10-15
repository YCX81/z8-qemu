/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Included after engine151_capture_sample. No guest task/CPU writes.
 * 151 {1,3}/45c=2 is the earlier 12-bit 4:2:2 surface; {2,1}/45c=5 is
 * packed10 4:2:0. Both are static profile candidates, not live calibration.
 * Simplified RGGB + reference HLG OETF + BT.2020 NCL full-range numerical
 * fixture. Real Nikon matrices, LUTs, range and packing remain unverified. */
static bool z8_hlg_span(NikonZ9State *s, uint64_t pa, uint64_t bytes)
{
    return bytes && bytes <= 128 * MiB && !(pa & 31) &&
        pa >= UINT64_C(0x800000000) && pa <= UINT64_C(0x900000000) - bytes &&
        dma_ram_range(s, pa, bytes);
}

static uint64_t z8_hlg_address(const uint32_t *c, unsigned off)
{ return c[off/4] | ((uint64_t)c[(off+4)/4] << 32); }

static bool z8_hlg_reject(NikonZ9State *s, const char *reason)
{
    s->hlg_capture.rejects++;
    s->hlg_capture.last_error=reason;
    return false;
}

static bool z8_hlg_hash_ram(NikonZ9State *s, uint64_t pa, uint64_t bytes,
                             char out[65])
{
    uint8_t buffer[16384];
    GChecksum *sum=g_checksum_new(G_CHECKSUM_SHA256);
    if (!sum) { return false; }
    for (uint64_t at=0; at<bytes;) {
        size_t n=MIN(bytes-at,sizeof(buffer));
        if (!dma_ram_range(s,pa+at,n) || address_space_read(&address_space_memory,
            pa+at,MEMTXATTRS_UNSPECIFIED,buffer,n)!=MEMTX_OK) {
            g_checksum_free(sum); return false;
        }
        g_checksum_update(sum,buffer,n);at+=n;
    }
    g_strlcpy(out,g_checksum_get_string(sum),65);g_checksum_free(sum);return true;
}

static bool z8_hlg_capture_pixels(NikonZ9State *s, unsigned i)
{
    Z8HLGCapture *h=&s->hlg_capture;
    const uint32_t *c=s->engine151_config[i];
    unsigned bits=s->primary_d980_packed14_model && s->primary_d980_cache_format==1 ? 14 : 12;
    Z8HLGPlan p;
    if (!h->enabled) { return z8_hlg_reject(s,"disabled"); }
    if (i || !z8_hlg_plan(c,bits,&p)) { return z8_hlg_reject(s,"profile_or_geometry"); }
    uint64_t source=z8_hlg_address(c,0x900), y=z8_hlg_address(c,0x980), uv=z8_hlg_address(c,0x9c0);
    if (s->engine151_pending[i] || !s->primary_d980_frames ||
        s->engine151_capture_consumed_frame==s->primary_d980_frames ||
        source!=s->primary_d980_last_physical || p.iw!=s->primary_d980_cache_width ||
        p.ih!=s->primary_d980_cache_height || p.pitch!=s->primary_d980_cache_stride) {
        return z8_hlg_reject(s,"producer_identity_or_pending");
    }
    NBSpan spans[2]={{y,p.plane_y},{uv,p.plane_uv}}, in={source,p.input_bytes};
    if (!z8_hlg_span(s,source,p.input_bytes) || !z8_hlg_span(s,y,p.plane_y) ||
        !z8_hlg_span(s,uv,p.plane_uv) || nb_overlap(spans[0],spans[1]) ||
        nb_overlap(in,spans[0]) || nb_overlap(in,spans[1]) ||
        s->engine151_capture_jobs==UINT64_MAX || h->jobs==UINT64_MAX) {
        return z8_hlg_reject(s,"dma_span_or_overlap");
    }
    /* Entire source is immutable for this synchronous job. All output spans
     * are checked before either write. The helpers never read preview RAM. */
    g_autofree uint8_t *input=g_try_malloc(p.input_bytes);
    g_autofree uint8_t *yp=g_try_malloc0(p.plane_y);
    g_autofree uint8_t *up=g_try_malloc0(p.plane_uv);
    if (!input || !yp || !up) { return z8_hlg_reject(s,"allocation"); }
    if (address_space_read(&address_space_memory,source,MEMTXATTRS_UNSPECIFIED,input,p.input_bytes)!=MEMTX_OK) {
        return z8_hlg_reject(s,"source_read");
    }
    unsigned white=(1u<<bits)-1, maximum=(1u<<p.output_bits)-1;
    uint16_t tone[16384];
    /* ITU-R BT.2100 reference OETF, normalized E in [0,1]. This does NOT
     * recover Nikon scene scaling, sensor colourimetry or Picture Control. */
    const double a=0.17883277, b=1-4*a, cc=0.5-a*log(4*a);
    for (unsigned v=0;v<=white;v++) {
        double e=v/(double)white;
        double t=e<=1.0/12 ? sqrt(3*e) : a*log(12*e-b)+cc;
        tone[v]=CLAMP(lrint(t*maximum),0,maximum);
    }
    uint64_t greens[2]={0,0};
    for (unsigned row=0;row<p.oh;row+=2) {
        const uint8_t *r0=input+(uint64_t)(p.cy+row)*p.pitch, *r1=r0+p.pitch;
        for (unsigned x=0;x<p.ow;x+=2) {
            unsigned sx=p.cx+x;
            unsigned g0=engine151_capture_sample(r0,sx+1,bits), g1=engine151_capture_sample(r1,sx,bits);
            greens[0]+=g0;greens[1]+=g1;
            double r=tone[engine151_capture_sample(r0,sx,bits)], g=tone[(g0+g1+1)/2];
            double blue=tone[engine151_capture_sample(r1,sx+1,bits)];
            double luma=0.2627*r+0.6780*g+0.0593*blue;
            unsigned yy=CLAMP(lrint(luma),0,maximum);
            unsigned u=CLAMP(lrint((blue-luma)/1.8814+(1u<<(p.output_bits-1))),0,maximum);
            unsigned v=CLAMP(lrint((r-luma)/1.4746+(1u<<(p.output_bits-1))),0,maximum);
            for (unsigned dy=0;dy<2;dy++) {
                uint8_t *target=yp+(uint64_t)(row+dy)*p.stride;
                z8_hlg_store(target,x,p.output_bits,yy); z8_hlg_store(target,x+1,p.output_bits,yy);
            }
            unsigned first=p.output_bits==10 ? row/2 : row, rows=p.output_bits==10 ? 1 : 2;
            for (unsigned dy=0;dy<rows;dy++) {
                uint8_t *target=up+(uint64_t)(first+dy)*p.stride;
                z8_hlg_store(target,x,p.output_bits,u);z8_hlg_store(target,x+1,p.output_bits,v);
            }
        }
    }
    uint64_t job=s->engine151_capture_jobs+1;
    if (!nb_reserve(&s->output_owner,0,job,s->primary_d980_frames,spans,2)) {
        return z8_hlg_reject(s,"ownership");
    }
    /* The existing generation journal only names SDR NV16. Do not publish a
     * false NV16 record for HLG; explicitly invalidate coverage at this DMA.
     * This header emits its own RAM hashes and shared ownership remains live. */
    if (s->producer_journal.enabled) {
        nj_unknown(&s->producer_journal,qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL));
        s->producer_journal.gap=true;
    }
    bool ok=address_space_write(&address_space_memory,y,MEMTXATTRS_UNSPECIFIED,yp,p.plane_y)==MEMTX_OK &&
            address_space_write(&address_space_memory,uv,MEMTXATTRS_UNSPECIFIED,up,p.plane_uv)==MEMTX_OK;
    char yh[65]={0}, uh[65]={0};
    if (!ok || !z8_hlg_hash_ram(s,y,p.plane_y,yh) || !z8_hlg_hash_ram(s,uv,p.plane_uv,uh)) {
        nb_cancel(&s->output_owner,0);return z8_hlg_reject(s,"partial_dma_or_readback");
    }
    if (!nb_complete(&s->output_owner,0,job)) {
        nb_cancel(&s->output_owner,0);return z8_hlg_reject(s,"ownership_complete");
    }
    g_autofree char *ih=g_compute_checksum_for_data(G_CHECKSUM_SHA256,input,p.input_bytes);
    g_strlcpy(h->source_sha256,ih,sizeof(h->source_sha256));
    g_strlcpy(h->y_sha256,yh,sizeof(h->y_sha256));g_strlcpy(h->uv_sha256,uh,sizeof(h->uv_sha256));
    h->jobs++;h->source_frame=s->primary_d980_frames;h->source=source;h->source_bytes=p.input_bytes;
    h->y=y;h->uv=uv;h->plan=p;h->last_output_hlg=true;h->last_error=NULL;
    s->engine151_capture_jobs=job;s->engine151_capture_bytes+=p.plane_y+p.plane_uv;
    s->engine151_capture_consumed_frame=s->primary_d980_frames;
    s->engine151_capture_source=source;s->engine151_capture_y=y;s->engine151_capture_uv=uv;
    s->engine151_capture_width=p.ow;s->engine151_capture_height=p.oh;s->engine151_capture_stride=p.stride;
    s->engine151_green_samples=(uint64_t)(p.ow/2)*(p.oh/2);
    for(unsigned k=0;k<2;k++) {
        s->engine151_green_means[k]=(greens[k]+s->engine151_green_samples/2)/s->engine151_green_samples;
    }
    s->engine151_average_valid=true;
    s->engine151_capture_pending=true;s->engine151_pending[i]|=2;
    /* Do not notify the SDR NV16 consumer: it cannot unpack these surfaces. */
    fprintf(stderr,"Z8_HLG151 job=%" PRIu64 " frame=%" PRIu64 " source=%" PRIx64
        " geometry=%ux%u stride=%u bits=%u y=%" PRIx64 " uv=%" PRIx64
        " y_sha256=%s uv_sha256=%s bit_order_verified=false\n",
        job,h->source_frame,source,p.ow,p.oh,p.stride,p.output_bits,y,uv,yh,uh);
    return true;
}

static bool get_z8_hlg_model(Object *obj, Error **errp)
{ return NIKON_Z9_LAB(obj)->hlg_capture.enabled; }
static void set_z8_hlg_model(Object *obj, bool value, Error **errp)
{
    NikonZ9State *s=NIKON_Z9_LAB(obj);
    if (s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp,"z8-capture-hlg-pixels-model is immutable after realization");return;
    }
    s->hlg_capture.enabled=value;
}
static char *get_z8_hlg_state(Object *obj, Error **errp)
{
    NikonZ9State *s=NIKON_Z9_LAB(obj);Z8HLGCapture *h=&s->hlg_capture;
    /* Native run02/03 verified this configuration, not the numerical ISP or
     * bit order. Smaller QTest tuples and the packed10 candidate stay false. */
    bool native=h->jobs && h->plan.output_bits==12 && h->plan.iw==8280 && h->plan.ih==5520 &&
        h->plan.ow==8256 && h->plan.oh==5504 && h->plan.cx==12 && h->plan.cy==8 &&
        h->plan.pitch==12448 && h->plan.stride==12384;
    return g_strdup_printf("{\"enabled\":%s,\"jobs\":%" PRIu64 ",\"rejects\":%" PRIu64
        ",\"source_frame\":%" PRIu64 ",\"source\":%" PRIu64 ",\"source_bytes\":%" PRIu64
        ",\"y\":%" PRIu64 ",\"uv\":%" PRIu64 ",\"y_bytes\":%" PRIu64 ",\"uv_bytes\":%" PRIu64
        ",\"width\":%u,\"height\":%u,\"stride\":%u,\"output_bits\":%u,\"uv_height\":%u,"
        "\"source_sha256\":\"%s\",\"y_sha256\":\"%s\",\"uv_sha256\":\"%s\","
        "\"pending\":%s,\"last_error\":\"%s\",\"bit_order_verified\":false,"
        "\"native_profile_observed\":%s,\"profile_evidence\":\"heif-hlg-20261014-02/03\",\"physical_algorithm_verified\":false,"
        "\"scope\":\"151 actual RAM; packed12-422 or candidate packed10-420; RGGB/reference HLG/full-range BT2020 numerical hypothesis; downstream has a separate opt-in contract\"}",
        h->enabled?"true":"false",h->jobs,h->rejects,h->source_frame,h->source,h->source_bytes,
        h->y,h->uv,h->plan.plane_y,h->plan.plane_uv,h->plan.ow,h->plan.oh,h->plan.stride,h->plan.output_bits,h->plan.uv_height,
        h->source_sha256,h->y_sha256,h->uv_sha256,
        h->last_output_hlg&&s->engine151_capture_pending?"true":"false",h->last_error?h->last_error:"",native?"true":"false");
}
