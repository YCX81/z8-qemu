/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Adapted from WonderLantern/zr-qemu f9cc5d1, nikon-z9-pixels.h.
 * Functional combined input completion, NOT recovered silicon timing.
 * Z8 410b0ce8 ->410b0fa0 delivers 04220013; 40e8b9cc is its consumer.
 * Only the existing actual six-plane transfer may publish this cause.
 * No task memory, callback result, codec bytes or file header is written. */
static bool get_raw_pipeline_input_pair_model(Object *obj, Error **errp)
{
    return NIKON_Z9_LAB(obj)->raw_pipeline_input_pair_model;
}

static void set_raw_pipeline_input_pair_model(Object *obj, bool value, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    if (s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp, "raw-pipeline-input-pair-model is immutable after realization");
        return;
    }
    s->raw_pipeline_input_pair_model = value;
}

static char *get_raw_pipeline_input_pair_state(Object *obj, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    return g_strdup_printf("{\"enabled\":%s,\"completions\":%" PRIu64
        ",\"acks\":%" PRIu64 ",\"cancelled\":%" PRIu64 ",\"pending\":%u,"
        "\"scope\":\"functional input pair after six actual RAW-derived plane writes\","
        "\"physical_timing_verified\":false,\"recording_verified\":false}",
        s->raw_pipeline_input_pair_model ? "true" : "false",
        s->raw_pipeline_input_pair_completions, s->raw_pipeline_input_pair_acks,
        s->raw_pipeline_input_pair_cancelled, s->raw_primary_pending & 0x80000000);
}

/* Preflight the entire transfer before modifying any destination. The
 * inherited per-channel check alone misses aliases between two channels. */
static bool raw_input_pair_spans_disjoint(const uint64_t ys[3],
                                         const uint64_t uvs[3],
                                         const unsigned strides[3],
                                         const unsigned heights[3],
                                         const bool halves[3])
{
    uint64_t address[6], bytes[6];
    for (unsigned ch = 0; ch < 3; ch++) {
        address[2 * ch] = ys[ch];
        address[2 * ch + 1] = uvs[ch];
        bytes[2 * ch] = (uint64_t)strides[ch] * heights[ch];
        bytes[2 * ch + 1] = bytes[2 * ch] / (halves[ch] ? 2 : 1);
    }
    for (unsigned j = 0; j < 6; j++) {
        for (unsigned k = 0; k < j; k++) {
            if (address[j] < address[k] + bytes[k] &&
                address[k] < address[j] + bytes[j]) {
                return false;
            }
        }
    }
    return true;
}
