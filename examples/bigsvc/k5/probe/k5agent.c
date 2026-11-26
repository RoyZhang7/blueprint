// SPDX-License-Identifier: GPL-2.0
// Phase K5 (RESULTS-K 5, items A1-A3): the node agent. One process per node, pinned off the victims' CPUs. Each module
// has its own switch, so the overhead ladder can run any one of them alone:
//   probe    loads k5probe.bpf.o with the chosen programs and flags. Per tick one record per victim cgroup, from the ring
//            buffer the probe's 100 ms bpf_timer fills (--timer 1), or by reading and folding the per-CPU maps here
//            (--timer 0, the map-read path of the K1/K4 lean probe, kept as the fallback). Counters are cumulative in
//            the kernel; both paths difference them.
//   perf     cycles, instructions and LLC-load-misses in cgroup mode (PERF_FLAG_PID_CGROUP), one group per cgroup and
//            CPU (a victim's CPUs, or the cgroup's cpus= list, or every online CPU). --perf 1 arms them; --perf-read 1
//            reads them every tick -> CPI and MPKI per cgroup per window.
//   resctrl  one monitor group per cgroup (k5-<name>), its threads (cgroup.threads of the cgroup and its children)
//            written to the group's tasks file and re-scanned every --rescan-s. --resctrl 1 assigns the RMIDs;
//            --resctrl-read 1 reads llc_occupancy (and mbm_total_bytes where the CPU has MBM) every tick.
//   sampler  cpu.stat and cpu.pressure of each cgroup, and the /proc/stat lines of the victims' CPUs, every tick.
// Output (--out): a "start" record, one line per tick {"t","k","cg":[...],"perf":{..},"cmt":{..},"mbm":{..},"smp":{..},
// "pst":{..}}, a "self" record every second (own CPU ns, RSS, BPF program run time and count, BPF map memory, the
// probe's tick sequence and ring-buffer misses) and an "end" record. resctrl's groups are removed at exit.
//
// usage: k5agent --out FILE [--obj k5probe.bpf.o] [--tick-ms 100] [--secs N] [--cpu N]
//          --cg NAME=PATH[,victim=CPUS][,cpus=CPUS]  ...  (victims first; at most 8; PATH = a cgroup directory: a pod
//                                                          slice and its container scopes are one slot)
//          [--probe switch[,wakeup][,softirq]] [--rq 1] [--sib 0] [--timer 0] [--rd 0]
//          [--perf 0] [--perf-read 1] [--perf-cg A,B]  [--resctrl 0] [--resctrl-read 1] [--resctrl-cg A,B] [--rescan-s 10]
//          [--perf-td 1] (Phase I step 4: three raw stall counters after cycles, instructions, LLC misses in each
//          cgroup's group, Ice Lake server encodings: CYCLE_ACTIVITY.STALLS_TOTAL 0xA3/0x04 cmask 4, .STALLS_MEM_ANY
//          0xA3/0x14 cmask 20, .STALLS_L3_MISS 0xA3/0x06 cmask 6; "perf" then carries six values: memory-bound share
//          = mem_any / cycles, DRAM-bound = l3_miss / cycles, core-bound = (total - mem_any) / cycles)
//          [--rdt-parent NAME=DIR[,NAME=DIR]] (Phase I step 5: this cgroup's mon group is made under that resctrl ctrl
//          group and its tasks are written to the ctrl group first; the ctrl group and its schemata are k5ctl's)
//          [--mhz-cpus 1+3] (Phase I: APERF/MPERF of these CPUs each tick through /dev/cpu/N/msr, "mhz":{"cpu":[MHz, C0 %]};
//          replaces the recorders' per-window clock read, which falls back to /proc/cpuinfo when there is no cpufreq
//          driver and then costs ~3 ms of the victim's CPU per window on a 144-CPU node, RESULTS-I 4)
//          [--sampler 0] [--sampler-cg A,B]
#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/perf_event.h>
#include <sched.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/resource.h>
#include <time.h>
#include <unistd.h>
#include <bpf/bpf.h>
#include <bpf/libbpf.h>
#include "k5probe.h"

typedef unsigned long long u64;
#define MAXCG NCG
#define MAXCPUS 256

static volatile int stop;
static void on_sig(int s) { (void)s; stop = 1; }

static u64 mono_ns(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return (u64)t.tv_sec * 1000000000ULL + t.tv_nsec; }
static u64 wall_ms(void) { struct timespec t; clock_gettime(CLOCK_REALTIME, &t); return (u64)t.tv_sec * 1000 + t.tv_nsec / 1000000; }

static int parse_cpus(const char *s, int *out, int max)
{
	int n = 0; char *d = strdup(s), *tok, *save;
	for (tok = strtok_r(d, ":;", &save); tok; tok = strtok_r(NULL, ":;", &save)) {
		char *t2, *s2;
		for (t2 = strtok_r(tok, "+ ", &s2); t2; t2 = strtok_r(NULL, "+ ", &s2)) {
			int lo, hi; if (sscanf(t2, "%d-%d", &lo, &hi) != 2) hi = lo = atoi(t2);
			for (int c = lo; c <= hi && n < max; c++) out[n++] = c;
		}
	}
	free(d); return n;
}
// cpu lists inside --cg use '+' or ':' between items because ',' separates the options ("victim=1+3" or "victim=1-3")

struct cgc {
	char name[64], path[512];
	int victim, cpus[MAXCPUS], ncpus;
	u64 ids[NID]; int nids;
	// perf
	int perf, nfd, lead[MAXCPUS], nev;
	u64 pv[MAXCPUS][10];            // last cumulative per CPU: cycles, instructions, llc misses, [3 stall counters, ref-cycles, llc refs,] enabled, running
	u64 ptot[8], ptot_prev[8]; int pfirst;
	u64 pen, prun, pen_prev, prun_prev;   // J1: time_enabled / time_running summed over the CPUs (multiplexing check)
	// resctrl
	int rdt, rdt_tasks_fd, ndom, occ_fd[8], mbm_fd[8]; char rdt_dir[600]; u64 mbm_prev[8]; int mbm_first;
	int rdt_parent_fd;              // --rdt-parent: the ctrl group's tasks file (a task joins it before its mon group)
	// sampler
	int smp, stat_fd, psi_fd; u64 sv_prev[8]; int sfirst;
};
static struct cgc CG[MAXCG]; static int ncg, nvict;

static void add_ids(struct cgc *g, const char *dir, int depth)
{
	struct stat st;
	if (stat(dir, &st) == 0 && g->nids < NID) g->ids[g->nids++] = (u64)st.st_ino;
	if (depth <= 0) return;
	DIR *d = opendir(dir); if (!d) return;
	struct dirent *e;
	while ((e = readdir(d))) {
		if (e->d_type != DT_DIR || e->d_name[0] == '.') continue;
		char p[1024]; snprintf(p, sizeof(p), "%s/%s", dir, e->d_name);
		add_ids(g, p, depth - 1);
	}
	closedir(d);
}

static int online_cpus(int *out)
{
	FILE *f = fopen("/sys/devices/system/cpu/online", "r"); char b[256] = "0";
	if (f) { if (!fgets(b, sizeof(b), f)) strcpy(b, "0"); fclose(f); }
	b[strcspn(b, "\n")] = 0;
	for (char *p = b; *p; p++) if (*p == ',') *p = '+';
	return parse_cpus(b, out, MAXCPUS);
}

static int sibling_of(int c)
{
	char p[128], b[128]; snprintf(p, sizeof(p), "/sys/devices/system/cpu/cpu%d/topology/thread_siblings_list", c);
	FILE *f = fopen(p, "r"); if (!f) return -1;
	if (!fgets(b, sizeof(b), f)) { fclose(f); return -1; }
	fclose(f); b[strcspn(b, "\n")] = 0;
	for (char *q = b; *q; q++) if (*q == ',') *q = '+';
	int l[8], n = parse_cpus(b, l, 8);
	for (int i = 0; i < n; i++) if (l[i] != c) return l[i];
	return -1;
}

static int in_list(const char *list, const char *name)
{
	if (!list) return 1;
	char *d = strdup(list), *tok, *save; int hit = 0;
	for (tok = strtok_r(d, ",", &save); tok; tok = strtok_r(NULL, ",", &save)) if (!strcmp(tok, name)) hit = 1;
	free(d); return hit;
}

static u64 read_u64_fd(int fd) { char b[64]; ssize_t n = pread(fd, b, sizeof(b) - 1, 0); if (n <= 0) return 0; b[n] = 0; return strtoull(b, NULL, 10); }

// ---------------------------------------------------------------- perf
static long perf_open(struct perf_event_attr *a, int pid, int cpu, int group, unsigned long flags) { return syscall(SYS_perf_event_open, a, pid, cpu, group, flags); }

static int perf_td;   // --perf-td: the three stall counters (Phase I step 4)
static int perf_ref;  // --perf-ref (J1 row 9): ref-cycles and LLC references after the stall counters: 8 events, 6 programmable + 2 fixed
static void perf_setup(struct cgc *g, FILE *log)
{
	int cgfd = open(g->path, O_RDONLY | O_CLOEXEC);
	if (cgfd < 0) { fprintf(log, "perf: open %s: %s\n", g->path, strerror(errno)); return; }
	u64 cfgs[8][2] = { { PERF_TYPE_HARDWARE, PERF_COUNT_HW_CPU_CYCLES }, { PERF_TYPE_HARDWARE, PERF_COUNT_HW_INSTRUCTIONS },
		{ PERF_TYPE_HW_CACHE, PERF_COUNT_HW_CACHE_LL | (PERF_COUNT_HW_CACHE_OP_READ << 8) | (PERF_COUNT_HW_CACHE_RESULT_MISS << 16) },
		{ PERF_TYPE_RAW, 0xA3 | (0x04 << 8) | (4ULL << 24) }, { PERF_TYPE_RAW, 0xA3 | (0x14 << 8) | (20ULL << 24) },
		{ PERF_TYPE_RAW, 0xA3 | (0x06 << 8) | (6ULL << 24) },
		{ PERF_TYPE_HARDWARE, PERF_COUNT_HW_REF_CPU_CYCLES },
		{ PERF_TYPE_HW_CACHE, PERF_COUNT_HW_CACHE_LL | (PERF_COUNT_HW_CACHE_OP_READ << 8) | (PERF_COUNT_HW_CACHE_RESULT_ACCESS << 16) } };
	int want = perf_ref ? 8 : (perf_td ? 6 : 3);
	g->nev = want; g->nfd = 0;
	for (int i = 0; i < g->ncpus; i++) {
		int lead = -1;
		for (int e = 0; e < want; e++) {
			struct perf_event_attr a; memset(&a, 0, sizeof(a));
			a.size = sizeof(a); a.type = (unsigned)cfgs[e][0]; a.config = cfgs[e][1];
			a.read_format = PERF_FORMAT_GROUP | PERF_FORMAT_TOTAL_TIME_ENABLED | PERF_FORMAT_TOTAL_TIME_RUNNING;
			a.disabled = e == 0;
			long fd = perf_open(&a, cgfd, g->cpus[i], lead, PERF_FLAG_PID_CGROUP | PERF_FLAG_FD_CLOEXEC);
			if (fd < 0) {
				if (e == 0) { fprintf(log, "perf: %s cpu %d cycles: %s\n", g->name, g->cpus[i], strerror(errno)); break; }
				if (e == 2) { fprintf(log, "perf: %s cpu %d LLC-load-misses: %s (two events)\n", g->name, g->cpus[i], strerror(errno)); g->nev = 2; continue; }
				if (e >= 6) { fprintf(log, "perf: %s cpu %d event %d (ref-cycles / LLC refs): %s\n", g->name, g->cpus[i], e, strerror(errno)); g->nev = e; break; }
				if (e >= 3) { fprintf(log, "perf: %s cpu %d stall counter %d: %s (no stall columns)\n", g->name, g->cpus[i], e, strerror(errno)); g->nev = 3; break; }
				fprintf(log, "perf: %s cpu %d event %d: %s\n", g->name, g->cpus[i], e, strerror(errno)); break;
			}
			if (e == 0) lead = (int)fd;
		}
		g->lead[i] = lead;
		if (lead >= 0) { ioctl(lead, PERF_EVENT_IOC_ENABLE, PERF_IOC_FLAG_GROUP); g->nfd++; }
	}
	close(cgfd);
	g->perf = g->nfd > 0; g->pfirst = 1;
	fprintf(log, "perf: %s armed on %d of %d CPUs, %d events\n", g->name, g->nfd, g->ncpus, g->nev);
}

static void perf_read(struct cgc *g)
{
	u64 tot[8] = {0, 0, 0, 0, 0, 0, 0, 0}, en_t = 0, run_t = 0;
	for (int i = 0; i < g->ncpus; i++) {
		if (g->lead[i] < 0) continue;
		u64 b[3 + 8]; ssize_t n = read(g->lead[i], b, sizeof(b));
		if (n < (ssize_t)(3 * 8)) continue;
		u64 nr = b[0], en = b[1], run = b[2];
		for (u64 e = 0; e < nr && e < 8; e++) {
			u64 v = b[3 + e];
			if (run && run < en) v = (u64)((double)v * (double)en / (double)run);   // multiplexed: scale
			g->pv[i][e] = v;
		}
		g->pv[i][8] = en; g->pv[i][9] = run; en_t += en; run_t += run;
		for (u64 e = 0; e < nr && e < 8; e++) tot[e] += g->pv[i][e];
	}
	for (int e = 0; e < 8; e++) { g->ptot_prev[e] = g->ptot[e]; g->ptot[e] = tot[e]; }
	g->pen_prev = g->pen; g->prun_prev = g->prun; g->pen = en_t; g->prun = run_t;
}

// ---------------------------------------------------------------- J1: the clock from per-CPU perf events (cycles / ref-cycles), msr retired
static int clk_cpu[MAXCPUS], clk_fd[MAXCPUS], nclk; static u64 clk_prev[MAXCPUS][2], clk_cur[MAXCPUS][2]; static double clk_nominal;
static double nominal_mhz(void)
{
	FILE *f = fopen("/proc/cpuinfo", "r"); char l[512]; double v = 0;
	while (f && fgets(l, sizeof(l), f)) { char *p = strstr(l, "@ "); if (strstr(l, "model name") && p && strstr(p, "GHz")) { v = atof(p + 2) * 1000.0; break; } }
	if (f) fclose(f);
	return v > 0 ? v : 2400.0;
}
static void clk_open(const char *list, FILE *log)
{
	int c[MAXCPUS], n;
	if (!strcmp(list, "all")) n = online_cpus(c); else n = parse_cpus(list, c, MAXCPUS);
	clk_nominal = nominal_mhz();
	for (int i = 0; i < n && nclk < MAXCPUS; i++) {
		struct perf_event_attr a; memset(&a, 0, sizeof(a)); a.size = sizeof(a); a.type = PERF_TYPE_HARDWARE; a.config = PERF_COUNT_HW_CPU_CYCLES;
		a.read_format = PERF_FORMAT_GROUP | PERF_FORMAT_TOTAL_TIME_ENABLED | PERF_FORMAT_TOTAL_TIME_RUNNING; a.disabled = 1;
		long lead = perf_open(&a, -1, c[i], -1, PERF_FLAG_FD_CLOEXEC);
		if (lead < 0) { fprintf(log, "clock: cpu %d cycles: %s\n", c[i], strerror(errno)); continue; }
		a.config = PERF_COUNT_HW_REF_CPU_CYCLES; a.disabled = 0;
		long sib = perf_open(&a, -1, c[i], (int)lead, PERF_FLAG_FD_CLOEXEC);
		if (sib < 0) { fprintf(log, "clock: cpu %d ref-cycles: %s\n", c[i], strerror(errno)); close((int)lead); continue; }
		ioctl((int)lead, PERF_EVENT_IOC_ENABLE, PERF_IOC_FLAG_GROUP);
		clk_cpu[nclk] = c[i]; clk_fd[nclk++] = (int)lead;
	}
	fprintf(log, "clock: %d CPUs, nominal %.0f MHz (cycles / ref-cycles)\n", nclk, clk_nominal);
}
static void clk_read(void) { for (int i = 0; i < nclk; i++) { u64 b[5]; if (read(clk_fd[i], b, sizeof(b)) >= (ssize_t)(5 * 8)) { clk_cur[i][0] = b[3]; clk_cur[i][1] = b[4]; } } }

// ---------------------------------------------------------------- resctrl
static const char *RDT = "/sys/fs/resctrl";
static const char *rdt_parent_list;   // --rdt-parent NAME=DIR[,NAME=DIR]: the mon group is made under that ctrl group (step 5 CAT / MBA)

static const char *rdt_parent_of(const char *name)
{
	static char out[600];
	if (!rdt_parent_list) return NULL;
	size_t n = strlen(name); const char *p = rdt_parent_list;
	while (*p) {
		const char *eq = strchr(p, '='); if (!eq) return NULL;
		const char *end = strchr(eq, ','); size_t l = end ? (size_t)(end - eq - 1) : strlen(eq + 1);
		if ((size_t)(eq - p) == n && !strncmp(p, name, n)) { snprintf(out, sizeof(out), "%.*s", (int)l, eq + 1); return out; }
		if (!end) return NULL;
		p = end + 1;
	}
	return NULL;
}

static int rdt_assign(struct cgc *g, const char *dir, int depth)
{
	int n = 0; char p[1024]; snprintf(p, sizeof(p), "%s/cgroup.threads", dir);
	FILE *f = fopen(p, "r");
	if (f) {
		char line[64];
		while (fgets(line, sizeof(line), f)) {
			int tid = atoi(line); if (tid <= 0) continue;
			char b[32]; int l = snprintf(b, sizeof(b), "%d\n", tid);
			if (g->rdt_parent_fd >= 0) (void)!write(g->rdt_parent_fd, b, l);   // into the ctrl group first (EINVAL otherwise)
			if (write(g->rdt_tasks_fd, b, l) == l) n++;
		}
		fclose(f);
	}
	if (depth <= 0) return n;
	DIR *d = opendir(dir); if (!d) return n;
	struct dirent *e;
	while ((e = readdir(d))) {
		if (e->d_type != DT_DIR || e->d_name[0] == '.') continue;
		char q[1024]; snprintf(q, sizeof(q), "%s/%s", dir, e->d_name);
		n += rdt_assign(g, q, depth - 1);
	}
	closedir(d);
	return n;
}

static void rdt_setup(struct cgc *g, FILE *log)
{
	char p[700]; snprintf(p, sizeof(p), "%s/info", RDT);
	struct stat st; if (stat(p, &st) != 0) { fprintf(log, "resctrl: %s not mounted\n", RDT); return; }
	g->rdt_parent_fd = -1;
	const char *parent = rdt_parent_of(g->name);
	if (parent) {
		snprintf(p, sizeof(p), "%s/tasks", parent); g->rdt_parent_fd = open(p, O_WRONLY | O_CLOEXEC);
		if (g->rdt_parent_fd < 0) { fprintf(log, "resctrl: parent %s: %s\n", p, strerror(errno)); return; }
	}
	snprintf(g->rdt_dir, sizeof(g->rdt_dir), "%s/mon_groups/k5-%s", parent ? parent : RDT, g->name);
	if (mkdir(g->rdt_dir, 0755) != 0 && errno != EEXIST) { fprintf(log, "resctrl: mkdir %s: %s\n", g->rdt_dir, strerror(errno)); return; }
	snprintf(p, sizeof(p), "%s/tasks", g->rdt_dir);
	g->rdt_tasks_fd = open(p, O_WRONLY | O_CLOEXEC);
	if (g->rdt_tasks_fd < 0) { fprintf(log, "resctrl: open %s: %s\n", p, strerror(errno)); return; }
	int n = rdt_assign(g, g->path, 3);
	snprintf(p, sizeof(p), "%s/mon_data", g->rdt_dir);
	DIR *d = opendir(p); g->ndom = 0;
	if (d) {
		struct dirent *e;
		char names[8][64]; int nn = 0;
		while ((e = readdir(d)) && nn < 8) if (!strncmp(e->d_name, "mon_L3_", 7)) snprintf(names[nn++], 64, "%s", e->d_name);
		closedir(d);
		for (int i = 0; i < nn; i++) for (int j = i + 1; j < nn; j++) if (strcmp(names[i], names[j]) > 0) { char t[64]; strcpy(t, names[i]); strcpy(names[i], names[j]); strcpy(names[j], t); }
		for (int i = 0; i < nn; i++) {
			char f[800];
			snprintf(f, sizeof(f), "%s/mon_data/%s/llc_occupancy", g->rdt_dir, names[i]); g->occ_fd[i] = open(f, O_RDONLY | O_CLOEXEC);
			snprintf(f, sizeof(f), "%s/mon_data/%s/mbm_total_bytes", g->rdt_dir, names[i]); g->mbm_fd[i] = open(f, O_RDONLY | O_CLOEXEC);
		}
		g->ndom = nn;
	}
	g->rdt = 1; g->mbm_first = 1;
	fprintf(log, "resctrl: %s -> %s, %d threads, %d L3 domains, mbm %s%s%s\n", g->name, g->rdt_dir, n, g->ndom, (g->ndom && g->mbm_fd[0] >= 0) ? "yes" : "no",
		parent ? ", ctrl group " : "", parent ? parent : "");
}

static void rdt_teardown(struct cgc *g)
{
	if (!g->rdt) return;
	for (int i = 0; i < g->ndom; i++) { if (g->occ_fd[i] >= 0) close(g->occ_fd[i]); if (g->mbm_fd[i] >= 0) close(g->mbm_fd[i]); }
	if (g->rdt_tasks_fd >= 0) close(g->rdt_tasks_fd);
	if (g->rdt_parent_fd >= 0) close(g->rdt_parent_fd);   // the ctrl group itself is the controller's
	rmdir(g->rdt_dir);
	g->rdt = 0;
}

// ---------------------------------------------------------------- sampler
static int pstat_fd = -1, pst_cpus[MAXCPUS], npst; static u64 pst_prev[MAXCPUS][4]; static int pst_first = 1;

static void smp_setup(struct cgc *g, FILE *log)
{
	char p[700];
	snprintf(p, sizeof(p), "%s/cpu.stat", g->path); g->stat_fd = open(p, O_RDONLY | O_CLOEXEC);
	snprintf(p, sizeof(p), "%s/cpu.pressure", g->path); g->psi_fd = open(p, O_RDONLY | O_CLOEXEC);
	g->smp = g->stat_fd >= 0; g->sfirst = 1;
	fprintf(log, "sampler: %s cpu.stat %s, cpu.pressure %s\n", g->name, g->stat_fd >= 0 ? "ok" : "missing", g->psi_fd >= 0 ? "ok" : "missing");
}

static void smp_read(struct cgc *g, u64 v[8])
{
	char b[1024]; memset(v, 0, 8 * sizeof(u64));
	ssize_t n = pread(g->stat_fd, b, sizeof(b) - 1, 0);
	if (n > 0) {
		b[n] = 0;
		const char *keys[6] = { "usage_usec", "user_usec", "system_usec", "nr_periods", "nr_throttled", "throttled_usec" };
		for (char *l = strtok(b, "\n"); l; l = strtok(NULL, "\n"))
			for (int k = 0; k < 6; k++) { size_t kl = strlen(keys[k]); if (!strncmp(l, keys[k], kl) && l[kl] == ' ') v[k] = strtoull(l + kl + 1, NULL, 10); }
	}
	if (g->psi_fd >= 0 && (n = pread(g->psi_fd, b, sizeof(b) - 1, 0)) > 0) {
		b[n] = 0;
		char *s = strstr(b, "some"), *f = strstr(b, "full"), *t;
		if (s && (t = strstr(s, "total="))) v[6] = strtoull(t + 6, NULL, 10);
		if (f && (t = strstr(f, "total="))) v[7] = strtoull(t + 6, NULL, 10);
	}
}

static void pst_read(u64 cur[MAXCPUS][4])
{
	static char b[65536];
	ssize_t n = pread(pstat_fd, b, sizeof(b) - 1, 0); if (n <= 0) return; b[n] = 0;
	for (char *l = strtok(b, "\n"); l; l = strtok(NULL, "\n")) {
		if (strncmp(l, "cpu", 3) || l[3] == ' ') continue;
		int c = atoi(l + 3);
		for (int i = 0; i < npst; i++) if (pst_cpus[i] == c) {
			u64 f[8] = {0}; char *q = strchr(l, ' ');
			for (int k = 0; k < 8 && q; k++) { f[k] = strtoull(q, &q, 10); }
			cur[i][0] = f[0] + f[1] + f[2] + f[5] + f[6] + f[7]; cur[i][1] = f[5]; cur[i][2] = f[6]; cur[i][3] = f[3] + f[4];
		}
	}
}

// ---------------------------------------------------------------- probe
static struct bpf_object *obj; static struct cfg *C; static int winm_fd = -1, gs_fd = -1, pcsm_fd = -1;
static struct rec prev_rec[NV], cur_rec[NV]; static int have_prev[NV];
static int sibs_tab[MAXCPU]; static int ncpu_possible;
static struct ring_buffer *RB; static struct rec rb_rec[NV]; static int rb_seen[NV]; static int rb_ready; static u64 rb_seq;

static void fold_user(struct win *vals, int v, struct rec *s)
{
	memset(s, 0, sizeof(*s));
	for (int i = 0; i < ncpu_possible && i < MAXCPU; i++) {
		struct win *w = &vals[i];
		int mine = BIT_TEST(C->vmask[v], i);
		int si = sibs_tab[i]; int sibof = si >= 0 && si < MAXCPU && BIT_TEST(C->vmask[v], si);
		for (int h = 0; h < NH; h++) { s->R[h] += w->R[v][h]; s->Wo[h] += w->Wo[v][h]; }
		s->Rn += w->Rn[v]; s->Rrd += w->Rrd[v]; s->Rrdn += w->Rrdn[v]; s->mig += w->mig[v]; s->sw += w->sw[v]; s->pre += w->pre[v]; s->wk += w->wk[v];
		if (mine) {
			for (int h = 0; h < NH; h++) { s->occ[h] += w->occ[h]; s->sib[h] += w->sibm[v][h]; }
			s->sirq += w->sirq; s->sirqn += w->sirqn; s->sirqc += w->sirqc[v];
			for (int k = 0; k < NVEC; k++) s->vec[k] += w->vec[k];
		}
		if (sibof) for (int h = 0; h < NH; h++) s->sib[h] += w->sibo[v][h];
		for (unsigned j = 0; j < C->nvcpu[v] && j < MAXVC; j++) {
			unsigned cj = C->vcpu[v][j];
			if ((int)cj == i) { for (int h = 0; h < NH; h++) s->csib[j][h] += w->sibm[v][h]; s->csirq[j][0] += w->sirq; s->csirq[j][1] += w->sirqc[v]; }
			if (sibof && (int)cj == si) for (int h = 0; h < NH; h++) s->csib[j][h] += w->sibo[v][h];
		}
	}
}

static int on_rec(void *ctx, void *data, size_t sz)
{
	(void)ctx;
	if (sz < sizeof(struct rec)) return 0;
	struct rec *r = data;
	if (r->v >= NV) return 0;
	if (rb_ready == 0 || r->seq != rb_seq) { memset(rb_seen, 0, sizeof(rb_seen)); rb_seq = r->seq; rb_ready = 1; }
	rb_rec[r->v] = *r; rb_seen[r->v] = 1;
	return 0;
}

static void put_arr(FILE *o, const char *k, const __u64 *a, int n)
{
	fprintf(o, ",\"%s\":[", k);
	for (int i = 0; i < n; i++) fprintf(o, "%s%llu", i ? "," : "", (unsigned long long)a[i]);
	fputc(']', o);
}

static void put_rec(FILE *o, const char *name, const struct rec *r, int v)
{
	fprintf(o, "{\"n\":\"%s\"", name);
	put_arr(o, "occ", r->occ, NH); put_arr(o, "R", r->R, NH);
	fprintf(o, ",\"Rn\":%llu,\"Rrd\":%llu,\"Rrdn\":%llu,\"mig\":%llu,\"sw\":%llu,\"pre\":%llu,\"wk\":%llu",
		(unsigned long long)r->Rn, (unsigned long long)r->Rrd, (unsigned long long)r->Rrdn, (unsigned long long)r->mig,
		(unsigned long long)r->sw, (unsigned long long)r->pre, (unsigned long long)r->wk);
	if (C->flags & F_WAKE) put_arr(o, "Wo", r->Wo, NH);
	if (C->flags & F_SIB) {
		put_arr(o, "sib", r->sib, NH);
		fputs(",\"csib\":[", o);
		for (unsigned j = 0; j < C->nvcpu[v] && j < MAXVC; j++) { fprintf(o, "%s[%u", j ? "," : "", C->vcpu[v][j]); for (int h = 0; h < NH; h++) fprintf(o, ",%llu", (unsigned long long)r->csib[j][h]); fputc(']', o); }
		fputc(']', o);
	}
	if (C->flags & F_SOFTIRQ) {
		fprintf(o, ",\"sirq\":%llu,\"sirqn\":%llu,\"sirqc\":%llu", (unsigned long long)r->sirq, (unsigned long long)r->sirqn, (unsigned long long)r->sirqc);
		put_arr(o, "vec", r->vec, NVEC);
		fputs(",\"csirq\":[", o);
		for (unsigned j = 0; j < C->nvcpu[v] && j < MAXVC; j++) fprintf(o, "%s[%u,%llu,%llu]", j ? "," : "", C->vcpu[v][j], (unsigned long long)r->csirq[j][0], (unsigned long long)r->csirq[j][1]);
		fputc(']', o);
	}
	fputc('}', o);
}

static u64 self_cpu_ns(void)
{
	FILE *f = fopen("/proc/self/stat", "r"); if (!f) return 0;
	char buf[1024]; size_t n = fread(buf, 1, sizeof(buf) - 1, f); fclose(f); buf[n] = 0;
	char *p = strrchr(buf, ')'); if (!p) return 0;
	unsigned long ut = 0, st = 0; int i = 0; char *tok = strtok(p + 2, " ");
	while (tok) { i++; if (i == 12) ut = strtoul(tok, NULL, 10); if (i == 13) { st = strtoul(tok, NULL, 10); break; } tok = strtok(NULL, " "); }
	return (u64)(ut + st) * (1000000000ULL / sysconf(_SC_CLK_TCK));
}
static long self_rss_kb(void) { FILE *f = fopen("/proc/self/statm", "r"); long a = 0, b = 0; if (f) { if (fscanf(f, "%ld %ld", &a, &b) != 2) b = 0; fclose(f); } return b * (sysconf(_SC_PAGESIZE) / 1024); }
static long map_memlock(int fd)
{
	char p[64], l[256]; snprintf(p, sizeof(p), "/proc/self/fdinfo/%d", fd);
	FILE *f = fopen(p, "r"); long m = 0; if (!f) return 0;
	while (fgets(l, sizeof(l), f)) if (!strncmp(l, "memlock:", 8)) m = atol(l + 8);
	fclose(f); return m;
}

// Phase I: the victim CPUs' clock from APERF (0xE8) / MPERF (0xE7); MPERF counts at the base clock in C0, so
// MHz = base x dAPERF / dMPERF and C0 % = dMPERF / (base x tick). base = MSR_PLATFORM_INFO (0xCE) bits 15:8 x 100.
static int mhz_cpu[MAXCPUS], mhz_fd[MAXCPUS], nmhz; static u64 mhz_prev[MAXCPUS][2], mhz_cur[MAXCPUS][2]; static double mhz_base;
static int msr_rd(int fd, __u32 reg, u64 *v) { return pread(fd, v, 8, reg) == 8 ? 0 : -1; }
static void mhz_open(const char *list, FILE *log)
{
	int c[MAXCPUS]; int n = parse_cpus(list, c, MAXCPUS);
	for (int i = 0; i < n; i++) {
		char p[64]; snprintf(p, sizeof(p), "/dev/cpu/%d/msr", c[i]);
		int fd = open(p, O_RDONLY | O_CLOEXEC);
		if (fd < 0) { fprintf(log, "mhz: %s: %s\n", p, strerror(errno)); continue; }
		if (!mhz_base) { u64 pi; if (msr_rd(fd, 0xCE, &pi) == 0) mhz_base = (double)((pi >> 8) & 0xff) * 100.0; }
		mhz_cpu[nmhz] = c[i]; mhz_fd[nmhz++] = fd;
	}
	fprintf(log, "mhz: %d CPUs, base %.0f MHz\n", nmhz, mhz_base);
}
static void mhz_read(void) { for (int i = 0; i < nmhz; i++) { msr_rd(mhz_fd[i], 0xE8, &mhz_cur[i][0]); msr_rd(mhz_fd[i], 0xE7, &mhz_cur[i][1]); } }

int main(int argc, char **argv)
{
	const char *obj_path = "k5probe.bpf.o", *out = NULL, *progs = "", *perf_cg = NULL, *rdt_cg = NULL, *smp_cg = NULL, *mhz_list = NULL, *clock_list = NULL;
	int tick_ms = 100, rq = 1, sib = 0, timer = 0, rd = 0, perf = 0, perf_rd = 1, rdt = 0, rdt_rd = 1, smp = 0, rescan_s = 10, pin = -1, cgf = 0, hold = 0;
	double secs = 0;
	for (int i = 1; i + 1 < argc; i += 2) {
		const char *k = argv[i], *v = argv[i + 1];
		if (!strcmp(k, "--cgf")) { cgf = atoi(v); continue; }            // J1: cgroup-filtered mode on every CPU (unpinned victims)
		if (!strcmp(k, "--hold")) { hold = atoi(v); continue; }          // J1 row 2: the holder state on every switch
		if (!strcmp(k, "--clock-cpus")) { clock_list = v; continue; }   // J1 row 11: per-CPU cycles / ref-cycles events ("all" or a list)
		if (!strcmp(k, "--perf-ref")) { perf_ref = atoi(v); continue; } // J1 row 9: ref-cycles and LLC references in the cgroup group
		if (!strcmp(k, "--obj")) obj_path = v; else if (!strcmp(k, "--out")) out = v; else if (!strcmp(k, "--tick-ms")) tick_ms = atoi(v);
		else if (!strcmp(k, "--secs")) secs = atof(v); else if (!strcmp(k, "--cpu")) pin = atoi(v);
		else if (!strcmp(k, "--probe")) progs = v; else if (!strcmp(k, "--rq")) rq = atoi(v); else if (!strcmp(k, "--rd")) rd = atoi(v); else if (!strcmp(k, "--sib")) sib = atoi(v);
		else if (!strcmp(k, "--timer")) timer = atoi(v); else if (!strcmp(k, "--perf")) perf = atoi(v); else if (!strcmp(k, "--perf-read")) perf_rd = atoi(v);
		else if (!strcmp(k, "--perf-cg")) perf_cg = v; else if (!strcmp(k, "--resctrl")) rdt = atoi(v); else if (!strcmp(k, "--resctrl-read")) rdt_rd = atoi(v);
		else if (!strcmp(k, "--resctrl-cg")) rdt_cg = v; else if (!strcmp(k, "--rescan-s")) rescan_s = atoi(v);
		else if (!strcmp(k, "--sampler")) smp = atoi(v); else if (!strcmp(k, "--sampler-cg")) smp_cg = v;
		else if (!strcmp(k, "--mhz-cpus")) mhz_list = v; else if (!strcmp(k, "--perf-td")) perf_td = atoi(v);
		else if (!strcmp(k, "--rdt-parent")) rdt_parent_list = v;
		else if (!strcmp(k, "--cg")) {
			if (ncg >= MAXCG) { fprintf(stderr, "too many --cg\n"); return 2; }
			struct cgc *g = &CG[ncg]; memset(g, 0, sizeof(*g)); g->rdt_tasks_fd = -1; g->stat_fd = g->psi_fd = -1;
			char *d = strdup(v), *eq = strchr(d, '=');
			if (!eq) { fprintf(stderr, "--cg NAME=PATH[,victim=CPUS][,cpus=CPUS]\n"); return 2; }
			*eq = 0; snprintf(g->name, sizeof(g->name), "%s", d);
			char *rest = eq + 1, *opt = strchr(rest, ',');
			if (opt) *opt++ = 0;
			snprintf(g->path, sizeof(g->path), "%s", rest);
			while (opt && *opt) {
				char *nx = strchr(opt, ','); if (nx) *nx++ = 0;
				if (!strncmp(opt, "victim=", 7)) { g->victim = 1; g->ncpus = strcmp(opt + 7, "all") ? parse_cpus(opt + 7, g->cpus, MAXCPUS) : 0; }   // "all" (J1): every online CPU
				else if (!strncmp(opt, "cpus=", 5) && !g->victim) g->ncpus = strcmp(opt + 5, "all") ? parse_cpus(opt + 5, g->cpus, MAXCPUS) : 0;
				opt = nx;
			}
			free(d);
			if (g->victim && ncg != nvict) { fprintf(stderr, "victims must come first\n"); return 2; }
			if (g->victim) nvict++;
			add_ids(g, g->path, 2);
			if (!g->nids) { fprintf(stderr, "no cgroup at %s\n", g->path); return 1; }
			ncg++;
		}
	}
	if (!out) { fprintf(stderr, "--out required\n"); return 2; }
	{ struct rlimit rl; if (getrlimit(RLIMIT_NOFILE, &rl) == 0) { rl.rlim_cur = rl.rlim_max; setrlimit(RLIMIT_NOFILE, &rl); } }   // J1: 144 CPUs x 8 perf events + 288 clock events
	if (pin >= 0) { cpu_set_t s; CPU_ZERO(&s); CPU_SET(pin, &s); sched_setaffinity(0, sizeof(s), &s); }
	FILE *fo = fopen(out, "w");
	if (!fo) { fprintf(stderr, "open %s failed\n", out); return 1; }
	FILE *log = stderr;
	int onl[MAXCPUS], nonl = online_cpus(onl);
	for (int i = 0; i < ncg; i++) if (!CG[i].ncpus) { memcpy(CG[i].cpus, onl, sizeof(int) * nonl); CG[i].ncpus = nonl; }
	signal(SIGINT, on_sig); signal(SIGTERM, on_sig);

	int want_switch = strstr(progs, "switch") != NULL, want_wake = strstr(progs, "wakeup") != NULL, want_sirq = strstr(progs, "softirq") != NULL;
	int use_probe = want_switch || want_wake || want_sirq;
	struct bpf_program *prog; int prog_fd[16], nprog = 0; const char *prog_name[16];
	int map_fds[16], nmap = 0;
	if (use_probe && nvict == 0) { fprintf(stderr, "the probe needs a victim (--cg NAME=PATH,victim=CPUS)\n"); return 2; }
	if (use_probe) {
		obj = bpf_object__open_file(obj_path, NULL);
		if (!obj) { fprintf(stderr, "open %s: %s\n", obj_path, strerror(errno)); return 1; }
		struct bpf_map *ro = bpf_object__find_map_by_name(obj, ".rodata.cfg"), *m;
		if (!ro) bpf_object__for_each_map(m, obj) { const char *nm = bpf_map__name(m); size_t l = strlen(nm); if (l >= 10 && !strcmp(nm + l - 10, "rodata.cfg")) ro = m; }
		size_t sz = 0; C = ro ? bpf_map__initial_value(ro, &sz) : NULL;
		if (!C || sz < sizeof(*C)) { fprintf(stderr, "no .rodata.cfg (size %zu)\n", sz); return 1; }
		memset(C, 0, sizeof(*C));
		for (int i = 0; i < ncg; i++) for (int j = 0; j < CG[i].nids && C->nid < NID; j++) { C->cgid[C->nid] = CG[i].ids[j]; C->cgslot[C->nid] = i; C->nid++; }
		C->nv = nvict;
		struct stat st; C->root = stat("/sys/fs/cgroup", &st) == 0 ? (u64)st.st_ino : 1;
		if (cgf) rq = 0;                                          // J1: the wait is credited to the last holder in the cgf path, no time-share split
		for (int v = 0; v < nvict; v++) {
			for (int j = 0; j < CG[v].ncpus; j++) { int c = CG[v].cpus[j]; if (c < MAXCPU) { BIT_SET(C->vmask[v], c); if (!cgf && C->nvcpu[v] < MAXVC) C->vcpu[v][C->nvcpu[v]++] = c; } }
			for (int k = 0; k < NW; k++) C->mask[k] |= C->vmask[v][k];
		}
		ncpu_possible = libbpf_num_possible_cpus();
		C->ncpu = ncpu_possible > MAXCPU ? MAXCPU : ncpu_possible;
		for (int c = 0; c < MAXCPU; c++) sibs_tab[c] = c < ncpu_possible ? sibling_of(c) : -1;
		for (int c = 0; c < MAXCPU; c++) if (BIT_TEST(C->mask, c)) { int s = sibs_tab[c]; if (s >= 0 && s < MAXCPU && !BIT_TEST(C->mask, s)) BIT_SET(C->sibmask, s); }
		C->tick_ns = (u64)tick_ms * 1000000ULL;
		C->flags = (rq ? F_RQ : 0) | (sib ? F_SIB : 0) | (want_wake ? F_WAKE : 0) | (want_sirq ? F_SOFTIRQ : 0) | (timer ? F_TIMER : 0) | (rd ? F_RD : 0) | (cgf ? F_CGF : 0) | (hold ? F_HOLD : 0);
		if (!rq && !cgf) bpf_map__set_max_entries(bpf_object__find_map_by_name(obj, "tst"), 1);
		if (!timer) bpf_map__set_max_entries(bpf_object__find_map_by_name(obj, "rb"), 4096);
		bpf_object__for_each_program(prog, obj) {
			const char *nm = bpf_program__name(prog); int on = 0;
			if (!strcmp(nm, "k5_switch")) on = want_switch;
			else if (strstr(nm, "wakeup")) on = want_wake;
			else if (strstr(nm, "sirq")) on = want_sirq;
			else if (!strcmp(nm, "k5_arm")) on = timer;
			bpf_program__set_autoload(prog, on);
		}
		if (bpf_object__load(obj)) { fprintf(stderr, "load failed\n"); return 1; }
		int sfd = bpf_map__fd(bpf_object__find_map_by_name(obj, "sibs"));
		for (__u32 c = 0; c < MAXCPU; c++) { __u32 val = sibs_tab[c] >= 0 ? (__u32)sibs_tab[c] : 0xffffffffu; bpf_map_update_elem(sfd, &c, &val, BPF_ANY); }
		winm_fd = bpf_map__fd(bpf_object__find_map_by_name(obj, "winm"));
		if (cgf) { struct bpf_map *pm = bpf_object__find_map_by_name(obj, "pcsm"); pcsm_fd = pm ? bpf_map__fd(pm) : -1; }
		struct bpf_map *gm = bpf_object__find_map_by_name(obj, ".data.gs");
		if (!gm) bpf_object__for_each_map(m, obj) { const char *nm = bpf_map__name(m); size_t l = strlen(nm); if (l >= 7 && !strcmp(nm + l - 7, "data.gs")) gm = m; }
		gs_fd = gm ? bpf_map__fd(gm) : -1;
		bpf_object__for_each_map(m, obj) if (nmap < 16) map_fds[nmap++] = bpf_map__fd(m);
		bpf_object__for_each_program(prog, obj) {
			if (!bpf_program__autoload(prog)) continue;
			const char *nm = bpf_program__name(prog);
			if (!strcmp(nm, "k5_arm")) continue;
			if (!bpf_program__attach(prog)) { fprintf(stderr, "attach %s failed\n", nm); return 1; }
			struct bpf_prog_info info = {}; __u32 il = sizeof(info);
			bpf_obj_get_info_by_fd(bpf_program__fd(prog), &info, &il);
			fprintf(stderr, "prog %s id %u\n", nm, info.id);
			prog_fd[nprog] = bpf_program__fd(prog); prog_name[nprog] = nm; nprog++;
		}
		if (timer) {
			RB = ring_buffer__new(bpf_map__fd(bpf_object__find_map_by_name(obj, "rb")), on_rec, NULL, NULL);
			struct bpf_program *ap = bpf_object__find_program_by_name(obj, "k5_arm");
			LIBBPF_OPTS(bpf_test_run_opts, topts);
			int e = ap ? bpf_prog_test_run_opts(bpf_program__fd(ap), &topts) : -1;
			if (!RB || e || topts.retval) { fprintf(stderr, "timer record: arm failed (err %d, retval %u); falling back to the map-read path\n", e, topts.retval); timer = 0; C->flags &= ~F_TIMER; }
			else fprintf(stderr, "timer armed: %d ms, pinned to CPU %d\n", tick_ms, sched_getcpu());
		}
	}
	for (int i = 0; i < ncg; i++) {
		if (perf && in_list(perf_cg, CG[i].name)) perf_setup(&CG[i], log);
		if (rdt && in_list(rdt_cg, CG[i].name)) rdt_setup(&CG[i], log);
		if (smp && in_list(smp_cg, CG[i].name)) smp_setup(&CG[i], log);
	}
	if (smp) {
		pstat_fd = open("/proc/stat", O_RDONLY | O_CLOEXEC);
		for (int v = 0; v < nvict; v++) for (int j = 0; j < CG[v].ncpus && npst < MAXCPUS; j++) pst_cpus[npst++] = CG[v].cpus[j];
		if (!nvict) for (int j = 0; j < nonl && npst < MAXCPUS; j++) pst_cpus[npst++] = onl[j];   // J0'': no victim (the nodewatch agent): every online CPU
	}

	fprintf(fo, "{\"event\":\"start\",\"version\":\"k5-agent-2\",\"t\":%llu,\"tick_ms\":%d,\"probe\":\"%s\",\"flags\":%u,\"rq\":%d,\"sib\":%d,\"timer\":%d,"
		"\"perf\":%d,\"perf_read\":%d,\"resctrl\":%d,\"resctrl_read\":%d,\"sampler\":%d,\"cgf\":%d,\"hold\":%d,\"clock_cpus\":\"%s\",\"perf_ref\":%d,\"holders\":[",
		wall_ms(), tick_ms, progs, C ? C->flags : 0, rq, sib, timer, perf, perf_rd, rdt, rdt_rd, smp, cgf, hold, clock_list ? clock_list : "", perf_ref);
	for (int i = 0; i < NCG; i++) fprintf(fo, "%s\"%s\"", i ? "," : "", i < ncg ? CG[i].name : "-");
	fprintf(fo, ",\"root\",\"other\",\"idle\"],\"cg\":[");
	for (int i = 0; i < ncg; i++) {
		fprintf(fo, "%s{\"n\":\"%s\",\"path\":\"%s\",\"victim\":%d,\"ids\":%d,\"cpus\":[", i ? "," : "", CG[i].name, CG[i].path, CG[i].victim, CG[i].nids);
		for (int j = 0; j < CG[i].ncpus; j++) fprintf(fo, "%s%d", j ? "," : "", CG[i].cpus[j]);
		fprintf(fo, "],\"perf\":%d,\"perf_cpus\":%d,\"perf_events\":%d,\"resctrl\":%d,\"l3\":%d,\"sampler\":%d}", CG[i].perf, CG[i].nfd, CG[i].nev, CG[i].rdt, CG[i].ndom, CG[i].smp);
	}
	fprintf(fo, "],\"sibmask\":\"");   // the bitmaps as hex words, CPU 0 in the first (word 0 first)
	for (int k = 0; k < NW; k++) fprintf(fo, "%s%llx", k ? "," : "", C ? (unsigned long long)C->sibmask[k] : 0ULL);
	fprintf(fo, "\",\"mask\":\"");
	for (int k = 0; k < NW; k++) fprintf(fo, "%s%llx", k ? "," : "", C ? (unsigned long long)C->mask[k] : 0ULL);
	fprintf(fo, "\"}\n");
	fflush(fo);

	if (mhz_list) mhz_open(mhz_list, stderr);
	if (clock_list) clk_open(clock_list, stderr);
	struct win *vals = winm_fd >= 0 ? calloc(ncpu_possible, sizeof(struct win)) : NULL;
	struct pcs *pcv = pcsm_fd >= 0 ? calloc(ncpu_possible, sizeof(struct pcs)) : NULL, *pcp = pcsm_fd >= 0 ? calloc(ncpu_possible, sizeof(struct pcs)) : NULL;
	int pcs_have = 0;
	u64 t_start = mono_ns(), next = t_start + (u64)tick_ms * 1000000ULL, last_self = t_start, last_rescan = t_start;
	long ticks = 0; int first = 1;
	u64 pst_cur[MAXCPUS][4];
	while (!stop) {
		int have = 0;
		if (timer) {
			ring_buffer__poll(RB, 250);
			int all = rb_ready; for (int v = 0; v < nvict; v++) if (!rb_seen[v]) all = 0;
			if (all) { for (int v = 0; v < nvict; v++) cur_rec[v] = rb_rec[v]; rb_ready = 0; memset(rb_seen, 0, sizeof(rb_seen)); have = 2; }
			else if (stop) break; else continue;
		} else {
			u64 now = mono_ns();
			if (next > now) { struct timespec ts = { (time_t)(next / 1000000000ULL), (long)(next % 1000000000ULL) }; clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &ts, NULL); }
			next += (u64)tick_ms * 1000000ULL;
			if (stop) break;
			if (vals) {
				__u32 k = 0;
				if (bpf_map_lookup_elem(winm_fd, &k, vals) == 0) {
					for (int v = 0; v < nvict; v++) {
						struct rec cum; fold_user(vals, v, &cum);
						if (have_prev[v]) {
							__u64 *a = (__u64 *)&cum, *b = (__u64 *)&prev_rec[v], *d = (__u64 *)&cur_rec[v];
							for (size_t i = 2; i < RECW; i++) d[i] = a[i] - b[i];
						}
						prev_rec[v] = cum; have_prev[v] = 1;
					}
					have = 1;
				}
			}
		}
		for (int i = 0; i < ncg; i++) if (CG[i].perf && perf_rd) perf_read(&CG[i]);
		u64 sv[MAXCG][8];
		for (int i = 0; i < ncg; i++) if (CG[i].smp) smp_read(&CG[i], sv[i]);
		if (smp && pstat_fd >= 0) pst_read(pst_cur);
		if (nmhz) mhz_read();
		if (nclk) clk_read();
		if (pcv) { __u32 k0 = 0; pcs_have = bpf_map_lookup_elem(pcsm_fd, &k0, pcv) == 0; }
		u64 occb[MAXCG][8], mbmb[MAXCG][8];
		for (int i = 0; i < ncg; i++) if (CG[i].rdt && rdt_rd) for (int d = 0; d < CG[i].ndom; d++) {
			occb[i][d] = CG[i].occ_fd[d] >= 0 ? read_u64_fd(CG[i].occ_fd[d]) : 0;
			mbmb[i][d] = CG[i].mbm_fd[d] >= 0 ? read_u64_fd(CG[i].mbm_fd[d]) : 0;
		}
		if (first) {                                             // baseline only
			first = 0;
			for (int i = 0; i < ncg; i++) { if (CG[i].smp) memcpy(CG[i].sv_prev, sv[i], sizeof(sv[i])); for (int d = 0; d < CG[i].ndom; d++) CG[i].mbm_prev[d] = mbmb[i][d]; }
			if (smp) memcpy(pst_prev, pst_cur, sizeof(pst_prev));
			if (nmhz) memcpy(mhz_prev, mhz_cur, sizeof(mhz_prev));
			if (nclk) memcpy(clk_prev, clk_cur, sizeof(clk_prev));
			if (pcv && pcs_have) memcpy(pcp, pcv, sizeof(struct pcs) * ncpu_possible);
			continue;
		}
		ticks++;
		fprintf(fo, "{\"t\":%llu,\"k\":%ld", wall_ms(), ticks);
		if (have) {
			fprintf(fo, ",\"src\":\"%s\",\"cg\":[", have == 2 ? "rb" : "map");
			for (int v = 0; v < nvict; v++) { if (v) fputc(',', fo); put_rec(fo, CG[v].name, &cur_rec[v], v); }
			fputc(']', fo);
		}
		int any = 0;
		for (int i = 0; i < ncg; i++) if (CG[i].perf && perf_rd) {
			fprintf(fo, "%s\"%s\":[%llu,%llu,%llu", any ? "," : ",\"perf\":{", CG[i].name, CG[i].ptot[0] - CG[i].ptot_prev[0], CG[i].ptot[1] - CG[i].ptot_prev[1], CG[i].ptot[2] - CG[i].ptot_prev[2]); any = 1;
			for (int e = 3; e < CG[i].nev; e++) fprintf(fo, ",%llu", CG[i].ptot[e] - CG[i].ptot_prev[e]);
			fputc(']', fo);
		}
		if (any) fputc('}', fo);
		any = 0;
		for (int i = 0; i < ncg; i++) if (CG[i].rdt && rdt_rd) {
			fprintf(fo, "%s\"%s\":[", any ? "," : ",\"cmt\":{", CG[i].name); any = 1;
			for (int d = 0; d < CG[i].ndom; d++) fprintf(fo, "%s%llu", d ? "," : "", occb[i][d]);
			fputc(']', fo);
		}
		if (any) fputc('}', fo);
		any = 0;
		for (int i = 0; i < ncg; i++) if (CG[i].rdt && rdt_rd && CG[i].ndom && CG[i].mbm_fd[0] >= 0) {
			fprintf(fo, "%s\"%s\":[", any ? "," : ",\"mbm\":{", CG[i].name); any = 1;
			for (int d = 0; d < CG[i].ndom; d++) { fprintf(fo, "%s%llu", d ? "," : "", mbmb[i][d] - CG[i].mbm_prev[d]); CG[i].mbm_prev[d] = mbmb[i][d]; }
			fputc(']', fo);
		}
		if (any) fputc('}', fo);
		any = 0;
		for (int i = 0; i < ncg; i++) if (CG[i].smp) {
			fprintf(fo, "%s\"%s\":[", any ? "," : ",\"smp\":{", CG[i].name); any = 1;
			for (int k = 0; k < 8; k++) fprintf(fo, "%s%llu", k ? "," : "", sv[i][k] - CG[i].sv_prev[k]);
			memcpy(CG[i].sv_prev, sv[i], sizeof(sv[i]));
			fputc(']', fo);
		}
		if (any) fputc('}', fo);
		if (nmhz) {
			fputs(",\"mhz\":{", fo);
			for (int i = 0; i < nmhz; i++) {
				u64 da = mhz_cur[i][0] - mhz_prev[i][0], dm = mhz_cur[i][1] - mhz_prev[i][1];
				fprintf(fo, "%s\"%d\":[%.0f,%.1f]", i ? "," : "", mhz_cpu[i], dm ? mhz_base * (double)da / (double)dm : 0.0,
				        mhz_base ? 100.0 * (double)dm / (mhz_base * 1e3 * tick_ms) : 0.0);
			}
			fputc('}', fo);
			memcpy(mhz_prev, mhz_cur, sizeof(mhz_prev));
		}
		// J1: the per-CPU clock events: MHz = nominal x cycles / ref-cycles, C0 % = ref-cycles / (nominal x tick); kept per CPU for the weighting below
		double clk_mhz[MAXCPUS], clk_c0[MAXCPUS]; int clk_idx[MAXCPU];
		for (int c = 0; c < MAXCPU; c++) clk_idx[c] = -1;
		if (nclk) {
			double sm = 0; int nb = 0;
			for (int i = 0; i < nclk; i++) {
				u64 dc = clk_cur[i][0] - clk_prev[i][0], dr = clk_cur[i][1] - clk_prev[i][1];
				clk_mhz[i] = dr ? clk_nominal * (double)dc / (double)dr : 0.0; clk_c0[i] = 100.0 * (double)dr / (clk_nominal * 1e3 * tick_ms);
				if (clk_cpu[i] < MAXCPU) clk_idx[clk_cpu[i]] = i;
				if (clk_c0[i] > 5.0) { sm += clk_mhz[i]; nb++; }
			}
			fprintf(fo, ",\"clk_busy\":[%.0f,%d]", nb ? sm / nb : 0.0, nb);   // the mean clock of the CPUs over 5 %% C0, and their number
			memcpy(clk_prev, clk_cur, sizeof(clk_prev));
		}
		// J1 (F_CGF): per victim, the CPUs it ran on this tick with its on-CPU ns and wait ns there, the softirq ns on those CPUs (of which while
		// it was current), phi_irq weighted by the on-CPU time, and the on-CPU-weighted clock from the per-CPU events when they are armed
		if (pcv && pcs_have) {
			fputs(",\"j\":{", fo);
			for (int v = 0; v < nvict; v++) {
				double won = 0, wsq = 0, wmhz = 0, wc0 = 0, wclk = 0; int first1 = 1;
				fprintf(fo, "%s\"%s\":{\"on\":{", v ? "," : "", CG[v].name);
				for (int c = 0; c < ncpu_possible && c < MAXCPU; c++) {
					u64 on = pcv[c].on[v] - pcp[c].on[v]; if (!on) continue;
					u64 wt = pcv[c].wait[v] - pcp[c].wait[v], sq = pcv[c].sirq - pcp[c].sirq, sqc = pcv[c].sirqc[v] - pcp[c].sirqc[v];
					fprintf(fo, "%s\"%d\":[%llu,%llu,%llu,%llu", first1 ? "" : ",", c, (unsigned long long)on, (unsigned long long)wt, (unsigned long long)sq, (unsigned long long)sqc);
					int ci = c < MAXCPU ? clk_idx[c] : -1;
					if (ci >= 0) { fprintf(fo, ",%.0f,%.1f", clk_mhz[ci], clk_c0[ci]); wmhz += (double)on * clk_mhz[ci]; wc0 += (double)on * clk_c0[ci]; wclk += (double)on; }
					fputc(']', fo); first1 = 0;
					won += (double)on; wsq += (double)on * ((double)sq / ((double)tick_ms * 1e6));
				}
				fprintf(fo, "},\"on_ns\":%.0f,\"phi_irq_w\":%.4f", won, won ? wsq / won : 0.0);
				if (wclk) fprintf(fo, ",\"mhz_w\":%.0f,\"c0_w\":%.1f", wmhz / wclk, wc0 / wclk);
				fputc('}', fo);
			}
			fputc('}', fo);
			memcpy(pcp, pcv, sizeof(struct pcs) * ncpu_possible);
		}
		any = 0;
		for (int i = 0; i < ncg; i++) if (CG[i].perf && perf_rd) {   // J1: time_enabled / time_running over the group's CPUs this tick (equal = no multiplexing)
			fprintf(fo, "%s\"%s\":[%llu,%llu]", any ? "," : ",\"pmux\":{", CG[i].name, (unsigned long long)(CG[i].pen - CG[i].pen_prev), (unsigned long long)(CG[i].prun - CG[i].prun_prev)); any = 1;
		}
		if (any) fputc('}', fo);
		if (smp && npst) {
			fputs(",\"pst\":{", fo);
			for (int i = 0; i < npst; i++) { fprintf(fo, "%s\"%d\":[%llu,%llu,%llu,%llu]", i ? "," : "", pst_cpus[i], pst_cur[i][0] - pst_prev[i][0], pst_cur[i][1] - pst_prev[i][1], pst_cur[i][2] - pst_prev[i][2], pst_cur[i][3] - pst_prev[i][3]); }
			fputc('}', fo);
			memcpy(pst_prev, pst_cur, sizeof(pst_prev));
		}
		fputs("}\n", fo);
		fflush(fo);
		u64 now = mono_ns();
		if (now - last_self >= 1000000000ULL) {
			last_self = now;
			fprintf(fo, "{\"event\":\"self\",\"t\":%llu,\"cpu_ns\":%llu,\"rss_kb\":%ld,\"progs\":{", wall_ms(), self_cpu_ns(), self_rss_kb());
			for (int p = 0; p < nprog; p++) {
				struct bpf_prog_info info = {}; __u32 il = sizeof(info);
				bpf_obj_get_info_by_fd(prog_fd[p], &info, &il);
				fprintf(fo, "%s\"%s\":[%llu,%llu]", p ? "," : "", prog_name[p], (unsigned long long)info.run_time_ns, (unsigned long long)info.run_cnt);
			}
			long mem = 0; for (int i = 0; i < nmap; i++) mem += map_memlock(map_fds[i]);
			struct gstate gsv = {0}; if (gs_fd >= 0) { __u32 k = 0; bpf_map_lookup_elem(gs_fd, &k, &gsv); }
			fprintf(fo, "},\"map_memlock\":%ld,\"seq\":%u,\"lost\":%u,\"armed\":%u}\n", mem, gsv.seqno, gsv.lost, gsv.armed);
			fflush(fo);
		}
		if (rdt && rescan_s > 0 && now - last_rescan >= (u64)rescan_s * 1000000000ULL) {
			last_rescan = now;
			for (int i = 0; i < ncg; i++) if (CG[i].rdt) rdt_assign(&CG[i], CG[i].path, 3);
		}
		if (secs > 0 && now - t_start >= (u64)(secs * 1e9)) break;
	}
	fprintf(fo, "{\"event\":\"end\",\"t\":%llu,\"ticks\":%ld,\"cpu_ns\":%llu}\n", wall_ms(), ticks, self_cpu_ns());
	fclose(fo);
	for (int i = 0; i < ncg; i++) { rdt_teardown(&CG[i]); for (int j = 0; j < CG[i].ncpus; j++) if (CG[i].perf && CG[i].lead[j] >= 0) close(CG[i].lead[j]); }
	if (RB) ring_buffer__free(RB);
	if (obj) bpf_object__close(obj);
	return 0;
}
