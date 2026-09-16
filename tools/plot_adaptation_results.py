import os
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.colors import Normalize

# Ensure output directory exists (dynamically resolve relative to repo root)
OUTPUT_DIR = os.environ.get('ASSETS_DIR', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'docs', 'assets'))
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Apply styling
plt.style.use('seaborn-v0_8-whitegrid')
import matplotlib as mpl
mpl.rcParams['figure.dpi'] = 300
mpl.rcParams['savefig.dpi'] = 300
mpl.rcParams['font.size'] = 11

def mackenzie_sound_speed(T, S, D):
    return (1448.96 + 4.591*T - 5.304e-2 * T**2 + 2.374e-4 * T**3 +
            1.340*(S - 35) + 1.630e-2 * D + 1.675e-7 * D**2 -
            1.025e-2 * T * (S - 35) - 7.139e-13 * T * D**3)

def ainslie_mccolm_absorption(f, T, S, D, pH=8.0):
    f1 = 0.78 * np.sqrt(S/35) * np.exp(T/26)
    f2 = 42 * np.exp(T/17)
    a1 = 0.106 * np.exp((pH - 8)/0.56)
    a2 = 0.52 * (1 + T/43) * (S/35) * np.exp(-D/6000)
    a3 = 0.00049 * np.exp(-(T/27 + D/17000))
    alpha_sw = (a1 * f1 * f**2 / (f1**2 + f**2) +
                a2 * f2 * f**2 / (f2**2 + f**2) +
                a3 * f**2) * 1e-3
    return alpha_sw

def rayleigh_turbidity_scattering(f, Cv):
    return Cv * 50.0 * (f / 100.0)**2

# Canonical active sonar equation parameters matching firmware and testbench
NOMINAL_SL_DB = 190.0          # Source Level (dB re 1 uPa @ 1m)
TARGET_STRENGTH_DB = -15.0     # Target Strength (dB)
TARGET_SNR_DB = 15.0           # Minimum detectable echo SNR threshold (dB)

def total_attenuation(f, T, S, D, Cv, pH=8.0):
    return ainslie_mccolm_absorption(f, T, S, D, pH) + rayleigh_turbidity_scattering(f, Cv)

def sonar_snr(f, R, T, S, D, Cv, pH=8.0):
    alpha_tot = total_attenuation(f, T, S, D, Cv, pH)
    TL = 40 * np.log10(R) + 2 * alpha_tot * R
    DI = 15.0 + 20 * np.log10(f / 100.0)
    N0 = -15.0 + 20 * np.log10(f)  # Canonical thermal noise density N0(f)
    B = 0.22 * f * 1000.0          # Fractional bandwidth (Hz)
    NL = N0 + 10 * np.log10(B)
    SNR = NOMINAL_SL_DB + TARGET_STRENGTH_DB + DI - TL - NL
    return SNR

def bisection_step(f_low, f_high, R, T, S, D, Cv, pH=8.0):
    f_mid = (f_low + f_high) / 2.0
    snr = sonar_snr(f_mid, R, T, S, D, Cv, pH)
    if snr >= TARGET_SNR_DB:
        return f_mid, f_high, f_mid
    else:
        return f_low, f_mid, f_mid

def run_adaptation_engine(R, T, S, D, Cv, pH=8.0, get_history=False):
    f_low, f_high = 100.0, 500.0
    history = []
    bounds = []
    
    for _ in range(10):
        if get_history:
            bounds.append((f_low, f_high))
            history.append((f_low + f_high) / 2.0)
        f_low, f_high, _ = bisection_step(f_low, f_high, R, T, S, D, Cv, pH)
        
    f_c = (f_low + f_high) / 2.0
    if get_history:
        bounds.append((f_low, f_high))
        history.append(f_c)
        
    f_c_clamped = np.clip(f_c, 112.35955, 450.45045)
    
    if get_history:
        return f_c_clamped, history, bounds
    return f_c_clamped

def get_pulse_duration(R):
    if R <= 25.0:
        return 1.0
    elif R <= 300.0:
        return 1.0 + (R - 25.0) / (300.0 - 25.0) * 9.0
    else:
        return 10.0

def get_amplitude_scaling(R, f_c, T, S, D, Cv, pH=8.0):
    if R <= 25.0 and Cv <= 1e-5 and D <= 50.0:
        return 0.20
    
    snr = sonar_snr(f_c, R, T, S, D, Cv, pH)
    delta_snr = snr - TARGET_SNR_DB
    A_scale = 10**(-delta_snr / 20.0)
    return np.clip(A_scale, 0.20, 1.00)

def generate_plots():
    print("Starting generation of plots...")
    import time
    start_time = time.time()
    
    # Plot 1: Absorption vs Turbidity
    f_arr = np.logspace(np.log10(50), np.log10(600), 200)
    Cvs = [0, 1e-5, 1e-4, 1e-3]
    labels = ['Cv = 0', 'Cv = 1e-5', 'Cv = 1e-4', 'Cv = 1e-3']
    
    plt.figure(figsize=(8, 6))
    for cv, label in zip(Cvs, labels):
        alpha = total_attenuation(f_arr, 15, 35, 50, cv) * 1000 # dB/km
        plt.loglog(f_arr, alpha, label=label, lw=2)
        
    plt.annotate('MgSO4 Relaxation Knee', xy=(100, 30), xytext=(60, 100),
                 arrowprops=dict(facecolor='black', shrink=0.05, width=1.5, headwidth=6))
    plt.annotate('Turbidity Divergence', xy=(400, 2000), xytext=(150, 4000),
                 arrowprops=dict(facecolor='black', shrink=0.05, width=1.5, headwidth=6))
    plt.xlabel('Acoustic Frequency (kHz)')
    plt.ylabel('Total Attenuation (dB/km)')
    plt.title('Total Acoustic Attenuation vs Frequency')
    plt.legend()
    plt.grid(True, which="both", ls="-", alpha=0.5)
    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, '01_absorption_vs_turbidity.png'))
    plt.close()

    # Plot 2: Adaptation Frequency Envelope
    R_arr = np.linspace(1, 350, 200)
    f_c_arr = np.array([run_adaptation_engine(r, 15, 35, 50, 1e-4) for r in R_arr])
    B_arr = 0.22 * f_c_arr
    f_start = np.maximum(100.0, f_c_arr - 0.5 * B_arr)
    f_end = np.minimum(500.0, f_c_arr + 0.5 * B_arr)
    
    plt.figure(figsize=(8, 6))
    plt.plot(R_arr, f_c_arr, 'k-', lw=2, label='Carrier Frequency ($f_c$)')
    plt.fill_between(R_arr, f_start, f_end, color='blue', alpha=0.2, label='Bandwidth Envelope')
    plt.axhline(112.35955, color='r', linestyle='--', label='Lower Guardband (112.4 kHz)')
    plt.axhline(450.45045, color='r', linestyle='--', label='Upper Guardband (450.5 kHz)')
    plt.xlabel('Target Range (m)')
    plt.ylabel('Frequency (kHz)')
    plt.title('Adaptation Frequency Envelope')
    plt.legend()
    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, '02_adaptation_frequency_envelope.png'))
    plt.close()

    # Plot 3: Pulse and Power Scaling
    R_arr3 = np.linspace(1, 350, 400)
    Tp_arr = np.array([get_pulse_duration(r) for r in R_arr3])
    f_c_arr_3 = np.array([run_adaptation_engine(r, 15, 35, 50, 1e-5) for r in R_arr3])
    A_scale_arr = []
    for r, fc in zip(R_arr3, f_c_arr_3):
        A_scale_arr.append(get_amplitude_scaling(r, fc, 15, 35, 50, 1e-5))
    A_scale_arr = np.array(A_scale_arr)
    power_red = 20 * np.log10(A_scale_arr)
    
    fig, (ax1, ax2) = plt.subplots(2, 1, sharex=True, figsize=(8, 8))
    ax1.plot(R_arr3, Tp_arr, 'b-', lw=2)
    ax1.axvline(25, color='r', linestyle='--', label='R = 25m Boundary')
    ax1.set_ylabel('Pulse Duration (ms)')
    ax1.set_title('Pulse Duration and Amplitude Scaling Adaptation')
    ax1.legend()
    
    ax2.plot(R_arr3, A_scale_arr, 'g-', lw=2, label='Amplitude Scaling')
    ax2.set_ylabel('Linear Scale ($A_{scale}$)', color='g')
    ax2.tick_params(axis='y', labelcolor='g')
    
    ax2b = ax2.twinx()
    ax2b.plot(R_arr3, power_red, 'm--', lw=2, label='Power Reduction (dB)')
    ax2b.axhline(-13.98, color='orange', linestyle=':', lw=2, label='~ -14 dB Conservation Zone')
    ax2b.set_ylabel('Power Reduction (dB)', color='m')
    ax2b.tick_params(axis='y', labelcolor='m')
    
    lines, labels = ax2.get_legend_handles_labels()
    lines2, labels2 = ax2b.get_legend_handles_labels()
    ax2.legend(lines + lines2, labels + labels2, loc='lower right')
    
    ax2.set_xlabel('Target Range (m)')
    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, '03_pulse_and_power_scaling.png'))
    plt.close()

    # Plot 4: Bisection Convergence
    # Canonical Scenarios matching test_sonar_adaptation.py:
    # 1. Deep Turbid Cold: R=300m, D=500m, T=2C, S=35ppt, Cv=1e-3
    # 2. Littoral Mid-Depth: R=100m, D=50m, T=15C, S=32ppt, Cv=1e-4
    # 3. Shallow Clear Warm: R=25m, D=10m, T=25C, S=35ppt, Cv=0
    fc1, hist1, bnd1 = run_adaptation_engine(300, 2, 35, 500, 1e-3, get_history=True)
    fc2, hist2, bnd2 = run_adaptation_engine(100, 15, 32, 50, 1e-4, get_history=True)
    fc3, hist3, bnd3 = run_adaptation_engine(25, 25, 35, 10, 0, get_history=True)
    
    iters = np.arange(11)
    
    plt.figure(figsize=(10, 6))
    
    plt.plot(iters, hist1, 'o-', color='b', label='Deep Turbid Cold ($C_v=10^{-3}$)')
    plt.fill_between(iters, [b[0] for b in bnd1], [b[1] for b in bnd1], color='b', alpha=0.1)
    
    plt.plot(iters, hist2, 's-', color='g', label='Littoral Mid-Depth ($C_v=10^{-4}$)')
    plt.fill_between(iters, [b[0] for b in bnd2], [b[1] for b in bnd2], color='g', alpha=0.1)
    
    plt.plot(iters, hist3, 'd-', color='r', label='Shallow Clear Warm ($C_v=0$)')
    plt.fill_between(iters, [b[0] for b in bnd3], [b[1] for b in bnd3], color='r', alpha=0.1)
    
    plt.xticks(iters)
    plt.xlabel('Bisection Iteration Index')
    plt.ylabel('Candidate Midpoint Frequency $f_{mid}$ (kHz)')
    plt.title('Bisection Engine Convergence Trajectory')
    plt.legend()
    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, '04_bisection_convergence.png'))
    plt.close()

    # Plot 5: SNR vs Frequency Tradeoff
    f_sweep = np.linspace(100, 500, 200)
    Rs = [25, 50, 100, 200, 300]
    
    plt.figure(figsize=(8, 6))
    for r in Rs:
        snr_vals = sonar_snr(f_sweep, r, 15, 35, 50, 1e-4)
        plt.plot(f_sweep, snr_vals, lw=2, label=f'R = {r} m')
        # find peak
        max_idx = np.argmax(snr_vals)
        plt.plot(f_sweep[max_idx], snr_vals[max_idx], 'k*')
        
    plt.axhline(TARGET_SNR_DB, color='r', linestyle='--', label=f'SNR Target = {TARGET_SNR_DB:.0f} dB')
    plt.xlabel('Frequency (kHz)')
    plt.ylabel('Received Echo SNR (dB)')
    plt.title('SNR vs Frequency Trade-off')
    plt.legend()
    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, '05_snr_vs_frequency_tradeoff.png'))
    plt.close()

    # Plot 6: Resolution vs Range Tradeoff
    f_c_sweep = np.linspace(100, 500, 200)
    c = mackenzie_sound_speed(15, 35, 50)
    res_mm = c / (2 * 0.22 * f_c_sweep)
    
    # Find max range for each fc
    max_ranges = []
    for fc in f_c_sweep:
        r_low, r_high = 1.0, 5000.0
        for _ in range(40):
            r_mid = (r_low + r_high) / 2.0
            snr = sonar_snr(fc, r_mid, 15, 35, 50, 1e-4)
            if snr >= TARGET_SNR_DB:
                r_low = r_mid
            else:
                r_high = r_mid
        max_ranges.append((r_low + r_high) / 2.0)
        
    fig, ax1 = plt.subplots(figsize=(8, 6))
    ax1.plot(f_c_sweep, res_mm, 'b-', lw=2, label='Range Resolution (mm)')
    ax1.set_xlabel('Carrier Frequency $f_c$ (kHz)')
    ax1.set_ylabel('Theoretical Range Resolution (mm)', color='b')
    ax1.tick_params(axis='y', labelcolor='b')
    
    ax2 = ax1.twinx()
    ax2.plot(f_c_sweep, max_ranges, 'r-', lw=2, label=f'Max Range (SNR >= {TARGET_SNR_DB:.0f} dB)')
    ax2.set_ylabel('Maximum Penetration Range (m)', color='r')
    ax2.tick_params(axis='y', labelcolor='r')
    
    plt.title('Range Resolution vs Penetration Range Trade-off')
    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, '06_resolution_vs_range_tradeoff.png'))
    plt.close()

    # Plot 7: Pulse Duration vs Blindzone
    Tp_sweep = np.linspace(1.0, 10.0, 100)
    # Gain = 10 log10(B * Tp) with B = 0.22 * 200 = 44 kHz, Tp in seconds. 
    # Wait, B is in kHz, Tp is in ms. So B*Tp is (44*10^3) * (Tp*10^-3) = 44 * Tp
    proc_gain = 10 * np.log10(44.0 * Tp_sweep)
    blind_zone = c * (Tp_sweep * 1e-3) / 2.0
    
    fig, ax1 = plt.subplots(figsize=(8, 6))
    ax1.plot(Tp_sweep, proc_gain, 'b-', lw=2)
    ax1.set_xlabel('Pulse Duration $T_p$ (ms)')
    ax1.set_ylabel('Matched Filter Processing Gain (dB)', color='b')
    ax1.tick_params(axis='y', labelcolor='b')
    
    ax2 = ax1.twinx()
    ax2.plot(Tp_sweep, blind_zone, 'r-', lw=2)
    ax2.set_ylabel('Receiver Blind Zone Distance (m)', color='r')
    ax2.tick_params(axis='y', labelcolor='r')
    
    plt.title('Pulse Duration vs Receiver Blind Zone')
    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, '07_pulse_duration_vs_blindzone.png'))
    plt.close()

    # Plot 8: Operational Envelope Heatmap
    R_grid = np.linspace(10, 300, 100)
    Cv_ppm_grid = np.linspace(0, 1000, 100)
    R_mesh, Cv_mesh = np.meshgrid(R_grid, Cv_ppm_grid)
    fc_mesh = np.zeros_like(R_mesh)
    
    for i in range(100):
        for j in range(100):
            cv = Cv_mesh[i, j] * 1e-6
            fc_mesh[i, j] = run_adaptation_engine(R_mesh[i, j], 15, 35, 50, cv)
            
    plt.figure(figsize=(9, 7))
    cf = plt.contourf(R_mesh, Cv_mesh, fc_mesh, levels=50, cmap='viridis')
    cbar = plt.colorbar(cf)
    cbar.set_label('Optimal Carrier Frequency $f_c$ (kHz)')
    
    cs = plt.contour(R_mesh, Cv_mesh, fc_mesh, levels=[150, 250, 350, 450], colors='white', linewidths=1.5)
    plt.clabel(cs, inline=True, fontsize=10, fmt='%d kHz')
    
    plt.xlabel('Target Range (m)')
    plt.ylabel('Turbidity $C_v$ (ppm / NTU)')
    plt.title('Operational Envelope Optimization Heatmap')
    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, '08_operational_envelope_heatmap.png'))
    plt.close()

    end_time = time.time()
    
    print("\n--- Summary Verification Table ---")
    print(f"Execution Time: {end_time - start_time:.2f} seconds")
    print(f"{'Benchmark':<20} | {'Expected fc':<15} | {'Actual fc':<15}")
    print("-" * 55)
    print(f"{'Deep Turbid Cold':<20} | {'~ 112.4 kHz':<15} | {fc1:>10.2f} kHz")
    print(f"{'Littoral Mid-Depth':<20} | {'~ 266.5 kHz':<15} | {fc2:>10.2f} kHz")
    print(f"{'Shallow Clear Warm':<20} | {'~ 450.5 kHz':<15} | {fc3:>10.2f} kHz")
    print("----------------------------------\n")
    print("Plots saved successfully to docs/assets/")

if __name__ == '__main__':
    generate_plots()
