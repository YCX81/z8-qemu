/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Standalone backend check on stopped-guest RAM snapshots. This executable
 * is NOT a native movie queue or camera recording implementation. */
#include <stdbool.h>
#include <stdint.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <glib.h>
#include "nikon-video-plane.h"
#include "nikon-wave5-codec.h"

static bool read_plane(const char *name, NVPInfo *info, uint8_t **raw)
{
    gchar *packet = NULL;
    gsize bytes = 0;
    GError *error = NULL;
    if (!g_file_get_contents(name,&packet,&bytes,&error)) {
        fprintf(stderr,"read failed: %s\n",error->message);g_error_free(error);return false;
    }
    if (!nvp_header((uint8_t *)packet,bytes,info) || bytes != info->packet_bytes) { g_free(packet);return false; }
    *raw = g_malloc(info->raw_bytes);
    bool valid = nvp_decode((uint8_t *)packet,bytes,*raw,info->raw_bytes,info);
    g_free(packet);
    if (!valid) { g_free(*raw);*raw=NULL; }
    return valid;
}

int main(int argc, char **argv)
{
    if (argc != 5) {
        fprintf(stderr,"usage: codec-probe Y.packet UV.packet output.hevc reference.i420\n");return 2;
    }
    NVPInfo y,uv;
    uint8_t *yp=NULL,*uvp=NULL,*i420=NULL;
    WlHevcCodec *codec=NULL;
    GByteArray *encoded=g_byte_array_new();
    int result=1;
    if (!read_plane(argv[1],&y,&yp) || !read_plane(argv[2],&uv,&uvp) ||
        y.plane || uv.plane!=1 || y.width!=uv.width || y.height!=uv.height*2 ||
        y.epoch!=uv.epoch || y.virtual_ns!=uv.virtual_ns) { goto done; }
    size_t luma=(size_t)y.width*y.height, chroma=luma/4;
    i420=g_malloc(luma+2*chroma);
    for (unsigned row=0;row<y.height;row++) {
        memcpy(i420+(size_t)row*y.width,yp+(size_t)row*y.stride,y.width);
    }
    for (unsigned row=0;row<uv.height;row++) {
        for (unsigned x=0;x<uv.width/2;x++) {
            i420[luma+(size_t)row*(uv.width/2)+x]=uvp[(size_t)row*uv.stride+2*x];
            i420[luma+chroma+(size_t)row*(uv.width/2)+x]=uvp[(size_t)row*uv.stride+2*x+1];
        }
    }
    const unsigned crop[4]={0,0,0,0};
    codec=wl_video_open(1,y.width,y.height,8,30,1,true,crop);
    if (!codec || !wl_hevc_headers(codec,encoded)) { goto done; }
    uint8_t *planes[3]={i420,i420+luma,i420+luma+chroma};
    unsigned strides[3]={y.width,y.width/2,y.width/2},nal_type=0;
    if (!wl_hevc_picture(codec,planes,strides,0,encoded,&nal_type) || codec->frames!=1) { goto done; }
    FILE *out=fopen(argv[3],"wbx");
    if (!out) { goto done; }
    bool written=fwrite(encoded->data,1,encoded->len,out)==encoded->len;
    bool closed=fclose(out)==0;
    if (!written || !closed) { goto done; }
    out=fopen(argv[4],"wbx");
    if (!out) { goto done; }
    written=fwrite(i420,1,luma+2*chroma,out)==luma+2*chroma;
    closed=fclose(out)==0;
    if (!written || !closed) { goto done; }
    printf("{\"width\":%u,\"height\":%u,\"epoch\":%" PRIu64
           ",\"encoded_pictures\":%" PRIu64 ",\"encoded_bytes\":%u,"
           "\"native_movie_queue_verified\":false,\"camera_recording\":false}\n",
           y.width,y.height,y.epoch,codec->frames,encoded->len);
    result=0;
done:
    wl_hevc_close(codec);g_byte_array_unref(encoded);g_free(yp);g_free(uvp);g_free(i420);
    return result;
}
