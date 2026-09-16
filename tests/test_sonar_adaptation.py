#!/usr/bin/env python3
"""
E2E Oceanographic Acoustic Adaptation Testbench & Verification Suite
===================================================================

Target Microcontroller: STM32G474RE (ARM Cortex-M4 with Single-Precision FPU)
Project: Adaptive Sonar Transmitter Payload for AUVs

This module implements:
1. Authoritative Oceanographic Acoustic Reference Models:
   - Mackenzie (1981) 9-term sound velocity: c(T, S, D)
   - Ainslie-McColm (1998) seawater absorption: alpha_sw(f, T, S, D, pH)
   - Suspended sediment particulate scattering: alpha_turb(f, C_v)
   - Ambient ocean noise spectral density: N_0(f) dominated by thermal agitation
   - Active sonar equation & SNR(f) evaluation
2. Closed-Loop Adaptation Engine:
   - 10-iteration bounded bisection search in [100, 500] kHz
   - Transducer guardband clamping to [112.35955 kHz, 450.45045 kHz]
   - Constant fractional bandwidth B = 0.22 * f_c, f_start = f_c - B/2, f_end = f_c + B/2
   - Range-dependent pulse length T_pulse in [1.0, 10.0] ms
   - Transmit amplitude scaling A_scale in [0.20, 1.00] with battery conservation
3. Canonical 3,888-Point Parametric Sweep:
   - 6 x 6 x 6 x 3 x 2 grid across Turbidity, Depth, Range, Temperature, Salinity
4. Test Suites Covering All Acceptance Criteria:
   - Test 1: 100% of 3,888 sweep points pass with zero violations
   - Test 2: Clamping: 100 kHz <= f_start < f_c < f_end <= 500 kHz for every vector
   - Test 3: Bandwidth: B == f_end - f_start and abs(B / f_c - 0.22) < 1e-3
   - Test 4: Pulse length: 1.0 ms <= T_pulse <= 10.0 ms
   - Test 5: Power multiplier: 0.20 <= A_scale <= 1.00
   - Test 6: Monotonicity: Increasing C_v never causes f_c to increase (df_c / dC_v <= 0)
   - Test 7: Battery conservation: A_scale == 0.20 for R <= 25m in clear shallow water
   - Test 8: The 3 Benchmark Cases (Shallow Clear Warm, Deep Turbid Cold, Littoral Transitional)
   - Test 9: C library ctypes bridge: Validates shared library build if libadaptive_sonar.so exists
"""

import ctypes
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Generator, List, Optional, Tuple

import numpy as np

try:
    import pytest
except ImportError:
    pytest = None

# ============================================================================
# Physical Constants and Operational Specifications
# ============================================================================

FREQ_MIN_HZ = 100000.0        # 100 kHz lower transducer cutoff
FREQ_MAX_HZ = 500000.0        # 500 kHz upper transducer cutoff
FRACTIONAL_BANDWIDTH = 0.22   # B / f_c = 0.22 (Q ≈ 4.545)
HALF_BW_RATIO = FRACTIONAL_BANDWIDTH / 2.0  # 0.11

# Guardband clamping limits to ensure [f_start, f_end] in [100 kHz, 500 kHz]
# f_start = f_c * (1 - 0.11) = 0.89 * f_c >= 100 kHz => f_c >= 100 / 0.89
FC_MIN_HZ = FREQ_MIN_HZ / (1.0 - HALF_BW_RATIO)  # 112359.55056 Hz (112.35955 kHz)
# f_end = f_c * (1 + 0.11) = 1.11 * f_c <= 500 kHz => f_c <= 500 / 1.11
FC_MAX_HZ = FREQ_MAX_HZ / (1.0 + HALF_BW_RATIO)  # 450450.45045 Hz (450.45045 kHz)

PULSE_DURATION_MIN_S = 0.001   # 1.0 ms
PULSE_DURATION_MAX_S = 0.010   # 10.0 ms

AMP_SCALE_MIN = 0.20           # Minimum transmitter voltage scale (-14 dB power)
AMP_SCALE_MAX = 1.00           # Full power transmitter scale (0 dB)

BATTERY_CONSERVE_RANGE_M = 25.0
BATTERY_CONSERVE_CV_MAX = 1.0e-5
BATTERY_CONSERVE_DEPTH_MAX_M = 50.0

DEFAULT_OCEAN_PH = 8.0
NOMINAL_SL_DB = 190.0          # Nominal source level (dB re 1 uPa @ 1m)
TARGET_STRENGTH_DB = -15.0     # Target strength (dB)
TARGET_SNR_DB = 15.0           # Target SNR threshold for adaptation search (dB)
BISECTION_ITERATIONS = 10      # Deterministic bisection iteration count


# ============================================================================
# Data Structures
# ============================================================================

@dataclass
class SonarOutputs:
    """Outputs produced by the adaptation engine."""
    f_c_hz: float
    f_start_hz: float
    f_end_hz: float
    bandwidth_hz: float
    t_pulse_s: float
    a_scale: float
    sound_speed_mps: float
    absorption_db_per_m: float
    snr_margin_db: float
    bisection_iters: int

    @property
    def f_c_khz(self) -> float:
        return self.f_c_hz / 1000.0

    @property
    def f_start_khz(self) -> float:
        return self.f_start_hz / 1000.0

    @property
    def f_end_khz(self) -> float:
        return self.f_end_hz / 1000.0

    @property
    def bandwidth_khz(self) -> float:
        return self.bandwidth_hz / 1000.0

    @property
    def t_pulse_ms(self) -> float:
        return self.t_pulse_s * 1000.0


# ============================================================================
# Ctypes ABI Struct Definitions (Matching include/adaptive_sonar_engine.h)
# ============================================================================

class SonarOceanInputs(ctypes.Structure):
    _fields_ = [
        ("range_m", ctypes.c_float),
        ("depth_m", ctypes.c_float),
        ("temperature_c", ctypes.c_float),
        ("salinity_ppt", ctypes.c_float),
        ("turbidity_ntu", ctypes.c_float),
        ("ph", ctypes.c_float),
    ]


class SonarOutputParams(ctypes.Structure):
    _fields_ = [
        ("f_c_hz", ctypes.c_float),
        ("f_start_hz", ctypes.c_float),
        ("f_end_hz", ctypes.c_float),
        ("bandwidth_hz", ctypes.c_float),
        ("t_pulse_s", ctypes.c_float),
        ("a_scale", ctypes.c_float),
        ("sound_speed_mps", ctypes.c_float),
        ("absorption_db_per_m", ctypes.c_float),
        ("snr_margin_db", ctypes.c_float),
        ("bisection_iters", ctypes.c_uint8),
        ("reserved", ctypes.c_uint8 * 3),
    ]


# ============================================================================
# Authoritative Oceanographic Acoustic Reference Models
# ============================================================================

def mackenzie_sound_speed(temp_c: float, salinity_ppt: float, depth_m: float) -> float:
    """
    Mackenzie (1981) 9-term empirical sound velocity equation.

    Valid across:
        -2.0 <= temp_c <= 30.0 deg C
        25.0 <= salinity_ppt <= 40.0 ppt (PSU)
        0.0 <= depth_m <= 8000.0 m

    Returns:
        Sound speed in meters per second (m/s).
    """
    t = float(temp_c)
    s = float(salinity_ppt)
    d = float(depth_m)
    ds = s - 35.0

    c = (
        1448.96
        + 4.591 * t
        - 5.304e-2 * (t ** 2)
        + 2.374e-4 * (t ** 3)
        + 1.340 * ds
        + 1.630e-2 * d
        + 1.675e-7 * (d ** 2)
        - 1.025e-2 * t * ds
        - 7.139e-13 * t * (d ** 3)
    )
    return float(c)


def medwin_sound_speed(temp_c: float, salinity_ppt: float, depth_m: float) -> float:
    """
    Medwin (1975) simplified sound velocity equation for shallow depths (D <= 1000m).
    """
    t = float(temp_c)
    s = float(salinity_ppt)
    d = float(depth_m)
    c = (
        1449.2
        + 4.6 * t
        - 0.055 * (t ** 2)
        + 0.00029 * (t ** 3)
        + (1.34 - 0.010 * t) * (s - 35.0)
        + 0.016 * d
    )
    return float(c)


def ainslie_mccolm_absorption(
    freq_khz: float,
    temp_c: float,
    salinity_ppt: float,
    depth_m: float,
    ph: float = DEFAULT_OCEAN_PH,
) -> Tuple[float, float]:
    """
    Ainslie and McColm (1998) seawater sound absorption model.

    Accounts for:
        1. Boric acid B(OH)3 relaxation
        2. Magnesium sulfate MgSO4 relaxation
        3. Pure water viscous dissipation

    Parameters:
        freq_khz: Frequency in kHz [100.0, 500.0]
        temp_c: Temperature in deg C
        salinity_ppt: Salinity in ppt / PSU
        depth_m: Operating depth in meters
        ph: Seawater pH (default 8.0)

    Returns:
        (alpha_sw_db_per_km, alpha_sw_db_per_m)
    """
    f = float(freq_khz)
    t = float(temp_c)
    s = float(salinity_ppt)
    d_km = float(depth_m) / 1000.0
    effective_ph = float(ph) if ph > 0.0 else DEFAULT_OCEAN_PH

    # Relaxation frequencies in kHz
    f1 = 0.78 * math.sqrt(max(s, 0.0) / 35.0) * math.exp(t / 26.0)
    f2 = 42.0 * math.exp(t / 17.0)

    # Amplitude coefficients
    a1 = 0.106 * math.exp((effective_ph - 8.0) / 0.56)
    a2 = 0.52 * (1.0 + t / 43.0) * (s / 35.0) * math.exp(-d_km / 6.0)
    a3 = 0.00049 * math.exp(-(t / 27.0 + d_km / 17.0))

    f_sq = f ** 2
    term_boric = a1 * (f1 * f_sq) / (f1 ** 2 + f_sq)
    term_mgso4 = a2 * (f2 * f_sq) / (f2 ** 2 + f_sq)
    term_water = a3 * f_sq

    alpha_db_km = term_boric + term_mgso4 + term_water
    alpha_db_m = alpha_db_km / 1000.0

    return alpha_db_km, alpha_db_m


def turbidity_scattering_attenuation(freq_khz: float, turbidity_cv: float) -> Tuple[float, float]:
    """
    Suspended sediment / particulate scattering attenuation.
    Scaling in Rayleigh regime: alpha_turb increases monotonically with C_v and f^2.

    Parameters:
        freq_khz: Frequency in kHz
        turbidity_cv: Particulate volume concentration [0.0, 1.0e-3]

    Returns:
        (alpha_turb_db_per_km, alpha_turb_db_per_m)
    """
    cv = float(max(turbidity_cv, 0.0))
    f = float(freq_khz)

    # alpha_turb in dB/m: Cv * 50.0 * (f_kHz / 100.0)^2
    alpha_m = cv * 50.0 * ((f / 100.0) ** 2)
    alpha_km = alpha_m * 1000.0
    return alpha_km, alpha_m


def ambient_noise_spectral_density(freq_khz: float) -> float:
    """
    Ambient ocean noise spectral density N_0(f) in dB re 1 uPa^2/Hz.
    In the 100 to 500 kHz ultrasonic regime, ambient noise is completely
    dominated by thermal molecular agitation of water, increasing at +20 dB/decade:
        N_0(f_kHz) = -15.0 + 20.0 * log10(f_kHz)
    """
    f = float(max(freq_khz, 1.0))
    n0 = -15.0 + 20.0 * math.log10(f)
    return n0


def calculate_active_sonar_snr(
    freq_khz: float,
    range_m: float,
    turbidity_cv: float,
    depth_m: float,
    temp_c: float,
    salinity_ppt: float,
    ph: float = DEFAULT_OCEAN_PH,
) -> float:
    """
    Evaluates the active sonar equation for a target at range R:
        SNR(f) = SL + TS + DI(f) - 2TL(f, R) - NL(f, B)

    Parameters:
        freq_khz: Acoustic carrier frequency in kHz
        range_m: Target range in meters
        turbidity_cv: Particulate volume concentration
        depth_m: Operating depth in meters
        temp_c: Water temperature in deg C
        salinity_ppt: Salinity in ppt
        ph: Seawater pH
    """
    r = float(max(range_m, 1.0))
    f = float(freq_khz)

    # 1. Medium attenuation (seawater absorption + sediment scattering)
    _, alpha_sw_m = ainslie_mccolm_absorption(f, temp_c, salinity_ppt, depth_m, ph)
    _, alpha_tb_m = turbidity_scattering_attenuation(f, turbidity_cv)
    alpha_tot_m = alpha_sw_m + alpha_tb_m

    # 2. Two-way transmission loss: spherical spreading + absorption
    tl_two_way = 40.0 * math.log10(r) + 2.0 * alpha_tot_m * r

    # 3. Fractional bandwidth and receiver noise level
    b_hz = FRACTIONAL_BANDWIDTH * f * 1000.0
    n0 = ambient_noise_spectral_density(f)
    nl = n0 + 10.0 * math.log10(b_hz)

    # 4. Transducer Directivity Index (DI) scaling with frequency
    di = 15.0 + 20.0 * math.log10(f / 100.0)

    # 5. Echo Signal-to-Noise Ratio (at nominal source level)
    snr = NOMINAL_SL_DB + TARGET_STRENGTH_DB + di - tl_two_way - nl
    return snr


# ============================================================================
# Core Adaptation Engine
# ============================================================================

def adapt_sonar(
    turbidity_cv: float,
    depth_m: float,
    range_m: float,
    temp_c: float,
    salinity_ppt: float,
    ph: float = DEFAULT_OCEAN_PH,
) -> SonarOutputs:
    """
    Reference Closed-Loop Oceanographic Adaptation Algorithm.

    Computes:
        1. Center Frequency f_c via 10-iteration bounded bisection in [100, 500] kHz,
           clamped to the transducer guardband [112.35955 kHz, 450.45045 kHz].
        2. Chirp Sweep Bandwidth B = 0.22 * f_c, f_start = f_c - B/2, f_end = f_c + B/2.
        3. Pulse Duration T_pulse in [1.0, 10.0] ms.
        4. Amplitude Multiplier A_scale in [0.20, 1.00], enforcing strict battery
           conservation (A_scale = 0.20) for R <= 25m in clear shallow water.
    """
    r = float(max(range_m, 1.0))
    d = float(max(depth_m, 0.0))
    cv = float(max(turbidity_cv, 0.0))
    t = float(temp_c)
    s = float(salinity_ppt)

    # 1. 10-Iteration Bounded Bisection Search for Optimal Center Frequency
    f_low = FREQ_MIN_HZ / 1000.0    # 100.0 kHz
    f_high = FREQ_MAX_HZ / 1000.0   # 500.0 kHz

    snr_low = calculate_active_sonar_snr(f_low, r, cv, d, t, s, ph)
    snr_high = calculate_active_sonar_snr(f_high, r, cv, d, t, s, ph)

    if snr_high >= TARGET_SNR_DB:
        f_raw = f_high
    elif snr_low <= TARGET_SNR_DB:
        f_raw = f_low
    else:
        for _ in range(BISECTION_ITERATIONS):
            f_mid = 0.5 * (f_low + f_high)
            snr_mid = calculate_active_sonar_snr(f_mid, r, cv, d, t, s, ph)
            if snr_mid >= TARGET_SNR_DB:
                f_low = f_mid
            else:
                f_high = f_mid
        f_raw = 0.5 * (f_low + f_high)

    # 2. Transducer Guardband Clamping to Guarantee [f_start, f_end] in [100, 500] kHz
    fc_min_khz = FC_MIN_HZ / 1000.0  # 112.35955 kHz
    fc_max_khz = FC_MAX_HZ / 1000.0  # 450.45045 kHz
    f_c_khz = float(np.clip(f_raw, fc_min_khz, fc_max_khz))
    f_c_hz = f_c_khz * 1000.0

    # 3. Fractional Bandwidth & Chirp Edge Frequencies
    b_hz = FRACTIONAL_BANDWIDTH * f_c_hz
    f_start_hz = f_c_hz - (0.5 * b_hz)
    f_end_hz = f_c_hz + (0.5 * b_hz)

    # 4. Pulse Duration Scheduling
    if r <= BATTERY_CONSERVE_RANGE_M:
        t_pulse_s = PULSE_DURATION_MIN_S  # 1.0 ms
    else:
        t_pulse_ms = min(10.0, 1.0 + 9.0 * (r - 25.0) / (300.0 - 25.0))
        t_pulse_s = t_pulse_ms / 1000.0

    # 5. Amplitude Multiplier & Battery Conservation Rule
    is_clear_shallow_short_range = (
        (r <= BATTERY_CONSERVE_RANGE_M)
        and (cv <= BATTERY_CONSERVE_CV_MAX)
        and (d <= BATTERY_CONSERVE_DEPTH_MAX_M)
    )

    snr_at_fc = calculate_active_sonar_snr(f_c_khz, r, cv, d, t, s, ph)
    snr_margin_db = snr_at_fc - TARGET_SNR_DB

    if is_clear_shallow_short_range:
        a_scale = AMP_SCALE_MIN  # Strictly 0.20
    else:
        if snr_margin_db >= 13.9794:
            a_scale = AMP_SCALE_MIN  # 0.20
        elif snr_margin_db <= 0.0:
            a_scale = AMP_SCALE_MAX  # 1.00
        else:
            calc_scale = 10.0 ** (-snr_margin_db / 20.0)
            a_scale = float(np.clip(calc_scale, AMP_SCALE_MIN, AMP_SCALE_MAX))

    # Diagnostic telemetry
    c_mps = mackenzie_sound_speed(t, s, d)
    _, alpha_sw_m = ainslie_mccolm_absorption(f_c_khz, t, s, d, ph)
    _, alpha_tb_m = turbidity_scattering_attenuation(f_c_khz, cv)
    total_abs_m = alpha_sw_m + alpha_tb_m

    return SonarOutputs(
        f_c_hz=f_c_hz,
        f_start_hz=f_start_hz,
        f_end_hz=f_end_hz,
        bandwidth_hz=b_hz,
        t_pulse_s=t_pulse_s,
        a_scale=a_scale,
        sound_speed_mps=c_mps,
        absorption_db_per_m=total_abs_m,
        snr_margin_db=snr_margin_db,
        bisection_iters=BISECTION_ITERATIONS,
    )


# ============================================================================
# 3,888-Point Parametric Sweep Definition
# ============================================================================

SWEEP_TURBIDITIES = [0.0, 1.0e-5, 5.0e-5, 1.0e-4, 5.0e-4, 1.0e-3]  # 6 levels
SWEEP_DEPTHS = [5.0, 20.0, 50.0, 100.0, 250.0, 500.0]              # 6 levels
SWEEP_RANGES = [10.0, 25.0, 50.0, 100.0, 200.0, 300.0]             # 6 levels
SWEEP_TEMPERATURES = [2.0, 15.0, 30.0]                             # 3 levels
SWEEP_SALINITIES = [30.0, 35.0]                                     # 2 levels
SWEEP_PH = [7.5, 8.0, 8.4]                                         # 3 levels

TOTAL_SWEEP_POINTS = (
    len(SWEEP_TURBIDITIES)
    * len(SWEEP_DEPTHS)
    * len(SWEEP_RANGES)
    * len(SWEEP_TEMPERATURES)
    * len(SWEEP_SALINITIES)
    * len(SWEEP_PH)
)  # 6 * 6 * 6 * 3 * 2 * 3 = 3,888


def generate_parametric_sweep() -> Generator[Tuple[float, float, float, float, float, float], None, None]:
    """Yields all 3,888 parameter vectors (turbidity, depth, range, temp, salinity, ph)."""
    for d in SWEEP_DEPTHS:
        for r in SWEEP_RANGES:
            for t in SWEEP_TEMPERATURES:
                for s in SWEEP_SALINITIES:
                    for ph in SWEEP_PH:
                        for cv in SWEEP_TURBIDITIES:
                            yield (cv, d, r, t, s, ph)


# ============================================================================
# C Shared Library Bridge Helper
# ============================================================================

def get_c_engine_library() -> Optional[ctypes.CDLL]:
    """
    Locates and loads build/libadaptive_sonar.so if present.
    Returns None if the shared library has not yet been built.
    """
    repo_root = Path(__file__).resolve().parent.parent
    so_candidates = [
        repo_root / "build" / "libadaptive_sonar.so",
        repo_root / "libadaptive_sonar.so",
    ]
    for candidate in so_candidates:
        if candidate.exists():
            try:
                c_lib = ctypes.CDLL(str(candidate))
                return c_lib
            except OSError:
                pass
    return None


# ============================================================================
# Test Suite Implementation
# ============================================================================

class TestAcousticPhysicalModels:
    """Unit tests verifying underlying physical and oceanographic acoustic models."""

    def test_mackenzie_sound_speed_calibration_points(self):
        """Mackenzie sound velocity matches benchmark values across ocean strata."""
        # Calibration Case 1: Standard calibration point (T=15 C, S=35 ppt, D=100 m)
        c1 = mackenzie_sound_speed(15.0, 35.0, 100.0)
        assert abs(c1 - 1508.3239) < 0.05, f"Expected ~1508.32 m/s, got {c1}"

        # Calibration Case 2: Shallow Warm Tropical Surface (T=28 C, S=36 ppt, D=10 m)
        c2 = mackenzie_sound_speed(28.0, 36.0, 10.0)
        assert abs(c2 - 1542.3521) < 0.05, f"Expected ~1542.35 m/s, got {c2}"

        # Calibration Case 3: Deep Oceanic Horizon (T=2 C, S=34.5 ppt, D=3000 m)
        c3 = mackenzie_sound_speed(2.0, 34.5, 3000.0)
        assert abs(c3 - 1507.6409) < 0.05, f"Expected ~1507.64 m/s, got {c3}"

    def test_ainslie_mccolm_absorption_properties(self):
        """Seawater absorption increases strictly monotonically with frequency."""
        freqs = [100.0, 200.0, 300.0, 400.0, 500.0]
        prev_alpha = 0.0
        for f in freqs:
            alpha_km, alpha_m = ainslie_mccolm_absorption(f, 15.0, 35.0, 100.0, 8.0)
            assert alpha_km > prev_alpha, f"Absorption at {f} kHz must exceed {prev_alpha}"
            assert math.isclose(alpha_m, alpha_km / 1000.0, rel_tol=1e-5)
            prev_alpha = alpha_km

        # Check values at 100 kHz and 500 kHz against Ainslie-McColm specification
        alpha_100_km, _ = ainslie_mccolm_absorption(100.0, 15.0, 35.0, 100.0, 8.0)
        alpha_500_km, _ = ainslie_mccolm_absorption(500.0, 15.0, 35.0, 100.0, 8.0)
        assert abs(alpha_100_km - 37.428) < 0.2, f"Expected ~37.4 dB/km at 100 kHz, got {alpha_100_km}"
        assert abs(alpha_500_km - 137.261) < 0.5, f"Expected ~137.3 dB/km at 500 kHz, got {alpha_500_km}"

    def test_turbidity_scattering_attenuation(self):
        """Turbidity scattering evaluates to zero at Cv=0 and increases with Cv and f."""
        _, alpha_zero = turbidity_scattering_attenuation(300.0, 0.0)
        assert alpha_zero == 0.0, "Turbidity attenuation must be 0.0 when Cv=0.0"

        _, alpha_low = turbidity_scattering_attenuation(300.0, 1.0e-5)
        _, alpha_high = turbidity_scattering_attenuation(300.0, 1.0e-3)
        assert alpha_high > alpha_low > 0.0

    def test_ambient_noise_spectral_density(self):
        """Thermal ambient noise rises at +20 dB/decade (+13.98 dB between 100 and 500 kHz)."""
        n_100 = ambient_noise_spectral_density(100.0)
        n_500 = ambient_noise_spectral_density(500.0)
        expected_diff = 20.0 * math.log10(500.0 / 100.0)  # ~13.9794 dB
        assert abs((n_500 - n_100) - expected_diff) < 1e-4


class TestAcceptanceCriteria:
    """
    Formal Acceptance Criteria Verification Suite.
    Directly addresses Requirements R1-R3 and Acceptance Criteria in ORIGINAL_REQUEST.md.
    """

    def test_sweep_3888_zero_violations(self):
        """
        Test 1: 100% of the 3,888 parametric sweep test cases pass with zero violations.
        Evaluates grid: 6 Turbidity x 6 Depth x 6 Range x 3 Temp x 2 Salinity = 3,888 points.
        """
        total_points = 0
        violations: List[str] = []

        for cv, d, r, t, s, ph in generate_parametric_sweep():
            total_points += 1
            out = adapt_sonar(cv, d, r, t, s, ph)

            # Verification 1: Transducer clamping
            if not (FREQ_MIN_HZ <= out.f_start_hz < out.f_c_hz < out.f_end_hz <= FREQ_MAX_HZ + 1e-2):
                violations.append(
                    f"Clamping violation at (Cv={cv}, D={d}, R={r}, T={t}, S={s}): "
                    f"f_start={out.f_start_hz}, f_c={out.f_c_hz}, f_end={out.f_end_hz}"
                )

            # Verification 2: Bandwidth consistency
            expected_bw = out.f_end_hz - out.f_start_hz
            if abs(out.bandwidth_hz - expected_bw) > 1e-3:
                violations.append(
                    f"Bandwidth difference violation: B={out.bandwidth_hz} vs f_end-f_start={expected_bw}"
                )
            if abs(out.bandwidth_hz / out.f_c_hz - FRACTIONAL_BANDWIDTH) > 1e-3:
                violations.append(
                    f"Fractional bandwidth violation: B/fc={out.bandwidth_hz/out.f_c_hz}"
                )

            # Verification 3: Pulse duration limits
            if not (PULSE_DURATION_MIN_S - 1e-6 <= out.t_pulse_s <= PULSE_DURATION_MAX_S + 1e-6):
                violations.append(f"Pulse length violation: T_pulse={out.t_pulse_s} s")

            # Verification 4: Power multiplier limits
            if not (AMP_SCALE_MIN - 1e-6 <= out.a_scale <= AMP_SCALE_MAX + 1e-6):
                violations.append(f"Power multiplier violation: A_scale={out.a_scale}")

            # Verification 5: Battery conservation mode
            if r <= BATTERY_CONSERVE_RANGE_M and cv <= BATTERY_CONSERVE_CV_MAX and d <= BATTERY_CONSERVE_DEPTH_MAX_M:
                if abs(out.a_scale - AMP_SCALE_MIN) > 1e-6:
                    violations.append(
                        f"Battery conservation violated at R={r}, Cv={cv}, D={d}: A_scale={out.a_scale}"
                    )

        assert total_points == TOTAL_SWEEP_POINTS == 3888, f"Expected 3,888 points, evaluated {total_points}"
        assert len(violations) == 0, f"Encountered {len(violations)} violations:\n" + "\n".join(violations[:10])

    def test_clamping_bounds_across_all_points(self):
        """
        Test 2: Clamping: 100 kHz <= f_start < f_c < f_end <= 500 kHz for every vector.
        """
        for cv, d, r, t, s, ph in generate_parametric_sweep():
            out = adapt_sonar(cv, d, r, t, s, ph)
            assert out.f_start_hz >= FREQ_MIN_HZ - 1e-2, f"f_start {out.f_start_hz} < 100 kHz"
            assert out.f_start_hz < out.f_c_hz, f"f_start {out.f_start_hz} >= f_c {out.f_c_hz}"
            assert out.f_c_hz < out.f_end_hz, f"f_c {out.f_c_hz} >= f_end {out.f_end_hz}"
            assert out.f_end_hz <= FREQ_MAX_HZ + 1e-2, f"f_end {out.f_end_hz} > 500 kHz"

    def test_bandwidth_consistency(self):
        """
        Test 3: Bandwidth: B == f_end - f_start and abs(B / f_c - 0.22) < 1e-3 across all vectors.
        """
        for cv, d, r, t, s, ph in generate_parametric_sweep():
            out = adapt_sonar(cv, d, r, t, s, ph)
            diff = abs(out.bandwidth_hz - (out.f_end_hz - out.f_start_hz))
            assert diff < 1e-4, f"B != f_end - f_start (diff={diff})"
            ratio_err = abs(out.bandwidth_hz / out.f_c_hz - FRACTIONAL_BANDWIDTH)
            assert ratio_err < 1e-4, f"B/fc error {ratio_err} exceeds tolerance"

    def test_pulse_length_bounds(self):
        """
        Test 4: Pulse length: 1.0 ms <= T_pulse <= 10.0 ms across all vectors.
        """
        for cv, d, r, t, s, ph in generate_parametric_sweep():
            out = adapt_sonar(cv, d, r, t, s, ph)
            assert 1.0 <= out.t_pulse_ms <= 10.0 + 1e-5, f"T_pulse {out.t_pulse_ms} ms out of [1.0, 10.0] ms"

    def test_power_multiplier_bounds(self):
        """
        Test 5: Power multiplier: 0.20 <= A_scale <= 1.00 across all vectors.
        """
        for cv, d, r, t, s, ph in generate_parametric_sweep():
            out = adapt_sonar(cv, d, r, t, s, ph)
            assert 0.20 <= out.a_scale <= 1.00 + 1e-5, f"A_scale {out.a_scale} out of [0.20, 1.00]"

    def test_monotonicity_turbidity(self):
        """
        Test 6: Monotonicity Property: Increasing turbidity (C_v) must never cause
        center frequency f_c to increase: df_c / dC_v <= 0.
        Tested across all (Depth, Range, Temp, Salinity) slices as C_v increases.
        """
        mono_checks = 0
        violations = []

        for d in SWEEP_DEPTHS:
            for r in SWEEP_RANGES:
                for t in SWEEP_TEMPERATURES:
                    for s in SWEEP_SALINITIES:
                        for ph in SWEEP_PH:
                            prev_fc = 1e12
                            for cv in SWEEP_TURBIDITIES:
                                out = adapt_sonar(cv, d, r, t, s, ph)
                                mono_checks += 1
                                if out.f_c_hz > prev_fc + 1e-3:
                                    violations.append(
                                        f"Monotonicity violation at (D={d}, R={r}, T={t}, S={s}, pH={ph}, Cv={cv}): "
                                        f"fc={out.f_c_hz} Hz > prev_fc={prev_fc} Hz"
                                    )
                                prev_fc = out.f_c_hz

        assert mono_checks == 3888
        assert len(violations) == 0, f"Monotonicity violated in {len(violations)} cases:\n" + "\n".join(violations[:5])

    def test_battery_conservation_clear_shallow(self):
        """
        Test 7: Battery Conservation: For ranges <= 25m in clear shallow water
        (C_v <= 1e-5, Depth <= 50m), A_scale must evaluate to exactly 0.20.
        """
        tested_cases = 0
        for d in SWEEP_DEPTHS:
            for r in SWEEP_RANGES:
                for t in SWEEP_TEMPERATURES:
                    for s in SWEEP_SALINITIES:
                        for ph in SWEEP_PH:
                            for cv in SWEEP_TURBIDITIES:
                                if r <= 25.0 and cv <= 1.0e-5 and d <= 50.0:
                                    out = adapt_sonar(cv, d, r, t, s, ph)
                                    tested_cases += 1
                                    assert abs(out.a_scale - 0.20) < 1e-6, (
                                        f"Battery conservation failed at R={r}, Cv={cv}, D={d}, pH={ph}: "
                                        f"A_scale was {out.a_scale}, expected 0.20"
                                    )
        assert tested_cases > 0, "At least one clear shallow short-range case must be evaluated"

    def test_benchmark_cases(self):
        """
        Test 8: The 3 Required Oceanographic Benchmark Cases:
            - Benchmark Case 1: Shallow Clear Warm Water (Docking/Avoidance)
            - Benchmark Case 2: Deep Turbid Cold Water (Long-Range Bathymetry)
            - Benchmark Case 3: Littoral Mid-Depth Transitional Environment
        """
        # Benchmark Case 1: Shallow Clear Warm Water
        # Cv = 0.0, D = 10.0m, R = 25.0m, T = 25.0 C, S = 35.0 ppt, pH = 8.0
        bm1 = adapt_sonar(turbidity_cv=0.0, depth_m=10.0, range_m=25.0, temp_c=25.0, salinity_ppt=35.0)
        assert abs(bm1.f_c_khz - 450.45) < 0.05, f"BM1 fc expected ~450.45 kHz, got {bm1.f_c_khz}"
        assert abs(bm1.bandwidth_khz - 99.10) < 0.05, f"BM1 B expected ~99.10 kHz, got {bm1.bandwidth_khz}"
        assert abs(bm1.f_start_khz - 400.90) < 0.05, f"BM1 f_start expected ~400.90 kHz, got {bm1.f_start_khz}"
        assert abs(bm1.f_end_khz - 500.00) < 0.05, f"BM1 f_end expected ~500.00 kHz, got {bm1.f_end_khz}"
        assert abs(bm1.t_pulse_ms - 1.00) < 1e-4, f"BM1 T_pulse expected 1.00 ms, got {bm1.t_pulse_ms}"
        assert abs(bm1.a_scale - 0.200) < 1e-4, f"BM1 A_scale expected 0.200, got {bm1.a_scale}"

        # Benchmark Case 2: Deep Turbid Cold Water
        # Cv = 1.0e-3, D = 500.0m, R = 300.0m, T = 2.0 C, S = 35.0 ppt, pH = 8.0
        bm2 = adapt_sonar(turbidity_cv=1.0e-3, depth_m=500.0, range_m=300.0, temp_c=2.0, salinity_ppt=35.0)
        assert abs(bm2.f_c_khz - 112.36) < 0.05, f"BM2 fc expected ~112.36 kHz, got {bm2.f_c_khz}"
        assert abs(bm2.bandwidth_khz - 24.72) < 0.05, f"BM2 B expected ~24.72 kHz, got {bm2.bandwidth_khz}"
        assert abs(bm2.f_start_khz - 100.00) < 0.05, f"BM2 f_start expected ~100.00 kHz, got {bm2.f_start_khz}"
        assert abs(bm2.f_end_khz - 124.72) < 0.05, f"BM2 f_end expected ~124.72 kHz, got {bm2.f_end_khz}"
        assert abs(bm2.t_pulse_ms - 10.00) < 1e-4, f"BM2 T_pulse expected 10.00 ms, got {bm2.t_pulse_ms}"
        assert abs(bm2.a_scale - 1.000) < 1e-4, f"BM2 A_scale expected 1.000, got {bm2.a_scale}"

        # Benchmark Case 3: Littoral Mid-Depth Transitional Environment
        # Cv = 1.0e-4, D = 50.0m, R = 100.0m, T = 15.0 C, S = 32.0 ppt, pH = 8.0
        bm3 = adapt_sonar(turbidity_cv=1.0e-4, depth_m=50.0, range_m=100.0, temp_c=15.0, salinity_ppt=32.0)
        assert abs(bm3.f_c_khz - 265.82) < 0.20, f"BM3 fc expected ~265.82 kHz, got {bm3.f_c_khz}"
        assert abs(bm3.bandwidth_khz - 58.48) < 0.10, f"BM3 B expected ~58.48 kHz, got {bm3.bandwidth_khz}"
        assert abs(bm3.f_start_khz - 236.58) < 0.10, f"BM3 f_start expected ~236.58 kHz, got {bm3.f_start_khz}"
        assert abs(bm3.f_end_khz - 295.06) < 0.10, f"BM3 f_end expected ~295.06 kHz, got {bm3.f_end_khz}"
        assert abs(bm3.t_pulse_ms - 3.45) < 0.05, f"BM3 T_pulse expected ~3.45 ms, got {bm3.t_pulse_ms}"
        assert abs(bm3.a_scale - 1.000) < 0.01, f"BM3 A_scale expected ~1.000, got {bm3.a_scale}"

    def test_ctypes_c_engine_bridge(self):
        """
        Test 9: C Library ctypes Bridge Verification.
        If build/libadaptive_sonar.so exists, execute all 3,888 points through
        the compiled C engine and assert exact numerical parity:
            - |f_c,py - f_c,c| <= 1.0 Hz
            - |A_scale,py - A_scale,c| <= 1e-4
            - status == 0 (SONAR_STATUS_SUCCESS)
        If the shared library has not yet been built (Milestone 2 track),
        gracefully skip with an informative message.
        """
        c_lib = get_c_engine_library()
        if c_lib is None:
            if pytest is not None:
                pytest.skip(
                    "C shared library build/libadaptive_sonar.so not yet present. "
                    "C engine is implemented in Milestone 2 (worker_m2). "
                    "C-Python parity test will execute when libadaptive_sonar.so is built."
                )
            return

        # Setup ctypes function signatures
        adapt_fn = getattr(c_lib, "sonar_engine_adapt", None) or getattr(c_lib, "sonar_engine_calculate", None)
        assert adapt_fn is not None, "C library missing sonar_engine_adapt / sonar_engine_calculate"

        adapt_fn.argtypes = [ctypes.POINTER(SonarOceanInputs), ctypes.POINTER(SonarOutputParams)]
        adapt_fn.restype = ctypes.c_int

        evaluated = 0
        for cv, d, r, t, s, ph in generate_parametric_sweep():
            evaluated += 1
            py_out = adapt_sonar(cv, d, r, t, s, ph)

            c_in = SonarOceanInputs(
                range_m=float(r),
                depth_m=float(d),
                temperature_c=float(t),
                salinity_ppt=float(s),
                turbidity_ntu=float(cv),
                ph=float(ph),
            )
            c_out = SonarOutputParams()

            status = adapt_fn(ctypes.byref(c_in), ctypes.byref(c_out))
            assert status == 0, f"C engine returned error status {status} for inputs (Cv={cv}, D={d}, R={r}, T={t}, S={s})"

            # Parity assertions
            fc_diff = abs(py_out.f_c_hz - c_out.f_c_hz)
            as_diff = abs(py_out.a_scale - c_out.a_scale)

            assert fc_diff <= 1.0, (
                f"f_c mismatch: py={py_out.f_c_hz} Hz, c={c_out.f_c_hz} Hz (diff={fc_diff} Hz)"
            )
            assert as_diff <= 1e-4, (
                f"A_scale mismatch: py={py_out.a_scale}, c={c_out.a_scale} (diff={as_diff})"
            )

        assert evaluated == 3888


# ============================================================================
# Standalone CLI Test Runner
# ============================================================================

def run_standalone_testbench() -> int:
    """Executes the full suite directly and prints diagnostic report."""
    print("=" * 78)
    print("Adaptive Sonar Transmitter Payload — Oceanographic Adaptation Testbench")
    print("Target: STM32G474RE | Band: 100 kHz - 500 kHz")
    print("=" * 78)

    print("\n[1/4] Running Acoustic Model Unit Tests...")
    test_models = TestAcousticPhysicalModels()
    test_models.test_mackenzie_sound_speed_calibration_points()
    test_models.test_ainslie_mccolm_absorption_properties()
    test_models.test_turbidity_scattering_attenuation()
    test_models.test_ambient_noise_spectral_density()
    print("  -> All acoustic model tests PASSED.")

    print("\n[2/4] Running 3,888-Point Parametric Sweep...")
    test_suite = TestAcceptanceCriteria()
    test_suite.test_sweep_3888_zero_violations()
    test_suite.test_clamping_bounds_across_all_points()
    test_suite.test_bandwidth_consistency()
    test_suite.test_pulse_length_bounds()
    test_suite.test_power_multiplier_bounds()
    test_suite.test_monotonicity_turbidity()
    test_suite.test_battery_conservation_clear_shallow()
    print("  -> 3,888 sweep points evaluated: 100.0% PASS, 0 VIOLATIONS.")

    print("\n[3/4] Verifying 3 Canonical Benchmark Cases...")
    test_suite.test_benchmark_cases()
    bm1 = adapt_sonar(0.0, 10.0, 25.0, 25.0, 35.0)
    bm2 = adapt_sonar(1e-3, 500.0, 300.0, 2.0, 35.0)
    bm3 = adapt_sonar(1e-4, 50.0, 100.0, 15.0, 32.0)
    print(f"  Benchmark 1 (Shallow Clear Warm):  fc={bm1.f_c_khz:.2f} kHz, B={bm1.bandwidth_khz:.2f} kHz, Tp={bm1.t_pulse_ms:.2f} ms, A_scale={bm1.a_scale:.3f}")
    print(f"  Benchmark 2 (Deep Turbid Cold):    fc={bm2.f_c_khz:.2f} kHz, B={bm2.bandwidth_khz:.2f} kHz, Tp={bm2.t_pulse_ms:.2f} ms, A_scale={bm2.a_scale:.3f}")
    print(f"  Benchmark 3 (Littoral Transition): fc={bm3.f_c_khz:.2f} kHz, B={bm3.bandwidth_khz:.2f} kHz, Tp={bm3.t_pulse_ms:.2f} ms, A_scale={bm3.a_scale:.3f}")
    print("  -> All 3 benchmark cases MATCH expected specifications.")

    print("\n[4/4] Checking C Library ctypes Bridge Status...")
    c_lib = get_c_engine_library()
    if c_lib is not None:
        print("  -> libadaptive_sonar.so detected! Running C-Python parity check...")
        test_suite.test_ctypes_c_engine_bridge()
        print("  -> C-Python 3,888-point parity PASSED (|df_c| <= 1.0 Hz, |dA_scale| <= 1e-4).")
    else:
        print("  -> libadaptive_sonar.so not yet present (built in Milestone 2). Bridge test skipped cleanly.")

    print("\n" + "=" * 78)
    print("TESTBENCH RESULT: ALL SUITES PASSED (0 FAILURES, 0 VIOLATIONS)")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(run_standalone_testbench())
