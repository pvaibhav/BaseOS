/* AXP2202 (AXP717 register map) soft poweroff. Keep the kernel driver bound;
 * identify its client before touching the single supported register, 0x27.
 * Without --now this is a read-only probe. See docs/05-runtime-power-network.md.
 */
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/i2c.h>
#include <linux/i2c-dev.h>
#include <mntent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mount.h>
#include <unistd.h>

#define DRIVER_SUBPATH  "/bus/i2c/drivers/axp20x-i2c"
#define EXPECT_ADDR     0x34
#define REG             0x27   /* the only register this program will ever touch */

#define SOFT_PWROFF   (1 << 0)
#define RESTART       (1 << 1)   /* masked off every write — never set this */

/* Kernel/DT identification, not a silicon ID. Only add measured parts. */
static const char *const KNOWN_PARTS[] = { "axp2202" };

static void read_part(const char *dir, const char *entry, char *part, size_t plen)
{
	char path[1024];
	ssize_t n;
	int fd;

	*part = '\0';
	snprintf(path, sizeof path, "%s/%s/name", dir, entry);
	if ((fd = open(path, O_RDONLY)) < 0)
		return;
	n = read(fd, part, plen - 1);
	close(fd);
	if (n <= 0)
		return;
	part[n] = '\0';
	part[strcspn(part, " \t\r\n")] = '\0';
}

static int find_pmic(const char *sysroot, unsigned *bus, unsigned *addr,
		     char *whence, size_t wlen, char *part, size_t plen)
{
	char dir[512];
	DIR *d;
	struct dirent *e;
	unsigned b, a;
	char tail;
	int found = 0;

	snprintf(dir, sizeof dir, "%s%s", sysroot, DRIVER_SUBPATH);
	if ((d = opendir(dir)) == NULL)
		return -1;
	while ((e = readdir(d)) != NULL) {
		if (sscanf(e->d_name, "%u-%4x%c", &b, &a, &tail) != 2)
			continue;
		if (found && a != EXPECT_ADDR)
			continue;
		*bus = b;
		*addr = a;
		snprintf(whence, wlen, "axp20x-i2c/%s", e->d_name);
		read_part(dir, e->d_name, part, plen);
		found = 1;
		if (a == EXPECT_ADDR)
			break;
	}
	closedir(d);
	return found ? 0 : -1;
}

static int part_is_known(const char *part)
{
	size_t i;

	for (i = 0; i < sizeof KNOWN_PARTS / sizeof KNOWN_PARTS[0]; i++)
		if (strcmp(part, KNOWN_PARTS[i]) == 0)
			return 1;
	return 0;
}

/* One adapter-locked transfer: a kernel regmap access must not change the
 * register pointer between selecting REG and reading its value. */
static int rd(int fd, unsigned char *out)
{
	unsigned char reg = REG;
	struct i2c_msg msgs[] = {
		{ .addr = EXPECT_ADDR, .len = 1, .buf = &reg },
		{ .addr = EXPECT_ADDR, .flags = I2C_M_RD, .len = 1, .buf = out },
	};
	struct i2c_rdwr_ioctl_data transfer = { .msgs = msgs, .nmsgs = 2 };
	int n = ioctl(fd, I2C_RDWR, &transfer);

	if (n == 2)
		return 0;
	if (n >= 0)
		errno = EIO;
	return -1;
}

static int volatile_fs(const char *type)
{
	static const char *const types[] = {
		"proc", "sysfs", "tmpfs", "devtmpfs", "devpts", "debugfs",
		"configfs", "cgroup", "cgroup2", "ramfs", "rootfs", "securityfs",
		"pstore", "tracefs", "fusectl", "mqueue", "hugetlbfs", "binfmt_misc",
		"functionfs"
	};
	for (size_t i = 0; i < sizeof types / sizeof types[0]; i++)
		if (!strcmp(type, types[i]))
			return 1;
	return 0;
}

/* musl's hasmntopt() also matches substrings: "ro" matches the FAT option
 * "errors=remount-ro" and "sync" matches "dirsync". Only whole comma-separated
 * options describe the VFS mount flags that we must preserve or verify. */
static int mount_option(const struct mntent *m, const char *option)
{
	const char *p = m->mnt_opts;
	size_t len = strlen(option);

	while (*p) {
		size_t n = strcspn(p, ",");
		if (n == len && !strncmp(p, option, len))
			return 1;
		p += n;
		if (*p)
			p++;
	}
	return 0;
}

/* Preserve VFS options when making a mount read-only. Filesystem-specific
 * options remain unchanged with a NULL remount data argument. */
static unsigned long readonly_flags(struct mntent *m)
{
	unsigned long flags = MS_REMOUNT | MS_RDONLY;
	static const struct { const char *name; unsigned long flag; } options[] = {
		{ "nosuid", MS_NOSUID }, { "nodev", MS_NODEV }, { "noexec", MS_NOEXEC },
		{ "sync", MS_SYNCHRONOUS }, { "dirsync", MS_DIRSYNC },
		{ "noatime", MS_NOATIME }, { "nodiratime", MS_NODIRATIME },
		{ "relatime", MS_RELATIME }, { "mand", MS_MANDLOCK }
	};
	for (size_t i = 0; i < sizeof options / sizeof options[0]; i++)
		if (mount_option(m, options[i].name))
			flags |= options[i].flag;
	return flags;
}

static int readonly_mounts(const char *path, int prepare)
{
	FILE *f = setmntent(path, "r");
	struct mntent *m;
	int root_seen = 0, failed = 0;

	if (!f)
		return -1;
	while ((m = getmntent(f))) {
		if (!strcmp(m->mnt_dir, "/"))
			root_seen = 1;
		if (volatile_fs(m->mnt_type) || mount_option(m, "ro"))
			continue;
		if (!prepare || mount(NULL, m->mnt_dir, NULL, readonly_flags(m), NULL)) {
			fprintf(stderr, "axp-off: %s is not safely read-only; refusing power cut\n",
				m->mnt_dir);
			failed = 1;
		}
	}
	failed |= ferror(f) || !root_seen;
	endmntent(f);
	return failed ? -1 : 0;
}

static int no_swap(const char *path)
{
	FILE *f = fopen(path, "r");
	char line[512];
	int safe;

	if (!f)
		return 0;
	safe = fgets(line, sizeof line, f) != NULL;
	while (fgets(line, sizeof line, f))
		if (strspn(line, " \t\r\n") != strlen(line))
			safe = 0;
	safe &= !ferror(f);
	fclose(f);
	return safe;
}

static int prepare_poweroff(void)
{
	/* USB mass storage can write an unmounted block device from kernel space.
	 * Let the normal kernel shutdown disconnect and drain that gadget. */
	if (access("/run/usb-storage-device", F_OK) == 0 || !no_swap("/proc/swaps")) {
		fprintf(stderr, "axp-off: active USB storage or swap; using kernel shutdown\n");
		return -1;
	}
	sync();
	/* Includes the rootfs: the vendor initramfs mounts it writable. Do not
	 * trust sync alone, or a failed umount/remount, before cutting the rails. */
	if (readonly_mounts("/proc/mounts", 1))
		return -1;
	return readonly_mounts("/proc/mounts", 0);
}

int main(int argc, char **argv)
{
	const char *sysroot;
	char bus_path[512], whence[128], part[64];
	unsigned bus = 0, addr = 0;
	int fd, i, arm = 0;
	unsigned long funcs;
	unsigned char cur, val;

	for (i = 1; i < argc; i++) {
		if (!strcmp(argv[i], "--now"))
			arm = 1;
		else {
			fprintf(stderr, "usage: axp-off [--now]\n");
			return 2;
		}
	}

	sysroot = getenv("BASEOS_SYS_ROOT");
	if (sysroot == NULL || *sysroot == '\0')
		sysroot = "/sys";

	if (find_pmic(sysroot, &bus, &addr, whence, sizeof whence, part, sizeof part)) {
		fprintf(stderr, "axp-off: no axp20x-i2c client under %s%s\n",
			sysroot, DRIVER_SUBPATH);
		return 1;
	}
	if (addr != EXPECT_ADDR) {
		fprintf(stderr, "axp-off: %s sits at 0x%02x, not the expected 0x%02x; refusing\n",
			whence, addr, EXPECT_ADDR);
		return 1;
	}
	if (!part_is_known(part)) {
		if (*part == '\0')
			fprintf(stderr, "axp-off: %s publishes no readable name; refusing\n",
				whence);
		else
			fprintf(stderr, "axp-off: %s is a \"%s\", not a part this has been measured on; refusing\n",
				whence, part);
		return 1;
	}
	snprintf(bus_path, sizeof bus_path, "/dev/i2c-%u", bus);
	printf("axp-off: PMIC %s at %s addr 0x%02x (%s)\n", part, bus_path, addr, whence);

	if ((fd = open(bus_path, O_RDWR)) < 0) {
		fprintf(stderr, "axp-off: open %s: %s\n", bus_path, strerror(errno));
		return 1;
	}
	if (ioctl(fd, I2C_FUNCS, &funcs) < 0 || !(funcs & I2C_FUNC_I2C)) {
		fprintf(stderr, "axp-off: adapter does not support combined I2C transfers\n");
		return 1;
	}
	if (ioctl(fd, I2C_SLAVE_FORCE, addr) < 0) {
		fprintf(stderr, "axp-off: I2C_SLAVE_FORCE 0x%02x: %s\n", addr, strerror(errno));
		return 1;
	}
	if (rd(fd, &cur)) {
		fprintf(stderr, "axp-off: read REG27H: %s\n", strerror(errno));
		return 1;
	}

	val = (unsigned char)((cur & ~RESTART) | SOFT_PWROFF);

	printf("axp-off: REG27H 0x%02x -> 0x%02x\n", cur, val);
	if (!arm) {
		printf("axp-off: not armed (pass --now to cut power)\n");
		return 0;
	}

	if (prepare_poweroff())
		return 1;
	/* Filesystem cleanup may take time; preserve the current control bits. */
	if (rd(fd, &cur)) {
		fprintf(stderr, "axp-off: final REG27H read failed; refusing power cut\n");
		return 1;
	}
	val = (unsigned char)((cur & ~RESTART) | SOFT_PWROFF);
	fflush(stdout);

	if (write(fd, (unsigned char[]){ REG, val }, 2) != 2)
		fprintf(stderr, "axp-off: write REG27H: %s\n", strerror(errno));

	/* Allow the write to take effect before the caller can restore writable
	 * mounts or enter kernel shutdown, even if the adapter reported an error.
	 * There is no retry of a power command. */
	sleep(5);
	fprintf(stderr, "axp-off: still running after poweroff request\n");
	return 1;
}
