/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Optional board-write provenance, adapted from WonderLantern z9-qemu shared441.
 * No address translation, write replacement, completion, interrupt or
 * guest-state repair. Input is an explicit interval inside one RAM-backed
 * window of this machine: work RAM, or the allocated image-bank span that
 * holds the Z8 C30 history block pool. No firmware addresses are compiled in.
 * Only writes issued by this board through address_space_write() or
 * cpu_physical_memory_write() are observed; guest CPU stores are not. */
#define WL_DMA_OBSERVER_LIMIT 4096
#define WL_DMA_WORK_RAM UINT64_C(0xa00000000)
static uint64_t wl_dma_observer_low, wl_dma_observer_high;
static uint64_t wl_dma_observer_work_end;
static bool wl_dma_observer_banks;
static gint wl_dma_observer_records;

/* Image banks leave a small unbacked gap; sampling must not read it as MMIO. */
static bool wl_dma_observer_backed(uint64_t start, uint64_t bytes)
{
    if (start >= WL_DMA_WORK_RAM && start + bytes <= wl_dma_observer_work_end) {
        return true;
    }
    for (unsigned i = 0; wl_dma_observer_banks && i < ARRAY_SIZE(image_bank_ranges); i++) {
        if (start >= image_bank_ranges[i].base &&
            start + bytes <= image_bank_ranges[i].end) {
            return true;
        }
    }
    return false;
}

/* Called once at machine creation, so a bad interval fails before any run. */
static void wl_dma_observer_configure(uint64_t work_ram_bytes, bool image_banks)
{
    const char *text = g_getenv("WL_DMA_WRITE_OBSERVE");
    uint64_t low = 0, high = 0;
    char tail;
    if (!text) {
        return;
    }
    uint64_t bank_low = image_bank_ranges[0].base;
    uint64_t bank_high = image_bank_ranges[ARRAY_SIZE(image_bank_ranges) - 1].end;
    uint64_t work_end = WL_DMA_WORK_RAM + work_ram_bytes;
    bool parsed = sscanf(text, "%"SCNx64":%"SCNx64"%c", &low, &high, &tail) == 2 &&
                  low < high;
    bool work = parsed && low >= WL_DMA_WORK_RAM && high <= work_end;
    bool banks = parsed && image_banks && low >= bank_low && high <= bank_high;
    if (!work && !banks) {
        error_report("WL_DMA_WRITE_OBSERVE requires low:high inside work RAM "
                     "0x%"PRIx64"..0x%"PRIx64" or, with image-bank-model=on, "
                     "image banks 0x%"PRIx64"..0x%"PRIx64,
                     WL_DMA_WORK_RAM, work_end, bank_low, bank_high);
        exit(1);
    }
    wl_dma_observer_low = low;
    wl_dma_observer_high = high;
    wl_dma_observer_work_end = work_end;
    wl_dma_observer_banks = image_banks;
}

static void wl_dma_observer_record(AddressSpace *as, hwaddr address,
    MemTxAttrs attrs, const void *buffer, hwaddr bytes, int result,
    const char *producer, unsigned line)
{
    uint64_t low = wl_dma_observer_low, high = wl_dma_observer_high;
    if (!high || as != &address_space_memory || !bytes || address >= high ||
        bytes > UINT64_MAX - address || address + bytes <= low) {
        return;
    }
    gint index = g_atomic_int_add(&wl_dma_observer_records, 1);
    if (index > WL_DMA_OBSERVER_LIMIT) { return; }
    if (index == WL_DMA_OBSERVER_LIMIT) {
        fprintf(stderr, "WL_DMA_OBSERVE {\"limit_reached\":true,\"limit\":%d}\n",
                WL_DMA_OBSERVER_LIMIT);
        return;
    }
    uint64_t start = MAX(address, low), end = MIN(address + bytes, high);
    unsigned n = MIN(end - start, 64);
    const uint8_t *expected = (const uint8_t *)buffer + (start - address);
    uint8_t actual[64];
    bool backed = wl_dma_observer_backed(start, n);
    bool matches = backed &&
                   address_space_read(as, start, attrs, actual, n) == MEMTX_OK &&
                   !memcmp(actual, expected, n);
    GString *out = g_string_new(NULL);
    g_string_append_printf(out, "WL_DMA_OBSERVE {\"index\":%d,\"producer\":\"%s\","
        "\"line\":%u,\"virtual_ns\":%"PRId64",\"address\":\"0x%"PRIx64"\","
        "\"bytes\":%"PRIu64",\"api_result\":%d,\"sample_address\":\"0x%"PRIx64"\","
        "\"sample_ram_backed\":%s,\"sample_matches_RAM\":%s,\"sample_hex\":\"",
        index, producer, line, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL),
        (uint64_t)address, (uint64_t)bytes, result, start,
        backed ? "true" : "false", matches ? "true" : "false");
    for (unsigned i = 0; i < n; i++) { g_string_append_printf(out, "%02x", expected[i]); }
    g_string_append(out, "\"}\n");
    fputs(out->str, stderr);
    g_string_free(out, true);
}

static MemTxResult wl_observed_address_space_write(AddressSpace *as, hwaddr address,
    MemTxAttrs attrs, const void *buffer, hwaddr bytes, const char *producer, unsigned line)
{
    MemTxResult result = address_space_write(as, address, attrs, buffer, bytes);
    wl_dma_observer_record(as, address, attrs, buffer, bytes, result, producer, line);
    return result;
}

static void wl_observed_cpu_physical_write(hwaddr address, const void *buffer,
    hwaddr bytes, const char *producer, unsigned line)
{
    cpu_physical_memory_write(address, buffer, bytes);
    /* The void API has no completion result. Its sampled RAM check is explicit;
     * -1 must not be interpreted as either a successful or failed transaction. */
    wl_dma_observer_record(&address_space_memory, address, MEMTXATTRS_UNSPECIFIED,
                           buffer, bytes, -1, producer, line);
}

#define address_space_write(as, address, attrs, buffer, bytes) \
    wl_observed_address_space_write(as, address, attrs, buffer, bytes, __func__, __LINE__)
#define cpu_physical_memory_write(address, buffer, bytes) \
    wl_observed_cpu_physical_write(address, buffer, bytes, __func__, __LINE__)
