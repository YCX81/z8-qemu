/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Read-only submitted configuration. No observer writes guest/device state. */
static void z8_video_state_words(GString *out, const char *name,
                                 const uint32_t *words, size_t count)
{
    bool comma = false;
    g_string_append_printf(out, ",\"%s\":{", name);
    for (size_t i = 0; i < count; i++) {
        if (words[i]) {
            g_string_append_printf(out, "%s\"0x%zx\":%u", comma ? "," : "",
                                   i * 4, words[i]);
            comma = true;
        }
    }
    g_string_append_c(out, '}');
}

static char *get_z8_video_data_state(Object *obj, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    GString *out = g_string_new("{\"schema\":1,\"observation_only\":true");
#define Z8_VIDEO_STATE(name, field) \
    z8_video_state_words(out, name, s->field, ARRAY_SIZE(s->field))
    Z8_VIDEO_STATE("geometry0", z8_video_geometry_latched[0]);
    Z8_VIDEO_STATE("geometry1", z8_video_geometry_latched[1]);
    Z8_VIDEO_STATE("receiver", z8_video152c_config);
    Z8_VIDEO_STATE("receiver_side", z8_video15ac_config);
    Z8_VIDEO_STATE("decoder", z8_video1530_config);
    Z8_VIDEO_STATE("decoder_side", z8_video15b0_config);
    Z8_VIDEO_STATE("supplier0", engine1542_config[0]);
    Z8_VIDEO_STATE("supplier1", engine1542_config[1]);
    Z8_VIDEO_STATE("c0_current", engine1542_config[10]);
    Z8_VIDEO_STATE("c1_current", engine1542_config[11]);
    Z8_VIDEO_STATE("shared_buffer_config", z8_video1564_config);
#undef Z8_VIDEO_STATE
    g_string_append(out, ",\"shared_buffer_addresses\":[");
    for (unsigned bank = 0; bank < 4; bank++) {
        g_string_append_printf(out, "%s[", bank ? "," : "");
        for (unsigned slot = 0; slot < 8; slot++) {
            g_string_append_printf(out, "%s%" PRIu64, slot ? "," : "", s->z8_video1564_addresses[bank][slot]);
        }
        g_string_append_c(out, ']');
    }
    g_string_append_c(out, ']');
    g_string_append(out, ",\"bus_remaps\":[");
    bool comma = false;
    for (unsigned group = 0; group < 17; group++) {
        for (unsigned bank = 0; bank < 2; bank++) {
            for (unsigned slot = 0; slot < 32; slot++) {
                const uint64_t *w = s->bus_windows[group][bank][slot];
                if (!w[2]) { continue; }
                g_string_append_printf(out, "%s{\"group\":%u,\"bank\":%u,\"slot\":%u,"
                    "\"words\":[%" PRIu64 ",%" PRIu64 ",%" PRIu64 "]}",
                    comma ? "," : "", group, bank, slot, w[0], w[1], w[2]);
                comma = true;
            }
        }
    }
    g_string_append_c(out, ']');
    g_string_append_c(out, '}');
    return g_string_free(out, false);
}
