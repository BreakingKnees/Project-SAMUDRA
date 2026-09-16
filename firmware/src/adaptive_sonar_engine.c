/**
 * @file adaptive_sonar_engine.c
 * @brief Closed-loop Oceanographic Acoustic Adaptation Engine Implementation for STM32G474RE.
 *
 * Implements the core physical acoustic models and optimization engine:
 * 1. Mackenzie (1981) 9-term sound velocity polynomial in Horner form.
 * 2. Ainslie-McColm (1998) seawater sound absorption (Boric acid, MgSO4, pure water).
 * 3. Suspended particulate sediment Rayleigh scattering attenuation.
 * 4. Ambient ocean thermal noise spectral density and monostatic active sonar equation.
 * 5. Deterministic bounded 10-iteration bisection search for carrier frequency f_c.
 * 6. Fractional bandwidth B = 0.22 * f_c with guardband clamping to [100 kHz, 500 kHz].
 * 7. Pulse duration scheduling T_pulse in [1.0 ms, 10.0 ms].
 * 8. Amplitude scaling A_scale in [0.20, 1.00] with strict battery conservation.
 *
 * @note Zero dynamic memory allocation (0 bytes heap).
 * @note Strict single-precision IEEE 754 float arithmetic (zero double promotion).
 * @note MISRA C:2012 compliant.
 */

#include "adaptive_sonar_engine.h"
#include <math.h>
#include <stddef.h>

/* ========================================================================= */
/* Static Assertion Verification                                             */
/* ========================================================================= */

#if defined(__STDC_VERSION__) && (__STDC_VERSION__ >= 201112L)
_Static_assert(sizeof(sonar_ocean_inputs_t) == 24U, "sonar_ocean_inputs_t must be 24 bytes");
_Static_assert(sizeof(sonar_output_params_t) == 40U, "sonar_output_params_t must be 40 bytes");
#endif

/* ========================================================================= */
/* Internal Physical Constants & Helpers                                     */
/* ========================================================================= */

#define SONAR_REF_TARGET_SNR_DB         (15.0f)
#define SONAR_MAX_SNR_MARGIN_CLAMP_DB   (13.9794f)
#define SONAR_TURBIDITY_THRESHOLD_CV    (0.01f)
#define SONAR_PPM_TO_VOLUME_FRAC        (1.0e-6f)
#define SONAR_KHZ_TO_HZ                 (1000.0f)
#define SONAR_HZ_TO_KHZ                 (0.001f)

/**
 * @brief Convert input turbidity (which may be volume concentration Cv or NTU/ppm)
 *        to dimensionless sediment volume concentration Cv.
 *
 * @param[in] turbidity Input turbidity value.
 * @return Volume concentration Cv in range [0.0f, 1.0e-3f].
 */
static float sonar_get_volume_concentration(float turbidity)
{
    if (turbidity > SONAR_TURBIDITY_THRESHOLD_CV) {
        return turbidity * SONAR_PPM_TO_VOLUME_FRAC;
    }
    return turbidity;
}

/**
 * @brief Internal active sonar SNR evaluator for a candidate frequency in kHz.
 *
 * Evaluates: SNR = SL_nom (190) + TS (-15) + DI - 2*TL - NL
 *
 * @param[in] f_khz        Frequency in kHz.
 * @param[in] range_m      Target range in meters.
 * @param[in] c_v          Turbidity volume concentration.
 * @param[in] depth_m      Depth in meters.
 * @param[in] temp_c       Temperature in degrees Celsius.
 * @param[in] salinity_ppt Salinity in ppt.
 * @param[in] eff_ph       Effective ocean pH.
 * @return Echo SNR in dB.
 */
static float sonar_calc_snr_internal(
    float f_khz,
    float range_m,
    float c_v,
    float depth_m,
    float temp_c,
    float salinity_ppt,
    float eff_ph
)
{
    const float f_hz = f_khz * SONAR_KHZ_TO_HZ;
    const float alpha_sw = sonar_engine_calc_absorption(f_hz, temp_c, salinity_ppt, depth_m, eff_ph);
    const float f_ratio = f_khz / 100.0f;
    const float alpha_turb = c_v * 50.0f * (f_ratio * f_ratio);
    const float alpha_tot = alpha_sw + alpha_turb;

    const float eff_r = (range_m < 1.0f) ? 1.0f : range_m;
    const float tl = 40.0f * log10f(eff_r) + 2.0f * alpha_tot * eff_r;

    const float b_hz = SONAR_FRACTIONAL_BANDWIDTH * f_hz;
    const float n0 = -15.0f + 20.0f * log10f(f_khz);
    const float nl = n0 + 10.0f * log10f(b_hz);
    const float di = 15.0f + 20.0f * log10f(f_ratio);

    /* Active sonar equation: SNR = 190 (SL_nom) - 15 (TS) + DI - TL - NL */
    return 175.0f + di - tl - nl;
}

/* ========================================================================= */
/* Public API Implementation                                                 */
/* ========================================================================= */

sonar_status_t sonar_engine_init(void)
{
    /* Zero heap allocation, pure re-entrant logic */
    return SONAR_STATUS_OK;
}

sonar_status_t sonar_engine_validate_inputs(const sonar_ocean_inputs_t *inputs)
{
    if (inputs == NULL) {
        return SONAR_STATUS_INVALID_POINTER;
    }

    /* Check for NaN or Infinity */
    if (isnan(inputs->range_m) || isinf(inputs->range_m) ||
        isnan(inputs->depth_m) || isinf(inputs->depth_m) ||
        isnan(inputs->temperature_c) || isinf(inputs->temperature_c) ||
        isnan(inputs->salinity_ppt) || isinf(inputs->salinity_ppt) ||
        isnan(inputs->turbidity_ntu) || isinf(inputs->turbidity_ntu) ||
        isnan(inputs->ph) || isinf(inputs->ph)) {
        return SONAR_STATUS_NAN_INF_ERROR;
    }

    /* Range: [1.0 m, 2000.0 m] */
    if ((inputs->range_m < 1.0f) || (inputs->range_m > 2000.0f)) {
        return SONAR_STATUS_OUT_OF_BOUNDS;
    }

    /* Depth: [0.0 m, 6000.0 m] */
    if ((inputs->depth_m < 0.0f) || (inputs->depth_m > 6000.0f)) {
        return SONAR_STATUS_OUT_OF_BOUNDS;
    }

    /* Temperature: [-2.0 C, 40.0 C] */
    if ((inputs->temperature_c < -2.0f) || (inputs->temperature_c > 40.0f)) {
        return SONAR_STATUS_OUT_OF_BOUNDS;
    }

    /* Salinity: [0.0 ppt, 45.0 ppt] */
    if ((inputs->salinity_ppt < 0.0f) || (inputs->salinity_ppt > 45.0f)) {
        return SONAR_STATUS_OUT_OF_BOUNDS;
    }

    /* Turbidity: [0.0, 1000.0 NTU/ppm] */
    if ((inputs->turbidity_ntu < 0.0f) || (inputs->turbidity_ntu > 1000.0f)) {
        return SONAR_STATUS_OUT_OF_BOUNDS;
    }

    /* pH: [6.0, 9.5] (or 0.0 for default) */
    if ((inputs->ph != 0.0f) && ((inputs->ph < 6.0f) || (inputs->ph > 9.5f))) {
        return SONAR_STATUS_OUT_OF_BOUNDS;
    }

    return SONAR_STATUS_OK;
}

float sonar_engine_calc_sound_speed(float temperature_c, float salinity_ppt, float depth_m)
{
    const float delta_s = salinity_ppt - 35.0f;
    const float d2 = depth_m * depth_m;
    const float d3 = d2 * depth_m;

    /*
     * Mackenzie (1981) 9-term polynomial evaluated in Horner form:
     * c = 1448.96 + 4.591*T - 5.304e-2*T^2 + 2.374e-4*T^3 + 1.340*(S-35)
     *     + 1.630e-2*D + 1.675e-7*D^2 - 1.025e-2*T*(S-35) - 7.139e-13*T*D^3
     */
    const float c = 1448.96f
        + depth_m * (0.01630f + 1.675e-7f * depth_m)
        + delta_s * (1.340f - 0.01025f * temperature_c)
        + temperature_c * (4.591f + temperature_c * (-0.05304f + 0.0002374f * temperature_c) - 7.139e-13f * d3);

    return c;
}

float sonar_engine_calc_absorption(float freq_hz, float temperature_c, float salinity_ppt, float depth_m, float ph)
{
    if (freq_hz <= 0.0f) {
        return 0.0f;
    }

    const float f_khz = freq_hz * SONAR_HZ_TO_KHZ;
    const float f_sq = f_khz * f_khz;

    const float eff_ph = (ph <= 0.0f) ? SONAR_DEFAULT_OCEAN_PH : ph;
    const float eff_s = (salinity_ppt < 0.0f) ? 0.0f : salinity_ppt;

    /* Chemical relaxation frequencies in kHz */
    const float f1 = 0.78f * sqrtf(eff_s / 35.0f) * expf(temperature_c / 26.0f);
    const float f2 = 42.0f * expf(temperature_c / 17.0f);

    /* Relaxation amplitude coefficients in dB/(km*kHz) */
    const float a1 = 0.106f * expf((eff_ph - 8.0f) / 0.56f);
    const float a2 = 0.52f * (1.0f + (temperature_c / 43.0f)) * (eff_s / 35.0f) * expf(-depth_m / 6000.0f);
    const float a3 = 0.00049f * expf(-((temperature_c / 27.0f) + (depth_m / 17000.0f)));

    /* Component absorption values in dB/km */
    const float term_boric = a1 * (f1 * f_sq) / ((f1 * f1) + f_sq);
    const float term_mgso4 = a2 * (f2 * f_sq) / ((f2 * f2) + f_sq);
    const float term_water = a3 * f_sq;

    const float alpha_db_km = term_boric + term_mgso4 + term_water;

    /* Return in dB/m */
    return alpha_db_km * SONAR_HZ_TO_KHZ;
}

sonar_status_t sonar_engine_adapt(const sonar_ocean_inputs_t *inputs, sonar_output_params_t *outputs)
{
    if ((inputs == NULL) || (outputs == NULL)) {
        return SONAR_STATUS_INVALID_POINTER;
    }

    const sonar_status_t valid_status = sonar_engine_validate_inputs(inputs);
    if (valid_status != SONAR_STATUS_OK) {
        return valid_status;
    }

    const float eff_ph = (inputs->ph <= 0.0f) ? SONAR_DEFAULT_OCEAN_PH : inputs->ph;
    const float c_v = sonar_get_volume_concentration(inputs->turbidity_ntu);

    /* 1. Bounded Bisection Search on [100.0 kHz, 500.0 kHz] */
    float f_low_khz = SONAR_FREQ_MIN_HZ * SONAR_HZ_TO_KHZ;
    float f_high_khz = SONAR_FREQ_MAX_HZ * SONAR_HZ_TO_KHZ;

    const float snr_high = sonar_calc_snr_internal(
        f_high_khz, inputs->range_m, c_v, inputs->depth_m,
        inputs->temperature_c, inputs->salinity_ppt, eff_ph
    );
    const float snr_low = sonar_calc_snr_internal(
        f_low_khz, inputs->range_m, c_v, inputs->depth_m,
        inputs->temperature_c, inputs->salinity_ppt, eff_ph
    );

    float f_raw_khz;
    if (snr_high >= SONAR_REF_TARGET_SNR_DB) {
        f_raw_khz = f_high_khz;
    } else if (snr_low <= SONAR_REF_TARGET_SNR_DB) {
        f_raw_khz = f_low_khz;
    } else {
        for (uint32_t iter = 0U; iter < SONAR_BISECTION_ITERATIONS; ++iter) {
            const float f_mid_khz = 0.5f * (f_low_khz + f_high_khz);
            const float snr_mid = sonar_calc_snr_internal(
                f_mid_khz, inputs->range_m, c_v, inputs->depth_m,
                inputs->temperature_c, inputs->salinity_ppt, eff_ph
            );

            if (snr_mid >= SONAR_REF_TARGET_SNR_DB) {
                f_low_khz = f_mid_khz;
            } else {
                f_high_khz = f_mid_khz;
            }
        }
        f_raw_khz = 0.5f * (f_low_khz + f_high_khz);
    }

    /* 2. Guardband Clamping to [112.35955 kHz, 450.45045 kHz] */
    const float min_carrier_khz = SONAR_CARRIER_MIN_HZ * SONAR_HZ_TO_KHZ;
    const float max_carrier_khz = SONAR_CARRIER_MAX_HZ * SONAR_HZ_TO_KHZ;
    float f_c_khz = f_raw_khz;
    if (f_c_khz < min_carrier_khz) {
        f_c_khz = min_carrier_khz;
    } else if (f_c_khz > max_carrier_khz) {
        f_c_khz = max_carrier_khz;
    }

    const float f_c_hz = f_c_khz * SONAR_KHZ_TO_HZ;

    /* 3. Fractional Bandwidth & Guardband-Compliant Chirp Frequencies */
    const float bandwidth_hz = SONAR_FRACTIONAL_BANDWIDTH * f_c_hz;
    float f_start_hz = f_c_hz - (0.5f * bandwidth_hz);
    float f_end_hz = f_c_hz + (0.5f * bandwidth_hz);

    if (f_start_hz < SONAR_FREQ_MIN_HZ) {
        f_start_hz = SONAR_FREQ_MIN_HZ;
    }
    if (f_end_hz > SONAR_FREQ_MAX_HZ) {
        f_end_hz = SONAR_FREQ_MAX_HZ;
    }

    /* 4. Pulse Duration Scheduling in [1.0 ms, 10.0 ms] */
    float t_pulse_s;
    if (inputs->range_m <= SONAR_BATTERY_CONSERVE_RANGE_M) {
        t_pulse_s = SONAR_PULSE_DURATION_MIN_S;
    } else {
        const float range_delta = inputs->range_m - SONAR_BATTERY_CONSERVE_RANGE_M;
        const float range_span = 300.0f - SONAR_BATTERY_CONSERVE_RANGE_M;
        const float t_pulse_ms_calc = 1.0f + 9.0f * (range_delta / range_span);
        const float t_pulse_ms = (t_pulse_ms_calc > 10.0f) ? 10.0f : t_pulse_ms_calc;
        t_pulse_s = t_pulse_ms * 0.001f;
    }

    /* 5. Active Sonar SNR Margin & Amplitude Multiplier */
    const float snr_at_fc = sonar_calc_snr_internal(
        f_c_khz, inputs->range_m, c_v, inputs->depth_m,
        inputs->temperature_c, inputs->salinity_ppt, eff_ph
    );
    const float snr_margin_db = snr_at_fc - SONAR_REF_TARGET_SNR_DB;

    float a_scale;
    if ((inputs->range_m <= SONAR_BATTERY_CONSERVE_RANGE_M) &&
        (c_v <= SONAR_CLEAR_WATER_TURB_MAX) &&
        (inputs->depth_m <= SONAR_SHALLOW_DEPTH_MAX_M)) {
        /* Strict battery conservation: 0.20f */
        a_scale = SONAR_AMP_SCALE_MIN;
    } else {
        if (snr_margin_db >= SONAR_MAX_SNR_MARGIN_CLAMP_DB) {
            a_scale = SONAR_AMP_SCALE_MIN;
        } else if (snr_margin_db <= 0.0f) {
            a_scale = SONAR_AMP_SCALE_MAX;
        } else {
            a_scale = powf(10.0f, -snr_margin_db / 20.0f);
            if (a_scale < SONAR_AMP_SCALE_MIN) {
                a_scale = SONAR_AMP_SCALE_MIN;
            } else if (a_scale > SONAR_AMP_SCALE_MAX) {
                a_scale = SONAR_AMP_SCALE_MAX;
            }
        }
    }

    /* 6. Channel Diagnostics */
    const float sound_speed = sonar_engine_calc_sound_speed(
        inputs->temperature_c, inputs->salinity_ppt, inputs->depth_m
    );
    const float alpha_sw_at_fc = sonar_engine_calc_absorption(
        f_c_hz, inputs->temperature_c, inputs->salinity_ppt, inputs->depth_m, eff_ph
    );
    const float f_ratio = f_c_khz / 100.0f;
    const float alpha_turb_at_fc = c_v * 50.0f * (f_ratio * f_ratio);
    const float total_absorption_db_m = alpha_sw_at_fc + alpha_turb_at_fc;

    /* Assign output fields */
    outputs->f_c_hz = f_c_hz;
    outputs->f_start_hz = f_start_hz;
    outputs->f_end_hz = f_end_hz;
    outputs->bandwidth_hz = bandwidth_hz;
    outputs->t_pulse_s = t_pulse_s;
    outputs->a_scale = a_scale;
    outputs->sound_speed_mps = sound_speed;
    outputs->absorption_db_per_m = total_absorption_db_m;
    outputs->snr_margin_db = snr_margin_db;
    outputs->bisection_iters = (uint8_t)SONAR_BISECTION_ITERATIONS;
    outputs->reserved[0] = 0U;
    outputs->reserved[1] = 0U;
    outputs->reserved[2] = 0U;

    return SONAR_STATUS_OK;
}

/* ========================================================================= */
/* Compatibility Aliases                                                     */
/* ========================================================================= */

sonar_status_t sonar_engine_calculate(const sonar_ocean_inputs_t *inputs, sonar_output_params_t *outputs)
{
    return sonar_engine_adapt(inputs, outputs);
}

float sonar_engine_compute_sound_speed(float temperature_c, float salinity_ppt, float depth_m)
{
    return sonar_engine_calc_sound_speed(temperature_c, salinity_ppt, depth_m);
}

float sonar_engine_compute_absorption(
    float freq_hz,
    float temperature_c,
    float salinity_ppt,
    float depth_m,
    float turbidity_ntu,
    float ph
)
{
    const float alpha_sw = sonar_engine_calc_absorption(freq_hz, temperature_c, salinity_ppt, depth_m, ph);
    const float c_v = sonar_get_volume_concentration(turbidity_ntu);
    const float f_khz = freq_hz * SONAR_HZ_TO_KHZ;
    const float f_ratio = f_khz / 100.0f;
    const float alpha_turb = c_v * 50.0f * (f_ratio * f_ratio);
    return alpha_sw + alpha_turb;
}

const char *sonar_engine_get_status_string(sonar_status_t status)
{
    switch (status) {
        case SONAR_STATUS_OK:
            return "SUCCESS";
        case SONAR_STATUS_INVALID_POINTER:
            return "ERROR_NULL_POINTER";
        case SONAR_STATUS_OUT_OF_BOUNDS:
            return "ERROR_OUT_OF_BOUNDS";
        case SONAR_STATUS_NAN_INF_ERROR:
            return "ERROR_NAN_OR_INF";
        default:
            return "ERROR_UNKNOWN";
    }
}

/* ========================================================================= */
/* Standalone Test Runner (Activated only when compiled with -DBUILD_TEST_C) */
/* ========================================================================= */

#ifdef BUILD_TEST_C

#include <stdio.h>
#include <stdlib.h>

static int g_tests_run = 0;
static int g_tests_failed = 0;

#define ASSERT_TRUE(cond, msg) do { \
    g_tests_run++; \
    if (!(cond)) { \
        printf("  [FAIL] Line %d: %s\n", __LINE__, (msg)); \
        g_tests_failed++; \
    } \
} while (0)

#define ASSERT_NEAR(actual, expected, tol, msg) do { \
    g_tests_run++; \
    float diff = fabsf((actual) - (expected)); \
    if (diff > (tol)) { \
        printf("  [FAIL] Line %d: %s (actual=%.6f, expected=%.6f, diff=%.6f, tol=%.6f)\n", \
               __LINE__, (msg), (double)(actual), (double)(expected), (double)diff, (double)(tol)); \
        g_tests_failed++; \
    } \
} while (0)

static void test_struct_layout(void)
{
    printf("\n--- Test 1: Struct Layout and Sizing ---\n");

    ASSERT_TRUE(sizeof(sonar_ocean_inputs_t) == 24U, "sizeof(sonar_ocean_inputs_t) == 24");
    ASSERT_TRUE(sizeof(sonar_output_params_t) == 40U, "sizeof(sonar_output_params_t) == 40");

    ASSERT_TRUE(offsetof(sonar_ocean_inputs_t, range_m) == 0U, "inputs.range_m offset == 0");
    ASSERT_TRUE(offsetof(sonar_ocean_inputs_t, depth_m) == 4U, "inputs.depth_m offset == 4");
    ASSERT_TRUE(offsetof(sonar_ocean_inputs_t, temperature_c) == 8U, "inputs.temperature_c offset == 8");
    ASSERT_TRUE(offsetof(sonar_ocean_inputs_t, salinity_ppt) == 12U, "inputs.salinity_ppt offset == 12");
    ASSERT_TRUE(offsetof(sonar_ocean_inputs_t, turbidity_ntu) == 16U, "inputs.turbidity_ntu offset == 16");
    ASSERT_TRUE(offsetof(sonar_ocean_inputs_t, ph) == 20U, "inputs.ph offset == 20");

    ASSERT_TRUE(offsetof(sonar_output_params_t, f_c_hz) == 0U, "outputs.f_c_hz offset == 0");
    ASSERT_TRUE(offsetof(sonar_output_params_t, f_start_hz) == 4U, "outputs.f_start_hz offset == 4");
    ASSERT_TRUE(offsetof(sonar_output_params_t, f_end_hz) == 8U, "outputs.f_end_hz offset == 8");
    ASSERT_TRUE(offsetof(sonar_output_params_t, bandwidth_hz) == 12U, "outputs.bandwidth_hz offset == 12");
    ASSERT_TRUE(offsetof(sonar_output_params_t, t_pulse_s) == 16U, "outputs.t_pulse_s offset == 16");
    ASSERT_TRUE(offsetof(sonar_output_params_t, a_scale) == 20U, "outputs.a_scale offset == 20");
    ASSERT_TRUE(offsetof(sonar_output_params_t, sound_speed_mps) == 24U, "outputs.sound_speed_mps offset == 24");
    ASSERT_TRUE(offsetof(sonar_output_params_t, absorption_db_per_m) == 28U, "outputs.absorption_db_per_m offset == 28");
    ASSERT_TRUE(offsetof(sonar_output_params_t, snr_margin_db) == 32U, "outputs.snr_margin_db offset == 32");
    ASSERT_TRUE(offsetof(sonar_output_params_t, bisection_iters) == 36U, "outputs.bisection_iters offset == 36");
    ASSERT_TRUE(offsetof(sonar_output_params_t, reserved) == 37U, "outputs.reserved offset == 37");
    printf("  Struct layout verification passed!\n");
}

static void test_sound_speed_and_absorption(void)
{
    printf("\n--- Test 2: Mackenzie Sound Speed & Ainslie-McColm Absorption ---\n");

    /* Mackenzie SV-01: T=15 C, S=35 ppt, D=100 m -> 1508.3239 m/s */
    float c1 = sonar_engine_calc_sound_speed(15.0f, 35.0f, 100.0f);
    ASSERT_NEAR(c1, 1508.3239f, 0.05f, "SV-01 Calibration c=1508.32 m/s");

    /* Mackenzie SV-02: T=28 C, S=36 ppt, D=10 m -> 1542.3521 m/s */
    float c2 = sonar_engine_calc_sound_speed(28.0f, 36.0f, 10.0f);
    ASSERT_NEAR(c2, 1542.3521f, 0.05f, "SV-02 Tropical c=1542.35 m/s");

    /* Mackenzie SV-05: T=2 C, S=34.5 ppt, D=3000 m -> 1507.6409 m/s */
    float c3 = sonar_engine_calc_sound_speed(2.0f, 34.5f, 3000.0f);
    ASSERT_NEAR(c3, 1507.6409f, 0.05f, "SV-05 Deep Abyssal c=1507.64 m/s");

    /* Ainslie-McColm absorption at 100 kHz (T=15 C, S=35 ppt, D=100 m, pH=8.0) -> 0.037428 dB/m */
    float a100 = sonar_engine_calc_absorption(100000.0f, 15.0f, 35.0f, 100.0f, 8.0f);
    ASSERT_NEAR(a100, 0.037428f, 0.001f, "Ainslie-McColm absorption at 100 kHz (0.0374 dB/m)");

    /* Ainslie-McColm absorption at 500 kHz -> 0.137261 dB/m */
    float a500 = sonar_engine_calc_absorption(500000.0f, 15.0f, 35.0f, 100.0f, 8.0f);
    ASSERT_NEAR(a500, 0.137261f, 0.002f, "Ainslie-McColm absorption at 500 kHz (0.1373 dB/m)");
    printf("  Physical models verification passed!\n");
}

static void test_benchmark_cases(void)
{
    printf("\n--- Test 3: Benchmark Cases ---\n");

    /* Case 1: Shallow Clear Warm Water */
    sonar_ocean_inputs_t in1 = {
        .range_m = 25.0f,
        .depth_m = 10.0f,
        .temperature_c = 25.0f,
        .salinity_ppt = 35.0f,
        .turbidity_ntu = 0.0f,
        .ph = 8.0f
    };
    sonar_output_params_t out1;
    sonar_status_t st1 = sonar_engine_adapt(&in1, &out1);

    ASSERT_TRUE(st1 == SONAR_STATUS_OK, "Benchmark Case 1 status OK");
    ASSERT_NEAR(out1.f_c_hz, 450450.45f, 1.0f, "Case 1 f_c clamped to upper guardband (450450 Hz)");
    ASSERT_TRUE(out1.f_start_hz >= 100000.0f, "Case 1 f_start >= 100 kHz");
    ASSERT_TRUE(out1.f_end_hz <= 500000.0f, "Case 1 f_end <= 500 kHz");
    ASSERT_TRUE(out1.f_start_hz < out1.f_c_hz, "Case 1 f_start < f_c");
    ASSERT_TRUE(out1.f_c_hz < out1.f_end_hz, "Case 1 f_c < f_end");
    ASSERT_NEAR(out1.bandwidth_hz, out1.f_end_hz - out1.f_start_hz, 0.01f, "Case 1 B == f_end - f_start");
    ASSERT_NEAR(out1.t_pulse_s, 0.0010f, 0.0001f, "Case 1 T_pulse == 1.0 ms");
    ASSERT_NEAR(out1.a_scale, 0.20f, 0.0001f, "Case 1 A_scale == 0.20 (Battery Conservation)");
    ASSERT_TRUE(out1.bisection_iters == 10U, "Case 1 iters == 10");

    /* Case 2: Deep Turbid Cold Water */
    sonar_ocean_inputs_t in2 = {
        .range_m = 300.0f,
        .depth_m = 500.0f,
        .temperature_c = 2.0f,
        .salinity_ppt = 35.0f,
        .turbidity_ntu = 1.0e-3f,
        .ph = 8.0f
    };
    sonar_output_params_t out2;
    sonar_status_t st2 = sonar_engine_adapt(&in2, &out2);

    ASSERT_TRUE(st2 == SONAR_STATUS_OK, "Benchmark Case 2 status OK");
    ASSERT_NEAR(out2.f_c_hz, 112359.55f, 1.0f, "Case 2 f_c clamped to lower guardband (112360 Hz)");
    ASSERT_TRUE(out2.f_start_hz >= 100000.0f, "Case 2 f_start >= 100 kHz");
    ASSERT_TRUE(out2.f_end_hz <= 500000.0f, "Case 2 f_end <= 500 kHz");
    ASSERT_TRUE(out2.f_start_hz < out2.f_c_hz, "Case 2 f_start < f_c");
    ASSERT_TRUE(out2.f_c_hz < out2.f_end_hz, "Case 2 f_c < f_end");
    ASSERT_NEAR(out2.bandwidth_hz, out2.f_end_hz - out2.f_start_hz, 0.01f, "Case 2 B == f_end - f_start");
    ASSERT_NEAR(out2.t_pulse_s, 0.0100f, 0.0001f, "Case 2 T_pulse == 10.0 ms");
    ASSERT_NEAR(out2.a_scale, 1.00f, 0.0001f, "Case 2 A_scale == 1.00");

    /* Case 3: Littoral Mid-Depth Transitional */
    sonar_ocean_inputs_t in3 = {
        .range_m = 100.0f,
        .depth_m = 50.0f,
        .temperature_c = 15.0f,
        .salinity_ppt = 32.0f,
        .turbidity_ntu = 1.0e-4f,
        .ph = 8.0f
    };
    sonar_output_params_t out3;
    sonar_status_t st3 = sonar_engine_adapt(&in3, &out3);

    ASSERT_TRUE(st3 == SONAR_STATUS_OK, "Benchmark Case 3 status OK");
    ASSERT_TRUE((out3.f_c_hz >= 265000.0f) && (out3.f_c_hz <= 267000.0f), "Case 3 f_c converged ~266 kHz");
    ASSERT_TRUE(out3.f_start_hz >= 100000.0f, "Case 3 f_start >= 100 kHz");
    ASSERT_TRUE(out3.f_end_hz <= 500000.0f, "Case 3 f_end <= 500 kHz");
    ASSERT_NEAR(out3.bandwidth_hz, out3.f_end_hz - out3.f_start_hz, 0.01f, "Case 3 B == f_end - f_start");
    ASSERT_NEAR(out3.t_pulse_s, 0.0034545f, 0.0001f, "Case 3 T_pulse == 3.45 ms");
    ASSERT_TRUE(out3.a_scale >= 0.99f, "Case 3 A_scale ~ 1.00");
    printf("  Benchmark cases verification passed!\n");
}

static void test_monotonicity(void)
{
    printf("\n--- Test 4: Monotonicity with Turbidity (df_c / dC_v <= 0) ---\n");

    const float turb_grid[6] = {
        0.0f, 1.0e-5f, 5.0e-5f, 1.0e-4f, 5.0e-4f, 1.0e-3f
    };

    sonar_ocean_inputs_t in = {
        .range_m = 100.0f,
        .depth_m = 50.0f,
        .temperature_c = 15.0f,
        .salinity_ppt = 32.0f,
        .turbidity_ntu = 0.0f,
        .ph = 8.0f
    };

    float prev_fc = 1.0e9f;
    for (int i = 0; i < 6; ++i) {
        in.turbidity_ntu = turb_grid[i];
        sonar_output_params_t out;
        sonar_status_t st = sonar_engine_adapt(&in, &out);
        ASSERT_TRUE(st == SONAR_STATUS_OK, "Adaptation OK");
        ASSERT_TRUE(out.f_c_hz <= prev_fc + 0.1f, "Increasing turbidity never increases f_c");
        printf("  Cv=%.1e: f_c = %.1f Hz, B = %.1f Hz, A_scale = %.3f\n",
               (double)turb_grid[i], (double)out.f_c_hz, (double)out.bandwidth_hz, (double)out.a_scale);
        prev_fc = out.f_c_hz;
    }
    printf("  Monotonicity verification passed!\n");
}

static void test_battery_conservation_boundary(void)
{
    printf("\n--- Test 5: Battery Conservation Boundary ---\n");

    /* Exactly R=25.0, Cv=1e-5, D=50.0 -> must be 0.20f */
    sonar_ocean_inputs_t in = {
        .range_m = 25.0f,
        .depth_m = 50.0f,
        .temperature_c = 20.0f,
        .salinity_ppt = 35.0f,
        .turbidity_ntu = 1.0e-5f,
        .ph = 8.0f
    };
    sonar_output_params_t out;
    ASSERT_TRUE(sonar_engine_adapt(&in, &out) == SONAR_STATUS_OK, "Status OK");
    ASSERT_NEAR(out.a_scale, 0.20f, 0.0001f, "Boundary R=25, D=50, Cv=1e-5 -> A_scale == 0.20");

    /* R=25.1 -> should step into dynamic pulse and power */
    in.range_m = 25.1f;
    ASSERT_TRUE(sonar_engine_adapt(&in, &out) == SONAR_STATUS_OK, "Status OK");
    ASSERT_TRUE(out.t_pulse_s > 0.0010f, "R=25.1m T_pulse > 1.0ms");

    /* Deep water D=55m at R=20m -> not shallow water */
    in.range_m = 20.0f;
    in.depth_m = 55.0f;
    in.turbidity_ntu = 1.0e-3f; /* turbid deep */
    ASSERT_TRUE(sonar_engine_adapt(&in, &out) == SONAR_STATUS_OK, "Status OK");
    ASSERT_TRUE(out.a_scale > 0.20f, "Turbid deep R=20m A_scale > 0.20");
    printf("  Battery conservation boundary verification passed!\n");
}

static void test_error_handling(void)
{
    printf("\n--- Test 6: Defensive Parameter Validation & Error Handling ---\n");

    sonar_output_params_t out;
    sonar_ocean_inputs_t in = {
        .range_m = 50.0f,
        .depth_m = 20.0f,
        .temperature_c = 15.0f,
        .salinity_ppt = 35.0f,
        .turbidity_ntu = 1.0e-4f,
        .ph = 8.0f
    };

    /* NULL pointers */
    ASSERT_TRUE(sonar_engine_adapt(NULL, &out) == SONAR_STATUS_INVALID_POINTER, "NULL input returns INVALID_POINTER");
    ASSERT_TRUE(sonar_engine_adapt(&in, NULL) == SONAR_STATUS_INVALID_POINTER, "NULL output returns INVALID_POINTER");
    ASSERT_TRUE(sonar_engine_validate_inputs(NULL) == SONAR_STATUS_INVALID_POINTER, "NULL validate returns INVALID_POINTER");

    /* NaN checks */
    in.range_m = (float)NAN;
    ASSERT_TRUE(sonar_engine_adapt(&in, &out) == SONAR_STATUS_NAN_INF_ERROR, "NaN range returns NAN_INF_ERROR");
    in.range_m = 50.0f;

    in.depth_m = (float)INFINITY;
    ASSERT_TRUE(sonar_engine_adapt(&in, &out) == SONAR_STATUS_NAN_INF_ERROR, "Inf depth returns NAN_INF_ERROR");
    in.depth_m = 20.0f;

    /* Out of bounds checks */
    in.range_m = 0.5f; /* Min is 1.0m */
    ASSERT_TRUE(sonar_engine_adapt(&in, &out) == SONAR_STATUS_OUT_OF_BOUNDS, "Range < 1.0m returns OUT_OF_BOUNDS");
    in.range_m = 2500.0f; /* Max is 2000.0m */
    ASSERT_TRUE(sonar_engine_adapt(&in, &out) == SONAR_STATUS_OUT_OF_BOUNDS, "Range > 2000.0m returns OUT_OF_BOUNDS");
    in.range_m = 50.0f;

    in.depth_m = -5.0f;
    ASSERT_TRUE(sonar_engine_adapt(&in, &out) == SONAR_STATUS_OUT_OF_BOUNDS, "Depth < 0m returns OUT_OF_BOUNDS");
    in.depth_m = 7000.0f;
    ASSERT_TRUE(sonar_engine_adapt(&in, &out) == SONAR_STATUS_OUT_OF_BOUNDS, "Depth > 6000m returns OUT_OF_BOUNDS");
    in.depth_m = 20.0f;

    in.temperature_c = -5.0f;
    ASSERT_TRUE(sonar_engine_adapt(&in, &out) == SONAR_STATUS_OUT_OF_BOUNDS, "Temp < -2C returns OUT_OF_BOUNDS");
    in.temperature_c = 45.0f;
    ASSERT_TRUE(sonar_engine_adapt(&in, &out) == SONAR_STATUS_OUT_OF_BOUNDS, "Temp > 40C returns OUT_OF_BOUNDS");
    in.temperature_c = 15.0f;

    in.salinity_ppt = -1.0f;
    ASSERT_TRUE(sonar_engine_adapt(&in, &out) == SONAR_STATUS_OUT_OF_BOUNDS, "Salinity < 0 returns OUT_OF_BOUNDS");
    in.salinity_ppt = 50.0f;
    ASSERT_TRUE(sonar_engine_adapt(&in, &out) == SONAR_STATUS_OUT_OF_BOUNDS, "Salinity > 45 returns OUT_OF_BOUNDS");
    in.salinity_ppt = 35.0f;

    in.turbidity_ntu = -0.1f;
    ASSERT_TRUE(sonar_engine_adapt(&in, &out) == SONAR_STATUS_OUT_OF_BOUNDS, "Turbidity < 0 returns OUT_OF_BOUNDS");
    in.turbidity_ntu = 1500.0f;
    ASSERT_TRUE(sonar_engine_adapt(&in, &out) == SONAR_STATUS_OUT_OF_BOUNDS, "Turbidity > 1000 returns OUT_OF_BOUNDS");
    in.turbidity_ntu = 1.0e-4f;

    in.ph = 4.0f;
    ASSERT_TRUE(sonar_engine_adapt(&in, &out) == SONAR_STATUS_OUT_OF_BOUNDS, "pH < 6.0 returns OUT_OF_BOUNDS");
    in.ph = 11.0f;
    ASSERT_TRUE(sonar_engine_adapt(&in, &out) == SONAR_STATUS_OUT_OF_BOUNDS, "pH > 9.5 returns OUT_OF_BOUNDS");
    in.ph = 8.0f;

    printf("  Defensive parameter validation verification passed!\n");
}

int main(void)
{
    printf("=================================================================\n");
    printf("  Adaptive Sonar Transmitter C Engine Standalone Verification   \n");
    printf("  Target: STM32G474RE (Cortex-M4 single-precision FPU)           \n");
    printf("=================================================================\n");

    test_struct_layout();
    test_sound_speed_and_absorption();
    test_benchmark_cases();
    test_monotonicity();
    test_battery_conservation_boundary();
    test_error_handling();

    printf("\n=================================================================\n");
    printf("  Summary: %d assertions executed, %d failures\n", g_tests_run, g_tests_failed);
    printf("=================================================================\n");

    if (g_tests_failed > 0) {
        printf("  VERIFICATION RESULT: FAILED\n");
        return EXIT_FAILURE;
    }

    printf("  VERIFICATION RESULT: ALL TESTS PASSED (100%% SUCCESS)\n");
    return EXIT_SUCCESS;
}

#endif /* BUILD_TEST_C */
