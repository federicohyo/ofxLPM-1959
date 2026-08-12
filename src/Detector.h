#pragma once

// Membrane-potential front end for one die.
//
// Deliberately free of any openFrameworks dependency so the test target can
// compile it on its own against a scripted trace.
//
// What it has to cope with, in order:
//
//  1. The signal arrives AC-coupled through a DC block, so the absolute
//     resting level is gone and the level that is left drifts as the die
//     warms. A 0.5 Hz one-pole highpass tracks whatever baseline remains and
//     removes it. Nothing here assumes zero mean over a short window.
//
//  2. A spike is a ~940 mV excursion and a subthreshold EPSP is ~29 mV, about
//     30 dB apart. Amplitude is normalised by a >= 30 s envelope of the peak
//     excursion, so the detector threshold can be stated in units of "one
//     spike" and stays valid across bias drift. The envelope is slow on
//     purpose: it must never pump.
//
//  3. Detection is on the reset edge, not the peak. The membrane climbs a
//     slow staircase from ~450 mV, crosses at ~1043 mV and then crashes to
//     ~100 mV in well under a millisecond; that crash is the steepest and
//     least ambiguous feature in the trace. A peak follower holds the
//     pre-reset level and the reset is declared as soon as the present sample
//     sits `threshold` below it. Because the test is on the drop and not on
//     the slope, it does not care how many samples the crash takes.
//
//     The follower's 1 ms fall time is what separates a reset from a decaying
//     EPSP. For a membrane relaxing with time constant tau_m the follower
//     settles a fraction tau_f/(tau_m - tau_f) below the signal — about 0.11
//     at the fastest membrane the chip runs (10 ms) — whereas a sub-
//     millisecond crash leaves most of its full excursion below the follower.
//     The default threshold of 0.35 sits between the two, so a subthreshold
//     EPSP train cannot fire the detector however far the level envelope has
//     turned the gain up.
//
//  4. A refractory guard rejects re-triggering while the reset edge rings.
//
//  5. Two defences against a hot background. The level envelope is a peak
//     tracker, so when the chip's own signal arrives well below the input
//     stage's own floor the envelope amplifies that floor to full scale and
//     the follower fires on it. So: an optional lowpass on the detection path
//     only (the membrane has no content above a few kHz; the background does,
//     and cutting 24 kHz to 3 kHz takes about 2.8x of amplitude out of it
//     while leaving a 400 us crash intact), and an absolute floor under the
//     threshold set as a multiple of the running mean |signal| measured
//     between events. A candidate has to clear both the relative threshold
//     and that floor.

#include <atomic>
#include <cmath>
#include <cstdint>

struct SpikeEvent {
    uint64_t frame = 0;      // absolute input frame index of the reset edge
    float excursion = 0.0f;  // measured drop, in units of the level envelope
    float rateHz = 0.0f;     // instantaneous 1 / ISI at this spike
};

class MembraneDetector {
public:
    void setup(float sampleRate) {
        fs = sampleRate;
        aHP = std::exp(-2.0f * 3.14159265358979f * kBaselineHz / fs);
        kFall = 1.0f - std::exp(-1.0f / (kFollowerFallSec * fs));
        blockLen = (uint32_t)(kLevelBlockSec * fs);
        if (blockLen < 1) blockLen = 1;
        kLevel = 1.0f - std::exp(-kLevelBlockSec / kLevelTauSec);
        kBg = 1.0f - std::exp(-1.0f / (kBackgroundTauSec * fs));
        setInputLowpassHz(0.0f);
        setRateSmoothingMs(250.0f);
        setRefractoryMs(2.0f);
        reset();
    }

    void reset() {
        hp = 0.0f;
        xPrev = 0.0f;
        peak = 0.0f;
        v = 0.0f;
        vFilt = 0.0f;
        levelEst = kLevelFloor;
        lastScale = kLevelFloor;
        levelSeeded = false;
        blockPeak = 0.0f;
        blockPos = 0;
        rawPeak = 0.0f;
        rawPeakHold = 0.0f;
        haveSpike = false;
        lastSpike = 0;
        lastISI = 0;
        refracUntil = 0;
        rateSmooth = 0.0f;
        spikeCount = 0;
        lp = 0.0f;
        bgEst = 0.0f;
        bgSeeded = false;
    }

    void setThreshold(float t) { threshold.store(t, std::memory_order_relaxed); }
    void setRefractoryMs(float ms) {
        uint64_t n = (uint64_t)(ms * 1e-3f * fs);
        refractoryFrames.store(n < 1 ? 1 : n, std::memory_order_relaxed);
    }
    void setBackgroundMult(float m) { backgroundMult.store(m, std::memory_order_relaxed); }
    // 0 disables the filter and detection runs on the raw baseline-removed signal
    void setInputLowpassHz(float hz) {
        kLp.store(hz <= 0.0f ? 0.0f
                             : 1.0f - std::exp(-2.0f * 3.14159265358979f * hz / fs),
                  std::memory_order_relaxed);
    }
    // Fall time of the peak follower, in ms. The default of 1 ms suits an
    // audio-rate capture; it is meaningless at a low sample rate (at 400 Hz it
    // is 0.4 of a sample, so the follower collapses onto the signal and the
    // fall it is supposed to measure is always zero). Set it from the sample
    // rate and the shape you are detecting: it wants to be longer than the
    // crash and shorter than the recovery.
    void setFollowerFallMs(float ms) {
        const float tau = ms * 1e-3f;
        kFall = (tau <= 0.0f) ? 1.0f : 1.0f - std::exp(-1.0f / (tau * fs));
    }
    // When false, the level envelope is disabled and scale = 1.0 — detection
    // runs on the raw baseline-removed signal in input units. Use this for a
    // clean audio source where amplitude normalisation would be destructive.
    void setLevelNorm(bool on) { useLevelNorm.store(on, std::memory_order_relaxed); }
    void setRateSmoothingMs(float ms) {
        const float tau = ms * 1e-3f;
        // atomic: the settings hot-reload writes this from the main thread
        // while the audio callback is reading it
        kRate.store(tau <= 0.0f ? 1.0f : 1.0f - std::exp(-1.0f / (tau * fs)),
                    std::memory_order_relaxed);
    }

    // Process one input frame. Returns true (and fills ev) if this frame is
    // the reset edge of a spike.
    bool process(float x, uint64_t frame, SpikeEvent &ev) {
        // --- baseline removal -------------------------------------------
        hp = aHP * (hp + x - xPrev);
        xPrev = x;

        // --- level envelope (peak per block, one-pole over >= 30 s) ------
        // Can be disabled for clean audio sources.
        float scale;
        if (useLevelNorm.load(std::memory_order_relaxed)) {
            const float mag = std::fabs(hp);
            if (mag > blockPeak) blockPeak = mag;
            if (mag > rawPeakHold) rawPeakHold = mag;
            if (++blockPos >= blockLen) {
                if (!levelSeeded) {
                    levelEst = blockPeak > kLevelFloor ? blockPeak : kLevelFloor;
                    levelSeeded = true;
                } else {
                    levelEst += (blockPeak - levelEst) * kLevel;
                }
                rawPeak = rawPeakHold;
                rawPeakHold = 0.0f;
                blockPeak = 0.0f;
                blockPos = 0;
            }
            scale = levelEst > kLevelFloor ? levelEst : kLevelFloor;
        } else {
            scale = 1.0f;
            levelSeeded = true;
            levelEst = 1.0f;
        }
        v = hp / scale;

        // detection path: optionally band-limited, then normalised the same way
        const float a_lp = kLp.load(std::memory_order_relaxed);
        if (a_lp > 0.0f) lp += (hp - lp) * a_lp; else lp = hp;
        const float vd = lp / scale;
        vFilt = vd;

        // The follower lives in normalised units, so when the envelope moves
        // the units under it, its history has to move too. Without this the
        // first envelope update rescales v but not the follower, and the step
        // reads as a reset edge.
        if (scale != lastScale) {
            peak *= lastScale / scale;
            lastScale = scale;
        }

        // --- peak follower ----------------------------------------------
        if (vd > peak) peak = vd;
        else peak += (vd - peak) * kFall;
        const float fall = peak - vd;

        // --- background level of the detection statistic ------------------
        // Measured on `fall` itself, not on the signal magnitude: during a
        // volley the membrane sits far from zero while `fall` stays small, so
        // the magnitude would inflate the very quantity meant to reject the
        // background.
        //
        // Updated unconditionally over a 1 s window. Gating the update on
        // "nothing is happening" sounds safer and is in fact useless: once
        // the statistic is running hot the gate never opens, the estimate
        // stays at its seed and the floor never rises. Real resets are brief
        // enough (well under 1% duty even at 250 Hz) that they barely move a
        // 1 s mean, so no gate is needed to keep them from raising their own
        // rejection level.
        const float relThr = threshold.load(std::memory_order_relaxed);
        if (!bgSeeded) { bgEst = fall; bgSeeded = true; }
        else bgEst += (fall - bgEst) * kBg;

        // --- rate ---------------------------------------------------------
        // Held at 1 / ISI, then allowed to glide down as silence accumulates,
        // so a die that stops firing slides to zero instead of freezing on
        // its last value.
        float rNow = 0.0f;
        if (haveSpike) {
            uint64_t since = frame - lastSpike;
            uint64_t den = since > lastISI ? since : lastISI;
            if (den < 1) den = 1;
            rNow = fs / (float)den;
        }
        rateSmooth += (rNow - rateSmooth) * kRate.load(std::memory_order_relaxed);

        // --- reset-edge test ---------------------------------------------
        if (!levelSeeded) return false;  // still filling the first level block
        if (frame < refracUntil) return false;
        // bgEst is already in normalised units — it is measured on `fall`
        const float absThr = backgroundMult.load(std::memory_order_relaxed) * bgEst;
        if (fall < relThr || fall < absThr) return false;

        ev.frame = frame;
        ev.excursion = fall;
        if (haveSpike) {
            lastISI = frame - lastSpike;
            if (lastISI < 1) lastISI = 1;
            ev.rateHz = fs / (float)lastISI;
        } else {
            lastISI = 0;
            ev.rateHz = 0.0f;
        }
        lastSpike = frame;
        haveSpike = true;
        refracUntil = frame + refractoryFrames.load(std::memory_order_relaxed);
        peak = vd;  // do not let the stale pre-reset level re-trigger
        ++spikeCount;
        return true;
    }

    // --- observers (render thread) ---------------------------------------
    float voltage() const { return v; }        // baseline-removed, normalised
    // Same signal with the detection lowpass applied. The membrane has no
    // content above a few kHz, so what this removes is the input stage, not
    // the neuron — it is a cleaner picture of the membrane, not a smoothed
    // one, and it passes through no value the filtered signal did not take.
    float filtered() const { return vFilt; }
    float baseline() const { return xPrev - hp; }
    float level() const { return levelEst; }   // envelope, input units
    float inputPeak() const { return rawPeak; }// per-block peak, input units
    float rate() const { return rateSmooth; }
    // background level of the detection statistic, normalised units
    float background() const { return bgEst; }
    // the threshold actually in force, in normalised units
    float activeThreshold() const {
        const float a = backgroundMult.load(std::memory_order_relaxed) * bgEst;
        const float r = threshold.load(std::memory_order_relaxed);
        return a > r ? a : r;
    }
    uint64_t spikes() const { return spikeCount; }
    bool live() const { return levelSeeded && levelEst > kLevelFloor * 2.0f; }
    bool levelEnabled() const { return useLevelNorm.load(std::memory_order_relaxed); }

private:
    static constexpr float kBaselineHz = 0.5f;
    static constexpr float kFollowerFallSec = 0.001f;
    static constexpr float kLevelBlockSec = 0.1f;
    static constexpr float kLevelTauSec = 30.0f;
    static constexpr float kLevelFloor = 0.002f;
    static constexpr float kBackgroundTauSec = 1.0f;

    float fs = 48000.0f;
    float aHP = 0.0f, kFall = 0.0f, kLevel = 0.0f, kBg = 0.0f;
    std::atomic<float> kRate{1.0f};
    float lp = 0.0f, bgEst = 0.0f, vFilt = 0.0f;
    bool bgSeeded = false;
    uint32_t blockLen = 4800, blockPos = 0;

    float hp = 0.0f, xPrev = 0.0f, peak = 0.0f, v = 0.0f;
    float levelEst = kLevelFloor, blockPeak = 0.0f, lastScale = kLevelFloor;
    float rawPeak = 0.0f, rawPeakHold = 0.0f;
    bool levelSeeded = false;

    bool haveSpike = false;
    uint64_t lastSpike = 0, lastISI = 0, refracUntil = 0, spikeCount = 0;
    float rateSmooth = 0.0f;

    std::atomic<float> threshold{0.35f};
    std::atomic<uint64_t> refractoryFrames{96};
    std::atomic<float> backgroundMult{4.0f};
    std::atomic<float> kLp{0.0f};
    std::atomic<bool> useLevelNorm{true};
};
