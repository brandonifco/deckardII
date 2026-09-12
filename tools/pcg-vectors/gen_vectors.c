/*
 * gen_vectors.c -- emit PCG32 test vectors by CALLING the upstream reference
 * implementation, which generate.sh fetches at a pinned commit and hash-verifies.
 *
 * Nothing in this file reimplements PCG. It drives pcg_basic.c through its published
 * API and prints what the reference produced, as JSON on stdout. That is the whole
 * point: a vector this file invented would be worthless as evidence.
 *
 * The framework never vendors the reference. See README.md in this directory.
 */
#include <stdio.h>
#include <inttypes.h>
#include "pcg_basic.h"

/* rot for a given state, expressed without a shift operator so that the value printed
 * here is arithmetic this file performs rather than a restatement of the reference. */
#define ROT_OF(state) ((uint64_t)((state) / 576460752303423488ULL))

static void seeded(const char *name, uint64_t initstate, uint64_t initseq, int n)
{
    pcg32_random_t rng;
    pcg32_srandom_r(&rng, initstate, initseq);
    printf("    {\n");
    printf("      \"kind\": \"seeded\",\n");
    printf("      \"name\": \"%s\",\n", name);
    printf("      \"initstate\": \"0x%016" PRIx64 "\",\n", initstate);
    printf("      \"initseq\": \"0x%016" PRIx64 "\",\n", initseq);
    printf("      \"state_after_seed\": \"0x%016" PRIx64 "\",\n", rng.state);
    printf("      \"inc_after_seed\": \"0x%016" PRIx64 "\",\n", rng.inc);
    printf("      \"outputs\": [");
    for (int i = 0; i < n; ++i) {
        printf("%s\"0x%08" PRIx32 "\"", i ? ", " : "", pcg32_random_r(&rng));
    }
    printf("]\n");
    printf("    },\n");
}

/* Drive the reference from an explicit (state, inc) pair. The struct members are public
 * in pcg_basic.h, and assigning them is exactly what restoring serialised state does. */
static void raw(const char *name, uint64_t state, uint64_t inc, int n)
{
    pcg32_random_t rng;
    rng.state = state;
    rng.inc = inc;
    printf("    {\n");
    printf("      \"kind\": \"raw\",\n");
    printf("      \"name\": \"%s\",\n", name);
    printf("      \"state\": \"0x%016" PRIx64 "\",\n", state);
    printf("      \"inc\": \"0x%016" PRIx64 "\",\n", inc);
    printf("      \"rot_of_first_draw\": %" PRIu64 ",\n", ROT_OF(state));
    printf("      \"outputs\": [");
    for (int i = 0; i < n; ++i) {
        printf("%s\"0x%08" PRIx32 "\"", i ? ", " : "", pcg32_random_r(&rng));
    }
    printf("],\n");
    printf("      \"state_after\": \"0x%016" PRIx64 "\",\n", rng.state);
    printf("      \"inc_after\": \"0x%016" PRIx64 "\"\n", rng.inc);
    printf("    },\n");
}

/* Seed, draw `skip` values, capture the state, draw `n` more. Pins mid-sequence resume:
 * a restored state must continue the sequence, not restart it. */
static void resume(const char *name, uint64_t initstate, uint64_t initseq,
                   int skip, int n)
{
    pcg32_random_t rng;
    pcg32_srandom_r(&rng, initstate, initseq);
    for (int i = 0; i < skip; ++i) {
        (void)pcg32_random_r(&rng);
    }
    printf("    {\n");
    printf("      \"kind\": \"resume\",\n");
    printf("      \"name\": \"%s\",\n", name);
    printf("      \"initstate\": \"0x%016" PRIx64 "\",\n", initstate);
    printf("      \"initseq\": \"0x%016" PRIx64 "\",\n", initseq);
    printf("      \"skip\": %d,\n", skip);
    printf("      \"state_at_capture\": \"0x%016" PRIx64 "\",\n", rng.state);
    printf("      \"inc_at_capture\": \"0x%016" PRIx64 "\",\n", rng.inc);
    printf("      \"outputs_after_capture\": [");
    for (int i = 0; i < n; ++i) {
        printf("%s\"0x%08" PRIx32 "\"", i ? ", " : "", pcg32_random_r(&rng));
    }
    printf("]\n");
    printf("    },\n");
}

int main(void)
{
    printf("{\n");
    printf("  \"generator\": \"pcg_setseq_64_xsh_rr_32\",\n");
    printf("  \"vectors\": [\n");

    /* The seed used by the reference project's own pcg32-demo and check-pcg32, whose
     * expected output is published upstream -- so this row is independently checkable
     * against a file nobody in this repository produced. */
    seeded("canonical-42-54", 42u, 54u, 16);
    seeded("zero-zero", 0u, 0u, 16);
    seeded("one-one", 1u, 1u, 16);
    seeded("max-state-max-seq", UINT64_MAX, UINT64_MAX, 16);
    seeded("high-entropy", 0x853c49e6748fea9bULL, 0xda3e39cb94b95bdbULL, 16);

    /* One initial state, three adjacent streams. Streams must never coincide. */
    seeded("stream-a", 42u, 0u, 8);
    seeded("stream-b", 42u, 1u, 8);
    seeded("stream-c", 42u, 2u, 8);

    /* Raw (state, inc) entry points, which is what state restore does. A zero state
     * forces rot == 0 on the first draw -- the branch where a port that writes a plain
     * 32-bit shift instead of a rotate still passes every other vector. The all-ones
     * state forces rot == 31, the other end of the same range. */
    raw("initializer", 0x853c49e6748fea9bULL, 0xda3e39cb94b95bdbULL, 8);
    raw("rot-zero", 0x0000000000000000ULL, 0x0000000000000001ULL, 8);
    raw("rot-max", 0xffffffffffffffffULL, 0x0000000000000001ULL, 8);

    resume("resume-42-54", 42u, 54u, 5, 8);
    resume("resume-canonical-long", 42u, 54u, 100, 8);

    /* Trailing sentinel: keeps every vector row above able to end in a comma, so a row
     * can be added or removed without touching its neighbours. generate.sh strips it. */
    printf("    null\n");
    printf("  ]\n");
    printf("}\n");
    return 0;
}
