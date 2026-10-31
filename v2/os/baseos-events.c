/*
 * baseos-events: BaseOS 2 event service (see v2/CONTRACT.md, "Events").
 *   baseos-events --daemon   system service: follows hardware changes, keeps
 *                            automatic audio routing current, serves listeners
 *   baseos-events            listener: prints one "name=value" line per change
 */
#include <errno.h>
#include <stdarg.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <linux/input.h>
#include <linux/netlink.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#define SOCK "/run/baseos/events.sock"
#define MAXL 16
#define BIT(a, n) ((a)[(n) / 8] & (1 << ((n) % 8)))

static int listeners[MAXL];

static void emit(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void emit(const char *fmt, ...)
{
	char line[1024];
	va_list ap;
	va_start(ap, fmt);
	int n = vsnprintf(line, sizeof(line) - 1, fmt, ap);
	va_end(ap);
	if (n < 0) return;
	if ((size_t)n >= sizeof(line) - 1) n = sizeof(line) - 2;
	line[n++] = '\n';
	for (int i = 0; i < MAXL; i++)
		if (listeners[i] > 0 && send(listeners[i], line, n, MSG_NOSIGNAL | MSG_DONTWAIT) < 0) {
			close(listeners[i]);
			listeners[i] = 0;
		}
}

static int read_int(const char *path)
{
	FILE *f = fopen(path, "r");
	int v = -1;
	if (f) { if (fscanf(f, "%d", &v) != 1) v = -1; fclose(f); }
	return v;
}

static void read_str(const char *path, char *buf, size_t len)
{
	FILE *f = fopen(path, "r");
	buf[0] = 0;
	if (f) { if (fgets(buf, len, f)) buf[strcspn(buf, "\n")] = 0; fclose(f); }
}

/* Re-resolve automatic audio routing; report the output if it changed. */
static void audio_update(void)
{
	static char last[32], last_bt[32];
	char now[32], bt[32];
	if (system("/usr/bin/baseos audio refresh 2>/dev/null"))
		return;
	read_str("/run/baseos/audio-resolved", now, sizeof(now));
	read_str("/run/baseos/bt-audio", bt, sizeof(bt));
	if (now[0] && (strcmp(now, last) || (!strcmp(now, "bluetooth") && strcmp(bt, last_bt)))) {
		strcpy(last, now);
		emit("audio_output=%s", now);
	}
	strcpy(last_bt, bt);
}

static int open_jack(void)
{
	for (int i = 0; i < 32; i++) {
		char path[64], name[64];
		snprintf(path, sizeof(path), "/sys/class/input/event%d/device/name", i);
		read_str(path, name, sizeof(name));
		if (!strstr(name, "Headphone Jack"))
			continue;
		snprintf(path, sizeof(path), "/dev/input/event%d", i);
		return open(path, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
	}
	return -1;
}

/* Radio worker snapshots contain no keys or passwords. */
static void radio_update(void)
{
	static char last_wifi[512], last_bt[512], last_audio[32];
	const char *paths[] = {"wifi-state", "bt-state", "bt-audio"};
	char *last[] = {last_wifi, last_bt, last_audio};
	for (int i = 0; i < 3; i++) {
		char path[80], now[512] = {0};
		snprintf(path, sizeof(path), "/run/baseos/%s", paths[i]);
		FILE *f = fopen(path, "r");
		if (!f) continue;
		size_t n = fread(now, 1, i == 2 ? 31 : sizeof(now) - 1, f);
		fclose(f);
		while (n && now[n - 1] == '\n') now[--n] = 0;
		for (size_t j = 0; j < n; j++) if (now[j] == '\n') now[j] = ' ';
		if (strcmp(now, last[i])) {
			strcpy(last[i], now);
			if (i == 2) audio_update();
			else if (now[0]) emit("%s", now);
		}
	}
}

static int daemon_main(void)
{
	int ue = socket(AF_NETLINK, SOCK_DGRAM | SOCK_CLOEXEC, NETLINK_KOBJECT_UEVENT);
	struct sockaddr_nl nl = { .nl_family = AF_NETLINK, .nl_groups = 1 };
	if (ue < 0 || bind(ue, (struct sockaddr *)&nl, sizeof(nl)))
		return perror("uevent"), 1;

	int srv = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
	struct sockaddr_un un = { .sun_family = AF_UNIX };
	strcpy(un.sun_path, SOCK);
	unlink(SOCK);
	if (srv < 0 || bind(srv, (struct sockaddr *)&un, sizeof(un)) || listen(srv, 4))
		return perror(SOCK), 1;
	chmod(SOCK, 0666);

	int jack = open_jack(), usb = -1, bat = -1;
	audio_update();
	for (;;) {
		struct pollfd p[3] = {{ue, POLLIN}, {srv, POLLIN}, {jack, POLLIN}};
		poll(p, jack >= 0 ? 3 : 2, 1000);
		radio_update();

		if (p[1].revents & POLLIN) {
			int c = accept4(srv, NULL, NULL, SOCK_CLOEXEC);
			for (int i = 0; c >= 0 && i < MAXL; i++)
				if (listeners[i] <= 0) { listeners[i] = c; c = -1; }
			if (c >= 0) close(c);
		}
		if (jack >= 0 && (p[2].revents & POLLIN)) {
			struct input_event ev;
			int changed = 0;
			while (read(jack, &ev, sizeof(ev)) == sizeof(ev))
				if (ev.type == EV_SW && ev.code == SW_HEADPHONE_INSERT) {
					emit("headphones=%d", ev.value);
					changed = 1;
				}
			if (changed) audio_update();
		}
		if (p[0].revents & POLLIN) {
			char buf[4096];
			ssize_t n = recv(ue, buf, sizeof(buf) - 1, 0);
			if (n > 0) {
				buf[n] = 0;
				int sound = 0;
				for (char *s = buf; s < buf + n; s += strlen(s) + 1)
					if (!strcmp(s, "SUBSYSTEM=sound")) sound = 1;
				if (sound) audio_update();
			}
		}
		/* Power supplies: on any wake-up, report what changed. */
		int u = read_int("/sys/class/power_supply/axp2202-usb/online");
		if (u >= 0 && u != usb) { if (usb >= 0) emit("usb_power=%d", u); usb = u; }
		int b = read_int("/sys/class/power_supply/axp2202-battery/capacity");
		if (b >= 0 && b != bat) {
			if (bat >= 0) emit("battery_percent=%d low=%d", b, b <= 10);
			bat = b;
		}
		if (jack < 0) jack = open_jack();
	}
}

int main(int argc, char **argv)
{
	if (argc > 1 && !strcmp(argv[1], "--daemon"))
		return daemon_main();
	int s = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
	struct sockaddr_un un = { .sun_family = AF_UNIX };
	strcpy(un.sun_path, SOCK);
	if (s < 0 || connect(s, (struct sockaddr *)&un, sizeof(un)))
		return perror(SOCK), 1;
	char buf[512];
	ssize_t n;
	setvbuf(stdout, NULL, _IOLBF, 0);
	while ((n = read(s, buf, sizeof(buf))) > 0)
		fwrite(buf, 1, n, stdout);
	return 0;
}
