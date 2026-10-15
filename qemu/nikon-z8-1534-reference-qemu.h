/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Independent bank0 entry. The existing bank1 admission is left unchanged.
 * Only byte-identical bank1 reference programs are admitted. Neutral NR
 * preserves actual Bayer/padding bytes; physical filtering is unverified. */
static void z8_1534_hash(const void *data,size_t bytes,char out[65])
{
    char *hash=g_compute_checksum_for_data(G_CHECKSUM_SHA256,data,bytes);
    g_strlcpy(out,hash,65);g_free(hash);
}
static void z8_1534_record_reference(NikonZ9State *s,const uint32_t *c,const uint8_t *program,
                                    uint64_t source,size_t bytes)
{
    Z8Heif1534 *m=&s->heif1534;
    if (!m->enabled) { return; }
    m->reference_valid=false;
    memcpy(m->reference_control,c,sizeof(m->reference_control));
    memcpy(m->reference_program,program,sizeof(m->reference_program));
    m->reference_frame=s->primary_d980_frames;m->reference_job=s->engine153_jobs[1];
    m->required_ack=s->engine153_acks[1]+1;m->source=source;m->bytes=bytes;
    m->width=s->primary_d980_cache_width;m->height=s->primary_d980_cache_height;
    m->stride=s->primary_d980_cache_stride;m->reference_format=s->primary_d980_cache_format;
    m->reference_valid=s->engine153_inplace_identity_model && m->reference_format==2 &&
        c[0x40/4]==11 && c[0x48/4]==0x02c97d00 && c[0x4c/4]==0xa;
    z8_1534_hash(program,Z8_153_PROGRAM_BYTES,m->reference_program_sha256);
}
static bool z8_1534_reject(NikonZ9State *s,const char *why)
{
    Z8Heif1534 *m=&s->heif1534;m->last_reject=why;m->rejects++;
    fprintf(stderr,"Z8_1534_REJECT reason=%s offset=%x frame=%" PRIu64 " reference_frame=%" PRIu64 "\n",
        why,m->mismatch_offset,s->primary_d980_frames,m->reference_frame);
    return false;
}
static bool z8_1534_inplace(NikonZ9State *s)
{
    Z8Heif1534 *m=&s->heif1534;const uint32_t *c=s->engine153_config[0];
    uint8_t program[Z8_153_PROGRAM_BYTES];uint64_t source=c[0xb4/4]|((uint64_t)c[0xb0/4]<<32);
    m->mismatch_offset=UINT32_MAX;m->program_sha256[0]='\0';
    if (!m->enabled || !s->engine153_inplace_identity_model || !m->reference_valid) {
        return z8_1534_reject(s,"missing-accepted-bank1-reference");
    }
    if (s->engine153_pending[0] || s->engine153_pending[1] ||
        s->engine153_jobs[1]!=m->reference_job || s->engine153_acks[1]!=m->required_ack ||
        !z8_1534_next_frame(m->reference_frame,s->primary_d980_frames,s->engine153_consumed_dma_frame[0])) {
        return z8_1534_reject(s,"pending-or-generation");
    }
    if (!z8_1534_same_control(m->reference_control,c,&m->mismatch_offset)) {
        return z8_1534_reject(s,"control-mismatch");
    }
    if (!dma_ram_range(s,UINT64_C(0xa02c97500),sizeof(program)) ||
        address_space_read(&address_space_memory,UINT64_C(0xa02c97500),MEMTXATTRS_UNSPECIFIED,
            program,sizeof(program))!=MEMTX_OK) { return z8_1534_reject(s,"program-ram"); }
    z8_1534_hash(program,sizeof(program),m->program_sha256);
    if (!z8_1534_same_program(m->reference_program,program,&m->mismatch_offset)) {
        return z8_1534_reject(s,"program-mismatch");
    }
    if (!s->primary_d980_cache || s->primary_d980_cache_format!=2 || s->primary_d980_pending!=0x4000000 ||
        m->width!=s->primary_d980_cache_width || m->height!=s->primary_d980_cache_height ||
        m->stride!=s->primary_d980_cache_stride || source!=s->primary_d980_last_physical ||
        source!=m->source || (source&31) || !m->bytes || m->bytes>128*MiB ||
        m->bytes!=(uint64_t)m->stride*m->height || source<UINT64_C(0x800000000) ||
        source>UINT64_C(0x900000000)-m->bytes || !dma_ram_range(s,source,m->bytes) ||
        !nb_writable(&s->output_owner,(NBSpan){source,m->bytes})) { return z8_1534_reject(s,"source-span"); }
    uint8_t *pixels=g_malloc(m->bytes),*check=g_malloc(m->bytes);
    char source_hash[65],readback_hash[65];
    bool ok=address_space_read(&address_space_memory,source,MEMTXATTRS_UNSPECIFIED,pixels,m->bytes)==MEMTX_OK;
    if (ok) {
        z8_1534_hash(pixels,m->bytes,source_hash);
        ok=address_space_write(&address_space_memory,source,MEMTXATTRS_UNSPECIFIED,pixels,m->bytes)==MEMTX_OK &&
           address_space_read(&address_space_memory,source,MEMTXATTRS_UNSPECIFIED,check,m->bytes)==MEMTX_OK &&
           !memcmp(pixels,check,m->bytes);
        if (ok) { z8_1534_hash(check,m->bytes,readback_hash); }
    }
    g_free(check);g_free(pixels);
    if (!ok) { return z8_1534_reject(s,"source-ram-readback"); }
    uint8_t program_after[Z8_153_PROGRAM_BYTES];
    if (address_space_read(&address_space_memory,UINT64_C(0xa02c97500),MEMTXATTRS_UNSPECIFIED,
        program_after,sizeof(program_after))!=MEMTX_OK || memcmp(program,program_after,sizeof(program))) {
        return z8_1534_reject(s,"program-changed-during-dma");
    }
    memcpy(m->accepted_control,c,sizeof(m->accepted_control));
    memcpy(m->accepted_program,program,sizeof(program));
    g_strlcpy(m->source_sha256,source_hash,65);g_strlcpy(m->readback_sha256,readback_hash,65);
    g_strlcpy(m->accepted_program_sha256,m->program_sha256,65);
    m->accepted_bytes=m->bytes;m->used_reference_frame=m->reference_frame;m->used_reference_job=m->reference_job;
    s->engine153_consumed_dma_frame[0]=s->primary_d980_frames;
    s->engine153_jobs[0]++;s->engine153_bytes[0]+=m->bytes;
    s->engine153_last_source[0]=s->engine153_last_target[0]=source;
    s->engine153_pending[0]=1;m->last_reject=NULL;
    fprintf(stderr,"Z8_1534_INPLACE frame=%" PRIu64 " reference_frame=%" PRIu64
        " bytes=%" PRIu64 " sha256=%s; exact bank1-equivalent neutral contract\n",
        s->primary_d980_frames,m->reference_frame,m->bytes,m->readback_sha256);
    return true;
}
static bool get_z8_heif_1534_model(Object *obj,Error **errp)
{ return NIKON_Z9_LAB(obj)->heif1534.enabled; }
static void set_z8_heif_1534_model(Object *obj,bool value,Error **errp)
{
    NikonZ9State *s=NIKON_Z9_LAB(obj);
    if (!object_dynamic_cast(obj,MACHINE_TYPE_NAME("nikon-z8-bringup")) || s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp,"z8-heif-1534-inplace-model requires Z8 at creation and is immutable");return;
    }
    s->heif1534.enabled=value;
}
static char *get_z8_heif_1534_state(Object *obj,Error **errp)
{
    NikonZ9State *s=NIKON_Z9_LAB(obj);Z8Heif1534 *m=&s->heif1534;GString *out=g_string_new(NULL);
    g_string_append_printf(out,"{\"enabled\":%s,\"reference_valid\":%s,\"reference_frame\":%" PRIu64
        ",\"reference_job\":%" PRIu64 ",\"required_ack\":%" PRIu64 ",\"jobs\":%" PRIu64
        ",\"consumed_frame\":%" PRIu64 ",\"pending\":%u,\"rejects\":%" PRIu64
        ",\"source\":%" PRIu64 ",\"bytes\":%" PRIu64 ",\"used_reference_frame\":%" PRIu64
        ",\"used_reference_job\":%" PRIu64 ",\"mismatch_offset\":%u,\"last_reject\":\"%s\","
        "\"reference_program_sha256\":\"%s\",\"candidate_program_sha256\":\"%s\",\"source_sha256\":\"%s\",\"readback_sha256\":\"%s\","
        "\"accepted_program_sha256\":\"%s\","
        "\"reference_format\":%u,\"physical_algorithm_verified\":false,\"physical_bank_equivalence_verified\":false,\"native_profile_observed\":false,"
        "\"scope\":\"Bank0 only after exact same-boot accepted/ACKed bank1 controls and program; neutral full RAW identity, no scratch result claim\",\"reference_control\":[",
        m->enabled?"true":"false",m->reference_valid?"true":"false",m->reference_frame,m->reference_job,
        m->required_ack,s->engine153_jobs[0],s->engine153_consumed_dma_frame[0],s->engine153_pending[0],m->rejects,
        s->engine153_last_source[0],m->accepted_bytes,m->used_reference_frame,m->used_reference_job,
        m->mismatch_offset,m->last_reject?m->last_reject:"",m->reference_program_sha256,
        m->program_sha256,m->source_sha256,m->readback_sha256,m->accepted_program_sha256,m->reference_format);
    for(unsigned n=0;n<84;n++) { g_string_append_printf(out,"%s%u",n?",":"",m->reference_control[n]); }
    g_string_append(out,"],\"reference_program_hex\":\"");
    for(unsigned n=0;n<Z8_153_PROGRAM_BYTES;n++) { g_string_append_printf(out,"%02x",m->reference_program[n]); }
    g_string_append(out,"\",\"accepted_control\":[");
    for(unsigned n=0;n<84;n++) { g_string_append_printf(out,"%s%u",n?",":"",m->accepted_control[n]); }
    g_string_append(out,"],\"accepted_program_hex\":\"");
    for(unsigned n=0;n<Z8_153_PROGRAM_BYTES;n++) { g_string_append_printf(out,"%02x",m->accepted_program[n]); }
    g_string_append(out,"\"}");return g_string_free(out,false);
}
