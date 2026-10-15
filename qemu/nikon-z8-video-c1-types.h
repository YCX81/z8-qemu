/* SPDX-License-Identifier: GPL-2.0-or-later */
typedef struct Z8VideoC1 {
    bool enabled, packet_owned, frame_ready, failed, ring_latched;
    uint32_t pending[2];
    qemu_irq irq[2];
    uint64_t epoch, captured_ns, packet_address, packet_bytes;
    uint64_t packets, decoded, decoded_samples, output_frames, output_bytes;
    uint64_t acks[2], cancellations, rejects, consumed_epoch;
    unsigned row, width, height, stride, capacity;
    unsigned input_bank;
    uint16_t input_bank_written[2];
    uint64_t input_bank_refills;
    bool primary_boundaries;
    uint32_t primary_pending;
    uint64_t primary_frames, primary_input_acks, primary_output_acks, primary_cancelled;
    bool source_end;
    unsigned source_group;
    uint64_t source_ends, source_end_acks, source_end_cancelled;
    bool fourth_output;
    uint16_t fourth_written[2];
    uint16_t fourth_valid[2];
    uint64_t fourth_frames, fourth_rejects, fourth_epoch;
    uint64_t fourth_alias[2], fourth_physical[2], fourth_packet_bytes[2];
    unsigned fourth_width, fourth_height;
    NikonRawPixels *pixels;
    char *decoded_sha256;
} Z8VideoC1;
