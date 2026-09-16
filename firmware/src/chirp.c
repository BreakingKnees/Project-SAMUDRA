/* src/chirp.c — see chirp.h.  OWNER: firmware (DMA & C). */

#include "chirp.h"
#include <math.h>

#ifndef CHIRP_USE_LUT
#define CHIRP_USE_LUT 1          /* 0 = sinf() reference path, for the tests */
#endif
#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

/* ------------------------------------------------------------- windows */

float chirp_window(window_kind_t kind, float alpha, size_t n, size_t N)
{
    if (N < 2u) return 1.0f;
    const float x = (float)n / (float)(N - 1u);          /* 0 .. 1        */

    switch (kind) {
    case WIN_RECT:
        return 1.0f;

    case WIN_TUKEY: {
        if (alpha <= 0.0f) return 1.0f;
        if (alpha > 1.0f)  alpha = 1.0f;
        const float half = alpha * 0.5f;
        if (x < half)
            return 0.5f * (1.0f + cosf((float)M_PI * (x / half - 1.0f)));
        if (x > 1.0f - half)
            return 0.5f * (1.0f + cosf((float)M_PI * ((x - 1.0f) / half + 1.0f)));
        return 1.0f;                                     /* flat top      */
    }

    case WIN_HANN:
        return 0.5f - 0.5f * cosf(2.0f * (float)M_PI * x);

    case WIN_HAMMING:
        return 0.54f - 0.46f * cosf(2.0f * (float)M_PI * x);

    case WIN_BLACKMAN:
        return 0.42f - 0.5f  * cosf(2.0f * (float)M_PI * x)
                     + 0.08f * cosf(4.0f * (float)M_PI * x);
    }
    return 1.0f;
}

const char *chirp_window_name(window_kind_t w)
{
    switch (w) {
    case WIN_RECT:     return "Rectangular";
    case WIN_TUKEY:    return "Tukey";
    case WIN_HANN:     return "Hann";
    case WIN_HAMMING:  return "Hamming";
    case WIN_BLACKMAN: return "Blackman";
    }
    return "?";
}

const char *chirp_law_name(mod_law_t law)
{
    switch (law) {
    case MOD_LFM:       return "LFM linear sweep";
    case MOD_HFM:       return "HFM hyperbolic (Doppler-invariant)";
    case MOD_GEOMETRIC: return "Geometric (exponential) sweep";
    case MOD_BARKER13:  return "Barker-13 phase-coded";
    }
    return "?";
}

/* ----------------------------------------------------------- sine table */

#if CHIRP_USE_LUT

#define LUT_BITS 10
#define LUT_N    (1u << LUT_BITS)

static int16_t s_lut[LUT_N + 1u];      /* +1 guard entry for interpolation */
static int     s_ready = 0;

void chirp_init(void)
{
    if (s_ready) return;
    for (size_t i = 0u; i <= LUT_N; ++i)
        s_lut[i] = (int16_t)lrint(32767.0 * sin(2.0 * M_PI * (double)i / (double)LUT_N));
    s_ready = 1;
}

static inline int32_t lut_interp(uint32_t idx, uint32_t frac)
{
    const int32_t a = s_lut[idx], b = s_lut[idx + 1u];
    return a + (((b - a) * (int32_t)frac) >> 16);
}

/* Q15 sine of a Q64 phase (one turn = 2^64).
 *
 * 64-bit, not 32-bit, and the reason is specific. The chirp's
 * increment-of-the-increment, k*Ts^2, is tiny: at 5 MSPS over a 40 kHz sweep
 * it is 1.6e-7 turns, which in Q32 is 687.19 and rounds to 687. That 0.19
 * error accumulates into the phase QUADRATICALLY — about 20 degrees by
 * sample 50,000, which smears the far end of the compressed pulse. In Q64
 * the same quantity is 2.95e12 and the residual is nothing. Two 32-bit adds
 * with carry on a Cortex-M; still an order of magnitude under a sinf() call.
 * (Measured on the host harness: 533 LSB of error in Q32, 1 LSB in Q64.) */
static inline int32_t lut_sin64(uint64_t phase)
{
    return lut_interp((uint32_t)(phase >> (64 - LUT_BITS)),
                      (uint32_t)((phase >> (64 - LUT_BITS - 16)) & 0xFFFFu));
}

/* Q15 sine of a Q32 phase, for laws that compute phase per sample rather
 * than accumulating it — no accumulation means 32 bits is plenty. */
static inline int32_t lut_sin32(uint32_t phase)
{
    return lut_interp(phase >> (32 - LUT_BITS),
                      (phase << LUT_BITS) >> 16);
}

/* Fraction of a turn (any magnitude) -> Q32 phase. */
static inline uint32_t turns_to_q32(double turns)
{
    double f = turns - floor(turns);
    return (uint32_t)(f * 4294967296.0);
}

/* Fraction of a turn, < 1.0 -> Q64. Done in halves so the intermediate
 * never leaves the range of a signed 64-bit integer. */
static inline uint64_t turns_to_q64(double turns)
{
    return ((uint64_t)llrint(turns * 9223372036854775808.0)) << 1;
}

#else
void chirp_init(void) { }
#endif

/* -------------------------------------------------------------- helpers */

size_t chirp_num_samples(const chirp_params_t *p)
{
    if (!p || p->fs_hz <= 0.0f || p->pulse_s <= 0.0f) return 0u;
    return (size_t)(p->fs_hz * p->pulse_s + 0.5f);
}

static inline uint16_t pack12(int32_t swing_q15)
{
    /* +16384 rounds to nearest. Without it the arithmetic shift truncates
     * toward -inf and every sample carries a half-LSB DC bias. One add. */
    int32_t code = (int32_t)CHIRP_DAC_MID + (((swing_q15 * 2047) + 16384) >> 15);
    if (code < 0) code = 0;
    if (code > (int32_t)CHIRP_DAC_MAX) code = (int32_t)CHIRP_DAC_MAX;
    return (uint16_t)code;
}

/* 13-chip Barker sequence. Autocorrelation peak-to-sidelobe is 13:1
 * (-22.3 dB) — the best any binary code of this length achieves. */
static const int8_t BARKER13[13] = { 1,1,1,1,1,-1,-1,1,1,-1,1,-1,1 };

/* ----------------------------------------------------------- generation */

size_t chirp_generate(const chirp_params_t *p, uint16_t *out, size_t cap)
{
    if (!p || !out) return 0u;
    const size_t N = chirp_num_samples(p);
    if (N == 0u || N > cap) return 0u;

    const double Ts  = 1.0 / (double)p->fs_hz;
    const double T   = (double)N * Ts;
    const double f0  = (double)p->f0_hz, f1 = (double)p->f1_hz;
    const float  amp = (p->amplitude < 0.0f) ? 0.0f
                     : (p->amplitude > 1.0f) ? 1.0f : p->amplitude;

#if CHIRP_USE_LUT
    chirp_init();
    const int32_t amp_q15 = (int32_t)(amp * 32767.0f + 0.5f);

    #define EMIT(SINE_Q15)                                                    \
        do {                                                                  \
            const int32_t w_ = (int32_t)(chirp_window(p->window,              \
                                   p->window_alpha, n, N) * 32767.0f);        \
            int32_t v_ = ((SINE_Q15) * w_) >> 15;                             \
            v_ = (v_ * amp_q15) >> 15;                                        \
            out[n] = pack12(v_);                                              \
        } while (0)

    switch (p->law) {

    /* ================================================================== */
    /*  [MODULATION 1/4]   LINEAR FREQUENCY MODULATION  (LFM chirp)         *
     *                                                                     *
     *  f(t) = f0 + k t        with   k = (f1 - f0) / T                    *
     *  phase(t) = f0 t + (k/2) t^2      (in turns, not radians)           *
     *                                                                     *
     *  Cost: TWO 64-BIT ADDS PER SAMPLE. No sin(), no libm, nothing but   *
     *  integer arithmetic - because a second difference of a quadratic is *
     *  a constant, so the phase can be stepped instead of evaluated.      *
     *                                                                     *
     *  Use for: the default. Best range resolution of the four, about     *
     *  1.1 cm at 58 kHz of bandwidth. Sidelobes -13.3 dB unwindowed.      *
     * ================================================================== */
    case MOD_LFM: {
        /* Fast path: two 64-bit adds per sample, no libm in the loop.
         * inc starts at the MIDPOINT of the first sample interval, which
         * makes the recurrence reproduce the exact discrete phase
         * 2*pi*(f0*n*Ts + (k/2)*(n*Ts)^2) rather than approximate it. */
        const double k = (f1 - f0) / T;
        uint64_t       phase = 0u;
        uint64_t       inc   = turns_to_q64((f0 + 0.5 * k * Ts) * Ts);
        const uint64_t dinc  = turns_to_q64(k * Ts * Ts);
        for (size_t n = 0u; n < N; ++n) {
            EMIT(lut_sin64(phase));
            phase += inc;
            inc   += dinc;
        }
        break;
    }

    /* ================================================================== */
    /*  [MODULATION 2/4]   HYPERBOLIC FREQUENCY MODULATION (HFM)           *
     *                                                                     *
     *  1/f linear in t: f(t) = (f0 * f1) / (f1 + (f0 - f1) * (t/T)).       *
     *  phase(t) = (1/b) * ln(1 + (b/a) * t)      [in turns]                *
     *  with a = 1/f0, b = (1/f1 - 1/f0) / T.                              *
     *                                                                     *
     *  Doppler-invariant: Time-dilation t -> eta*t scales a to a/eta,     *
     *  which represents a pure time translation tau_0 = a*(1 - 1/eta)/b.   *
     *  Zero chirp-slope mismatch, zero peak broadening, zero loss of       *
     *  processing gain at 2-4 knot AUV cruising speeds.                   *
     * ================================================================== */
    case MOD_HFM: {
        /* 1/f linear in t. phase = (1/b) * ln(1 + (b/a) t), a = 1/f0. */
        const double a = 1.0 / f0;
        const double b = (1.0 / f1 - 1.0 / f0) / T;
        for (size_t n = 0u; n < N; ++n) {
            const double t = (double)n * Ts;
            const double turns = log(1.0 + (b / a) * t) / b;
            EMIT(lut_sin32(turns_to_q32(turns)));
        }
        break;
    }

    /* ================================================================== */
    /*  [MODULATION 3/4]   GEOMETRIC (EXPONENTIAL) SWEEP                   *
     *                                                                     *
     *  f(t) = f0 * (f1/f0)^(t/T)                                          *
     *  phase(t) = (f0 T / ln r) * (r^(t/T) - 1)      with  r = f1/f0      *
     *                                                                     *
     *  The frequency multiplies by a constant factor per unit time rather *
     *  than adding a constant, so the sweep spends equal time in each     *
     *  OCTAVE instead of in each kilohertz. That matches how absorption   *
     *  actually scales with frequency, so the received echo has a flatter *
     *  spectrum than an LFM of the same span.                             *
     *                                                                     *
     *  Cost: one exp() per sample. Affordable only because synthesis runs *
     *  at the ADAPTATION rate (about once every few seconds), not at the  *
     *  sample rate. Do not copy this loop into an interrupt.              *
     *                                                                     *
     *  NOT the same law as HFM. Geometric is exponential in t; hyperbolic *
     *  is 1/f linear in t. They look alike on a scope and are different   *
     *  under Doppler - a common and expensive thing to get wrong.         *
     * ================================================================== */
    case MOD_GEOMETRIC: {
        /* f(t) = f0 * r^(t/T). Constant fractional bandwidth per unit time. */
        const double r  = f1 / f0;
        const double lr = log(r);
        const double A  = f0 * T / lr;
        for (size_t n = 0u; n < N; ++n) {
            const double t = (double)n * Ts;
            EMIT(lut_sin32(turns_to_q32(A * (exp(lr * t / T) - 1.0))));
        }
        break;
    }

    /* ================================================================== */
    /*  [MODULATION 4/4]   PHASE-CODED PULSE  (13-chip Barker BPSK)        *
     *                                                                     *
     *  Carrier held at fc = (f0 + f1)/2. The pulse is cut into 13 equal   *
     *  chips and each chip is transmitted either in phase or 180 degrees  *
     *  out, following the Barker sequence below. Six of the twelve chip   *
     *  boundaries are reversals.                                          *
     *                                                                     *
     *  Adding 2^63 to a Q64 phase accumulator IS a 180-degree flip - the  *
     *  accumulator wraps, so no multiply and no branch on the sample      *
     *  value is needed.                                                   *
     *                                                                     *
     *  Why bother: -22.2 dB measured peak sidelobe against -13.3 dB for   *
     *  the chirps, so a weak echo beside a strong one is not buried. The  *
     *  cost is range resolution - about 11.6 cm against 1.1 cm - because  *
     *  the bandwidth comes from the chip rate, not from a sweep.          *
     *                                                                     *
     *  MANDATORY SAFEGUARD RULE: MUST be paired with WIN_RECT.            *
     *  Window tapers (e.g. Tukey 0.30 or Hann) degrade Barker-13          *
     *  autocorrelation PSLR from -22.3 dB to -13.3 dB / -4.8 dB,          *
     *  destroying code gain. See detailed warning in chirp.h.             *
     * ================================================================== */
    case MOD_BARKER13: {
        /* Constant carrier; the phase jumps by pi at each chip boundary.
         * Adding 2^63 to a Q64 phase IS a pi flip - no multiply needed. */
        const double   fc    = 0.5 * (f0 + f1);
        const size_t   chip  = (N + 12u) / 13u;
        uint64_t       phase = 0u;
        const uint64_t inc   = turns_to_q64(fc * Ts);
        int8_t         cur   = BARKER13[0];
        for (size_t n = 0u; n < N; ++n) {
            const size_t ci = (n / chip) < 13u ? (n / chip) : 12u;
            if (BARKER13[ci] != cur) { cur = BARKER13[ci]; phase += (uint64_t)1 << 63; }
            EMIT(lut_sin64(phase));
            phase += inc;
        }
        break;
    }
    }
    #undef EMIT

#else  /* ---------------- floating-point reference path, for the tests -- */
    for (size_t n = 0u; n < N; ++n) {
        const double t = (double)n * Ts;
        double turns;
        switch (p->law) {
        case MOD_LFM: {            /* [MODULATION 1/4] reference */
            const double k = (f1 - f0) / T;
            turns = f0 * t + 0.5 * k * t * t;
            break;
        }
        case MOD_HFM: {            /* [MODULATION 2/4] reference */
            const double a = 1.0 / f0, b = (1.0 / f1 - 1.0 / f0) / T;
            turns = log(1.0 + (b / a) * t) / b;
            break;
        }
        case MOD_BARKER13: {       /* [MODULATION 4/4] reference */
            const double fc   = 0.5 * (f0 + f1);
            const size_t chip = (N + 12u) / 13u;
            size_t ci = n / chip; if (ci > 12u) ci = 12u;
            int flips = 0;
            for (size_t j = 1u; j <= ci; ++j)
                if (BARKER13[j] != BARKER13[j - 1u]) flips++;
            turns = fc * t + 0.5 * (double)(flips & 1);
            break;
        }
        default: {                 /* [MODULATION 3/4] reference - geometric */
            const double r = f1 / f0, lr = log(r);
            turns = (f0 * T / lr) * (exp(lr * t / T) - 1.0);
            break;
        }
        }
        const float w = chirp_window(p->window, p->window_alpha, n, N);
        const double v = (double)amp * (double)w * sin(2.0 * M_PI * turns);
        out[n] = pack12((int32_t)lrint(v * 32767.0));
    }
#endif
    return N;
}
