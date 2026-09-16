/* contracts/afe.h — the seam between the firmware engine and ROLE 2,
 * Analog Signal Conditioning (the hardware filter).
 *
 * OWNERSHIP
 *   Role 2 OWNS:  which window, its parameter, the target sidelobe level,
 *                 the reconstruction filter, the DAC buffer decision, and the
 *                 output impedance the DAC pin is allowed to see.
 *   Firmware OWNS: applying whatever they choose, at the sample rate, without
 *                 stalling the CPU.
 *
 * The problem statement puts digital windowing in the same sentence as the
 * analog filter ("...combined with digital windowing (Hamming/Hann/Blackman)
 * to smooth voltage transitions and eliminate sidelobe artifacts"), which is
 * why the window lives on THIS side of the line and not in the synthesis
 * header. Role 2 names the window; this engine implements every one of them.
 */
#ifndef CONTRACT_AFE_H
#define CONTRACT_AFE_H

typedef enum {
    WIN_RECT     = 0,  /* no taper. -13.3 dB first sidelobe, worst case.   */
    WIN_TUKEY    = 1,  /* tapered cosine. Flat top, so transmit energy is
                        * preserved while the switching edge is killed.
                        * The transmit window unless told otherwise.        */
    WIN_HANN     = 2,
    WIN_HAMMING  = 3,
    WIN_BLACKMAN = 4
} window_kind_t;

/* Role 2's current specification. Change these two lines when they hand over
 * a number, and nothing else in the firmware moves.
 *
 * NOTE: the idea deck says alpha = 0.30 and the technical audit says 0.10.
 * Those two documents disagree. Role 2 has to pick one; 0.30 is the deck's
 * value and is what is compiled here until they say otherwise. */
#define AFE_TX_WINDOW        WIN_TUKEY
#define AFE_TX_WINDOW_ALPHA  0.30f

/* DAC output buffer. Role 2's call, because it is about output impedance.
 *   0 = internal buffer ON.  Low impedance, drives a probe or a filter
 *       directly, but the internal amplifier slew-limits above ~1 MSPS.
 *   1 = internal buffer OFF. Good to 15 MSPS, but the pin then presents
 *       roughly 15 kOhm and MUST feed an external unity-gain follower —
 *       never a filter or a scope probe directly, or the amplitude collapses
 *       and the filter's cutoff moves.
 * Phase 1 (1 MSPS bench demo) uses 0. Phase 2 (5 MSPS) requires 1 AND their
 * follower stage to exist. */
#define AFE_DAC_BUFFER_OFF   0

#endif /* CONTRACT_AFE_H */
