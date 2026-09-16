/* src/chirp.h — waveform synthesis engine.  OWNER: firmware (DMA & C).
 *
 * Turns a parameter set into a block of 12-bit DAC codes. No hardware
 * dependency of any kind, so it compiles and runs on a PC — which is how
 * every claim about the waveform is proved before the board is powered.
 *
 * Four modulation laws, because the problem statement asks for "multiple
 * modulation types on the fly". Two of them cost two integer adds per
 * sample; two need a transcendental. Under precompute-once that difference
 * is affordable either way — synthesis runs at the ADAPTATION rate (~1 Hz),
 * not the sample rate.
 */
#ifndef CHIRP_H
#define CHIRP_H

#include <stddef.h>
#include <stdint.h>
#include "afe.h"

/* FOUR MODULATION LAWS:
 *   MOD_LFM        chirp.c, search for  [MODULATION 1/4]
 *   MOD_HFM        chirp.c, search for  [MODULATION 2/4]
 *   MOD_GEOMETRIC  chirp.c, search for  [MODULATION 3/4]
 *   MOD_BARKER13   chirp.c, search for  [MODULATION 4/4]
 *
 * HFM provides full wideband Doppler tolerance for moving AUVs (2-4 knots).
 */
typedef enum {
    MOD_LFM       = 0, /* linear sweep. f(t) = f0 + k t.
                        * Two integer adds per sample - no libm in the
                        * loop, and no accumulating rounding error because
                        * the increment is seeded at the half-sample
                        * midpoint. The workhorse.                        */
    MOD_HFM       = 1, /* hyperbolic sweep. 1/f linear in t.
                        * DOPPLER-INVARIANT: a moving platform dilation
                        * shifts the correlation peak in time without
                        * blunting or broadening the correlation peak.
                        * Evaluated via log() at adaptation rate.          */
    MOD_GEOMETRIC = 2, /* exponential / logarithmic sweep.
                        * f(t) = f0 * (f1/f0)^(t/T), so the fractional
                        * bandwidth per unit time is constant. Needs an
                        * exp() per sample, which is affordable because
                        * synthesis runs at the adaptation rate, not the
                        * sample rate.                                     */
    MOD_BARKER13  = 3  /* 13-chip Barker BPSK at the centre frequency.
                        * Constant carrier, phase flips 180 degrees at six
                        * of the twelve chip boundaries. The cheapest law
                        * here: an integer add plus an occasional flip.
                        *
                        * MANDATORY SAFEGUARD RULE:
                        * Barker-13 phase-coded pulses MUST be paired with
                        * WIN_RECT (rectangular window). A Barker code's
                        * 13:1 (-22.3 dB) autocorrelation depends on every
                        * chip carrying EQUAL amplitude. Applying an amplitude
                        * taper (such as Tukey or Hann) destroys destructive
                        * sidelobe cancellation, degrading PSLR from -22.3 dB
                        * to -13.3 dB (Tukey 0.30) or -4.8 dB (Hann), wiping
                        * out the processing gain benefit. Role 2 chooses the
                        * window and Role 1 chooses the modulation; ensure
                        * codes are strictly paired with WIN_RECT.         */
} mod_law_t;

typedef struct {
    float         fs_hz;        /* DAC sample rate                         */
    float         f0_hz;        /* sweep start (or lower band edge)        */
    float         f1_hz;        /* sweep end   (or upper band edge)        */
    float         pulse_s;      /* pulse length                            */
    float         amplitude;    /* 0.0 .. 1.0 of full scale                */
    mod_law_t     law;          /* set by Role 1 via the mode table        */
    window_kind_t window;       /* set by Role 2 via contracts/afe.h       */
    float         window_alpha; /* Tukey only; ignored by the others       */
} chirp_params_t;

#define CHIRP_DAC_MID   2048u   /* 12-bit unipolar midscale                */
#define CHIRP_DAC_MAX   4095u

/* Build the sine table. Call once before chirp_generate(). Idempotent. */
void   chirp_init(void);

/* Samples a parameter set will produce. */
size_t chirp_num_samples(const chirp_params_t *p);

/* Synthesise into a caller-supplied buffer. Returns samples written, or 0
 * if cap is too small. Output is 12-bit right-aligned for DAC_DHR12R1. */
size_t chirp_generate(const chirp_params_t *p, uint16_t *out, size_t cap);

/* Window value at sample n of N. Exposed so the host tests can check it
 * against an independent implementation. */
float  chirp_window(window_kind_t kind, float alpha, size_t n, size_t N);

/* Human-readable names, for the console and the test output. */
const char *chirp_law_name(mod_law_t law);
const char *chirp_window_name(window_kind_t w);

#endif /* CHIRP_H */
