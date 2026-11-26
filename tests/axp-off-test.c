/* Exercise the real program with an emulated adapter and mount table. */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/i2c.h>
#include <linux/i2c-dev.h>
#include <mntent.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <unistd.h>

static int adapter_ioctl(int fd, unsigned long request, ...);
static int device_open(const char *path, int flags, ...);
static ssize_t device_write(int fd, const void *buf, size_t count);
static FILE *mount_table(const char *path, const char *mode);
static FILE *swap_table(const char *path, const char *mode);
static int remount(const char *source, const char *target, const char *type,
		   unsigned long flags, const void *data);
static int storage_access(const char *path, int mode);
static unsigned int no_sleep(unsigned int seconds);
static void no_sync(void);

#define main axp_off_main
#define ioctl adapter_ioctl
#define open device_open
#define write device_write
#define setmntent mount_table
#define fopen swap_table
#define mount remount
#define access storage_access
#define sleep no_sleep
#define sync no_sync
#include "../src/axp-off.c"
#undef main
#undef ioctl
#undef open
#undef write
#undef setmntent
#undef fopen
#undef mount
#undef access
#undef sleep
#undef sync

static int rw[3], writes, reads, remounts, sleeps, syncs;
static int fail_mount, short_read, supported, usb_storage, swapping, empty_table;
static int ineffective_mount, write_error;
static unsigned char reg_value, written;
static const char *const points[] = { "/", "/data", "/mnt/sdcard" };

static int adapter_ioctl(int fd, unsigned long request, ...)
{
	assert(fd == 1000);
	va_list args;
	va_start(args, request);
	if (request == I2C_FUNCS) {
		*va_arg(args, unsigned long *) = supported ? I2C_FUNC_I2C : 0;
	} else if (request == I2C_SLAVE_FORCE) {
		assert(va_arg(args, unsigned) == 0x34);
	} else {
		assert(request == I2C_RDWR);
		struct i2c_rdwr_ioctl_data *t = va_arg(args, void *);
		assert(t->nmsgs == 2);
		assert(t->msgs[0].addr == 0x34 && t->msgs[0].flags == 0);
		assert(t->msgs[0].len == 1 && t->msgs[0].buf[0] == 0x27);
		assert(t->msgs[1].addr == 0x34 && t->msgs[1].flags == I2C_M_RD);
		assert(t->msgs[1].len == 1);
		t->msgs[1].buf[0] = reg_value;
		reads++;
		va_end(args);
		return short_read ? 1 : 2;
	}
	va_end(args);
	return 0;
}

static int device_open(const char *path, int flags, ...)
{
	if (!strcmp(path, "/dev/i2c-5"))
		return 1000;
	return open(path, flags);
}

static ssize_t device_write(int fd, const void *buf, size_t count)
{
	assert(fd == 1000 && count == 2); /* No separate register-select write. */
	const unsigned char *bytes = buf;
	assert(bytes[0] == 0x27);
	assert(!rw[0] && !rw[1] && !rw[2]);
	written = bytes[1];
	writes++;
	if (write_error) {
		errno = EIO;
		return -1;
	}
	return 2;
}

static FILE *mount_table(const char *path, const char *mode)
{
	assert(!strcmp(path, "/proc/mounts") && !strcmp(mode, "r"));
	FILE *f = tmpfile();
	assert(f);
	if (!empty_table) {
		for (int i = 0; i < 3; i++)
			fprintf(f, "/dev/mmcblk0p%d %s %s %s,noatime,nosuid,nodev%s 0 0\n",
				i + 5, points[i], i == 2 ? "vfat" : "ext4", rw[i] ? "rw" : "ro",
				i == 2 ? ",errors=remount-ro" : "");
		fputs("tmpfs /run tmpfs rw 0 0\nproc /proc proc rw 0 0\n"
		      "adb /dev/usb-ffs/adb functionfs rw 0 0\n", f);
	}
	rewind(f);
	return f;
}

static FILE *swap_table(const char *path, const char *mode)
{
	if (strcmp(path, "/proc/swaps"))
		return fopen(path, mode);
	FILE *f = tmpfile();
	assert(f);
	fputs("Filename Type Size Used Priority\n", f);
	if (swapping)
		fputs("/data/swap file 1024 512 -2\n", f);
	rewind(f);
	return f;
}

static int remount(const char *source, const char *target, const char *type,
		   unsigned long flags, const void *data)
{
	assert(!source && !type && !data);
	assert(flags == (MS_REMOUNT | MS_RDONLY | MS_NOATIME | MS_NOSUID | MS_NODEV));
	for (int i = 0; i < 3; i++) {
		if (strcmp(target, points[i]))
			continue;
		remounts++;
		if (i == fail_mount) {
			errno = EBUSY;
			return -1;
		}
		if (!ineffective_mount)
			rw[i] = 0;
		return 0;
	}
	assert(!"attempted to remount a virtual filesystem");
	return -1;
}

static int storage_access(const char *path, int mode)
{
	assert(!strcmp(path, "/run/usb-storage-device") && mode == F_OK);
	return usb_storage ? 0 : -1;
}
static unsigned int no_sleep(unsigned int seconds)
{
	assert(seconds == 5);
	sleeps++;
	return 0;
}
static void no_sync(void) { syncs++; }

static void reset(void)
{
	for (int i = 0; i < 3; i++) rw[i] = 1;
	writes = reads = remounts = sleeps = syncs = 0;
	short_read = usb_storage = swapping = empty_table = 0;
	ineffective_mount = write_error = 0;
	fail_mount = -1;
	supported = 1;
	reg_value = 0x08;
}

static int run(int armed)
{
	char *args[] = { "axp-off", "--now", NULL };
	return axp_off_main(armed ? 2 : 1, args);
}

int main(void)
{
	struct mntent options = { .mnt_opts = "rw,dirsync,errors=remount-ro" };
	assert(!mount_option(&options, "ro"));
	assert(mount_option(&options, "rw"));
	assert(!mount_option(&options, "sync"));
	assert(readonly_flags(&options) == (MS_REMOUNT | MS_RDONLY | MS_DIRSYNC));
	options.mnt_opts = "ro,noatime,nosuid,nodev,sync";
	assert(mount_option(&options, "ro"));
	assert(readonly_flags(&options) == (MS_REMOUNT | MS_RDONLY | MS_NOATIME |
		MS_NOSUID | MS_NODEV | MS_SYNCHRONOUS));
	options.mnt_opts = "xro,rofoo,errors=remount-ro,async,nosuidfoo,xnodev";
	assert(!mount_option(&options, "ro"));
	assert(!mount_option(&options, "sync"));
	assert(readonly_flags(&options) == (MS_REMOUNT | MS_RDONLY));
	options.mnt_opts = "";
	assert(!mount_option(&options, "ro"));
	options.mnt_opts = ",,rw,,ro,";
	assert(mount_option(&options, "ro"));

	assert(setenv("BASEOS_SYS_ROOT", "/tmp/axp-fixture", 1) == 0);
	reset();
	assert(run(0) == 0 && reads == 1 && writes == 0 && remounts == 0 && syncs == 0);
	/* An exact ro option needs no remount. An rw FAT mount whose error policy
	 * contains "ro" must still be remounted, and must fail the verification
	 * pass if a reported successful remount did not actually change it. */
	reset();
	for (int i = 0; i < 3; i++) rw[i] = 0;
	assert(run(1) == 1 && writes == 1 && remounts == 0);
	reset();
	rw[0] = rw[1] = 0;
	assert(readonly_mounts("/proc/mounts", 0) == -1 && remounts == 0);
	assert(readonly_mounts("/proc/mounts", 1) == 0 && remounts == 1 && !rw[2]);
	assert(readonly_mounts("/proc/mounts", 0) == 0);
	for (unsigned value = 0; value < 16; value++) {
		reset(); reg_value = value;
		assert(run(1) == 1); /* The emulated PMIC leaves the process alive. */
		assert(writes == 1 && written == ((value & ~2) | 1));
		assert(remounts == 3 && sleeps == 1);
	}
	for (int i = 0; i < 3; i++) {
		reset(); fail_mount = i;
		assert(run(1) == 1 && writes == 0 && rw[i]);
	}
	reset(); short_read = 1;
	assert(run(1) == 1 && writes == 0 && remounts == 0 && errno == EIO);
	reset(); supported = 0;
	assert(run(1) == 1 && writes == 0 && reads == 0 && remounts == 0);
	reset(); usb_storage = 1;
	assert(run(1) == 1 && writes == 0 && remounts == 0);
	reset(); swapping = 1;
	assert(run(1) == 1 && writes == 0 && remounts == 0);
	reset(); empty_table = 1;
	assert(run(1) == 1 && writes == 0);
	reset(); ineffective_mount = 1;
	assert(run(1) == 1 && writes == 0 && remounts == 3);
	reset(); write_error = 1;
	assert(run(1) == 1 && writes == 1 && sleeps == 1);
	return 0;
}
