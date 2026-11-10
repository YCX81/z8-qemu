/* SPDX-License-Identifier: GPL-2.0-or-later */
/* QOM integration for shared WonderLantern Wave5 with a Z8 RAM source hook.
 * No default-on codec or claimed execution of Nikon microcode. */
struct WlWave5State;struct WlWave5Instance;struct WlWave5Job;
static int z8_wave5_yuv_snapshot(struct WlWave5State *,struct WlWave5Instance *,struct WlWave5Job *);
#define WL_WAVE5_SOURCE_SNAPSHOT z8_wave5_yuv_snapshot
#include "nikon-z9-wave5.h"
#include "nikon-z8-yuv-wave5.h"

static void z8_wave5_properties(ObjectClass *oc)
{
    object_class_property_add_str(oc,"z8-yuv-state",get_z8_yuv_state,NULL);
    object_class_property_add_bool(oc,"z8-yuv-data-model",get_z8_yuv_data_model,set_z8_yuv_data_model);
    object_class_property_add_bool(oc,"z8-yuv-dma-model",get_z8_yuv_dma_model,set_z8_yuv_dma_model);
    object_class_property_add_bool(oc,"z8-yuv-single-model",get_z8_yuv_single_model,set_z8_yuv_single_model);
    object_class_property_add_bool(oc,"z8-video-ring-repeat-model",get_z8_video_ring_repeat_model,set_z8_video_ring_repeat_model);
    object_class_property_add_bool(oc,"z8-video-source-end-model",get_z8_video_source_end_model,set_z8_video_source_end_model);
    object_class_property_add_bool(oc,"wave5-vpu-model",get_wave5_model,set_wave5_model);
    object_class_property_add_bool(oc,"wave5-lossless",get_wave5_lossless,set_wave5_lossless);
    object_class_property_add_bool(oc,"wave5-host-codec-diagnostic",get_wave5_diagnostic,set_wave5_diagnostic);
    object_class_property_add_str(oc,"wave5-irq",get_wave5_irq,set_wave5_irq);
    object_class_property_add_bool(oc,"wave5-z8-header-report-model",get_wave5_z8_header_report,set_wave5_z8_header_report);
    object_class_property_add_bool(oc,"wave5-z8-session-reset-model",get_wave5_z8_session_reset,set_wave5_z8_session_reset);
    object_class_property_add_str(oc,"wave5-state",get_wave5_state,NULL);
}

static void z8_wave5_realize(NikonZ9State *s,DeviceState *gic)
{
    if (s->wave5_z8_header_report_model &&
        (!s->bootstrap_fixtures || !s->wave5_model || !s->wave5_host_codec_diagnostic)) {
        error_report("Z8 header report requires Z8 bootstrap and diagnostic Wave5");exit(1);
    }
    if (s->wave5_z8_session_reset_model && !s->wave5_z8_header_report_model) {
        error_report("Z8 session reset requires diagnostic Z8 header report");exit(1);
    }
    s->wave5_realized=true;
    if (s->wave5_model && s->wave5_irq_number) {
        if (s->wave5_irq_number<32 || s->wave5_irq_number>=512) {
            error_report("Wave5 requires a valid explicit SPI binding");exit(1);
        }
        s->wave5->irq=qdev_get_gpio_in(gic,s->wave5_irq_number-32);
    }
}

/* MAIN402f3130..3178 asserts IDs43..49 after DESTROY/VDI release;
 *402f2ef0..2f38 releases that cohort before a new VPU_Init. Individual
 * bit-to-IP identity is unmeasured: only the whole observed group resets
 * the VPU. Never infer a VPU reset from a single NR/clock/reset bit. */
static bool wave5_z8_session_reset_access(NikonZ9State *s,hwaddr addr,
                                          uint64_t value,unsigned size)
{
    const uint32_t cohort=UINT32_C(0x7f0000);
    if (!s->wave5_z8_session_reset_model ||
        (addr!=0x0d154348 && addr!=0x0d154248) || !(value&cohort)) { return false; }
    WlWave5State *w=s->wave5;
    if (!w || size!=4 || (value&~(uint64_t)cohort) || (value&(value-1)) ||
        w->busy || s->wave5_busy || w->job || w->pending || w->instances) { goto rejected; }
    for (unsigned n=0;n<WL_W5_INSTANCES;n++) {
        WlWave5Instance *i=&w->instance[n];
        if (i->created || i->configured || i->codec || i->result || i->sequence_result) { goto rejected; }
    }
    if (addr==0x0d154348) {
        if (w->session_released!=cohort || w->session_asserted==cohort ||
            value!=(UINT32_C(1)<<(16+ctpop32(w->session_asserted)))) { goto rejected; }
        w->session_asserted|=value;s->peripheral_release8&=~value;
        if (w->session_asserted==cohort) {
            /* No guest RAM, instance-pool bytes, DMA, completion or IRQ
             * is generated here. DESTROY already freed codec resources.
             * Keep cumulative byte/command evidence; clear hardware state. */
            memset(w->regs,0,sizeof(w->regs));memset(w->fio_words,0,sizeof(w->fio_words));
            w->fio_address=0;w->fio_data=0;w->interrupt_mask=0;
            w->booted=false;w->sleeping=false;w->session_released=0;
            w->resets++;w->session_resets++;wave5_irq_update(w);
        }
    } else {
        if ((w->session_asserted && w->session_asserted!=cohort) ||
            w->session_released==cohort ||
            value!=(UINT32_C(1)<<(16+ctpop32(w->session_released)))) { goto rejected; }
        w->session_released|=value;s->peripheral_release8|=value;
        if (w->session_released==cohort) { w->session_asserted=0; }
    }
    fprintf(stderr,"Z8_WAVE5_SESSION_RESET action=%s mask=%"PRIx64" asserted=%x released=%x cycles=%"PRIu64"; no completion\n",
        addr==0x0d154348 ? "assert" : "release",value,w->session_asserted,w->session_released,w->session_resets);
    return true;
rejected:
    fprintf(stderr,"Z8_WAVE5_SESSION_RESET_REJECT mask=%"PRIx64"; no state change\n",value);
    unknown_access(s,addr,value,size,true);return true;
}
