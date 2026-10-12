/* Recover an existing H700 gadget after the vendor OTG manager unbinds it.
 * Never select a USB role. Host/unknown roles and missing status fail closed.
 * Idle waits on netlink; USB transitions allow only a bounded settle window.
 */
#define _GNU_SOURCE
#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/netlink.h>
#include <poll.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#define SETTLE_MS 10000
#define RETRY_MS 250
static bool trace;

static const char *sys_root(void)
{
	const char *root = getenv("BASEOS_SYS_ROOT");
	return root && *root ? root : "/sys";
}

/* Read only known status attributes. In particular, usb_device, usb_host and
 * usb_null under usbc0 are READ TRIGGERS and must never be opened here. */
static bool read_status(const char *suffix, char *value, size_t size)
{
	char path[1024];
	int len = snprintf(path, sizeof(path), "%s/%s", sys_root(), suffix);
	if (len < 0 || (size_t)len >= sizeof(path))
		return false;
	int fd = open(path, O_RDONLY | O_CLOEXEC);
	if (fd < 0)
		return false;
	ssize_t n = read(fd, value, size - 1);
	close(fd);
	if (n < 0 || (size_t)n == size - 1)
		return false;
	while (n && isspace((unsigned char)value[n - 1]))
		n--;
	value[n] = '\0';
	return true;
}

static bool peripheral_role(void)
{
	char role[64];
	return read_status("devices/platform/soc/usbc0/otg_role", role, sizeof(role))
		&& !strcmp(role, "usb_device");
}

static bool gadget_unbound(void)
{
	char udc[128];
	return read_status("kernel/config/usb_gadget/g1/UDC", udc, sizeof(udc))
		&& !*udc;
}

/* This only restores the binding; it never recomposes functions, changes LUNs,
 * restarts adbd, or touches the role manager's controls. */
static void recover_gadget(void)
{
	char path[1024], controller[272] = "";
	if (!gadget_unbound() || !peripheral_role())
		return;
	snprintf(path, sizeof(path), "%s/class/udc", sys_root());
	DIR *dir = opendir(path);
	if (!dir)
		return;
	struct dirent *entry;
	while ((entry = readdir(dir))) {
		if (entry->d_name[0] == '.')
			continue;
		snprintf(controller, sizeof(controller), "%s\n", entry->d_name);
		break;
	}
	closedir(dir);
	if (!*controller || !peripheral_role() || !gadget_unbound())
		return;
	snprintf(path, sizeof(path), "%s/kernel/config/usb_gadget/g1/UDC", sys_root());
	int fd = open(path, O_WRONLY | O_CLOEXEC);
	if (fd < 0)
		return;
	size_t len = strlen(controller);
	bool restored = write(fd, controller, len) == (ssize_t)len;
	close(fd);
	if (trace)
		fprintf(stderr, "USB gadget rebind: %s\n", restored ? "ok" : "retry");
}

static bool event_has_field(const char *buf, size_t size, const char *field)
{
	for (size_t offset = 0; offset < size;) {
		size_t len = strnlen(buf + offset, size - offset);
		if (len == size - offset) /* Reject a truncated field. */
			return false;
		if (len == strlen(field) && !memcmp(buf + offset, field, len))
			return true;
		offset += len + 1;
	}
	return false;
}

static bool usb_event(const char *buf, size_t size)
{
	return event_has_field(buf, size, "SUBSYSTEM=android_usb")
		|| event_has_field(buf, size, "SUBSYSTEM=udc");
}

static bool recovery_event(const char *buf, size_t size)
{
	/* USB events may precede teardown. Power events are frequent, so only
	 * use them when an unbound gadget is already in peripheral role. */
	return usb_event(buf, size)
		|| (event_has_field(buf, size, "SUBSYSTEM=power_supply")
		    && gadget_unbound() && peripheral_role());
}

static long long recovery_deadline(long long deadline, long long now, bool new_transition)
{
	/* A late replug needs its own settling time. Events from a successful
	 * rebind arrive with a bound gadget and must not prolong this window. */
	return !deadline || new_transition ? now + SETTLE_MS : deadline;
}

static long long monotonic_ms(void)
{
	struct timespec t;
	clock_gettime(CLOCK_MONOTONIC, &t);
	return (long long)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}

static int event_socket(void)
{
	int fd = socket(AF_NETLINK, SOCK_DGRAM | SOCK_CLOEXEC, NETLINK_KOBJECT_UEVENT);
	if (fd < 0)
		return -1;
	struct sockaddr_nl addr = { .nl_family = AF_NETLINK, .nl_groups = 1 };
	int receive_buffer = 256 * 1024;
	setsockopt(fd, SOL_SOCKET, SO_RCVBUF, &receive_buffer, sizeof(receive_buffer));
	if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
		close(fd);
		return -1;
	}
	return fd;
}

int main(int argc, char **argv)
{
	if (argc == 2 && !strcmp(argv[1], "--once")) {
		recover_gadget();
		return 0;
	}
	if (argc == 2 && !strcmp(argv[1], "--trace"))
		trace = true;
	else if (argc != 1)
		return 2;

	/* Singleton across setup re-entry; the lock survives for this lifetime. */
	int lock = open("/run/usb-gadget-watch.lock", O_CREAT | O_RDWR | O_CLOEXEC, 0600);
	if (lock < 0 || flock(lock, LOCK_EX | LOCK_NB) < 0)
		return 1;
	int fd = event_socket();
	if (fd < 0)
		return 1;
	struct pollfd pfd = { .fd = fd, .events = POLLIN };
	/* Subscribe before checking state to cover setup/startup races. */
	long long deadline = gadget_unbound() ? monotonic_ms() + SETTLE_MS : 0;
	long long next_retry = 0;
	for (;;) {
		long long now = monotonic_ms();
		if (deadline && now >= deadline)
			deadline = 0;
		if (deadline && now >= next_retry) {
			recover_gadget();
			next_retry = now + RETRY_MS;
		}
		int timeout = deadline ? (int)((next_retry < deadline ? next_retry : deadline) - now) : -1;
		int result = poll(&pfd, 1, timeout);
		if (result < 0) {
			if (errno == EINTR)
				continue;
			return 1;
		}
		if (!result)
			continue;
		char buf[8192];
		struct sockaddr_nl source = {0};
		struct iovec iov = { .iov_base = buf, .iov_len = sizeof(buf) };
		struct msghdr msg = { .msg_name = &source, .msg_namelen = sizeof(source),
			.msg_iov = &iov, .msg_iovlen = 1 };
		ssize_t size = recvmsg(fd, &msg, 0);
		bool disconnected = false;
		if (size < 0) {
			if (errno == EINTR)
				continue;
			if (errno != ENOBUFS)
				return 1;
			/* Lost events: reconcile once with the same bounded window. */
		} else {
			if (!size || (msg.msg_flags & MSG_TRUNC) || source.nl_pid != 0)
				continue;
			if (!recovery_event(buf, (size_t)size))
				continue;
			disconnected = event_has_field(buf, (size_t)size, "USB_STATE=DISCONNECTED");
			if (trace) {
				for (ssize_t i = 0; i < size; i++)
					fputc(buf[i] ? buf[i] : ' ', stderr);
				fputc('\n', stderr);
			}
		}
		deadline = recovery_deadline(deadline, monotonic_ms(),
					     gadget_unbound() || disconnected);
		next_retry = 0;
	}
}
