/* baseos-prefetch: make the next boot read from the card in big batches.
 *
 * The card is slow at small scattered reads (FAT folders are read 512 bytes at
 * a time, and a cold walk of a ROM collection takes seconds), but it sits idle
 * for most of the first seconds of boot. So after each boot we note which
 * parts of the card the system read, and early in the next boot we ask the
 * kernel to read those parts in the background, before anyone needs them.
 *
 *   baseos-prefetch record LIST DEVICE[:START:LENGTH]...
 *       Writes to LIST the ranges of each device (or byte range of it) that
 *       are in the page cache now.
 *   baseos-prefetch replay LIST
 *       Starts background reads of those ranges.
 *
 * Only block devices themselves are covered: file system metadata (FAT
 * folders and tables) and the loop-mounted root image. Both are hints: a stale
 * list only costs some reads. */
#define _GNU_SOURCE
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <linux/fs.h>
#include <unistd.h>

#ifndef __NR_cachestat
#define __NR_cachestat 451
#endif

#define GRAIN (64 << 10)    /* smallest range we track */
#define JOIN (256 << 10)    /* gaps smaller than this are read too: one request is cheaper */
#define BUDGET (48 << 20)   /* most we ever read ahead */

struct cs_range { uint64_t off, len; };
struct cs { uint64_t nr_cache, nr_dirty, nr_writeback, nr_evicted, nr_recently_evicted; };

static FILE *out;
static const char *dev;
static uint64_t run_start, run_end, total;

static uint64_t cached_pages(int fd, uint64_t off, uint64_t len)
{
	struct cs_range r = { off, len };
	struct cs s;
	if (syscall(__NR_cachestat, fd, &r, &s, 0))
		return 0;
	return s.nr_cache;
}

static void emit(uint64_t off, uint64_t len)
{
	if (run_end && off <= run_end + JOIN) {
		run_end = off + len;
		return;
	}
	if (run_end && total + (run_end - run_start) <= BUDGET) {
		fprintf(out, "%s %llu %llu\n", dev, (unsigned long long)run_start,
			(unsigned long long)(run_end - run_start));
		total += run_end - run_start;
	}
	run_start = off;
	run_end = off + len;
}

/* Without the cachestat system call: map the device piece by piece and ask
 * which pages are in memory (one lookup per page, so slower). */
static void scan_mincore(int fd, uint64_t off, uint64_t len)
{
	const uint64_t chunk = 256 << 20;
	static unsigned char vec[(256 << 20) / 4096];
	for (uint64_t at = off; at < off + len; at += chunk) {
		uint64_t n = off + len - at < chunk ? off + len - at : chunk;
		void *p = mmap(0, n, PROT_READ, MAP_SHARED, fd, at);
		if (p == MAP_FAILED)
			return;
		if (!mincore(p, n, vec))
			for (uint64_t i = 0; i < n / 4096; i++)
				if (vec[i] & 1)
					emit(at + i * 4096, 4096);
		munmap(p, n);
	}
}

/* Splits the range in halves until each part is either empty or small. */
static void scan(int fd, uint64_t off, uint64_t len)
{
	uint64_t pages = cached_pages(fd, off, len);
	if (!pages)
		return;
	if (len <= GRAIN || pages * 4096 >= len) {
		emit(off, len);
		return;
	}
	uint64_t half = (len / 2 + GRAIN - 1) / GRAIN * GRAIN;
	scan(fd, off, half);
	scan(fd, off + half, len - half);
}

static int record(const char *list, int n, char **targets)
{
	char tmp[256];
	snprintf(tmp, sizeof(tmp), "%s.new", list);
	out = fopen(tmp, "w");
	if (!out)
		return 1;
	for (int i = 0; i < n; i++) {
		char path[128];
		unsigned long long start = 0, len = 0;
		if (sscanf(targets[i], "%127[^:]:%llu:%llu", path, &start, &len) < 1)
			return 2;
		int fd = open(path, O_RDONLY | O_CLOEXEC);
		if (fd < 0)
			continue;
		if (!len) {
			uint64_t size = 0;
			ioctl(fd, BLKGETSIZE64, &size);
			len = size;
		}
		dev = path;
		run_start = run_end = 0;
		struct cs_range r = { start, 4096 };
		struct cs st;
		if (syscall(__NR_cachestat, fd, &r, &st, 0) == 0)
			scan(fd, start, len);
		else
			scan_mincore(fd, start, len);
		emit(UINT64_MAX / 2, 0); /* flush the last run */
		close(fd);
	}
	if (fclose(out) || rename(tmp, list))
		return 1;
	return 0;
}

static int replay(const char *list)
{
	FILE *f = fopen(list, "r");
	if (!f)
		return 1;
	char path[128], open_path[128] = "";
	unsigned long long off, len;
	int fd = -1;
	while (fscanf(f, "%127s %llu %llu", path, &off, &len) == 3) {
		if (strcmp(path, open_path)) {
			if (fd >= 0)
				close(fd);
			fd = open(path, O_RDONLY | O_CLOEXEC);
			snprintf(open_path, sizeof(open_path), "%s", path);
		}
		if (fd >= 0)
			posix_fadvise(fd, off, len, POSIX_FADV_WILLNEED);
	}
	fclose(f);
	return 0;
}

int main(int argc, char **argv)
{
	if (argc >= 4 && !strcmp(argv[1], "record"))
		return record(argv[2], argc - 3, argv + 3);
	if (argc == 3 && !strcmp(argv[1], "replay"))
		return replay(argv[2]);
	fprintf(stderr, "usage: baseos-prefetch record LIST DEVICE[:START:LENGTH]...\n"
			"       baseos-prefetch replay LIST\n");
	return 2;
}
