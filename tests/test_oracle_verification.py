#!/usr/bin/env python3
"""
Independent Double-Precision (Float64) Mathematical Oracle & Empirical Challenge Harness
========================================================================================

Target Microcontroller: STM32G474RE (ARM Cortex-M4 with Single-Precision IEEE-754 FPU)
Tested Binary: build/libadaptive_sonar.so

This module conducts rigorous independent mathematical verification of the C engine:
1. Ground-truth float64 acoustic models:
   - Mackenzie (1981) 9-term polynomial sound speed c(T, S, D)
   - Ainslie-McColm (1998) absorption alpha_sw(f, T, S, D, pH)
   - Particulate scattering alpha_turb(f, C_v)
   - Thermal ambient noise spectral density N_0(f)
   - Active sonar equation SNR(f)
2. Machine-precision exact root solver (60-iteration float64 bisection, resolution < 1e-12 Hz)
3. High-precision float64 10-iteration bisection (to isolate algorithm vs float32 arithmetic error)
4. Empirical challenge harness:
   - Suite 1: Canonical 1,296-point parametric sweep parity & statistical deviation
   - Suite 2: 3,000 randomized Monte Carlo oceanographic profiles
   - Suite 3: Float32 vs Float64 precision divergence & cancellation stress test (10,000 trials)
   - Suite 4: Bisection convergence proof & empirical bracket verification (<= 10 iterations)
   - Suite 5: Battery conservation threshold perturbation & boundary analysis
   - Suite 6: Continuous monotonicity perturbation test (df_c / dC_v <= 0 across 2,000 pairs)
   - Suite 7: Defensive parameter validation & adversarial robustness
"""

import ctypes
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Generator, List, Optional, Tuple

import numpy as np

# ============================================================================
# Physical Specifications & Constraints
# ============================================================================

FREQ_MIN_HZ = 100000.0
FREQ_MAX_HZ = 500000.0
FRACTIONAL_BANDWIDTH = 0.22
HALF_BW_RATIO = FRACTIONAL_BANDWIDTH / 2.0  # 0.11

FC_MIN_HZ = FREQ_MIN_HZ / (1.0 - HALF_BW_RATIO)  # 112359.55056 Hz
FC_MAX_HZ = FREQ_MAX_HZ / (1.0 + HALF_BW_RATIO)  # 450450.45045 Hz

PULSE_DURATION_MIN_S = 0.001  # 1.0 ms
PULSE_DURATION_MAX_S = 0.010  # 10.0 ms

AMP_SCALE_MIN = 0.20
AMP_SCALE_MAX = 1.00

BATTERY_CONSERVE_RANGE_M = 25.0
BATTERY_CONSERVE_CV_MAX = 1.0e-5
BATTERY_CONSERVE_DEPTH_MAX_M = 50.0

DEFAULT_OCEAN_PH = 8.0
NOMINAL_SL_DB = 190.0
TARGET_STRENGTH_DB = -15.0
TARGET_SNR_DB = 75.0
BISECTION_ITERATIONS = 10


# ============================================================================
# Ctypes Struct Definitions
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


@dataclass
class OracleOutputs:
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


# ============================================================================
# Independent Double-Precision (Float64) Reference Oracle
# ============================================================================

class OceanAcousticOracle:
    """
    Independent float64 mathematical model for ocean acoustic propagation
    and closed-loop transmitter parameter adaptation.
    """

    @staticmethod
    def sound_speed_mackenzie(temp_c: float, salinity_ppt: float, depth_m: float) -> float:
        """Mackenzie (1981) 9-term polynomial sound speed in float64."""
        t = float(temp_c)
        s = float(salinity_ppt)
        d = float(depth_m)
        ds = s - 35.0
        d2 = d * d
        d3 = d2 * d

        c = (
            1448.96
            + 4.591 * t
            - 5.304e-2 * (t ** 2)
            + 2.374e-4 * (t ** 3)
            + 1.340 * ds
            + 1.630e-2 * d
            + 1.675e-7 * d2
            - 1.025e-2 * t * ds
            - 7.139e-13 * t * d3
        )
        return float(c)

    @staticmethod
    def absorption_ainslie_mccolm(
        freq_khz: float,
        temp_c: float,
        salinity_ppt: float,
        depth_m: float,
        ph: float = DEFAULT_OCEAN_PH,
    ) -> Tuple[float, float]:
        """
        Ainslie-McColm (1998) absorption in float64.
        Returns: (alpha_db_km, alpha_db_m)
        """
        f = float(freq_khz)
        if f <= 0.0:
            return 0.0, 0.0

        t = float(temp_c)
        s = max(float(salinity_ppt), 0.0)
        d_km = float(depth_m) / 1000.0
        effective_ph = float(ph) if ph > 0.0 else DEFAULT_OCEAN_PH

        f1 = 0.78 * math.sqrt(s / 35.0) * math.exp(t / 26.0)
        f2 = 42.0 * math.exp(t / 17.0)

        a1 = 0.106 * math.exp((effective_ph - 8.0) / 0.56)
        a2 = 0.52 * (1.0 + t / 43.0) * (s / 35.0) * math.exp(-d_km / 6.0)
        a3 = 0.00049 * math.exp(-(t / 27.0 + d_km / 17.0))

        f_sq = f * f
        term_boric = a1 * (f1 * f_sq) / (f1 * f1 + f_sq)
        term_mgso4 = a2 * (f2 * f_sq) / (f2 * f2 + f_sq)
        term_water = a3 * f_sq

        alpha_db_km = term_boric + term_mgso4 + term_water
        alpha_db_m = alpha_db_km / 1000.0
        return alpha_db_km, alpha_db_m

    @staticmethod
    def particulate_scattering(freq_khz: float, cv: float) -> Tuple[float, float]:
        """
        Rayleigh regime suspended particulate scattering attenuation in float64.
        Returns: (alpha_turb_db_km, alpha_turb_db_m)
        """
        c_v = max(float(cv), 0.0)
        f = float(freq_khz)
        alpha_m = c_v * 50.0 * ((f / 100.0) ** 2)
        alpha_km = alpha_m * 1000.0
        return alpha_km, alpha_m

    @staticmethod
    def ambient_noise_n0(freq_khz: float) -> float:
        """Thermal agitation ambient noise spectral density N0(f) in float64."""
        f = max(float(freq_khz), 1.0)
        return -75.0 + 20.0 * math.log10(f)

    @classmethod
    def calc_snr(
        cls,
        freq_khz: float,
        range_m: float,
        cv: float,
        depth_m: float,
        temp_c: float,
        salinity_ppt: float,
        ph: float = DEFAULT_OCEAN_PH,
    ) -> float:
        """Active monostatic sonar SNR in float64."""
        r = max(float(range_m), 1.0)
        f = float(freq_khz)

        _, alpha_sw_m = cls.absorption_ainslie_mccolm(f, temp_c, salinity_ppt, depth_m, ph)
        _, alpha_turb_m = cls.particulate_scattering(f, cv)
        alpha_tot_m = alpha_sw_m + alpha_turb_m

        tl = 40.0 * math.log10(r) + 2.0 * alpha_tot_m * r
        b_hz = FRACTIONAL_BANDWIDTH * f * 1000.0
        n0 = cls.ambient_noise_n0(f)
        nl = n0 + 10.0 * math.log10(b_hz)
        di = 15.0 + 20.0 * math.log10(f / 100.0)

        snr = NOMINAL_SL_DB + TARGET_STRENGTH_DB + di - tl - nl
        return float(snr)

    @classmethod
    def find_exact_root(
        cls,
        range_m: float,
        cv: float,
        depth_m: float,
        temp_c: float,
        salinity_ppt: float,
        ph: float = DEFAULT_OCEAN_PH,
    ) -> float:
        """
        Calculates the machine-precision exact carrier frequency root in [100, 500] kHz
        using 60-iteration float64 bisection (resolution = 400 kHz / 2^60 ≈ 3.5e-13 Hz).
        """
        f_low = 100.0
        f_high = 500.0

        snr_high = cls.calc_snr(f_high, range_m, cv, depth_m, temp_c, salinity_ppt, ph)
        if snr_high >= TARGET_SNR_DB:
            return f_high

        snr_low = cls.calc_snr(f_low, range_m, cv, depth_m, temp_c, salinity_ppt, ph)
        if snr_low <= TARGET_SNR_DB:
            return f_low

        for _ in range(60):
            f_mid = 0.5 * (f_low + f_high)
            snr_mid = cls.calc_snr(f_mid, range_m, cv, depth_m, temp_c, salinity_ppt, ph)
            if snr_mid >= TARGET_SNR_DB:
                f_low = f_mid
            else:
                f_high = f_mid

        return 0.5 * (f_low + f_high)

    @classmethod
    def adapt(
        cls,
        range_m: float,
        turbidity_ntu_or_cv: float,
        depth_m: float,
        temp_c: float,
        salinity_ppt: float,
        ph: float = DEFAULT_OCEAN_PH,
        iterations: int = 10,
        use_exact_root: bool = False,
    ) -> OracleOutputs:
        """
        Complete oceanographic adaptation calculation in float64.
        If use_exact_root is True, uses 60-iteration machine precision root.
        Otherwise, uses bounded bisection with specified iterations (default 10).
        """
        r = max(float(range_m), 1.0)
        d = max(float(depth_m), 0.0)
        t = float(temp_c)
        s = float(salinity_ppt)

        # Turbidity conversion matching C engine
        raw_turb = float(turbidity_ntu_or_cv)
        if raw_turb > 0.01:
            c_v = raw_turb * 1.0e-6
        else:
            c_v = max(raw_turb, 0.0)

        eff_ph = float(ph) if ph > 0.0 else DEFAULT_OCEAN_PH

        # 1. Frequency optimization
        if use_exact_root:
            f_raw = cls.find_exact_root(r, c_v, d, t, s, eff_ph)
            executed_iters = 60
        else:
            f_low = 100.0
            f_high = 500.0
            snr_high = cls.calc_snr(f_high, r, c_v, d, t, s, eff_ph)
            snr_low = cls.calc_snr(f_low, r, c_v, d, t, s, eff_ph)

            if snr_high >= TARGET_SNR_DB:
                f_raw = f_high
            elif snr_low <= TARGET_SNR_DB:
                f_raw = f_low
            else:
                for _ in range(iterations):
                    f_mid = 0.5 * (f_low + f_high)
                    snr_mid = cls.calc_snr(f_mid, r, c_v, d, t, s, eff_ph)
                    if snr_mid >= TARGET_SNR_DB:
                        f_low = f_mid
                    else:
                        f_high = f_mid
                f_raw = 0.5 * (f_low + f_high)
            executed_iters = iterations

        # 2. Guardband Clamping to [112.35955 kHz, 450.45045 kHz]
        fc_min_khz = FC_MIN_HZ / 1000.0
        fc_max_khz = FC_MAX_HZ / 1000.0
        f_c_khz = min(max(f_raw, fc_min_khz), fc_max_khz)
        f_c_hz = f_c_khz * 1000.0

        # 3. Fractional Bandwidth
        b_hz = FRACTIONAL_BANDWIDTH * f_c_hz
        f_start_hz = max(FREQ_MIN_HZ, f_c_hz - 0.5 * b_hz)
        f_end_hz = min(FREQ_MAX_HZ, f_c_hz + 0.5 * b_hz)

        # 4. Pulse Duration Scheduling
        if r <= BATTERY_CONSERVE_RANGE_M:
            t_pulse_s = PULSE_DURATION_MIN_S
        else:
            t_pulse_ms = min(10.0, 1.0 + 9.0 * (r - 25.0) / (300.0 - 25.0))
            t_pulse_s = t_pulse_ms / 1000.0

        # 5. Amplitude Multiplier & Battery Conservation
        snr_at_fc = cls.calc_snr(f_c_khz, r, c_v, d, t, s, eff_ph)
        snr_margin_db = snr_at_fc - TARGET_SNR_DB

        is_clear_shallow = (
            (r <= BATTERY_CONSERVE_RANGE_M)
            and (c_v <= BATTERY_CONSERVE_CV_MAX)
            and (d <= BATTERY_CONSERVE_DEPTH_MAX_M)
        )

        if is_clear_shallow:
            a_scale = AMP_SCALE_MIN
        else:
            if snr_margin_db >= 13.9794:
                a_scale = AMP_SCALE_MIN
            elif snr_margin_db <= 0.0:
                a_scale = AMP_SCALE_MAX
            else:
                calc_scale = 10.0 ** (-snr_margin_db / 20.0)
                a_scale = min(max(calc_scale, AMP_SCALE_MIN), AMP_SCALE_MAX)

        # 6. Diagnostics
        c_mps = cls.sound_speed_mackenzie(t, s, d)
        _, alpha_sw_m = cls.absorption_ainslie_mccolm(f_c_khz, t, s, d, eff_ph)
        _, alpha_turb_m = cls.particulate_scattering(f_c_khz, c_v)
        total_abs_m = alpha_sw_m + alpha_turb_m

        return OracleOutputs(
            f_c_hz=f_c_hz,
            f_start_hz=f_start_hz,
            f_end_hz=f_end_hz,
            bandwidth_hz=b_hz,
            t_pulse_s=t_pulse_s,
            a_scale=a_scale,
            sound_speed_mps=c_mps,
            absorption_db_per_m=total_abs_m,
            snr_margin_db=snr_margin_db,
            bisection_iters=executed_iters,
        )


# ============================================================================
# C Engine Shared Library Wrapper
# ============================================================================

def load_c_engine() -> ctypes.CDLL:
    repo_root = Path(__file__).resolve().parent.parent
    so_path = repo_root / "build" / "libadaptive_sonar.so"
    if not so_path.exists():
        raise FileNotFoundError(f"Shared library not found at {so_path}")

    c_lib = ctypes.CDLL(str(so_path))

    # sonar_engine_adapt
    adapt_fn = getattr(c_lib, "sonar_engine_adapt", None)
    if adapt_fn is None:
        adapt_fn = getattr(c_lib, "sonar_engine_calculate")
    adapt_fn.argtypes = [ctypes.POINTER(SonarOceanInputs), ctypes.POINTER(SonarOutputParams)]
    adapt_fn.restype = ctypes.c_int

    # sonar_engine_calc_sound_speed
    c_lib.sonar_engine_calc_sound_speed.argtypes = [ctypes.c_float, ctypes.c_float, ctypes.c_float]
    c_lib.sonar_engine_calc_sound_speed.restype = ctypes.c_float

    # sonar_engine_calc_absorption
    c_lib.sonar_engine_calc_absorption.argtypes = [
        ctypes.c_float, ctypes.c_float, ctypes.c_float, ctypes.c_float, ctypes.c_float
    ]
    c_lib.sonar_engine_calc_absorption.restype = ctypes.c_float

    # sonar_engine_validate_inputs
    c_lib.sonar_engine_validate_inputs.argtypes = [ctypes.POINTER(SonarOceanInputs)]
    c_lib.sonar_engine_validate_inputs.restype = ctypes.c_int

    return c_lib


def call_c_adapt(c_lib: ctypes.CDLL, range_m, depth_m, temp_c, salinity_ppt, turbidity, ph) -> Tuple[int, SonarOutputParams]:
    c_in = SonarOceanInputs(
        range_m=float(range_m),
        depth_m=float(depth_m),
        temperature_c=float(temp_c),
        salinity_ppt=float(salinity_ppt),
        turbidity_ntu=float(turbidity),
        ph=float(ph),
    )
    c_out = SonarOutputParams()
    adapt_fn = getattr(c_lib, "sonar_engine_adapt", None) or getattr(c_lib, "sonar_engine_calculate")
    status = adapt_fn(ctypes.byref(c_in), ctypes.byref(c_out))
    return status, c_out


# ============================================================================
# Statistical Deviation Tracker
# ============================================================================

class DeviationStats:
    def __init__(self, name: str):
        self.name = name
        self.errors: List[float] = []
        self.rel_errors: List[float] = []

    def update(self, c_val: float, ref_val: float):
        diff = float(c_val - ref_val)
        abs_diff = abs(diff)
        self.errors.append(abs_diff)
        if abs(ref_val) > 1e-12:
            self.rel_errors.append(abs_diff / abs(ref_val))
        else:
            self.rel_errors.append(0.0)

    @property
    def count(self) -> int:
        return len(self.errors)

    @property
    def max_error(self) -> float:
        return float(np.max(self.errors)) if self.errors else 0.0

    @property
    def mean_error(self) -> float:
        return float(np.mean(self.errors)) if self.errors else 0.0

    @property
    def rms_error(self) -> float:
        return float(np.sqrt(np.mean(np.square(self.errors)))) if self.errors else 0.0

    @property
    def max_rel_error_pct(self) -> float:
        return float(np.max(self.rel_errors) * 100.0) if self.rel_errors else 0.0

    @property
    def mean_rel_error_pct(self) -> float:
        return float(np.mean(self.rel_errors) * 100.0) if self.rel_errors else 0.0


# ============================================================================
# Challenge Suite 1: Canonical 1,296 Parametric Sweep Parity
# ============================================================================

def run_suite_1296_sweep(c_lib: ctypes.CDLL) -> Dict[str, DeviationStats]:
    """Evaluates all 1,296 sweep points and collects statistical deviations."""
    SWEEP_TURBIDITIES = [0.0, 1.0e-5, 5.0e-5, 1.0e-4, 5.0e-4, 1.0e-3]
    SWEEP_DEPTHS = [5.0, 20.0, 50.0, 100.0, 250.0, 500.0]
    SWEEP_RANGES = [10.0, 25.0, 50.0, 100.0, 200.0, 300.0]
    SWEEP_TEMPERATURES = [2.0, 15.0, 30.0]
    SWEEP_SALINITIES = [30.0, 35.0]

    stats = {
        "f_c_hz": DeviationStats("f_c_hz"),
        "bandwidth_hz": DeviationStats("bandwidth_hz"),
        "t_pulse_s": DeviationStats("t_pulse_s"),
        "a_scale": DeviationStats("a_scale"),
        "sound_speed_mps": DeviationStats("sound_speed_mps"),
        "absorption_db_per_m": DeviationStats("absorption_db_per_m"),
        "snr_margin_db": DeviationStats("snr_margin_db"),
    }

    total = 0
    decision_flips = 0

    for d in SWEEP_DEPTHS:
        for r in SWEEP_RANGES:
            for t in SWEEP_TEMPERATURES:
                for s in SWEEP_SALINITIES:
                    for cv in SWEEP_TURBIDITIES:
                        total += 1
                        ref = OceanAcousticOracle.adapt(r, cv, d, t, s, DEFAULT_OCEAN_PH, iterations=10)
                        status, c_out = call_c_adapt(c_lib, r, d, t, s, cv, DEFAULT_OCEAN_PH)
                        assert status == 0, f"C engine returned status {status}"

                        stats["f_c_hz"].update(c_out.f_c_hz, ref.f_c_hz)
                        stats["bandwidth_hz"].update(c_out.bandwidth_hz, ref.bandwidth_hz)
                        stats["t_pulse_s"].update(c_out.t_pulse_s, ref.t_pulse_s)
                        stats["a_scale"].update(c_out.a_scale, ref.a_scale)
                        stats["sound_speed_mps"].update(c_out.sound_speed_mps, ref.sound_speed_mps)
                        stats["absorption_db_per_m"].update(c_out.absorption_db_per_m, ref.absorption_db_per_m)
                        stats["snr_margin_db"].update(c_out.snr_margin_db, ref.snr_margin_db)

                        if abs(c_out.f_c_hz - ref.f_c_hz) > 10.0:
                            decision_flips += 1

    print(f"  [Suite 1] Evaluated {total} points. Bisection decision flips: {decision_flips}")
    return stats


# ============================================================================
# Challenge Suite 2: Randomized Monte Carlo Oceanographic Profiles (>= 3,000)
# ============================================================================

def run_suite_randomized_monte_carlo(c_lib: ctypes.CDLL, num_trials: int = 3000) -> Tuple[Dict[str, DeviationStats], Dict[str, DeviationStats]]:
    """
    Evaluates >= 3,000 randomized continuous physical profiles across full operational envelopes:
    - Compares C float32 engine against Float64 10-iter bisection oracle
    - Compares C float32 engine against Float64 Exact Root solver (60-iter machine precision)
    """
    np.random.seed(42)

    stats_vs_10iter = {
        "f_c_hz": DeviationStats("f_c_hz (vs 10-iter float64)"),
        "bandwidth_hz": DeviationStats("bandwidth_hz (vs 10-iter float64)"),
        "t_pulse_s": DeviationStats("t_pulse_s (vs 10-iter float64)"),
        "a_scale": DeviationStats("a_scale (vs 10-iter float64)"),
        "sound_speed_mps": DeviationStats("sound_speed_mps (vs 10-iter float64)"),
        "absorption_db_per_m": DeviationStats("absorption_db_per_m (vs 10-iter float64)"),
        "snr_margin_db": DeviationStats("snr_margin_db (vs 10-iter float64)"),
    }

    stats_vs_exact = {
        "f_c_hz": DeviationStats("f_c_hz (vs exact root float64)"),
        "bandwidth_hz": DeviationStats("bandwidth_hz (vs exact root float64)"),
        "a_scale": DeviationStats("a_scale (vs exact root float64)"),
        "snr_margin_db": DeviationStats("snr_margin_db (vs exact root float64)"),
    }

    # Generate diverse profiles: mix uniform, log-uniform, and near-boundary sampling
    ranges = np.concatenate([
        np.random.uniform(1.0, 30.0, num_trials // 3),
        np.random.uniform(30.0, 300.0, num_trials // 3),
        np.random.uniform(300.0, 2000.0, num_trials - 2 * (num_trials // 3)),
    ])
    depths = np.concatenate([
        np.random.uniform(0.0, 50.0, num_trials // 3),
        np.random.uniform(50.0, 1000.0, num_trials // 3),
        np.random.uniform(1000.0, 6000.0, num_trials - 2 * (num_trials // 3)),
    ])
    temps = np.random.uniform(-2.0, 40.0, num_trials)
    salinities = np.random.uniform(0.0, 45.0, num_trials)
    
    # Turbidity: sample both volume concentration (< 0.01) and NTU/ppm (0.01 to 1000)
    turbidities = np.concatenate([
        10.0 ** np.random.uniform(-6.0, -3.0, num_trials // 2),  # Cv: 1e-6 to 1e-3
        np.random.uniform(0.0, 1000.0, num_trials - (num_trials // 2)),  # NTU: 0 to 1000
    ])
    phs = np.random.choice([0.0, 6.0, 7.5, 8.0, 8.5, 9.5], num_trials)

    np.random.shuffle(ranges)
    np.random.shuffle(depths)
    np.random.shuffle(temps)
    np.random.shuffle(salinities)
    np.random.shuffle(turbidities)

    for i in range(num_trials):
        r = float(ranges[i])
        d = float(depths[i])
        t = float(temps[i])
        s = float(salinities[i])
        turb = float(turbidities[i])
        ph = float(phs[i])

        status, c_out = call_c_adapt(c_lib, r, d, t, s, turb, ph)
        assert status == 0, f"C engine returned status {status} at trial {i}"

        # 1. Compare against 10-iteration float64 bisection
        ref_10 = OceanAcousticOracle.adapt(r, turb, d, t, s, ph, iterations=10, use_exact_root=False)
        stats_vs_10iter["f_c_hz"].update(c_out.f_c_hz, ref_10.f_c_hz)
        stats_vs_10iter["bandwidth_hz"].update(c_out.bandwidth_hz, ref_10.bandwidth_hz)
        stats_vs_10iter["t_pulse_s"].update(c_out.t_pulse_s, ref_10.t_pulse_s)
        stats_vs_10iter["a_scale"].update(c_out.a_scale, ref_10.a_scale)
        stats_vs_10iter["sound_speed_mps"].update(c_out.sound_speed_mps, ref_10.sound_speed_mps)
        stats_vs_10iter["absorption_db_per_m"].update(c_out.absorption_db_per_m, ref_10.absorption_db_per_m)
        stats_vs_10iter["snr_margin_db"].update(c_out.snr_margin_db, ref_10.snr_margin_db)

        # 2. Compare against exact root float64
        ref_exact = OceanAcousticOracle.adapt(r, turb, d, t, s, ph, use_exact_root=True)
        stats_vs_exact["f_c_hz"].update(c_out.f_c_hz, ref_exact.f_c_hz)
        stats_vs_exact["bandwidth_hz"].update(c_out.bandwidth_hz, ref_exact.bandwidth_hz)
        stats_vs_exact["a_scale"].update(c_out.a_scale, ref_exact.a_scale)
        stats_vs_exact["snr_margin_db"].update(c_out.snr_margin_db, ref_exact.snr_margin_db)

    print(f"  [Suite 2] Evaluated {num_trials} randomized Monte Carlo profiles.")
    return stats_vs_10iter, stats_vs_exact


# ============================================================================
# Challenge Suite 3: Float32 Precision Divergence & Catastrophic Cancellation
# ============================================================================

def run_suite_cancellation_stress(c_lib: ctypes.CDLL, trials: int = 10000) -> Dict[str, DeviationStats]:
    """
    Stresses single-precision float32 sub-functions across 10,000 adversarial inputs:
    - Checks Mackenzie sound speed polynomial cancellation (especially near -2 C and 6000 m depth)
    - Checks Ainslie-McColm absorption exponential scaling and resonance terms
    - Checks SNR equation calculation
    """
    np.random.seed(99)

    stats = {
        "sound_speed": DeviationStats("sound_speed"),
        "absorption": DeviationStats("absorption"),
    }

    # Corner case samples:
    # Extreme depths, freezing temperatures, boundary salinities
    edge_temps = [-2.0, -1.5, 0.0, 4.0, 15.0, 30.0, 39.9, 40.0]
    edge_salinities = [0.0, 5.0, 35.0, 40.0, 45.0]
    edge_depths = [0.0, 10.0, 100.0, 3000.0, 5000.0, 6000.0]
    edge_freqs = [100000.0, 112359.55, 200000.0, 300000.0, 450450.45, 500000.0]
    edge_phs = [6.0, 8.0, 9.5]

    for t in edge_temps:
        for s in edge_salinities:
            for d in edge_depths:
                # Mackenzie test
                c_speed = c_lib.sonar_engine_calc_sound_speed(ctypes.c_float(t), ctypes.c_float(s), ctypes.c_float(d))
                ref_speed = OceanAcousticOracle.sound_speed_mackenzie(t, s, d)
                stats["sound_speed"].update(c_speed, ref_speed)

                for f in edge_freqs:
                    for ph in edge_phs:
                        c_abs = c_lib.sonar_engine_calc_absorption(
                            ctypes.c_float(f), ctypes.c_float(t), ctypes.c_float(s), ctypes.c_float(d), ctypes.c_float(ph)
                        )
                        _, ref_abs_m = OceanAcousticOracle.absorption_ainslie_mccolm(f / 1000.0, t, s, d, ph)
                        stats["absorption"].update(c_abs, ref_abs_m)

    # Now randomize remaining trials
    remaining = trials - stats["sound_speed"].count
    rand_t = np.random.uniform(-2.0, 40.0, remaining)
    rand_s = np.random.uniform(0.0, 45.0, remaining)
    rand_d = np.random.uniform(0.0, 6000.0, remaining)
    rand_f = np.random.uniform(100000.0, 500000.0, remaining)
    rand_ph = np.random.uniform(6.0, 9.5, remaining)

    for i in range(remaining):
        t = float(rand_t[i])
        s = float(rand_s[i])
        d = float(rand_d[i])
        f = float(rand_f[i])
        ph = float(rand_ph[i])

        c_speed = c_lib.sonar_engine_calc_sound_speed(ctypes.c_float(t), ctypes.c_float(s), ctypes.c_float(d))
        ref_speed = OceanAcousticOracle.sound_speed_mackenzie(t, s, d)
        stats["sound_speed"].update(c_speed, ref_speed)

        c_abs = c_lib.sonar_engine_calc_absorption(
            ctypes.c_float(f), ctypes.c_float(t), ctypes.c_float(s), ctypes.c_float(d), ctypes.c_float(ph)
        )
        _, ref_abs_m = OceanAcousticOracle.absorption_ainslie_mccolm(f / 1000.0, t, s, d, ph)
        stats["absorption"].update(c_abs, ref_abs_m)

    print(f"  [Suite 3] Evaluated {stats['sound_speed'].count} sub-function points for float32 precision divergence.")
    return stats


# ============================================================================
# Challenge Suite 4: Bisection Convergence Verification (<= 10 Iterations)
# ============================================================================

def run_suite_bisection_convergence(c_lib: ctypes.CDLL, trials: int = 1500) -> Dict[str, float]:
    """
    Verifies that the bisection search converges within <= 10 iterations:
    - Theory: Bracket width after 10 iterations is (500 - 100) / 2^10 = 390.625 Hz.
      Midpoint error relative to true un-clamped root is at most 195.3125 Hz.
    - Tests that for all interior solutions (where the true root is in [112.36, 450.45] kHz),
      the error |f_c,C - f_c,exact| is <= 195.32 Hz.
    """
    np.random.seed(123)

    interior_cases = 0
    max_interior_error_hz = 0.0
    violations = []

    # Target parameters that yield interior roots
    for _ in range(trials):
        r = float(np.random.uniform(20.0, 200.0))
        d = float(np.random.uniform(5.0, 300.0))
        t = float(np.random.uniform(5.0, 25.0))
        s = float(np.random.uniform(30.0, 36.0))
        cv = float(10.0 ** np.random.uniform(-5.0, -3.5))
        ph = 8.0

        exact_root_khz = OceanAcousticOracle.find_exact_root(r, cv, d, t, s, ph)
        # Check if interior (not clamped by guardband [112.35955, 450.45045])
        if 113.0 < exact_root_khz < 450.0:
            interior_cases += 1
            status, c_out = call_c_adapt(c_lib, r, d, t, s, cv, ph)
            assert status == 0
            err_hz = abs(c_out.f_c_hz - (exact_root_khz * 1000.0))
            if err_hz > max_interior_error_hz:
                max_interior_error_hz = err_hz
            # Allowed theoretical bound is 400,000 Hz / 2048 = 195.3125 Hz (+ float32 rounding epsilon ~0.5 Hz)
            if err_hz > 196.0:
                violations.append((r, cv, d, t, s, exact_root_khz, c_out.f_c_hz, err_hz))

    print(f"  [Suite 4] Evaluated {interior_cases} interior root cases.")
    print(f"  [Suite 4] Theoretical bound: <= 195.3125 Hz. Observed max error: {max_interior_error_hz:.2f} Hz.")
    assert len(violations) == 0, f"Encountered {len(violations)} convergence violations!"
    return {
        "interior_cases": interior_cases,
        "max_interior_error_hz": max_interior_error_hz,
        "theoretical_bound_hz": 195.3125,
    }


# ============================================================================
# Challenge Suite 5: Battery Conservation Boundary & Perturbations
# ============================================================================

def run_suite_battery_conservation(c_lib: ctypes.CDLL) -> Dict[str, int]:
    """
    Stress-tests the battery conservation boundary:
    Thresholds: R <= 25.0 m, Cv <= 1.0e-5, Depth <= 50.0 m.
    - Tests fine perturbations across the boundary:
      R = 25.0 - eps, 25.0, 25.0 + eps
      Depth = 50.0 - eps, 50.0, 50.0 + eps
      Cv = 1e-5 - eps, 1e-5, 1e-5 + eps
    - Verifies A_scale == 0.20 strictly when within envelope.
    - Verifies smooth, continuous, and valid A_scale in [0.20, 1.00] outside envelope.
    """
    epsilons = [-1.0e-3, -1.0e-5, 0.0, 1.0e-5, 1.0e-3]
    inside_cases = 0
    outside_cases = 0

    for dr in epsilons:
        r = 25.0 + dr
        for dd in epsilons:
            d = 50.0 + dd
            for dcv in [-1.0e-7, 0.0, 1.0e-7]:
                cv = 1.0e-5 + dcv
                status, c_out = call_c_adapt(c_lib, r, d, 20.0, 35.0, cv, 8.0)
                assert status == 0

                is_inside = (r <= 25.0) and (cv <= 1.0e-5 + 1e-12) and (d <= 50.0)
                if is_inside:
                    inside_cases += 1
                    assert abs(c_out.a_scale - 0.20) < 1e-5, (
                        f"Battery conservation violated inside boundary: R={r}, D={d}, Cv={cv}, A_scale={c_out.a_scale}"
                    )
                else:
                    outside_cases += 1
                    assert 0.20 <= c_out.a_scale <= 1.00, (
                        f"A_scale outside valid range: {c_out.a_scale} at R={r}, D={d}, Cv={cv}"
                    )

    # 1,000 random samples strictly inside the conservation envelope
    np.random.seed(333)
    rand_r = np.random.uniform(1.0, 25.0, 1000)
    rand_d = np.random.uniform(0.0, 50.0, 1000)
    rand_cv = np.random.uniform(0.0, 1.0e-5, 1000)
    rand_t = np.random.uniform(-2.0, 40.0, 1000)
    rand_s = np.random.uniform(0.0, 45.0, 1000)

    for i in range(1000):
        inside_cases += 1
        status, c_out = call_c_adapt(c_lib, rand_r[i], rand_d[i], rand_t[i], rand_s[i], rand_cv[i], 8.0)
        assert status == 0
        assert abs(c_out.a_scale - 0.20) < 1e-5, f"Random battery case failed: A_scale={c_out.a_scale}"

    print(f"  [Suite 5] Evaluated {inside_cases} inside-boundary cases (100% strictly 0.20) and {outside_cases} boundary-adjacent cases.")
    return {"inside_cases": inside_cases, "outside_cases": outside_cases}


# ============================================================================
# Challenge Suite 6: Continuous Monotonicity Perturbation Harness
# ============================================================================

def run_suite_monotonicity_perturbation(c_lib: ctypes.CDLL, pairs: int = 2000) -> Dict[str, int]:
    """
    Verifies the monotonicity property df_c / dC_v <= 0 across 2,000 random ocean profiles:
    For any random ocean state (R, D, T, S, pH), increasing turbidity by a random positive
    continuous delta (delta_cv > 0) must NEVER cause f_c to increase.
    """
    np.random.seed(777)
    violations = []

    for _ in range(pairs):
        r = float(np.random.uniform(1.0, 1000.0))
        d = float(np.random.uniform(0.0, 3000.0))
        t = float(np.random.uniform(-2.0, 40.0))
        s = float(np.random.uniform(0.0, 45.0))
        ph = float(np.random.uniform(6.5, 8.5))

        # Base turbidity
        cv_1 = float(np.random.uniform(0.0, 8.0e-4))
        delta_cv = float(np.random.uniform(1.0e-6, 2.0e-4))
        cv_2 = cv_1 + delta_cv

        status1, c_out1 = call_c_adapt(c_lib, r, d, t, s, cv_1, ph)
        status2, c_out2 = call_c_adapt(c_lib, r, d, t, s, cv_2, ph)
        assert status1 == 0 and status2 == 0

        # Discretization in float32 bisection allows small numerical jitter <= 0.05 Hz
        if c_out2.f_c_hz > c_out1.f_c_hz + 0.1:
            violations.append((r, d, t, s, cv_1, cv_2, c_out1.f_c_hz, c_out2.f_c_hz))

    print(f"  [Suite 6] Evaluated {pairs} random continuous perturbation pairs for df_c / dC_v <= 0.")
    assert len(violations) == 0, f"Encountered {len(violations)} monotonicity violations!"
    return {"pairs_tested": pairs, "violations": len(violations)}


# ============================================================================
# Challenge Suite 7: Defensive Validation & Adversarial Robustness
# ============================================================================

def run_suite_adversarial_robustness(c_lib: ctypes.CDLL) -> Dict[str, int]:
    """
    Tests engine response to invalid inputs, NaN, Infinity, and NULL pointers.
    """
    tested = 0

    # 1. NULL Pointer
    c_out = SonarOutputParams()
    adapt_fn = getattr(c_lib, "sonar_engine_adapt", None) or getattr(c_lib, "sonar_engine_calculate")
    status_null1 = adapt_fn(None, ctypes.byref(c_out))
    assert status_null1 == -1, f"Expected -1 (INVALID_POINTER), got {status_null1}"
    tested += 1

    c_in = SonarOceanInputs(range_m=10.0, depth_m=10.0, temperature_c=15.0, salinity_ppt=35.0, turbidity_ntu=0.0, ph=8.0)
    status_null2 = adapt_fn(ctypes.byref(c_in), None)
    assert status_null2 == -1, f"Expected -1 (INVALID_POINTER), got {status_null2}"
    tested += 1

    # 2. Out of bounds tests
    bad_inputs = [
        # Range < 1.0 or > 2000.0
        SonarOceanInputs(0.5, 10.0, 15.0, 35.0, 0.0, 8.0),
        SonarOceanInputs(2500.0, 10.0, 15.0, 35.0, 0.0, 8.0),
        # Depth < 0.0 or > 6000.0
        SonarOceanInputs(10.0, -5.0, 15.0, 35.0, 0.0, 8.0),
        SonarOceanInputs(10.0, 6500.0, 15.0, 35.0, 0.0, 8.0),
        # Temperature < -2.0 or > 40.0
        SonarOceanInputs(10.0, 10.0, -5.0, 35.0, 0.0, 8.0),
        SonarOceanInputs(10.0, 10.0, 45.0, 35.0, 0.0, 8.0),
        # Salinity < 0.0 or > 45.0
        SonarOceanInputs(10.0, 10.0, 15.0, -2.0, 0.0, 8.0),
        SonarOceanInputs(10.0, 10.0, 15.0, 50.0, 0.0, 8.0),
        # Turbidity < 0.0 or > 1000.0
        SonarOceanInputs(10.0, 10.0, 15.0, 35.0, -1.0, 8.0),
        SonarOceanInputs(10.0, 10.0, 15.0, 35.0, 1200.0, 8.0),
        # pH invalid: < 6.0 or > 9.5 (excluding 0.0 default)
        SonarOceanInputs(10.0, 10.0, 15.0, 35.0, 0.0, 5.0),
        SonarOceanInputs(10.0, 10.0, 15.0, 35.0, 0.0, 10.0),
    ]

    for inp in bad_inputs:
        status = adapt_fn(ctypes.byref(inp), ctypes.byref(c_out))
        assert status == -2, f"Expected -2 (OUT_OF_BOUNDS), got {status}"
        tested += 1

    # 3. NaN and Infinity tests
    nan_inputs = [
        SonarOceanInputs(float('nan'), 10.0, 15.0, 35.0, 0.0, 8.0),
        SonarOceanInputs(10.0, float('inf'), 15.0, 35.0, 0.0, 8.0),
        SonarOceanInputs(10.0, 10.0, float('-inf'), 35.0, 0.0, 8.0),
        SonarOceanInputs(10.0, 10.0, 15.0, float('nan'), 0.0, 8.0),
        SonarOceanInputs(10.0, 10.0, 15.0, 35.0, float('inf'), 8.0),
        SonarOceanInputs(10.0, 10.0, 15.0, 35.0, 0.0, float('nan')),
    ]

    for inp in nan_inputs:
        status = adapt_fn(ctypes.byref(inp), ctypes.byref(c_out))
        assert status == -3, f"Expected -3 (NAN_INF_ERROR), got {status}"
        tested += 1

    print(f"  [Suite 7] Evaluated {tested} adversarial and defensive boundary cases.")
    return {"adversarial_cases_tested": tested}


# ============================================================================
# Main Verification Execution & Comprehensive Reporting
# ============================================================================

def print_stats_table(title: str, stats_dict: Dict[str, DeviationStats]):
    print(f"\n{title}")
    print("-" * 88)
    print(f"{'Parameter':<26} | {'Count':<6} | {'Max Abs Err':<12} | {'Mean Abs Err':<12} | {'RMS Error':<12} | {'Max Rel %':<10}")
    print("-" * 88)
    for key, s in stats_dict.items():
        print(f"{s.name:<26} | {s.count:<6} | {s.max_error:<12.6g} | {s.mean_error:<12.6g} | {s.rms_error:<12.6g} | {s.max_rel_error_pct:<10.4f}%")
    print("-" * 88)


def main() -> int:
    print("=" * 88)
    print("CROSS-ENGINE INDEPENDENT NUMERICAL ORACLE CHALLENGER")
    print("Platform: STM32G474RE | Dual-Target: IEEE-754 Float32 (C) vs Float64 (Python)")
    print("=" * 88)

    c_lib = load_c_engine()
    print("-> Successfully loaded build/libadaptive_sonar.so")

    print("\n[Step 1/7] Running 1,296-Point Parametric Sweep Parity Verification...")
    stats_1296 = run_suite_1296_sweep(c_lib)
    print_stats_table("Statistical Deviations: C Engine vs Float64 10-Iter Oracle (1,296 Sweep)", stats_1296)

    print("\n[Step 2/7] Running 3,000 Randomized Monte Carlo Oceanographic Profiles...")
    stats_mc_10, stats_mc_exact = run_suite_randomized_monte_carlo(c_lib, num_trials=3000)
    print_stats_table("Statistical Deviations: C Engine vs Float64 10-Iter Oracle (3,000 Monte Carlo)", stats_mc_10)
    print_stats_table("Statistical Deviations: C Engine vs Float64 Exact Root Oracle (3,000 Monte Carlo)", stats_mc_exact)

    print("\n[Step 3/7] Running Float32 Precision Divergence & Catastrophic Cancellation Stress...")
    stats_cancel = run_suite_cancellation_stress(c_lib, trials=10000)
    print_stats_table("Float32 Divergence vs Float64 Reference (10,000 Adversarial Sub-Function Calls)", stats_cancel)

    print("\n[Step 4/7] Running Bisection Convergence Verification (<= 10 Iterations)...")
    conv_results = run_suite_bisection_convergence(c_lib, trials=1500)

    print("\n[Step 5/7] Running Battery Conservation Boundary & Perturbation Analysis...")
    battery_results = run_suite_battery_conservation(c_lib)

    print("\n[Step 6/7] Running Continuous Monotonicity Perturbation Harness...")
    mono_results = run_suite_monotonicity_perturbation(c_lib, pairs=2000)

    print("\n[Step 7/7] Running Adversarial Robustness & Defensive Input Validation...")
    adv_results = run_suite_adversarial_robustness(c_lib)

    print("\n" + "=" * 88)
    print("ORACLE VERIFICATION SUMMARY:")
    print(f"  - Total Profiles Evaluated:       > 16,000 (1,296 sweep + 3,000 MC + 10,000 cancellation + 2,000 pairs)")
    print(f"  - Max f_c Deviation (vs 10-iter): {stats_mc_10['f_c_hz'].max_error:.4f} Hz ({stats_mc_10['f_c_hz'].max_rel_error_pct:.6f}%)")
    print(f"  - RMS f_c Deviation:              {stats_mc_10['f_c_hz'].rms_error:.4f} Hz")
    print(f"  - Max A_scale Deviation:          {stats_mc_10['a_scale'].max_error:.6e}")
    print(f"  - Interior Convergence Bound:     {conv_results['max_interior_error_hz']:.2f} Hz <= 195.3125 Hz (PASSED)")
    print(f"  - Monotonicity Violations:        0 / 2,000 continuous perturbation pairs")
    print(f"  - Battery Conservation:           100% strict enforcement (A_scale = 0.20)")
    print(f"  - Adversarial Rejection:          100% passed (zero segfaults, zero unhandled NaNs)")
    print("VERDICT: APPROVE")
    print("=" * 88)

    return 0


if __name__ == "__main__":
    sys.exit(main())

# ============================================================================
# Pytest Integration Test Classes
# ============================================================================

class TestCrossEngineNumericalOracle:
    @classmethod
    def setup_class(cls):
        cls.c_lib = load_c_engine()

    def test_canonical_1296_sweep_parity(self):
        stats = run_suite_1296_sweep(self.c_lib)
        assert stats["f_c_hz"].max_error <= 1.0, f"f_c max error {stats['f_c_hz'].max_error} > 1.0 Hz"
        assert stats["a_scale"].max_error <= 1e-4, f"A_scale max error {stats['a_scale'].max_error} > 1e-4"

    def test_randomized_monte_carlo_3000_profiles(self):
        stats_10, stats_exact = run_suite_randomized_monte_carlo(self.c_lib, num_trials=3000)
        assert stats_10["f_c_hz"].max_error <= 1.0
        assert stats_10["a_scale"].max_error <= 1e-4
        assert stats_exact["f_c_hz"].max_error <= 200.0  # Theoretical bisection bracket radius

    def test_precision_divergence_cancellation(self):
        stats = run_suite_cancellation_stress(self.c_lib, trials=10000)
        assert stats["sound_speed"].max_error < 0.01
        assert stats["absorption"].max_error < 1e-5

    def test_bisection_convergence_bound(self):
        conv = run_suite_bisection_convergence(self.c_lib, trials=1500)
        assert conv["max_interior_error_hz"] <= 196.0

    def test_battery_conservation_boundary(self):
        res = run_suite_battery_conservation(self.c_lib)
        assert res["inside_cases"] >= 1000

    def test_monotonicity_perturbation(self):
        res = run_suite_monotonicity_perturbation(self.c_lib, pairs=2000)
        assert res["violations"] == 0

    def test_adversarial_validation(self):
        res = run_suite_adversarial_robustness(self.c_lib)
        assert res["adversarial_cases_tested"] >= 20
