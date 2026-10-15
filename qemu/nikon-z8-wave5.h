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
    object_class_property_add_str(oc,"wave5-state",get_wave5_state,NULL);
}

static void z8_wave5_realize(NikonZ9State *s,DeviceState *gic)
{
    s->wave5_realized=true;
    if (s->wave5_model && s->wave5_irq_number) {
        if (s->wave5_irq_number<32 || s->wave5_irq_number>=512) {
            error_report("Wave5 requires a valid explicit SPI binding");exit(1);
        }
        s->wave5->irq=qdev_get_gpio_in(gic,s->wave5_irq_number-32);
    }
}
