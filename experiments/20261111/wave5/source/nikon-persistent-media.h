/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Explicit, identity-bound logical NAND snapshots. This is a lab file format,
 * not camera NAND layout/ECC, and does not imply settings or NEF acceptance. */
#include <glib/gstdio.h>

#define NIKON_NAND_HEADER 72u
#define NIKON_NAND_DIGEST 32u
#define NIKON_NAND_RECORD (4u + Z9_NAND_PAGE)

typedef struct NikonNandImage {
    char *path;
    bool create, loaded, lock_open;
    int lock_fd;
    struct stat source_stat;
    uint8_t source_hash[NIKON_NAND_DIGEST];
    uint64_t source_bytes, commits, failed_commits;
} NikonNandImage;

static void nikon_media_sha256(const uint8_t *data, size_t length, uint8_t out[32])
{
    GChecksum *sum = g_checksum_new(G_CHECKSUM_SHA256);
    gsize size = 32;
    g_checksum_update(sum, data, length);
    g_checksum_get_digest(sum, out, &size);
    g_checksum_free(sum);
}

/* Check aliases on every commit too: atomic replacement must never replace
 * the source MAIN or follow a symlink supplied as the writable destination. */
static bool nikon_media_path(NikonNandImage *m, const char *path,
                             bool missing_ok, Error **errp)
{
    struct stat st;
    if (!g_stat(path, &st) && st.st_dev == m->source_stat.st_dev &&
        st.st_ino == m->source_stat.st_ino) {
        error_setg(errp, "NAND image cannot alias source MAIN");
        return false;
    }
    if (g_lstat(path, &st)) {
        if (errno == ENOENT && missing_ok) {
            return true;
        }
        error_setg_errno(errp, errno, "NAND image path does not exist or is inaccessible");
        return false;
    }
    if (!S_ISREG(st.st_mode) || st.st_nlink != 1) {
        error_setg(errp, "NAND image must be an independent regular file (no links)");
        return false;
    }
    return true;
}

static gint nikon_media_key_compare(gconstpointer a, gconstpointer b,
                                    gpointer unused)
{
    uintptr_t aa = (uintptr_t)*(gpointer const *)a;
    uintptr_t bb = (uintptr_t)*(gpointer const *)b;
    return (aa > bb) - (aa < bb);
}

static bool nikon_nand_image_commit(NikonNandImage *m, GHashTable *pages,
                                    Error **errp)
{
    guint count = 0;
    g_autofree gpointer *keys = g_hash_table_get_keys_as_array(pages, &count);
    size_t length = NIKON_NAND_HEADER + (size_t)count * NIKON_NAND_RECORD + 32;
    g_autofree uint8_t *data = NULL;
    g_autoptr(GError) error = NULL;

    if (!nikon_media_path(m, m->path, !m->loaded, errp)) {
        return false;
    }
    if (count > Z9_NAND_PAGES || !(data = g_try_malloc0(length))) {
        error_setg(errp, "NAND image allocation or page-count failure");
        return false;
    }
    memcpy(data, "NZ8NAND1", 8);
    stl_le_p(data + 8, 1);
    stq_le_p(data + 16, m->source_bytes);
    memcpy(data + 24, m->source_hash, 32);
    stl_le_p(data + 56, Z9_NAND_PAGE);
    stl_le_p(data + 60, Z9_NAND_PAGES);
    stl_le_p(data + 64, Z9_NAND_PPB);
    stl_le_p(data + 68, count);
    g_qsort_with_data(keys, count, sizeof(*keys), nikon_media_key_compare, NULL);
    for (unsigned i = 0; i < count; i++) {
        uint8_t *record = data + NIKON_NAND_HEADER + i * NIKON_NAND_RECORD;
        stl_le_p(record, GPOINTER_TO_UINT(keys[i]) - 1);
        memcpy(record + 4, g_hash_table_lookup(pages, keys[i]), Z9_NAND_PAGE);
    }
    nikon_media_sha256(data, length - 32, data + length - 32);
    /* GLib creates a sibling temporary file, fsyncs it, renames it atomically,
     * and syncs the directory. The companion lock remains on its own inode. */
    if (!g_file_set_contents_full(m->path, (const char *)data, length,
            G_FILE_SET_CONTENTS_CONSISTENT | G_FILE_SET_CONTENTS_DURABLE,
            0600, &error)) {
        error_setg(errp, "NAND image commit failed: %s", error->message);
        return false;
    }
    m->loaded = true;
    m->commits++;
    return true;
}

static bool nikon_nand_image_open(NikonNandImage *m, GHashTable *pages,
                                  const char *source, Error **errp)
{
    g_autofree char *lock_path = g_strdup_printf("%s.lock", m->path);
    g_autofree char *raw = NULL;
    gsize length = 0;
    g_autoptr(GError) error = NULL;
    struct stat st;
    uint8_t digest[32];

    if (!source || g_stat(source, &m->source_stat) ||
        !S_ISREG(m->source_stat.st_mode) ||
        !g_file_get_contents(source, &raw, &length, &error)) {
        error_setg(errp, "NAND image requires a readable regular source MAIN");
        return false;
    }
    m->source_bytes = length;
    nikon_media_sha256((uint8_t *)raw, length, m->source_hash);
    g_clear_pointer(&raw, g_free);
    if (!nikon_media_path(m, m->path, m->create, errp) ||
        !nikon_media_path(m, lock_path, true, errp)) {
        return false;
    }
    m->lock_fd = qemu_open_old(lock_path, O_CREAT | O_RDWR | O_NOFOLLOW, 0600);
    if (m->lock_fd < 0) {
        error_setg_errno(errp, errno, "Cannot open NAND image lock");
        return false;
    }
    m->lock_open = true;
    if (qemu_lock_fd(m->lock_fd, 0, 1, true)) {
        error_setg(errp, "NAND image is locked by another process");
        return false;
    }
    /* Recheck after acquiring the lock; a competing creator may have won. */
    if (!nikon_media_path(m, m->path, m->create, errp)) {
        return false;
    }
    if (g_stat(m->path, &st)) {
        return nikon_nand_image_commit(m, pages, errp);
    }
    if (st.st_size < NIKON_NAND_HEADER + 32 ||
        (uint64_t)st.st_size > NIKON_NAND_HEADER +
            (uint64_t)Z9_NAND_PAGES * NIKON_NAND_RECORD + 32 ||
        !g_file_get_contents(m->path, &raw, &length, &error) ||
        length < NIKON_NAND_HEADER + 32) {
        error_setg(errp, "invalid NAND image size or unreadable media");
        return false;
    }
    const uint8_t *data = (uint8_t *)raw;
    uint32_t count = ldl_le_p(data + 68);
    if (memcmp(data, "NZ8NAND1", 8) || ldl_le_p(data + 8) != 1 ||
        ldl_le_p(data + 12) || ldl_le_p(data + 56) != Z9_NAND_PAGE ||
        ldl_le_p(data + 60) != Z9_NAND_PAGES || ldl_le_p(data + 64) != Z9_NAND_PPB ||
        count > Z9_NAND_PAGES ||
        length != NIKON_NAND_HEADER + (size_t)count * NIKON_NAND_RECORD + 32) {
        error_setg(errp, "invalid NAND image header, geometry or record length");
        return false;
    }
    nikon_media_sha256(data, length - 32, digest);
    if (memcmp(digest, data + length - 32, 32)) {
        error_setg(errp, "NAND image checksum mismatch");
        return false;
    }
    if (ldq_le_p(data + 16) != m->source_bytes ||
        memcmp(data + 24, m->source_hash, 32)) {
        error_setg(errp, "NAND image source MAIN identity mismatch");
        return false;
    }
    uint32_t previous = 0;
    /* Validate every index before importing any page. */
    for (unsigned i = 0; i < count; i++) {
        uint32_t page = ldl_le_p(data + NIKON_NAND_HEADER + i * NIKON_NAND_RECORD);
        if (page >= Z9_NAND_PAGES || (i && page <= previous)) {
            error_setg(errp, "invalid NAND image page index/order");
            return false;
        }
        previous = page;
    }
    for (unsigned i = 0; i < count; i++) {
        const uint8_t *record = data + NIKON_NAND_HEADER + i * NIKON_NAND_RECORD;
        g_hash_table_insert(pages, GUINT_TO_POINTER(ldl_le_p(record) + 1),
                            g_memdup2(record + 4, Z9_NAND_PAGE));
    }
    m->loaded = true;
    return true;
}

/* Stage only the affected logical pages. On any file error restore them, and
 * let the caller expose the existing NAND failure status/IRQ, never success. */
static bool nikon_nand_image_update(NikonNandImage *m, GHashTable *pages,
                                    uint32_t first, uint32_t count,
                                    uint32_t offset, size_t per_page,
                                    const uint8_t *input, bool erase)
{
    g_autofree uint8_t **before = g_new0(uint8_t *, count);
    g_autoptr(Error) error = NULL;
    for (unsigned i = 0; i < count; i++) {
        gpointer key = GUINT_TO_POINTER(first + i + 1);
        uint8_t *old = g_hash_table_lookup(pages, key);
        before[i] = old ? g_memdup2(old, Z9_NAND_PAGE) : NULL;
        if (erase) {
            g_hash_table_remove(pages, key);
        } else {
            if (!old) {
                old = g_malloc(Z9_NAND_PAGE);
                memset(old, 255, Z9_NAND_PAGE);
                g_hash_table_insert(pages, key, old);
            }
            for (size_t j = 0; j < per_page; j++) {
                old[offset + j] &= input[i * per_page + j];
            }
        }
    }
    bool ok = nikon_nand_image_commit(m, pages, &error);
    for (unsigned i = 0; i < count; i++) {
        if (!ok) {
            gpointer key = GUINT_TO_POINTER(first + i + 1);
            if (before[i]) {
                g_hash_table_replace(pages, key, before[i]);
                before[i] = NULL;
            } else {
                g_hash_table_remove(pages, key);
            }
        }
        g_free(before[i]);
    }
    if (!ok) {
        m->failed_commits++;
        error_report_err(g_steal_pointer(&error));
    }
    return ok;
}

static void nikon_nand_image_close(NikonNandImage *m)
{
    if (m->lock_open) {
        qemu_unlock_fd(m->lock_fd, 0, 1);
        close(m->lock_fd);
    }
    g_free(m->path);
}
