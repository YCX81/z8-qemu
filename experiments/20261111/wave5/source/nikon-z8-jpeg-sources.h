/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Opt-in multi-generation NV16 provenance. No pixels are archived here.
 * Source identities are created by actual producer admission/publication,
 * never by a JPEG bank number, latest sensor frame, or address-only waiter. */
static bool jpeg_source_is_video(const Z8JpegSource *r)
{
    return r && (r->kind==Z8_JPEG_VIDEO_RESIZE || r->kind==Z8_JPEG_VIDEO_THUMB);
}

static uint64_t jpeg_pair(const uint32_t *p, unsigned off)
{ return p[off/4] | (uint64_t)p[off/4+1] << 32; }

static bool jpeg_ram_hash(NikonZ9State *s, uint64_t pa, uint64_t bytes, char out[65])
{
    if (!bytes || bytes > 64*MiB || !dma_ram_range(s,pa,bytes)) { return false; }
    GChecksum *sum=g_checksum_new(G_CHECKSUM_SHA256);
    uint8_t chunk[65536];
    for (uint64_t at=0;at<bytes;) {
        size_t n=MIN(bytes-at,sizeof(chunk));
        if (address_space_read(&address_space_memory,pa+at,MEMTXATTRS_UNSPECIFIED,chunk,n)!=MEMTX_OK) {
            g_checksum_free(sum);return false;
        }
        g_checksum_update(sum,chunk,n);at+=n;
    }
    g_strlcpy(out,g_checksum_get_string(sum),65);g_checksum_free(sum);return true;
}

static Z8JpegSource *jpeg_source_get(NikonZ9State *s, uint64_t generation)
{
    if (!generation) { return NULL; }
    for (unsigned i=0;i<Z8_JPEG_SOURCES;i++) {
        if (s->jpeg_sources.entries[i].generation==generation) { return &s->jpeg_sources.entries[i]; }
    }
    return NULL;
}

static bool jpeg_source_hash_valid(NikonZ9State *s, const Z8JpegSource *r)
{
    char y[65],uv[65];uint64_t n=(uint64_t)r->stride*r->height;
    return r->live && r->published && jpeg_ram_hash(s,r->y,n,y) && jpeg_ram_hash(s,r->uv,n,uv) &&
        !strcmp(y,r->y_sha) && !strcmp(uv,r->uv_sha);
}

static bool jpeg_source_overlap(const Z8JpegSource *r, NBSpan span)
{
    uint64_t n=(uint64_t)r->height*r->stride;
    return nb_overlap(span,(NBSpan){r->y,n}) || nb_overlap(span,(NBSpan){r->uv,n});
}

/* Call before a modeled DMA, not after overwriting a pinned generation. */
static bool jpeg_sources_write_begin(NikonZ9State *s, const NBSpan *spans, unsigned count)
{
    if (!s->engine15e2_bank1_model) { return true; }
    for (unsigned k=0;k<count;k++) {
        if (!nb_writable(&s->output_owner,spans[k])) { return false; }
        for (unsigned i=0;i<Z8_JPEG_SOURCES;i++) {
            Z8JpegSource *r=&s->jpeg_sources.entries[i];
            if (r->readers && r->published && jpeg_source_overlap(r,spans[k])) { return false; }
        }
    }
    for (unsigned i=0;i<Z8_JPEG_SOURCES;i++) {
        Z8JpegSource *r=&s->jpeg_sources.entries[i];
        if (!r->published || !r->live) { continue; }
        for (unsigned k=0;k<count;k++) { if (jpeg_source_overlap(r,spans[k])) { r->live=false; } }
    }
    return true;
}

static Z8JpegSource *jpeg_source_reserve(NikonZ9State *s, unsigned kind, unsigned index,
    uint64_t job, uint64_t y, uint64_t uv, unsigned w, unsigned h, unsigned stride,
    const Z8JpegSource *parent)
{
    if (!job || !w || (w&1) || !h || stride<w || (uint64_t)stride*h>64*MiB ||
        s->jpeg_sources.serial==UINT64_MAX) { s->jpeg_sources.failure="source_identity_or_counter";return NULL; }
    for (unsigned pass=0;pass<2;pass++) {
    for (unsigned i=0;i<Z8_JPEG_SOURCES;i++) {
        Z8JpegSource *r=&s->jpeg_sources.entries[i];
        bool committed=false;
        for (unsigned j=0;j<Z8_JPEG_ROLES;j++) { committed |= r->roles[j].committed; }
        if (r==parent || r->readers || r->reserved || (r->live && (!pass || !committed))) { continue; }
        Z8JpegSource copy=parent?*parent:(Z8JpegSource){0};
        memset(r,0,sizeof(*r));r->generation=++s->jpeg_sources.serial;
        r->kind=kind;r->index=index;r->producer_job=job;r->y=y;r->uv=uv;
        r->width=w;r->height=h;r->stride=stride;r->reserved=true;r->live=true;
        if (parent) {
            r->parent_generation=copy.generation;r->parent_151_job=copy.parent_151_job;
            r->frame=copy.frame;r->source_kind=copy.source_kind;r->decoder_job=copy.decoder_job;
            r->isp_record_job=copy.isp_record_job;r->captured_ns=copy.captured_ns;
            g_strlcpy(r->parent_y_sha,copy.y_sha,65);g_strlcpy(r->parent_uv_sha,copy.uv_sha,65);
        }
        return r;
    }
    }
    s->jpeg_sources.failure="source_table_full";return NULL;
}

static bool jpeg_source_publish(NikonZ9State *s, Z8JpegSource *r)
{
    if (!r || !r->reserved || !r->live || !r->frame || !r->parent_151_job ||
        !jpeg_ram_hash(s,r->y,(uint64_t)r->stride*r->height,r->y_sha) ||
        !jpeg_ram_hash(s,r->uv,(uint64_t)r->stride*r->height,r->uv_sha)) { return false; }
    r->published=true;r->reserved=false;return true;
}

/* Called from the existing successful151 notification, including the callback
 * which may already service a prepared resize. Preserve every live generation. */
static void jpeg_sources_refresh151(NikonZ9State *s)
{
    if (s->hlg_capture.last_output_hlg) { return; }
    if (!s->engine15e2_bank1_model || !s->engine151_capture_jobs ||
        s->jpeg_sources.last151==s->engine151_capture_jobs) { return; }
    uint64_t frame=s->engine151_capture_source_kind ? s->engine151_capture_hsfc_frame : s->engine151_capture_consumed_frame;
    Z8ISPRecord *parent=NULL;
    if (s->engine151_capture_source_kind) {
        for (unsigned i=0;i<Z8_HSFC_RECORDS;i++) {
            Z8ISPRecord *r=&s->z8_hsfc151_records[i];
            if (r->preview_valid && r->decoder_job==s->engine151_capture_decoder_job && r->frame_id==frame &&
                r->y==s->engine151_capture_y && r->uv==s->engine151_capture_uv) {
                if (parent) { goto failed; } parent=r;
            }
        }
        if (!parent) { goto failed; }
    }
    /*151 already passed nb_reserve before its DMA. Invalidate overlapping
     * unpinned old publications here, before creating the new receipt. */
    for (unsigned i=0;i<Z8_JPEG_SOURCES;i++) {
        Z8JpegSource *r=&s->jpeg_sources.entries[i];
        NBSpan y={s->engine151_capture_y,(uint64_t)s->engine151_capture_stride*s->engine151_capture_height};
        NBSpan uv={s->engine151_capture_uv,y.bytes};
        if (r->published && (jpeg_source_overlap(r,y)||jpeg_source_overlap(r,uv))) { r->live=false; }
    }
    Z8JpegSource *r=jpeg_source_reserve(s,Z8_JPEG_151,0,s->engine151_capture_jobs,
        s->engine151_capture_y,s->engine151_capture_uv,s->engine151_capture_width,
        s->engine151_capture_height,s->engine151_capture_stride,NULL);
    if (!r) { goto failed; }
    r->frame=frame;r->parent_151_job=s->engine151_capture_jobs;
    r->source_kind=s->engine151_capture_source_kind;r->decoder_job=s->engine151_capture_decoder_job;
    if (parent) { r->isp_record_job=parent->job;r->captured_ns=parent->captured_ns; }
    if (!jpeg_source_publish(s,r) || (parent &&
        (strcmp(r->y_sha,parent->y_sha256)||strcmp(r->uv_sha,parent->uv_sha256)))) {
        r->live=false;r->reserved=false;goto failed;
    }
    s->jpeg_sources.last151=s->engine151_capture_jobs;return;
failed:
    s->jpeg_sources.failure="151_publication_identity";
    unknown_access(s,0x15100008,1,4,true);
}

static Z8JpegSource *jpeg_source_tuple(NikonZ9State *s, unsigned kind, int index,
    uint64_t y,uint64_t uv,unsigned w,unsigned h,unsigned stride)
{
    Z8JpegSource *found=NULL;
    for (unsigned i=0;i<Z8_JPEG_SOURCES;i++) {
        Z8JpegSource *r=&s->jpeg_sources.entries[i];
        if (r->live && r->published && r->kind==kind && (index<0 || r->index==(unsigned)index) &&
            r->y==y && r->uv==uv && r->width==w && r->height==h && r->stride==stride) {
            if (found) { return NULL; } found=r;
        }
    }
    return found;
}

/* Video has no151/decoder ancestor. Its copied NV12 input SHA and observed
 * YUV DMA epoch are evidence, not a proof of complete DMA coverage. A2 must
 * inherit an actual A1 generation, never the latest unqualified YUV job. */
static bool jpeg_source_publish_video(NikonZ9State *s, Z8JpegSource *r,
    const uint8_t *copied_y, const uint8_t *copied_uv)
{
    if (!s->engine15e2_video_source_model || !jpeg_source_is_video(r) ||
        !r->reserved || !r->live || !r->frame || !r->producer_job ||
        (r->index!=4 && !(r->kind==Z8_JPEG_VIDEO_RESIZE && (r->index==3 || r->index==5))) ||
        r->source_kind!=2 || r->parent_151_job || r->decoder_job || r->isp_record_job ||
        !r->parent_y_sha[0] || !r->parent_uv_sha[0]) { return false; }
    if (r->kind==Z8_JPEG_VIDEO_THUMB) {
        Z8JpegSource *p=jpeg_source_get(s,r->parent_generation);
        if (!p || p->kind!=Z8_JPEG_VIDEO_RESIZE || p->index!=4 || !jpeg_source_hash_valid(s,p) ||
            p->frame!=r->frame || strcmp(p->y_sha,r->parent_y_sha) ||
            strcmp(p->uv_sha,r->parent_uv_sha)) { return false; }
    } else if (r->parent_generation) { return false; }
    /* Check every byte actually copied, including when the destination pitch
     * leaves padding untouched. Full-stride SHA below also binds that padding. */
    uint8_t *row=g_malloc(r->width);bool ok=true;
    for (unsigned p=0;p<2 && ok;p++) {
        for (unsigned y=0;y<r->height && ok;y++) {
            ok=address_space_read(&address_space_memory,(p?r->uv:r->y)+(uint64_t)y*r->stride,
                MEMTXATTRS_UNSPECIFIED,row,r->width)==MEMTX_OK &&
                !memcmp(row,(p?copied_uv:copied_y)+(size_t)y*r->width,r->width);
        }
    }
    g_free(row);
    if (!ok || !jpeg_ram_hash(s,r->y,(uint64_t)r->stride*r->height,r->y_sha) ||
        !jpeg_ram_hash(s,r->uv,(uint64_t)r->stride*r->height,r->uv_sha)) { return false; }
    r->published=true;r->reserved=false;return true;
}

static void jpeg_sources_cancel_tokens(NikonZ9State *s,uint64_t *tokens,unsigned count)
{
    for (unsigned i=0;i<count;i++) {
        Z8JpegSource *r=jpeg_source_get(s,tokens[i]);
        if (r && r->reserved) { r->reserved=false;r->live=false; }
        tokens[i]=0;
    }
}

/* A prepared resize with a known parent owns explicit child intent tokens.
 * If the parent has not published, no address-only JPEG waiter is created. */
static bool jpeg_sources_nv16_intents(NikonZ9State *s)
{
    if (!s->engine15e2_bank1_model) { return true; }
    jpeg_sources_refresh151(s);
    const uint32_t *src=s->capture_nv16_source;
    Z8JpegSource *parent=jpeg_source_tuple(s,Z8_JPEG_151,0,jpeg_pair(src,0x100),jpeg_pair(src,0x108),
        src[0x80/4]&0x7fff,src[0x80/4]>>16,src[0x88/4]);
    if (!parent || parent->producer_job<=s->capture_nv16_consumed151) { return false; }
    if (!jpeg_source_hash_valid(s,parent)) { return false; }
    if (s->jpeg_sources.nv16_tokens[0]) {
        Z8JpegSource *r=jpeg_source_get(s,s->jpeg_sources.nv16_tokens[0]);
        return r && r->live && r->reserved && r->parent_generation==parent->generation;
    }
    for (unsigned j=0;j<3;j++) {
        const uint32_t *b=s->capture_nv16_output[j],*f=s->capture_nv16_geometry[j];
        Z8JpegSource *r=jpeg_source_reserve(s,Z8_JPEG_NV16,j,s->capture_nv16_jobs+1,
            jpeg_pair(b,0x20),jpeg_pair(b,0x28),f[0x2c/4],f[0x30/4],b[0x38/4],parent);
        if (!r) { jpeg_sources_cancel_tokens(s,s->jpeg_sources.nv16_tokens,3);return false; }
        s->jpeg_sources.nv16_tokens[j]=r->generation;
    }
    return true;
}
static bool jpeg_source_border(NikonZ9State *s,const Z8JpegSource *r,const uint32_t *c)
{
    /* Photo/HLG preflight and video-source permission stay independent. */
    if (!(((r->kind==Z8_JPEG_THUMB || r->kind==Z8_JPEG_HLG_THUMB) && s->engine15e2_preflight_model) ||
          (r->kind==Z8_JPEG_VIDEO_THUMB && s->engine15e2_video_source_model))) { return false; }
    unsigned h=c[0x41c/4],w=c[0x418/4],stride=c[0x410/4];
    if (w!=r->width || stride!=r->stride || h<r->height || h-r->height>32 || ((h-r->height)&1)) { return false; }
    uint64_t head=(uint64_t)(h-r->height)/2*stride;
    uint64_t y=(uint64_t)c[0x430/4]<<4,uv=(uint64_t)c[0x434/4]<<4;
    if (r->y<head || r->uv<head || y!=r->y-head || uv!=r->uv-head) { return false; }
    if (!r->published) { return h==r->height; }
    uint8_t *row=g_malloc(stride);bool ok=true;unsigned pad=(h-r->height)/2;
    for (unsigned p=0;p<2 && ok;p++) {
        for (unsigned at=0;at<h && ok;at++) {
            if (at>=pad && at<pad+r->height) { continue; }
            ok=address_space_read(&address_space_memory,(p?uv:y)+(uint64_t)at*stride,MEMTXATTRS_UNSPECIFIED,row,stride)==MEMTX_OK;
            for (unsigned x=0;x<w && ok;x++) { ok=row[x]==(p?128:0); }
        }
    }
    g_free(row);return ok;
}

static bool jpeg_source_matches(NikonZ9State *s,const Z8JpegSource *r,const uint32_t *c)
{
    if (r->kind==Z8_JPEG_HLG_THUMB &&
        (!s->hlg_thumbnail.enabled || !s->hlg_thumbnail.valid || s->hlg_thumbnail.failed ||
         r->producer_job!=s->hlg_thumbnail.jobs || r->frame!=s->primary_d980_frames ||
         r->frame!=s->hlg_thumbnail.source_frame ||
         s->hlg_thumbnail.source_job!=s->hlg_pipeline.jobs ||
         r->parent_151_job!=s->hlg_pipeline.consumed151_job)) { return false; }
    return (!jpeg_source_is_video(r) || s->engine15e2_video_source_model) &&
        r->live && (r->reserved || r->published) && c[0x414/4]==c[0x410/4] &&
        ((r->y==((uint64_t)c[0x430/4]<<4) && r->uv==((uint64_t)c[0x434/4]<<4) &&
          r->width==c[0x418/4] && r->height==c[0x41c/4] && r->stride==c[0x410/4]) || jpeg_source_border(s,r,c));
}

#include "nikon-z8-hlg-jpeg-bank1.h"

static Z8JpegSource *jpeg_source_find_request(NikonZ9State *s,const uint32_t *c,const char **reason)
{
    Z8JpegSource *found=NULL;
    for (unsigned i=0;i<Z8_JPEG_SOURCES;i++) {
        Z8JpegSource *r=&s->jpeg_sources.entries[i];
        if (!jpeg_source_matches(s,r,c)) { continue; }
        if (found) { *reason="ambiguous_source";return NULL; } found=r;
    }
    if (!found) { *reason="no_source_or_intent"; }
    return found;
}

static int jpeg_source_owner(const Z8JpegSource *r)
{ return jpeg_source_is_video(r) ? -2 : !r || r->kind==Z8_JPEG_151 ? -1 : r->kind==Z8_JPEG_THUMB ? 3 : r->kind==Z8_JPEG_HLG_THUMB ? 4 : (int)r->index; }

/* Count/commit role identity excludes output address/capacity and bank.
 * Count may repeat; a commit consumes the latest actual count cycle. Without
 * a new count, another bank cannot replay the same producer/quantizer role. */
static void jpeg_role_signature(const uint32_t *c,char out[65])
{
    GChecksum *sum=g_checksum_new(G_CHECKSUM_SHA256);
    for (unsigned i=0;i<0x500/4;i++) {
        if (!((i>=0x100/4 && i<0x180/4) || i==0x18/4 || i==0x1c/4 ||
              i==0x410/4 || i==0x418/4 || i==0x41c/4 || i==0x400/4)) { continue; }
        uint32_t word=i==0x400/4 ? c[i]&0x30 : c[i];
        uint8_t bytes[4];stl_le_p(bytes,word);g_checksum_update(sum,bytes,4);
    }
    g_strlcpy(out,g_checksum_get_string(sum),65);g_checksum_free(sum);
}
static int jpeg_role_index(const Z8JpegSource *r,const char *signature)
{
    int free_slot=-1;
    for (unsigned i=0;i<Z8_JPEG_ROLES;i++) {
        if (r->roles[i].used && !strcmp(r->roles[i].signature,signature)) { return i; }
        if (!r->roles[i].used && free_slot<0) { free_slot=i; }
    }
    return free_slot;
}

static const uint32_t *jpeg_request_words(NikonZ9State *s,unsigned b)
{
    return s->engine15e2_bank1_model && s->engine15e2_jpeg_requested[b] && s->jpeg_request[b].snapshot ?
        s->jpeg_request[b].words : s->engine15e2_config[b];
}

static Z8JpegSource *jpeg_request_source(NikonZ9State *s,unsigned b)
{
    if (s->engine15e2_jpeg_requested[b] && s->jpeg_request[b].snapshot) {
        return jpeg_source_get(s,s->jpeg_request[b].generation);
    }
    const char *reason=NULL;
    Z8JpegSource *r=jpeg_source_find_request(s,s->engine15e2_config[b],&reason);
    return r && b && r->kind==Z8_JPEG_HLG_THUMB && z8_hlg_bank1_profile(s,r,s->engine15e2_config[b]) ? NULL : r;
}

/* Video measurement is a separate bounded role; no photo opt-ins implied. */
static bool jpeg_request_video_measure(NikonZ9State *s,unsigned b)
{
    if (!s->engine15e2_video_source_model || !s->engine15e2_bank1_model) { return false; }
    const uint32_t *c=jpeg_request_words(s,b);
    Z8JpegSource *r=jpeg_request_source(s,b);
    unsigned scale=c[0x400/4]==0x20?2:0;
    /* Observed full-size B3 is type5: original40eb75d0/75b4..75cc starts and
     * retries finite passes. It does not authorize a B3 measurement profile. */
    return jpeg_source_is_video(r) && !(r->kind==Z8_JPEG_VIDEO_RESIZE && r->index==3) && r->published &&
        c[0x404/4]==2 && c[0x458/4]==0 && (c[0x400/4]==0 || c[0x400/4]==0x20) &&
        c[0x1c/4]==(c[0x418/4]>>scale) && c[0x18/4]==(c[0x41c/4]>>scale);
}

/* Same retired video rate-control intent. Native STARTs 48862/48929 and
 * callback 40eb7454..749c/780c prove only quantizers may change on retry.
 * Keep target, capacity, masks and every other configuration word bound.
 * Hardware command/status/result registers do not identify a new request. */
static void jpeg_video_commit_signature(const uint32_t *c,char out[65])
{
    GChecksum *sum=g_checksum_new(G_CHECKSUM_SHA256);
    for (unsigned off=0;off<0x500;off+=4) {
        if (off>=0x100 && off<0x180) { continue; }
        if (off==4 || off==0x20 || off==0x2c || off==0x470 || off==0x474 || off==0x480 || off==0x488) { continue; }
        uint8_t bytes[4];stl_le_p(bytes,c[off/4]);g_checksum_update(sum,bytes,4);
    }
    g_strlcpy(out,g_checksum_get_string(sum),65);g_checksum_free(sum);
}

static bool jpeg_video_reencode(NikonZ9State *s,unsigned b,const Z8JpegSource *r,const uint32_t *c,const char **reason)
{
    if (!s->engine15e2_video_source_model || !jpeg_source_is_video(r) || !r->video_commits ||
        c[0x400/4] || c[0x404/4] || !c[0x458/4]) { *reason="video_retry_mode";return false; }
    if (r->video_commit_bank!=b) { *reason="video_retry_bank";return false; }
    const NBSlot *slot=&s->output_owner.slots[b?NB_SLOT_JPEG1:NB_SLOT_JPEG0];
    if (!r->video_commit_retired || slot->held || !slot->complete ||
        slot->job!=r->video_commit_token || slot->frame!=r->frame) { *reason="video_retry_not_retired";return false; }
    /* 40c9c3e8 supplies measurement+commit count to 40eb77d4. Once a
     * finite pass completes at ordinal30 (or31 after30 measurements), done. */
    if (r->video_passes>=Z8_JPEG_VIDEO_RATE_PASSES) { *reason="video_retry_limit";return false; }
    char signature[65];jpeg_video_commit_signature(c,signature);
    if (strcmp(signature,r->video_commit_signature)) {
        fprintf(stderr,"Z9_15E2_VIDEO_INTENT bank=%u generation=%" PRIu64 " expected=%s actual=%s\n",
            b,r->generation,r->video_commit_signature,signature);
        *reason="video_retry_intent_changed";return false;
    }
    return true;
}

static bool jpeg_bank_bind(NikonZ9State *s,unsigned b)
{
    const uint32_t *c=s->engine15e2_config[b];const char *reason=NULL;
    Z8JpegSource *r=jpeg_source_find_request(s,c,&reason);
    Z8JpegRequest *q=&s->jpeg_request[b];
    if (!r) { q->failure=reason;return false; }
    if (b && r->kind==Z8_JPEG_HLG_THUMB) {
        const char *hlg_reason=z8_hlg_bank1_profile(s,r,c);
        if (hlg_reason) { q->failure=hlg_reason;return false; }
    }
    if (!r->published && !s->engine15e2_deferred_input_model) { q->failure="source_not_published";return false; }
    if (r->published && !jpeg_source_hash_valid(s,r)) { q->failure="source_overwritten";return false; }
    bool no_limit=c[0x458/4]==0 && c[0x404/4]==2;
    bool video=jpeg_source_is_video(r);
    bool count=video?jpeg_request_video_measure(s,b):no_limit && s->engine15e2_preflight_model;
    if (video && (!r->published || !s->engine15e2_video_source_model)) { q->failure="video_source_unpublished";return false; }
    if (video && no_limit && !count) { q->failure="video_measure_profile";return false; }
    bool video_retry=false;
    if (video) {
        /* At measurement30 the callback selects one final finite pass;
         * there is no31st measurement and no extra retry after that commit. */
        if ((count && r->video_passes>=Z8_JPEG_VIDEO_RATE_PASSES) ||
            (!r->video_commits && r->video_passes>Z8_JPEG_VIDEO_RATE_PASSES)) {
            q->failure="video_pass_limit";return false;
        }
        bool committed=false;
        for (unsigned i=0;i<Z8_JPEG_ROLES;i++) {
            if (r->roles[i].claim) { q->failure="video_generation_consumed_or_claimed";return false; }
            committed |= r->roles[i].committed;
        }
        if (committed) {
            if (!jpeg_video_reencode(s,b,r,c,&reason)) { q->failure=reason;return false; }
            video_retry=true;
        }
    }
    if (no_limit && !r->published) { q->failure="unsupported_deferred_role";return false; }
    char signature[65];
    /* A retired video retry is the same finite role, not a ninth quantizer
     * role. Measurements/photos retain their original full-quantizer roles
     * and the eight-slot bound. Actual quantizers stay in the request snapshot. */
    if (video && !count) { jpeg_video_commit_signature(c,signature); }
    else { jpeg_role_signature(c,signature); }
    int slot=jpeg_role_index(r,signature);
    if (slot<0) { q->failure="role_table_full";return false; }
    Z8JpegRole role=r->roles[slot];
    if (role.claim || (!video_retry && !count && role.committed && role.counts==role.committed_count)) {
        q->failure="source_role_consumed_or_claimed";return false;
    }
    if (s->engine15e2_jpeg_requests[b]==UINT64_MAX || s->engine15e2_jpeg_jobs[b]==UINT64_MAX || role.counts==UINT64_MAX ||
        (!r->published && s->engine15e2_deferred_waits[b]==UINT64_MAX)) {
        q->failure="counter_overflow";return false;
    }
    uint64_t token=s->engine15e2_jpeg_requests[b]+1;
    NBOwner trial=s->output_owner;
    NBSpan output={(uint64_t)c[0x440/4]<<4,(uint64_t)c[0x458/4]*512};
    /* Trial reserve and read acquisition commit together, with START state. */
    if (!count && !no_limit && !nb_reserve(&trial,b?NB_SLOT_JPEG1:NB_SLOT_JPEG0,token,r->frame,&output,1)) {
        q->failure="output_lease_conflict";return false;
    }
    uint64_t n=(uint64_t)c[0x410/4]*c[0x41c/4];
    if (r->published && !nb_read_acquire(&trial,b,token,
        (NBSpan){(uint64_t)c[0x430/4]<<4,n},(NBSpan){(uint64_t)c[0x434/4]<<4,n})) {
        q->failure="source_read_lease";return false;
    }
    *q=(Z8JpegRequest){.generation=r->generation,.token=token,.role=slot,.snapshot=true,
        .lease=!count&&!no_limit,.count_only=count,.no_limit=no_limit,
        .frame=r->frame,.parent_151_job=r->parent_151_job,.decoder_job=r->decoder_job,
        .parent_generation=r->parent_generation,.source_kind=r->source_kind,.producer_kind=r->kind,.producer_index=r->index};
    memcpy(q->words,c,sizeof(q->words));s->output_owner=trial;
    if (!role.used) { role.used=true;g_strlcpy(role.signature,signature,65); }
    if (!count) {
        role.claim=b+1;
        if (video) {
            r->video_commit_retired=false;
            if (video_retry) { fprintf(stderr,"Z9_15E2_VIDEO_REENCODE bank=%u generation=%" PRIu64
                " previous_token=%" PRIu64 " new_token=%" PRIu64 " commit_index=%" PRIu64
                " pass_index=%" PRIu64 "\n",
                b,r->generation,r->video_commit_token,token,r->video_commits+1,r->video_passes+1); }
        }
    }
    r->roles[slot]=role;r->readers|=1u<<b;return true;
}

static void jpeg_bank_failed(NikonZ9State *s,unsigned b,const char *reason)
{
    Z8JpegRequest *q=&s->jpeg_request[b];Z8JpegSource *r=jpeg_source_get(s,q->generation);
    q->failure=reason;s->engine15e2_deferred_failed[b]=true;
    if (q->lease) { nb_cancel(&s->output_owner,b?NB_SLOT_JPEG1:NB_SLOT_JPEG0);q->lease=false; }
    nb_read_release(&s->output_owner,b,q->token);
    if (r) {
        r->readers&=~(1u<<b);
        if (r->roles[q->role].claim==b+1) { r->roles[q->role].claim=0; }
    }
    fprintf(stderr,"Z9_15E2_FAILED bank=%u generation=%" PRIu64 " reason=%s; busy retained, no completion\n",b,q->generation,reason);
}

static bool jpeg_bank_retire(NikonZ9State *s,unsigned b,uint32_t pending,uint32_t core)
{
    Z8JpegRequest *q=&s->jpeg_request[b];
    if (pending || core || !q->lease) { return true; }
    if (!nb_ack(&s->output_owner,b?NB_SLOT_JPEG1:NB_SLOT_JPEG0,q->token)) { return false; }
    Z8JpegSource *r=jpeg_source_get(s,q->generation);
    if (s->engine15e2_video_source_model && jpeg_source_is_video(r) &&
        !q->count_only && r->video_commit_token==q->token && r->video_commit_bank==b) {
        r->video_commit_retired=true;
        fprintf(stderr,"Z9_15E2_VIDEO_RETIRED bank=%u generation=%" PRIu64 " token=%" PRIu64 "\n",b,r->generation,q->token);
    }
    q->lease=false;return true;
}
