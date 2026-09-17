/* Guardian ABI 1. Each handle owns all DSP, link and resampler state.
 * Calls on one handle must be serialized; different handles are independent. */
#include <stdlib.h>
#include <string.h>
#include "shell/runtime.h"
#include "shell/resample.h"
#include "codec/dataframe.h"
#ifdef _WIN32
#define API __declspec(dllexport)
#else
#define API __attribute__((visibility("default")))
#endif
typedef struct {
    ardop_runtime rt;
    ardop_resampler down, up;
    uint64_t clock;
    unsigned char received[65536];
    size_t received_len;
    int failed;
} guardian_ardop;
static void observe(void *ctx, const ardop_obs *o) {
    guardian_ardop *g = ctx;
    if (o->kind == ARDOP_OBS_RX_DATA) {
        if (o->data_len > sizeof(g->received) - g->received_len) g->failed = 1;
        else {
            memcpy(g->received + g->received_len, o->data, o->data_len);
            g->received_len += o->data_len;
        }
    }
}
API int ga_abi(void) { return 1; }
API void *ga_create(const char *call, int receive_only) {
    guardian_ardop *g = calloc(1, sizeof(*g));
    const int lens[] = {2,4,8,16,32,36,50,64};
    if (!g) return NULL;
    if (!ardop_runtime_init(&g->rt, lens, 8) ||
        ardop_stationid_from_str(call, &g->rt.link.mycall) != 0 ||
        !ardop_resample_init(&g->down, ARDOP_RESAMPLE_DECIMATE, 4) ||
        !ardop_resample_init(&g->up, ARDOP_RESAMPLE_INTERPOLATE, 4)) {
        free(g); return NULL;
    }
    g->rt.link.bw_setting = ARDOP_ARQ_BW_500_MAX;
    g->rt.link.listening = !receive_only;
    g->rt.link.mode = receive_only ? ARDOP_MODE_RXO : ARDOP_MODE_ARQ;
    g->rt.link.auto_break = true;
    g->rt.link.arq_timeout = 120;
    ardop_mod_init(&g->rt.mod, 30);
    ardop_runtime_observe(&g->rt, observe, g);
    return g;
}
API void ga_destroy(guardian_ardop *g) { free(g); }
/* n is a 48 kHz sample count, a multiple of four, at most 4096. */
API int ga_rx(guardian_ardop *g, const int16_t *in, int n) {
    int16_t pcm[1024];
    if (!g || n < 0 || n > 4096 || n % 4) return -1;
    size_t count = ardop_resample(&g->down, in, (size_t)n, pcm);
    g->clock += count;
    ardop_runtime_rx(&g->rt, pcm, count, g->clock);
    ardop_runtime_timer(&g->rt, g->clock);
    return g->failed ? -1 : 0;
}
API int ga_tx(guardian_ardop *g, int16_t *out, int n) {
    int16_t pcm[1024] = {0};
    if (!g || n < 0 || n > 4096 || n % 4) return -1;
    ardop_mod_pull(&g->rt.mod, pcm, (size_t)n / 4);
    /* Always convert the complete block: trailing zeros flush the FIR. */
    ardop_resample(&g->up, pcm, (size_t)n / 4, out);
    return n;
}
API void ga_finish_tx(guardian_ardop *g, int elapsed_samples) {
    int16_t scratch[1];
    if (elapsed_samples > 0) g->clock += (uint64_t)elapsed_samples / 4;
    g->rt.now = g->clock;
    ardop_runtime_pull_tx(&g->rt, scratch, 1);
}
API int ga_command(guardian_ardop *g, int kind, const char *target,
                   const uint8_t *data, int n) {
    if (!g || n < 0 || kind < 0 || kind > 7) return -1;
    ardop_host_cmd cmd = {0}; cmd.kind = (ardop_host_cmd_kind)kind;
    cmd.bandwidth = ARDOP_ARQ_BW_500_MAX;
    if (kind == ARDOP_CMD_CONNECT &&
        (!target || ardop_stationid_from_str(target, &cmd.target) != 0)) return -1;
    if (kind == ARDOP_CMD_SEND_DATA && (size_t)n > g->rt.link.tx_cap - g->rt.link.tx_len) return -1;
    cmd.data = data; cmd.data_len = (size_t)n;
    ardop_runtime_host(&g->rt, &cmd, g->clock);
    return 0;
}
API int ga_read(guardian_ardop *g, uint8_t *out, int cap) {
    if (!g || cap < 0 || g->failed) return -1;
    size_t n = g->received_len < (size_t)cap ? g->received_len : (size_t)cap;
    memcpy(out, g->received, n);
    g->received_len -= n;
    memmove(g->received, g->received + n, g->received_len);
    return (int)n;
}
API int ga_status(guardian_ardop *g, int field) {
    switch (field) {
    case 0: return g->rt.link.state;
    case 1: return g->rt.tx_active;
    case 2: return (int)g->rt.link.tx_len;
    case 3: return g->rt.link.session_bw;
    case 4: return g->rt.last_quality;
    case 5: return g->rt.last_sn;
    case 6: return ardop_mod_busy(&g->rt.mod);
    case 7: return g->rt.link.last_data_sent;
    default: return -1;
    }
}
API int ga_remote(guardian_ardop *g, char *out, int cap) {
    const char *s = g->rt.link.remote.str;
    if (cap <= 0) return -1;
    size_t n = strlen(s); if (n >= (size_t)cap) return -1;
    memcpy(out, s, n+1); return (int)n;
}
/* Stateless FEC control frame, same ARDOP PHY, within the 500 Hz ceiling. */
API int ga_fec(guardian_ardop *g, const uint8_t *data, int n, int frame_type) {
    uint8_t encoded[ARDOP_DATAFRAME_MAX];
    int capacity;
    switch (frame_type) {
    case 0x48: case 0x42: capacity = 16; break;
    case 0x40: capacity = 64; break;
    case 0x50: capacity = 128; break;
    case 0x52: capacity = 216; break;
    case 0x54: capacity = 256; break;
    default: return -1;
    }
    if (n < 1 || n > capacity || g->rt.tx_active) return -1;
    int len = ardop_encode_data_frame(&g->rt.rs, (uint8_t)frame_type, 0xff, data, n, encoded);
    if (len <= 0 || !ardop_mod_begin(&g->rt.mod, (uint8_t)frame_type, encoded, (size_t)len,
                                    300, g->rt.tx_samples, ARDOP_MOD_MAX_SAMPLES)) return -1;
    g->rt.tx_active = true;
    return 0;
}
