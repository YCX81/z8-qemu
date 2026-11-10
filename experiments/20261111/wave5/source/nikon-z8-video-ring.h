/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Compatibility API for the single M C0/C1 stop implementation. */
static bool get_z8_video_ring_stop_model(Object *obj, Error **errp)
{
    return NIKON_Z9_LAB(obj)->z8_c0_stop_model;
}

static void set_z8_video_ring_stop_model(Object *obj, bool value, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    if (!object_dynamic_cast(obj, MACHINE_TYPE_NAME("nikon-z8-bringup")) ||
        s->image_ram.ram || s->image_ram.rom_device) {
        error_setg(errp, "z8-video-ring-stop-model requires Z8 at machine creation");
        return;
    }
    s->z8_c0_stop_model = value;
    s->z8_ring_stop_alias_requested = value;
}

static char *get_z8_video_ring_stop_state(Object *obj, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    return g_strdup_printf("{\"enabled\":%s,\"owned\":[%s,%s],"
        "\"stops\":[%" PRIu64 ",%" PRIu64 "],\"acks\":[%" PRIu64 ",%" PRIu64 "],"
        "\"pixel_bytes\":0,\"frame_events\":0,"
        "\"scope\":\"C0/C1 active101 to stop100 status20/mask/W1C; no encoded data\"}",
        s->z8_c0_stop_model ? "true" : "false",
        s->z8_ring_stop_owned[0] ? "true" : "false",
        s->z8_ring_stop_owned[1] ? "true" : "false",
        s->z8_ring_stops[0], s->z8_ring_stops[1],
        s->z8_ring_stop_acks[0], s->z8_ring_stop_acks[1]);
}
