/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Existing functional PCIe configuration model, reused for video experiments. */
/* Original Z9 4118a924..a960 / table43de9400. Two pairs of
 * arbitration choices; retention only, no remapping, link or DMA effect. */
static uint32_t *pcie_qos_word(NikonZ9State *s, hwaddr addr, unsigned size)
{
    static const hwaddr addresses[] = {0x1407b080, 0x14083080, 0x1408b080, 0x14093080};
    if (!s->bootstrap_fixtures || !s->pcie_link_diagnostic_mask || size != 4) {
        return NULL;
    }
    for (unsigned i = 0; i < ARRAY_SIZE(addresses); i++) {
        if (addr == addresses[i]) {
            return &s->pcie_qos_config[i];
        }
    }
    return NULL;
}

/* Partial native PCIe syscon configuration only. Reset zero is a lab
 * assumption; no status, IRQ, transaction completion or PHY readiness. */
static uint32_t *pcie_syscon_word(NikonZ9State *s, hwaddr addr, unsigned size,
                                 uint32_t *mask)
{
    static const uint32_t masks[] = {1, 0x3131, 0xf0f};
    if (!s->bootstrap_fixtures || !s->pcie_link_diagnostic_mask || size != 4) {
        return NULL;
    }
    for (unsigned bank = 0; bank < 2; bank++) {
        hwaddr base = 0x17a00700 + bank * 0x100;
        if (addr >= base && addr <= base + 8 && !(addr & 3)) {
            unsigned word = (addr - base) / 4;
            *mask = masks[word];
            return &s->pcie_syscon[bank][word];
        }
    }
    return NULL;
}

/* Native Z9 4028bab4..be58 configures two wrapper apertures before
 * entering DBI. Only observed fields are retained. Initial zero is an
 * explicit diagnostic assumption, not a measured power-on value. */
static uint32_t z9_pcie_wrapper_link_input(NikonZ9State *s);
static void z9_pcie_intx_update(NikonZ9State *s);

static uint32_t *pcie_wrapper_word(NikonZ9State *s, hwaddr addr, unsigned size,
                                  uint32_t *mask)
{
    static const unsigned offsets[] = {0xe4c, 0x58, 0x258, 0x54, 0x254};
    static const uint32_t masks[] = {0x707, 1, 1, 0x700, 0x700};
    if (!s->bootstrap_fixtures || !s->pcie_link_diagnostic_mask || size != 4) {
        return NULL;
    }
    for (unsigned bank = 0; bank < 2; bank++) {
        hwaddr base = 0x17b00000 + bank * 0x80000;
        for (unsigned i = 0; i < ARRAY_SIZE(offsets); i++) {
            if (addr == base + offsets[i]) {
                *mask = masks[i];
                return &s->pcie_wrapper[bank][i];
            }
        }
    }
    return NULL;
}

static bool pcie_wrapper_init_write(NikonZ9State *s, hwaddr addr,
                                    uint64_t value, unsigned size)
{
    /* Native writes at 4028bcac/bd1c/bd60. No retained status or ready bit,
     * no IRQ or transfer. Reads and other values remain strict. */
    if (!s->bootstrap_fixtures || !s->pcie_link_diagnostic_mask || size != 4) {
        return false;
    }
    for (unsigned bank = 0; bank < 2; bank++) {
        hwaddr base = 0x17b00000 + bank * 0x80000;
        if ((addr == base + 0xe4 || addr == base + 0x2e4) && value == 0xfe010000) {
            return true;
        }
        if ((addr == base + 0xe8 || addr == base + 0x2e8) && value == 0xf0000000) {
            return true;
        }
    }
    return false;
}

static bool video_pcie_read(NikonZ9State *s, hwaddr addr, unsigned size, uint64_t *result)
{
    if (s->bootstrap_fixtures && s->pcie_link_diagnostic_mask &&
        addr == 0x17a00080 && size == 4) {
        *result = s->pcie_port_selection; return true;
    }
    if (s->pcie_intx_model && addr == 0x17b800e8 && size == 4) {
        *result = s->pcie_intx_pending; return true;
    }
    if (s->pcie_dbi_model && addr == 0x17b800b4 && size == 4) {
        *result = z9_pcie_wrapper_link_input(s); return true;
    }
    uint32_t pcie_wrapper_mask;
    uint32_t *pcie_wrapper = pcie_wrapper_word(s, addr, size, &pcie_wrapper_mask);
    if (pcie_wrapper) {
        *result = *pcie_wrapper; return true;
    }
    uint32_t pcie_syscon_mask;
    uint32_t *pcie_syscon = pcie_syscon_word(s, addr, size, &pcie_syscon_mask);
    if (pcie_syscon) {
        *result = *pcie_syscon; return true;
    }
    uint32_t *pcie_qos = pcie_qos_word(s, addr, size);
    if (pcie_qos) {
        *result = *pcie_qos; return true;
    }
    return false;
}
static bool video_pcie_write(NikonZ9State *s, hwaddr addr, uint64_t value, unsigned size)
{
    if (s->bootstrap_fixtures && s->pcie_link_diagnostic_mask &&
        addr == 0x17a00080 && size == 4 && !(value & ~UINT64_C(7))) {
        s->pcie_port_selection = value;
        return true;
    }
    uint32_t pcie_wrapper_mask;
    uint32_t *pcie_wrapper = pcie_wrapper_word(s, addr, size, &pcie_wrapper_mask);
    if (pcie_wrapper && !(value & ~(uint64_t)pcie_wrapper_mask)) {
        *pcie_wrapper = value;
        if (s->pcie_intx_model) {
            z9_pcie_intx_update(s);
        }
        return true;
    }
    if (s->pcie_intx_model && addr == 0x17b800e8 && size == 4 &&
        !(value & ~UINT64_C(0xf000f000))) {
        s->pcie_intx_pending &= ~value;
        s->pcie_intx_acks++;
        z9_pcie_intx_update(s);
        return true;
    }
    if (pcie_wrapper_init_write(s, addr, value, size)) {
        return true;
    }
    uint32_t pcie_syscon_mask;
    uint32_t *pcie_syscon = pcie_syscon_word(s, addr, size, &pcie_syscon_mask);
    if (pcie_syscon && !(value & ~(uint64_t)pcie_syscon_mask)) {
        *pcie_syscon = value;
        return true;
    }
    uint32_t *pcie_qos = pcie_qos_word(s, addr, size);
    if (pcie_qos && (value == 0x110014 || value == 0x110020)) {
        *pcie_qos = value;
        return true;
    }
    return false;
}
static char *get_pcie_link_diagnostic_mask(Object *obj, Error **errp)
{
    return g_strdup_printf("%u", NIKON_Z9_LAB(obj)->pcie_link_diagnostic_mask);
}

static void set_pcie_link_diagnostic_mask(Object *obj, const char *value, Error **errp)
{
    NikonZ9State *s = NIKON_Z9_LAB(obj);
    uint64_t mask;
    if (s->image_ram.ram || s->image_ram.rom_device || !value || !*value ||
        strlen(value) > 2 || (value[0] == '0' && value[1]) ||
        strspn(value, "0123456789") != strlen(value) ||
        qemu_strtou64(value, NULL, 10, &mask) || mask > 15) {
        error_setg(errp, "pcie-link-diagnostic-mask requires pre-realization canonical decimal 0..15");
        return;
    }
    s->pcie_link_diagnostic_mask = mask;
}

