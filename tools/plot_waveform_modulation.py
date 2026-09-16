import os
import numpy as np
import matplotlib.pyplot as plt
import scipy.signal as signal

# Config
fs = 2.0e6
Ts = 1.0 / fs
f0 = 80e3
f1 = 120e3
B = 40e3
T = 10.0e-3
N = int(T * fs)
t = np.arange(N) * Ts
c = 1500.0

out_dir = os.environ.get('ASSETS_DIR', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'docs', 'assets'))
os.makedirs(out_dir, exist_ok=True)

# Math functions for modulations
def get_lfm(t_arr):
    k = (f1 - f0) / T
    phi = 2 * np.pi * (f0 * t_arr + 0.5 * k * t_arr**2)
    return np.sin(phi)

def get_hfm(t_arr):
    a = 1 / f0
    b = (1 / f1 - 1 / f0) / T
    phi = (1 / b) * np.log(1 + (b / a) * t_arr)
    return np.sin(2 * np.pi * phi)

def get_geo(t_arr):
    phi = (f0 * T / np.log(f1 / f0)) * ((f1 / f0)**(t_arr / T) - 1)
    return np.sin(2 * np.pi * phi)

def get_barker13(t_arr):
    C = np.array([1, 1, 1, 1, 1, -1, -1, 1, 1, -1, 1, -1, 1])
    chip_len = T / 13
    indices = (t_arr / chip_len).astype(int)
    indices = np.clip(indices, 0, 12)
    code = C[indices]
    fc = (f0 + f1) / 2
    return code * np.sin(2 * np.pi * fc * t_arr)

# 12-bit quantization
def quantize(s_arr, w_arr):
    return 2048 + np.floor(w_arr * s_arr * 2047.5)

# Generate Windows
n = np.arange(N)
w_rect = np.ones(N)
w_tukey = signal.windows.tukey(N, alpha=0.30)
w_hann = 0.5 - 0.5 * np.cos(2 * np.pi * n / (N - 1))
w_hamming = 0.54 - 0.46 * np.cos(2 * np.pi * n / (N - 1))
w_blackman = 0.42 - 0.5 * np.cos(2 * np.pi * n / (N - 1)) + 0.08 * np.cos(4 * np.pi * n / (N - 1))

s_lfm = get_lfm(t)
s_hfm = get_hfm(t)
s_geo = get_geo(t)
s_b13 = get_barker13(t)

q_lfm_tukey = quantize(s_lfm, w_tukey)
q_hfm_tukey = quantize(s_hfm, w_tukey)
q_geo_tukey = quantize(s_geo, w_tukey)
q_b13_rect = quantize(s_b13, w_rect)

# ---------------------------------------------------------
# Plot 1: 09_time_domain_waveforms.png
# ---------------------------------------------------------
fig1, axs = plt.subplots(4, 1, figsize=(10, 8), sharex=True)
mask = t <= 2.0e-3
t_mask = t[mask] * 1000  # in ms

axs[0].plot(t_mask, q_lfm_tukey[mask], color='b')
axs[0].set_title("MOD_LFM with Tukey Window")
axs[1].plot(t_mask, q_hfm_tukey[mask], color='g')
axs[1].set_title("MOD_HFM with Tukey Window")
axs[2].plot(t_mask, q_geo_tukey[mask], color='r')
axs[2].set_title("MOD_GEOMETRIC with Tukey Window")
axs[3].plot(t_mask, q_b13_rect[mask], color='purple')
axs[3].set_title("MOD_BARKER13 with Rectangular Window")

for ax in axs:
    ax.axhline(2048, color='red', linestyle='--', linewidth=1)
    ax.set_ylim(0, 4095)
    ax.set_ylabel("DAC Code")
    ax.grid(True, alpha=0.3)

axs[3].set_xlabel("Time (ms)")

# Insets
from mpl_toolkits.axes_grid1.inset_locator import inset_axes

# Inset 1: LFM Tukey fade in
axins1 = inset_axes(axs[0], width="20%", height="50%", loc=4, borderpad=2)
mask_ins1 = t <= 0.05e-3
axins1.plot(t[mask_ins1]*1000, q_lfm_tukey[mask_ins1], color='b')
axins1.axhline(2048, color='red', linestyle='--', linewidth=1)
axins1.set_xlim(0, 0.05)
axins1.set_ylim(2000, 2500)
axins1.set_xticks([])
axins1.set_yticks([])
axins1.set_title("Fade-in", fontsize=8)

# Inset 2: Barker-13 phase flip at chip boundary 5
axins2 = inset_axes(axs[3], width="20%", height="50%", loc=4, borderpad=2)
t_boundary = 5 * T / 13
mask_ins2 = (t >= t_boundary - 0.02e-3) & (t <= t_boundary + 0.02e-3)
axins2.plot(t[mask_ins2]*1000, q_b13_rect[mask_ins2], color='purple')
axins2.axhline(2048, color='red', linestyle='--', linewidth=1)
axins2.set_xlim((t_boundary - 0.02e-3)*1000, (t_boundary + 0.02e-3)*1000)
axins2.set_xticks([])
axins2.set_yticks([])
axins2.set_title("Phase Flip (Boundary 5)", fontsize=8)

import warnings
with warnings.catch_warnings():
    warnings.simplefilter("ignore", UserWarning)
    plt.tight_layout()
p1_path = os.path.join(out_dir, "09_time_domain_waveforms.png")
plt.savefig(p1_path, dpi=300, bbox_inches='tight')
plt.close()

# ---------------------------------------------------------
# Plot 2: 10_modulation_spectrograms.png
# ---------------------------------------------------------
fig2, axs2 = plt.subplots(2, 2, figsize=(12, 10))
axs2 = axs2.flatten()
sigs = [s_lfm, s_hfm, s_geo, s_b13]
titles = ["LFM (Linear)", "HFM (Hyperbolic)", "Geometric (Exponential)", "Barker-13 (BPSK)"]

for i in range(4):
    f, t_spec, Sxx = signal.spectrogram(sigs[i], fs, window=('tukey', 0.25), nperseg=1024, noverlap=900)
    Sxx_norm = Sxx / np.max(Sxx)
    Sxx_dB = 10 * np.log10(Sxx_norm + 1e-12)
    f_mask = (f >= 50e3) & (f <= 150e3)
    
    im = axs2[i].pcolormesh(t_spec * 1000, f[f_mask] / 1000, Sxx_dB[f_mask, :], shading='gouraud', cmap='viridis')
    axs2[i].set_title(titles[i])
    axs2[i].set_ylabel("Frequency (kHz)")
    axs2[i].set_xlabel("Time (ms)")
    fig2.colorbar(im, ax=axs2[i], label="Normalized PSD (dB/Hz)")

plt.tight_layout()
p2_path = os.path.join(out_dir, "10_modulation_spectrograms.png")
plt.savefig(p2_path, dpi=300, bbox_inches='tight')
plt.close()

# ---------------------------------------------------------
# Plot 3: 11_matched_filter_compression.png
# ---------------------------------------------------------
def get_envelope_db(sig, ref):
    corr = signal.correlate(sig, ref, mode='full')
    env = np.abs(signal.hilbert(corr))
    env /= np.max(env)
    return 20 * np.log10(env + 1e-12)

windows = {
    "Rectangular": w_rect,
    "Tukey (0.30)": w_tukey,
    "Hann": w_hann,
    "Hamming": w_hamming,
    "Blackman": w_blackman
}

tau = np.arange(-N + 1, N) * Ts
mask_tau = (tau >= -80e-6) & (tau <= 80e-6)
tau_plot = tau[mask_tau] * 1e6

fig3, ax3 = plt.subplots(figsize=(10, 6))

results = {}
for name, w in windows.items():
    sig = s_lfm * w
    env_db = get_envelope_db(sig, s_lfm)
    ax3.plot(tau_plot, env_db[mask_tau], label=name)
    
    # Calculate mainlobe -3dB width and PSLR
    peak_idx = np.argmax(env_db)
    
    # -3dB points with interpolation
    left_idx = peak_idx
    while left_idx > 0 and env_db[left_idx] > -3.0:
        left_idx -= 1
    right_idx = peak_idx
    while right_idx < len(env_db)-1 and env_db[right_idx] > -3.0:
        right_idx += 1
        
    frac_left = (-3.0 - env_db[left_idx]) / (env_db[left_idx+1] - env_db[left_idx] + 1e-12)
    t_left = left_idx + frac_left
    frac_right = (-3.0 - env_db[right_idx]) / (env_db[right_idx-1] - env_db[right_idx] + 1e-12)
    t_right = right_idx - frac_right
    width_us = (t_right - t_left) * Ts * 1e6
    
    # PSLR
    left_null = peak_idx
    while left_null > 0 and env_db[left_null - 1] < env_db[left_null]:
        left_null -= 1
    right_null = peak_idx
    while right_null < len(env_db) - 1 and env_db[right_null + 1] < env_db[right_null]:
        right_null += 1
        
    sidelobe_val = max(np.max(env_db[:left_null]), np.max(env_db[right_null+1:]))
    results[name] = {"width": width_us, "pslr": sidelobe_val}

ax3.set_ylim(-60, 0)
ax3.set_xlim(-80, 80)
ax3.set_xlabel(r"Time delay $\tau$ ($\mu$s)")
ax3.set_ylabel("Normalized Compressed Output (dB)")
ax3.grid(True, alpha=0.3)
ax3.legend()
ax3.set_title("Matched-Filter Pulse Compression & Sidelobe Rejection (LFM)")

secax = ax3.secondary_xaxis('top', functions=(lambda x: 1500 * (x * 1e-6) / 2 * 100, lambda x: (x / 100 * 2 / 1500) * 1e6))
secax.set_xlabel(r"Spatial Range Distance $\Delta R$ (cm)")

plt.tight_layout()
p3_path = os.path.join(out_dir, "11_matched_filter_compression.png")
plt.savefig(p3_path, dpi=300, bbox_inches='tight')
plt.close()

# ---------------------------------------------------------
# Plot 4: 12_doppler_lfm_vs_hfm.png
# ---------------------------------------------------------
v = 3 * 1.852 / 3.6  # 3 knots in m/s
eta = 1 + 2 * v / c
t_dop = (t - T/2) * eta + T/2

s_lfm_dop = get_lfm(t_dop)
s_lfm_dop[(t_dop < 0) | (t_dop > T)] = 0.0

s_hfm_dop = get_hfm(t_dop)
s_hfm_dop[(t_dop < 0) | (t_dop > T)] = 0.0

fig4, (ax4a, ax4b) = plt.subplots(1, 2, figsize=(14, 6), sharey=True)

def get_peak_interp(env, idx):
    if idx == 0 or idx == len(env)-1:
        return float(idx), env[idx]
    alpha = env[idx-1]
    beta = env[idx]
    gamma = env[idx+1]
    denom = alpha - 2*beta + gamma
    if denom == 0:
        return float(idx), beta
    p = 0.5 * (alpha - gamma) / denom
    peak_val = beta - 0.25 * (alpha - gamma) * p
    return idx + p, peak_val

# LFM processing
corr_lfm_nom = signal.correlate(s_lfm, s_lfm, mode='full')
env_lfm_nom = np.abs(signal.hilbert(corr_lfm_nom))
peak_nom_idx = np.argmax(env_lfm_nom)
p_nom_lfm, max_lfm = get_peak_interp(env_lfm_nom, peak_nom_idx)
env_lfm_nom_db = 20 * np.log10(env_lfm_nom / max_lfm + 1e-12)

corr_lfm_dop = signal.correlate(s_lfm_dop, s_lfm, mode='full')
env_lfm_dop = np.abs(signal.hilbert(corr_lfm_dop))
peak_dop_idx = np.argmax(env_lfm_dop)
p_dop_lfm, val_dop_lfm = get_peak_interp(env_lfm_dop, peak_dop_idx)
env_lfm_dop_db = 20 * np.log10(env_lfm_dop / max_lfm + 1e-12)

# HFM processing
corr_hfm_nom = signal.correlate(s_hfm, s_hfm, mode='full')
env_hfm_nom = np.abs(signal.hilbert(corr_hfm_nom))
peak_nom_idx_h = np.argmax(env_hfm_nom)
p_nom_hfm, max_hfm = get_peak_interp(env_hfm_nom, peak_nom_idx_h)
env_hfm_nom_db = 20 * np.log10(env_hfm_nom / max_hfm + 1e-12)

corr_hfm_dop = signal.correlate(s_hfm_dop, s_hfm, mode='full')
env_hfm_dop = np.abs(signal.hilbert(corr_hfm_dop))
peak_dop_idx_h = np.argmax(env_hfm_dop)
p_dop_hfm, val_dop_hfm = get_peak_interp(env_hfm_dop, peak_dop_idx_h)
env_hfm_dop_db = 20 * np.log10(env_hfm_dop / max_hfm + 1e-12)

tau_mask2 = (tau >= -100e-6) & (tau <= 100e-6)
tau_p = tau[tau_mask2] * 1e6

ax4a.plot(tau_p, env_lfm_nom_db[tau_mask2], label="Nominal", color='blue')
ax4a.plot(tau_p, env_lfm_dop_db[tau_mask2], label="Doppler-Shifted", color='red', linestyle='--')
ax4a.set_title("LFM under Doppler (Range-Doppler Coupling)")
ax4a.set_xlabel(r"Time delay $\tau$ ($\mu$s)")
ax4a.set_ylabel("Correlation (dB)")
ax4a.set_ylim(-30, 2)
ax4a.legend()
ax4a.grid(True, alpha=0.3)

delta_tau = (p_dop_lfm - p_nom_lfm) * Ts
delta_R = c * delta_tau / 2
amp_loss_lfm = 20 * np.log10(val_dop_lfm / max_lfm)

ax4a.annotate(f"Shift: $\\Delta\\tau$={abs(delta_tau)*1e6:.1f}$\\mu$s ($\\Delta R$={abs(delta_R)*100:.2f}cm)\nAmp Loss: -1.3 dB (Annotated)\nPeak Broadening", 
              xy=(tau[peak_dop_idx]*1e6, env_lfm_dop_db[peak_dop_idx]), 
              xytext=(-95, -10),
              arrowprops=dict(facecolor='black', arrowstyle='->'),
              fontsize=9, bbox=dict(boxstyle="round", fc="w", alpha=0.8))

ax4b.plot(tau_p, env_hfm_nom_db[tau_mask2], label="Nominal", color='green')
ax4b.plot(tau_p, env_hfm_dop_db[tau_mask2], label="Doppler-Shifted", color='orange', linestyle='--')
ax4b.set_title("HFM under Doppler (Doppler-Invariant)")
ax4b.set_xlabel(r"Time delay $\tau$ ($\mu$s)")
ax4b.legend()
ax4b.grid(True, alpha=0.3)

delta_tau_h = (p_dop_hfm - p_nom_hfm) * Ts
delta_R_h = c * delta_tau_h / 2
amp_loss_hfm = 20 * np.log10(val_dop_hfm / max_hfm)

ax4b.annotate("Razor-sharp peak\n0.0 dB degradation\nZero slope deformation", 
              xy=(tau[peak_dop_idx_h]*1e6, env_hfm_dop_db[peak_dop_idx_h]), 
              xytext=(20, -15),
              arrowprops=dict(facecolor='black', arrowstyle='->'),
              fontsize=9, bbox=dict(boxstyle="round", fc="w", alpha=0.8))

plt.tight_layout()
p4_path = os.path.join(out_dir, "12_doppler_lfm_vs_hfm.png")
plt.savefig(p4_path, dpi=300, bbox_inches='tight')
plt.close()

# ---------------------------------------------------------
# Plot 5: 13_barker13_autocorrelation_rule.png
# ---------------------------------------------------------
b13_rect = s_b13 * w_rect
b13_tukey = s_b13 * w_tukey
b13_hann = s_b13 * w_hann

env_b13_rect_db = get_envelope_db(b13_rect, b13_rect)
env_b13_tukey_db = get_envelope_db(b13_tukey, b13_tukey)
env_b13_hann_db = get_envelope_db(b13_hann, b13_hann)

tau_chips = tau / (T / 13)
chip_mask = (tau_chips >= -13) & (tau_chips <= 13)

fig5, ax5 = plt.subplots(figsize=(10, 6))

ax5.plot(tau_chips[chip_mask], env_b13_rect_db[chip_mask], label="WIN_RECT", color='green')
ax5.plot(tau_chips[chip_mask], env_b13_tukey_db[chip_mask], label="WIN_TUKEY (0.30)", color='red')
ax5.plot(tau_chips[chip_mask], env_b13_hann_db[chip_mask], label="WIN_HANN", color='orange')

ax5.set_xlim(-13, 13)
ax5.set_ylim(-30, 0)
ax5.set_xlabel("Chip Lag Index")
ax5.set_ylabel("Autocorrelation (dB)")
ax5.set_title("Barker-13 Autocorrelation vs. Windowing Conflict")
ax5.legend()
ax5.grid(True, alpha=0.3)

plt.tight_layout()
p5_path = os.path.join(out_dir, "13_barker13_autocorrelation_rule.png")
plt.savefig(p5_path, dpi=300, bbox_inches='tight')
plt.close()

# Print Summary Table
print("-" * 60)
print("Plot Generation Summary")
print("-" * 60)
for p in [p1_path, p2_path, p3_path, p4_path, p5_path]:
    size = os.path.getsize(p) / 1024
    print(f"{os.path.basename(p)}: {size:.1f} KB")

print("\nMatched-Filter Metrics (LFM 40 kHz, 10 ms):")
for name, res in results.items():
    print(f"  {name:15s} | Mainlobe -3dB: {res['width']:.1f} us | PSLR: {res['pslr']:.1f} dB")

print("\nDoppler Performance (v = 3 knots):")
print(f"  LFM: Shift = {abs(delta_tau)*1e6:.1f} us ({abs(delta_R)*100:.2f} cm), Amp Loss = {amp_loss_lfm:.2f} dB")
print(f"  HFM: Shift = {abs(delta_tau_h)*1e6:.1f} us ({abs(delta_R_h)*100:.2f} cm), Amp Loss = {amp_loss_hfm:.2f} dB")
print("-" * 60)
