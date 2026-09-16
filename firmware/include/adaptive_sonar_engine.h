/**
 * @file adaptive_sonar_engine.h
 * @brief Closed-loop Oceanographic Acoustic Adaptation Engine for STM32G474RE.
 *
 * This header defines the public API, data structures, error codes, and
 * configuration parameters for the Adaptive Sonar Transmitter Payload.
 * All floating-point operations use IEEE-754 32-bit single precision (float)
 * optimized for the ARM Cortex-M4 hardware FPU.
 *
 * @note MISRA C:2012 Compliant.
 * @note Zero dynamic memory allocation (0 bytes heap).
 * @copyright (c) 2026 Autonomous Underwater Systems Laboratory.
 */

#ifndef ADAPTIVE_SONAR_ENGINE_H
#define ADAPTIVE_SONAR_ENGINE_H

#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ========================================================================= */
/* Engine Configuration Constants (Single-Precision Float)                  */
/* ========================================================================= */

/** @brief Minimum allowable operational frequency in Hz (100 kHz). */
#define SONAR_FREQ_MIN_HZ               (100000.0f)

/** @brief Maximum allowable operational frequency in Hz (500 kHz). */
#define SONAR_FREQ_MAX_HZ               (500000.0f)

/** @brief Minimum carrier frequency under guardband clamping in Hz (100 kHz / 0.89). */
#define SONAR_CARRIER_MIN_HZ            (112359.55f)

/** @brief Maximum carrier frequency under guardband clamping in Hz (500 kHz / 1.11). */
#define SONAR_CARRIER_MAX_HZ            (450450.45f)

/** @brief Target fractional bandwidth ratio (B / f_c = 0.22). */
#define SONAR_FRACTIONAL_BANDWIDTH      (0.22f)

/** @brief Minimum allowable transmit pulse duration in seconds (1.0 ms). */
#define SONAR_PULSE_DURATION_MIN_S      (0.001f)

/** @brief Maximum allowable transmit pulse duration in seconds (10.0 ms). */
#define SONAR_PULSE_DURATION_MAX_S      (0.010f)

/** @brief Minimum transmit power amplitude multiplier (battery saving). */
#define SONAR_AMP_SCALE_MIN             (0.20f)

/** @brief Maximum transmit power amplitude multiplier (full power). */
#define SONAR_AMP_SCALE_MAX             (1.00f)

/** @brief Range threshold for shallow clear-water battery conservation (25 m). */
#define SONAR_BATTERY_CONSERVE_RANGE_M  (25.0f)

/** @brief Depth threshold for shallow water in meters (50 m). */
#define SONAR_SHALLOW_DEPTH_MAX_M       (50.0f)

/** @brief Maximum turbidity concentration for clear water (1e-5 volume fraction). */
#define SONAR_CLEAR_WATER_TURB_MAX      (1.0e-5f)

/** @brief Number of bisection search iterations (strictly bounded). */
#define SONAR_BISECTION_ITERATIONS      (10U)

/** @brief Default seawater pH value when unspecified (8.0). */
#define SONAR_DEFAULT_OCEAN_PH          (8.0f)

/* ========================================================================= */
/* Status and Error Enumerations                                             */
/* ========================================================================= */

/**
 * @brief Engine return status and error codes.
 */
typedef enum {
    SONAR_STATUS_OK                 =  0, /**< Operation succeeded without errors. */
    SONAR_STATUS_INVALID_POINTER    = -1, /**< Required pointer parameter was NULL. */
    SONAR_STATUS_OUT_OF_BOUNDS      = -2, /**< Input parameter outside valid physical range. */
    SONAR_STATUS_NAN_INF_ERROR      = -3, /**< Input or calculation produced NaN or Infinity. */

    /* Aliases for extended compatibility with test benches */
    SONAR_STATUS_SUCCESS            =  0, /**< Alias for OK. */
    SONAR_ERROR_NULL_POINTER        = -1, /**< Alias for INVALID_POINTER. */
    SONAR_ERROR_OUT_OF_BOUNDS       = -2, /**< Alias for OUT_OF_BOUNDS. */
    SONAR_ERROR_INVALID_RANGE       = -2, /**< Alias for OUT_OF_BOUNDS. */
    SONAR_ERROR_NAN_OR_INF          = -3  /**< Alias for NAN_INF_ERROR. */
} sonar_status_t;

/* ========================================================================= */
/* Data Structures                                                           */
/* ========================================================================= */

/**
 * @brief Environmental input parameters acquired from oceanographic sensors.
 *
 * All fields are 32-bit single-precision IEEE-754 floats to ensure 32-bit
 * alignment without compiler-dependent struct padding.
 * Total size: exactly 24 bytes.
 */
typedef struct {
    float range_m;          /**< Target stand-off range in meters [1.0f, 2000.0f]. */
    float depth_m;          /**< Operating depth in meters [0.0f, 6000.0f]. */
    float temperature_c;    /**< Seawater temperature in Celsius [-2.0f, 40.0f]. */
    float salinity_ppt;     /**< Practical salinity in ppt / PSU [0.0f, 45.0f]. */
    float turbidity_ntu;    /**< Water turbidity index / sediment volume concentration [0.0f, 1000.0f]. */
    float ph;               /**< Seawater pH [6.0f, 9.5f]. Default 8.0f if 0.0f. */
} sonar_ocean_inputs_t;

/**
 * @brief Optimized sonar transmission parameters produced by the adaptation engine.
 *
 * Total struct size: exactly 40 bytes (36 bytes float + 1 byte uint8 + 3 bytes padding).
 */
typedef struct {
    float f_c_hz;           /**< Center carrier frequency in Hz [112359.55f, 450450.45f]. */
    float f_start_hz;       /**< Chirp sweep start frequency in Hz >= 100000.0f. */
    float f_end_hz;         /**< Chirp sweep end frequency in Hz <= 500000.0f. */
    float bandwidth_hz;     /**< Sweep bandwidth in Hz == f_end - f_start. */
    float t_pulse_s;        /**< Transmit pulse duration in seconds [0.001f, 0.010f]. */
    float a_scale;          /**< Amplitude/power multiplier [0.20f, 1.00f]. */
    float sound_speed_mps;  /**< Evaluated sound speed in m/s (Mackenzie 1981). */
    float absorption_db_per_m; /**< Evaluated total acoustic absorption at f_c in dB/m. */
    float snr_margin_db;    /**< Estimated active sonar SNR margin in dB. */
    uint8_t bisection_iters;/**< Number of bisection search iterations executed (10). */
    uint8_t reserved[3];    /**< Explicit alignment padding to 32-bit boundary. */
} sonar_output_params_t;

/* ========================================================================= */
/* Core API Function Prototypes (Authoritative Interface Contract)            */
/* ========================================================================= */

/**
 * @brief Validate environmental input parameters defensively.
 *
 * Verifies that all inputs are within physically valid oceanographic envelopes
 * and are free from IEEE-754 NaN and Infinity values.
 *
 * @param[in] inputs Pointer to environmental input structure.
 * @return SONAR_STATUS_OK on success, or appropriate error code.
 */
sonar_status_t sonar_engine_validate_inputs(const sonar_ocean_inputs_t *inputs);

/**
 * @brief Execute the closed-loop oceanographic sonar adaptation calculation.
 *
 * Computes optimal center frequency f_c via 10-iteration bounded bisection,
 * calculates fractional chirp bandwidth B = 0.22 * f_c, clamps start/end
 * frequencies within [100 kHz, 500 kHz], evaluates pulse duration T_pulse,
 * and sets amplitude multiplier A_scale enforcing the battery conservation rule.
 *
 * @param[in]  inputs  Pointer to validated environmental input measurements.
 * @param[out] outputs Pointer to caller-allocated structure receiving output setpoints.
 * @return SONAR_STATUS_OK on success, or error status code on failure.
 */
sonar_status_t sonar_engine_adapt(
    const sonar_ocean_inputs_t *inputs,
    sonar_output_params_t *outputs
);

/**
 * @brief Calculate seawater sound speed using the Mackenzie (1981) 9-term equation.
 *
 * Implemented in Horner form for optimal execution on ARM Cortex-M4 FPU.
 *
 * @param[in] temperature_c Temperature in degrees Celsius [-2.0f, 40.0f].
 * @param[in] salinity_ppt  Salinity in ppt / PSU [0.0f, 45.0f].
 * @param[in] depth_m       Depth in meters [0.0f, 6000.0f].
 * @return Sound speed in meters per second (m/s).
 */
float sonar_engine_calc_sound_speed(
    float temperature_c,
    float salinity_ppt,
    float depth_m
);

/**
 * @brief Calculate seawater acoustic absorption using Ainslie-McColm (1998).
 *
 * Evaluates boric acid relaxation, magnesium sulfate relaxation, and pure water
 * viscous dissipation.
 *
 * @param[in] freq_hz       Acoustic frequency in Hz [100000.0f, 500000.0f].
 * @param[in] temperature_c Temperature in degrees Celsius.
 * @param[in] salinity_ppt  Salinity in ppt / PSU.
 * @param[in] depth_m       Depth in meters.
 * @param[in] ph            Ocean water pH (nominal 8.0f; if <= 0.0f, defaults to 8.0f).
 * @return Seawater absorption coefficient in dB per meter (dB/m).
 */
float sonar_engine_calc_absorption(
    float freq_hz,
    float temperature_c,
    float salinity_ppt,
    float depth_m,
    float ph
);

/* ========================================================================= */
/* Additional Helper / Compatibility APIs                                   */
/* ========================================================================= */

/**
 * @brief Initialize engine and verify static assertions.
 * @return SONAR_STATUS_OK.
 */
sonar_status_t sonar_engine_init(void);

/**
 * @brief Compatibility alias for sonar_engine_adapt.
 */
sonar_status_t sonar_engine_calculate(
    const sonar_ocean_inputs_t *inputs,
    sonar_output_params_t *outputs
);

/**
 * @brief Compatibility alias for sonar_engine_calc_sound_speed.
 */
float sonar_engine_compute_sound_speed(
    float temperature_c,
    float salinity_ppt,
    float depth_m
);

/**
 * @brief Compatibility wrapper for absorption calculation.
 */
float sonar_engine_compute_absorption(
    float freq_hz,
    float temperature_c,
    float salinity_ppt,
    float depth_m,
    float turbidity_ntu,
    float ph
);

/**
 * @brief Convert status code to human-readable string.
 * @param[in] status Status code value.
 * @return Pointer to constant string representation.
 */
const char *sonar_engine_get_status_string(sonar_status_t status);

#ifdef __cplusplus
}
#endif

#endif /* ADAPTIVE_SONAR_ENGINE_H */
