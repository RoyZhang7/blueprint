// SPDX-License-Identifier: GPL-2.0
// Phase K1: loader + userspace reader for k1probe.bpf.o. Sets the cgroup ids and CPU masks in .rodata, loads,
// attaches the four tp_btf programs, then every 100 ms sums and zeroes the per-CPU window counters and writes one JSON
// line with the same field names as probe.py ({"t", "Rself", ..., "Oidle", "Sself", ..., "Wo0..4", "Qidle", ...}), and
// every second walks the per-pid map for the per-thread R (printed to <out>-hist.jsonl, then zeroed) and the log2
// histogram -- the work bpftrace's interval:s:1 print(@Rthr) / print(@Rhist) does. A "self" record every second carries
// this process's CPU. Prints "prog <name> id <id>" to stderr after attach.
//
// usage: k1probe OBJ --cpuset 1,3,5,7 [--sibling 29,31,33,35] --svc-cgroup PATH [--aggr-cgroup PATH] --out FILE.jsonl
#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include <bpf/bpf.h>
#include <bpf/libbpf.h>

typedef unsigned long long u64;
struct cfg { u64 svc, aggr, root, cpumask, sibmask; };
struct ps { u64 cg, runnable, snap1, snap2, snap3, sleep, rthr; unsigned int rcpu, pad; };
struct win { u64 R[4], RL[4], Rn[4], occw[5], occs[5], Wo[5], Won[5], Qidle, Qidlen, preempt, multi, mig; };

static volatile int stop;
static void on_sig(int s) { (void)s; stop = 1; }

static u64 mask(const char *s)
{
	u64 m = 0; char *d = strdup(s), *tok, *save;
	for (tok = strtok_r(d, ",", &save); tok; tok = strtok_r(NULL, ",", &save)) {
		int lo, hi; if (sscanf(tok, "%d-%d", &lo, &hi) != 2) hi = lo = atoi(tok);
		for (int c = lo; c <= hi && c < 64; c++) m |= 1ULL << c;
	}
	free(d); return m;
}
static u64 cgid(const char *p) { struct stat st; return (p && *p && stat(p, &st) == 0) ? (u64)st.st_ino : ~0ULL; }
static u64 now_ms(void) { struct timespec ts; clock_gettime(CLOCK_REALTIME, &ts); return (u64)ts.tv_sec * 1000 + ts.tv_nsec / 1000000; }
static u64 self_ns(void)
{
	FILE *f = fopen("/proc/self/stat", "r"); if (!f) return 0;
	char buf[1024]; size_t n = fread(buf, 1, sizeof(buf) - 1, f); fclose(f); buf[n] = 0;
	char *p = strrchr(buf, ')'); if (!p) return 0;
	unsigned long ut = 0, stt = 0; int i = 0; char *tok = strtok(p + 2, " ");
	while (tok) { i++; if (i == 12) ut = strtoul(tok, NULL, 10); if (i == 13) { stt = strtoul(tok, NULL, 10); break; } tok = strtok(NULL, " "); }
	return (u64)(ut + stt) * (1000000000ULL / sysconf(_SC_CLK_TCK));
}

int main(int argc, char **argv)
{
	const char *obj_path = argc > 1 ? argv[1] : "k1probe.bpf.o", *cpuset = "1", *sib = "", *svc = NULL, *aggr = "", *out = "k1probe.jsonl";
	for (int i = 2; i + 1 < argc; i += 2) {
		if (!strcmp(argv[i], "--cpuset")) cpuset = argv[i + 1];
		else if (!strcmp(argv[i], "--sibling")) sib = argv[i + 1];
		else if (!strcmp(argv[i], "--svc-cgroup")) svc = argv[i + 1];
		else if (!strcmp(argv[i], "--aggr-cgroup")) aggr = argv[i + 1];
		else if (!strcmp(argv[i], "--out")) out = argv[i + 1];
	}
	if (!svc) { fprintf(stderr, "--svc-cgroup required\n"); return 1; }
	struct bpf_object *obj = bpf_object__open_file(obj_path, NULL);
	if (!obj) { fprintf(stderr, "open %s: %s\n", obj_path, strerror(errno)); return 1; }
	struct bpf_map *m, *ro = NULL;
	bpf_object__for_each_map(m, obj) { const char *nm = bpf_map__name(m); size_t l = strlen(nm); if (l >= 7 && !strcmp(nm + l - 7, ".rodata")) ro = m; }
	if (!ro) { fprintf(stderr, "no .rodata\n"); return 1; }
	size_t sz = 0; struct cfg *c = bpf_map__initial_value(ro, &sz);
	if (!c || sz < sizeof(*c)) { fprintf(stderr, "rodata size %zu\n", sz); return 1; }
	c->svc = cgid(svc); c->aggr = cgid(aggr); c->root = cgid("/sys/fs/cgroup"); c->cpumask = mask(cpuset); c->sibmask = *sib ? mask(sib) : 0;
	if (bpf_object__load(obj)) { fprintf(stderr, "load failed\n"); return 1; }
	struct bpf_program *prog;
	bpf_object__for_each_program(prog, obj) {
		if (!bpf_program__attach(prog)) { fprintf(stderr, "attach %s failed\n", bpf_program__name(prog)); return 1; }
		struct bpf_prog_info info = {}; __u32 il = sizeof(info);
		bpf_obj_get_info_by_fd(bpf_program__fd(prog), &info, &il);
		fprintf(stderr, "prog %s id %u\n", bpf_program__name(prog), info.id);
	}
	signal(SIGINT, on_sig); signal(SIGTERM, on_sig);
	int wfd = bpf_map__fd(bpf_object__find_map_by_name(obj, "winm")), pfd = bpf_map__fd(bpf_object__find_map_by_name(obj, "pst")), hfd = bpf_map__fd(bpf_object__find_map_by_name(obj, "hist"));
	int ncpu = libbpf_num_possible_cpus();
	struct win *vals = calloc(ncpu, sizeof(struct win)), *zeros = calloc(ncpu, sizeof(struct win));
	u64 *hv = calloc(ncpu, sizeof(u64)), *hz = calloc(ncpu, sizeof(u64));
	char hpath[4096]; snprintf(hpath, sizeof(hpath), "%.*s-hist.jsonl", (int)(strlen(out) > 6 ? strlen(out) - 6 : strlen(out)), out);
	FILE *fo = fopen(out, "w"), *fh = fopen(hpath, "w");
	fprintf(fo, "{\"event\":\"start\",\"version\":\"k1-libbpf\",\"cpuset\":\"%s\",\"sibling_cpuset\":\"%s\",\"svc_id\":%llu,\"aggr_id\":%llu,\"t\":%llu}\n", cpuset, sib, c->svc, c->aggr, now_ms());
	fflush(fo);
	u64 last_sec = now_ms(); int n = 0;
	struct timespec tick = {0, 100000000};
	while (!stop) {
		nanosleep(&tick, NULL);
		__u32 k = 0;
		if (bpf_map_lookup_elem(wfd, &k, vals)) continue;
		bpf_map_update_elem(wfd, &k, zeros, BPF_ANY);
		struct win s = {}; u64 *dst = (u64 *)&s;
		for (int cpu = 0; cpu < ncpu; cpu++) { u64 *src = (u64 *)&vals[cpu]; for (size_t i = 0; i < sizeof(s) / 8; i++) dst[i] += src[i]; }
		fprintf(fo, "{\"t\":%llu,\"Rself\":%llu,\"Raggr\":%llu,\"Rroot\":%llu,\"Rother\":%llu,\"Lself\":%llu,\"Laggr\":%llu,\"Lroot\":%llu,\"Lother\":%llu,"
			"\"nself\":%llu,\"naggr\":%llu,\"nroot\":%llu,\"nother\":%llu,\"Qidle\":%llu,\"Qidlen\":%llu,\"preempt\":%llu,\"multi\":%llu,\"mig\":%llu,"
			"\"Oself\":%llu,\"Oaggr\":%llu,\"Oroot\":%llu,\"Oother\":%llu,\"Oidle\":%llu,\"Sself\":%llu,\"Saggr\":%llu,\"Sroot\":%llu,\"Sother\":%llu,\"Sidle\":%llu,"
			"\"Wo0\":%llu,\"Wo1\":%llu,\"Wo2\":%llu,\"Wo3\":%llu,\"Wo4\":%llu,\"Won0\":%llu,\"Won4\":%llu}\n",
			now_ms(), s.R[0], s.R[1], s.R[2], s.R[3], s.RL[0], s.RL[1], s.RL[2], s.RL[3], s.Rn[0], s.Rn[1], s.Rn[2], s.Rn[3], s.Qidle, s.Qidlen, s.preempt, s.multi, s.mig,
			s.occw[0], s.occw[1], s.occw[2], s.occw[3], s.occw[4], s.occs[0], s.occs[1], s.occs[2], s.occs[3], s.occs[4], s.Wo[0], s.Wo[1], s.Wo[2], s.Wo[3], s.Wo[4], s.Won[0], s.Won[4]);
		if (++n % 10 == 0) fflush(fo);
		if (now_ms() - last_sec >= 1000) {
			last_sec = now_ms();
			// per-thread R: walk, print non-zero, zero (bpftrace: print(@Rthr); zero(@Rthr))
			__u32 key, nk; int first = 1; struct ps v;
			fprintf(fh, "{\"t\":%llu,\"kind\":\"map\",\"data\":{\"@Rthr\":{", last_sec);
			int more = bpf_map_get_next_key(pfd, NULL, &key) == 0;
			while (more) {
				more = bpf_map_get_next_key(pfd, &key, &nk) == 0;
				if (bpf_map_lookup_elem(pfd, &key, &v) == 0 && v.rthr) {
					fprintf(fh, "%s\"%u\":%llu", first ? "" : ",", key, v.rthr); first = 0;
					v.rthr = 0; bpf_map_update_elem(pfd, &key, &v, BPF_EXIST);
				}
				key = nk;
			}
			fprintf(fh, "}}}\n{\"t\":%llu,\"kind\":\"hist\",\"data\":{\"@Rhist\":[", last_sec);
			for (__u32 b = 0; b < 64; b++) {
				u64 tot = 0;
				if (bpf_map_lookup_elem(hfd, &b, hv) == 0) { for (int cpu = 0; cpu < ncpu; cpu++) tot += hv[cpu]; bpf_map_update_elem(hfd, &b, hz, BPF_ANY); }
				fprintf(fh, "%s%llu", b ? "," : "", tot);
			}
			fprintf(fh, "]}}\n"); fflush(fh);
			fprintf(fo, "{\"event\":\"self\",\"t\":%llu,\"cpu_ns\":%llu}\n", now_ms(), self_ns());
		}
	}
	fprintf(fo, "{\"event\":\"end\",\"t\":%llu,\"windows\":%d}\n", now_ms(), n);
	fclose(fo); fclose(fh);
	bpf_object__close(obj);
	return 0;
}
