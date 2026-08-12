#pragma once

#include "ofMain.h"

#include <string>

// Every tunable value in the piece. Loaded from bin/data/settings.json and
// re-read whenever that file's mtime changes, so the installation can be
// adjusted in the room without a restart.
struct Settings {
    // --- window -----------------------------------------------------------
    bool fullscreen = true;

    // --- spiral geometry --------------------------------------------------
    // theta(t) = 2*pi * (t mod spiralPeriod) / spiralPeriod
    // r(t)     = radiusBase + radiusGain * warp(V)
    // both radii are fractions of half the short screen edge.
    float spiralPeriod = 2.0f;
    bool spiralSlaveToBurst = false;  // slave the period to the mean burst interval
    float burstGapMs = 150.0f;        // silence that separates one burst from the next
    float radiusBase = 0.34f;
    float radiusGain = 0.30f;
    // Signed power law on the radius: r ~ sign(V)*|V|^radiusGamma. 1.0 is a
    // pure linear radius, which puts a 29 mV EPSP 32x inside a spike and makes
    // it invisible. 0.6 keeps both legible without inventing any value the
    // signal did not pass through — it is a monotone rescaling of the axis.
    // Signed power law on the radius. BELOW 1 lifts the small signal (0.6 puts
    // a 29 mV EPSP within 9x of a spike instead of 32x) — right when the
    // membrane's subthreshold dynamics actually survive the input path. ABOVE
    // 1 does the opposite: it squashes everything small and lets only the big
    // excursions reach out. That is what you want when the input is AC-coupled
    // hard enough that there is no subthreshold dynamics left to show and the
    // small stuff is the input stage rather than the neuron.
    // Above 2 is too much once the level envelope has settled: it normalises
    // so the LARGEST excursion maps to about 1, which puts a typical spike
    // below 1, and x^2.5 drives that to nothing. The symptom is spikes that
    // look large for the first half minute and then shrink to invisible while
    // the base ring saturates. Mild expansion, with the suppression done by
    // the duck instead, is what actually works.
    float radiusGamma = 1.2f;
    // inner floor, fraction of half-edge: the ring a reset notch bottoms out on
    float radiusMin = 0.05f;
    float radiusOffsetB = 0.012f;
    // The same trick the audio uses, applied to the drawn radius: collapse it
    // toward the base ring except around a DETECTED spike. Between spikes the
    // trajectory becomes a thin circle and each reset throws a filament out of
    // it, instead of a solid disc of amplified input stage. 0 disables it and
    // the raw normalised membrane is drawn.
    float drawDuckDepth = 0.75f;
    float drawDuckReleaseMs = 25.0f;
    // The duck also drives the trace's brightness, which is what stops the
    // base ring from saturating. The angle is time wrapped at a fixed period,
    // so every revolution lands on exactly the same angles: a ring drawn at
    // constant radius overprints itself 48000 samples per second and goes
    // white no matter how low traceAlpha is. Dimming it between spikes fixes
    // that at the source, and puts the brightness on the events where the
    // spec wants it.
    // Brightness between spikes, as an ABSOLUTE alpha rather than a fraction.
    // A fraction is the wrong control here: the base ring is drawn at constant
    // radius and the angle wraps at a fixed period, so every revolution lands
    // on the same pixels and each one takes about 50 hits per second. Over a
    // 40 s persistence that is ~2000x accumulation, and the ring saturates to
    // white at almost any fraction of traceAlpha. Setting the quiet level
    // directly is what makes it dialable: ring equilibrium is roughly
    // traceAlphaQuiet * 2000, so 0.0006 lands near 1.0 and anything much above
    // that is white.
    //
    // It scales with decaySeconds, so the two move together: at 40 s the ring
    // sat right at 0.0006, and at 15 s it needs 0.0016 to look the same. Drop
    // decaySeconds for a faster clear and raise this in proportion to hold the
    // ring where it was.
    float traceAlphaQuiet = 0.0016f;  // die B ring offset, fraction of half-edge

    // --- persistence and colour ------------------------------------------
    float decaySeconds = 15.0f;
    float traceAlpha = 0.50f;
    float traceWidth = 1.0f;
    float hueA = 0.52f;  // 0..1
    float hueB = 0.07f;
    float saturation = 0.50f;
    float brightness = 1.0f;
    // Accumulated A/B divergence pushes the two hues apart and does nothing
    // else. It is never printed as a number.
    float divergenceHueShift = 0.05f;

    // --- scope strip ------------------------------------------------------
    // A live waveform monitor along the bottom edge. It shows the raw jack
    // signal with its own manual gain, deliberately NOT the level-normalised
    // one the spiral uses, so it stays an honest picture of what is arriving
    // and can be used to set the interface gain. No text, so show mode stays
    // free of chrome; set scopePanel false for the gallery if you want the
    // spiral alone.
    bool scopePanel = true;
    float scopeHeight = 0.18f;   // fraction of the window height
    float scopeSeconds = 1.0f;   // time span shown, up to 1 s
    float scopeGain = 40.0f;
    bool scopeInvert = true;     // the monitor buffer inverts; flip it back
    float scopeAlpha = 0.85f;

    // Polarity of the monitor buffer, applied once at the jack so the
    // detector, the spiral and the audio all agree. The detector looks for a
    // downward reset crash; if your buffer inverts, set this true.
    bool invertInput = false;

    // --- detector ---------------------------------------------------------
    // Units of one level-envelope excursion.
    //
    // MEASURE THIS IN STEADY STATE. The level envelope has a 30 s time
    // constant and is seeded from the first 100 ms, so for the first half
    // minute it sits low and everything looks larger than it really is. A
    // threshold picked during that window is far too high, and when the
    // envelope settles the detector stops firing altogether — the symptom is
    // spikes that shrink to nothing and audio that fades to silence after
    // about half a minute.
    //
    // Measured on a real capture replayed long enough to settle: 0.35 gives a
    // steady 15.6 Hz with a 54 ms median interval against a chip firing at
    // about 14 Hz. 1.5, picked from the first few seconds of the same signal,
    // gives zero in steady state.
    float spikeThreshold = 0.35f;
    // Band limit on the detection path. The membrane has nothing above a few
    // kHz; the input stage does. Measured on a scripted trace, this alone
    // holds detection at 41/42 down to a 1.5:1 peak SNR, where an unfiltered
    // detector returns 6688 detections for 42 real spikes.
    float detectLowpassHz = 3000.0f;
    // Absolute floor under the threshold, as a multiple of the running mean of
    // the detection statistic. ON by default, on evidence: it is what makes
    // the warm-up survivable. While the level envelope is still settling the
    // raw threshold lets through 262 Hz on a signal doing 15; with this at 4.0
    // that falls to 42 Hz, and the steady-state rate is completely unchanged
    // at 15.6 Hz. It costs nothing where it matters and saves the first half
    // minute. (An earlier sweep on a synthetic trace concluded the opposite,
    // because that trace has no envelope-settling transient to survive.)
    float backgroundMult = 4.0f;
    // Draw the band-limited signal rather than the raw one. What this removes
    // is the input stage, not the neuron.
    bool drawBandLimited = true;
    float refractoryMs = 2.0f;
    float rateSmoothingMs = 250.0f;

    // --- audio ------------------------------------------------------------
    float mixDirect = 0.30f;  // the membrane itself, envelope-scaled
    float mixSpike = 0.60f;   // the reset click, made percussive
    float mixRate = 0.20f;    // firing rate as pitch
    float outputGain = 0.70f;
    // Band limit on the direct path. The membrane has nothing up here; the
    // input stage does, and above 2 kHz is where it is most audible. Same
    // argument as detectLowpassHz — it removes the interface, not the neuron.
    // 0 disables it.
    float audioLowpassHz = 6000.0f;
    // Downward expansion of the direct path, so the gap between spikes drops
    // away instead of sitting there as a wash. The control signal is an
    // envelope of the measured membrane, so nothing here runs free: with the
    // input unplugged the gain goes to zero and the channel is silent.
    //
    // Below the knee the gain falls as (env/knee)^(ratio-1), which is smooth
    // and has no threshold to click on. Note the trade: this deliberately
    // makes the subthreshold texture quieter as well, so it works against
    // "the small signal is the texture". Set ratio 1.0 to switch it off.
    //
    // The knee is a MULTIPLE of the measured background of that envelope, not
    // an absolute level, so it sits in the right place whatever the input gain
    // is. An absolute knee was tried first and did nothing: at a low input
    // level the background sat far above it and it never engaged.
    float audioExpanderKnee = 2.0f;    // multiples of the measured background
    float audioExpanderRatio = 3.0f;   // 1.0 = off
    float audioExpanderAttackMs = 2.0f;
    float audioExpanderReleaseMs = 80.0f;
    // How far the direct path drops between measured spikes. The expander can
    // only buy about 8 dB here, because at a low input level the background
    // and the membrane occupy the same envelope range and no amplitude rule
    // separates them. This does, because it is not an amplitude rule: the gain
    // is opened by a detected reset and decays, so the gap between spikes goes
    // as quiet as you ask. 0 disables it.
    //
    // Two consequences worth knowing. It removes the subthreshold texture
    // between spikes, which is the point but is also a departure from "the
    // small signal is the texture". And it is only as good as the detector: if
    // the threshold is set so low that it fires continuously, the gate never
    // closes and this does nothing.
    // A resonant bandpass across the direct path, mixed in by
    // audioResonanceMix. What arrives from this rig is a bare ~0.5 ms impulse
    // per spike — the AC coupling differentiated the membrane away, and no
    // inverse filter brings it back, because the staircase and the recovery
    // are not attenuated in the recording, they are absent. Measured: undoing
    // the coupling high-pass at 15, 30 or 60 Hz leaves the spike-triggered
    // average unchanged and moves 5% of the energy below 20 Hz to 51%, which
    // is rumble.
    //
    // So rather than pretend to restore a shape, give the impulse a body it
    // can actually have. A struck resonator rings at audioResonanceHz for
    // roughly Q/(pi*f) seconds — at 220 Hz and Q 12 that is about 17 ms, which
    // turns a click into a pitched note. It is a linear filter of the measured
    // signal, it invents no value the filtered signal did not take, and with
    // the chip unplugged it is silent. Mix 0 switches it off.
    float audioResonanceHz = 220.0f;
    float audioResonanceQ = 8.0f;
    float audioResonanceMix = 0.5f;

    float audioDuckDepth = 0.95f;
    // Release must be well under the inter-spike interval or the gate never
    // closes. At 14 Hz that interval is 71 ms; a 200 ms release was tried
    // first and bought only 5 dB because it stayed open almost continuously.
    // Swept against the real capture: 25 ms gives 15.6 dB of suppression for
    // 0.4 dB off the spike peak, 15 ms gives 19.3 dB for 0.7 dB off.
    float audioDuckReleaseMs = 25.0f;
    float spikeGrainMs = 90.0f;
    float spikeGrainRatio = 4.0f;  // grain pitch = ratio * rate-voice pitch
    float rateVoiceMinHz = 90.0f;
    float rateVoiceMaxHz = 880.0f;
    float rateVoiceFullScaleHz = 250.0f;  // firing rate that maps to maxHz
    float rateEnvSeconds = 1.5f;          // decay from the last measured spike

    // --- audio device -----------------------------------------------------
    int sampleRate = 48000;
    int bufferSize = 256;
    std::string inputDevice;   // substring match; empty = first with >= 1 in
    std::string outputDevice;  // substring match; empty = default

    void load(const std::string &path) {
        ofJson j;
        try {
            j = ofLoadJson(path);
        } catch (...) {
            ofLogError("Settings") << "could not parse " << path;
            return;
        }
        if (j.is_null()) return;

        num(j, "fullscreen", fullscreen);
        num(j, "spiralPeriod", spiralPeriod);
        num(j, "spiralSlaveToBurst", spiralSlaveToBurst);
        num(j, "burstGapMs", burstGapMs);
        num(j, "radiusBase", radiusBase);
        num(j, "radiusGain", radiusGain);
        num(j, "radiusGamma", radiusGamma);
        num(j, "radiusMin", radiusMin);
        num(j, "radiusOffsetB", radiusOffsetB);
        num(j, "drawDuckDepth", drawDuckDepth);
        num(j, "traceAlphaQuiet", traceAlphaQuiet);
        num(j, "drawDuckReleaseMs", drawDuckReleaseMs);
        num(j, "decaySeconds", decaySeconds);
        num(j, "traceAlpha", traceAlpha);
        num(j, "traceWidth", traceWidth);
        num(j, "hueA", hueA);
        num(j, "hueB", hueB);
        num(j, "saturation", saturation);
        num(j, "brightness", brightness);
        num(j, "divergenceHueShift", divergenceHueShift);
        num(j, "invertInput", invertInput);
        num(j, "scopePanel", scopePanel);
        num(j, "scopeHeight", scopeHeight);
        num(j, "scopeSeconds", scopeSeconds);
        num(j, "scopeGain", scopeGain);
        num(j, "scopeInvert", scopeInvert);
        num(j, "scopeAlpha", scopeAlpha);
        num(j, "spikeThreshold", spikeThreshold);
        num(j, "detectLowpassHz", detectLowpassHz);
        num(j, "backgroundMult", backgroundMult);
        num(j, "drawBandLimited", drawBandLimited);
        num(j, "refractoryMs", refractoryMs);
        num(j, "rateSmoothingMs", rateSmoothingMs);
        num(j, "mixDirect", mixDirect);
        num(j, "mixSpike", mixSpike);
        num(j, "mixRate", mixRate);
        num(j, "outputGain", outputGain);
        num(j, "audioLowpassHz", audioLowpassHz);
        num(j, "audioExpanderKnee", audioExpanderKnee);
        num(j, "audioExpanderRatio", audioExpanderRatio);
        num(j, "audioExpanderAttackMs", audioExpanderAttackMs);
        num(j, "audioExpanderReleaseMs", audioExpanderReleaseMs);
        num(j, "audioResonanceHz", audioResonanceHz);
        num(j, "audioResonanceQ", audioResonanceQ);
        num(j, "audioResonanceMix", audioResonanceMix);
        num(j, "audioDuckDepth", audioDuckDepth);
        num(j, "audioDuckReleaseMs", audioDuckReleaseMs);
        num(j, "spikeGrainMs", spikeGrainMs);
        num(j, "spikeGrainRatio", spikeGrainRatio);
        num(j, "rateVoiceMinHz", rateVoiceMinHz);
        num(j, "rateVoiceMaxHz", rateVoiceMaxHz);
        num(j, "rateVoiceFullScaleHz", rateVoiceFullScaleHz);
        num(j, "rateEnvSeconds", rateEnvSeconds);
        num(j, "sampleRate", sampleRate);
        num(j, "bufferSize", bufferSize);
        str(j, "inputDevice", inputDevice);
        str(j, "outputDevice", outputDevice);

        clamp();
    }

    void clamp() {
        spiralPeriod = ofClamp(spiralPeriod, 0.05f, 600.0f);
        burstGapMs = ofClamp(burstGapMs, 5.0f, 5000.0f);
        radiusBase = ofClamp(radiusBase, 0.0f, 1.0f);
        radiusGain = ofClamp(radiusGain, 0.0f, 1.0f);
        radiusGamma = ofClamp(radiusGamma, 0.1f, 8.0f);
        drawDuckDepth = ofClamp(drawDuckDepth, 0.0f, 1.0f);
        traceAlphaQuiet = ofClamp(traceAlphaQuiet, 0.0f, 1.0f);
        drawDuckReleaseMs = ofClamp(drawDuckReleaseMs, 5.0f, 5000.0f);
        radiusMin = ofClamp(radiusMin, 0.0f, 1.0f);
        decaySeconds = ofClamp(decaySeconds, 0.5f, 600.0f);
        traceAlpha = ofClamp(traceAlpha, 0.0f, 1.0f);
        traceWidth = ofClamp(traceWidth, 0.25f, 6.0f);
        saturation = ofClamp(saturation, 0.0f, 1.0f);
        brightness = ofClamp(brightness, 0.0f, 1.0f);
        scopeHeight = ofClamp(scopeHeight, 0.02f, 0.6f);
        scopeSeconds = ofClamp(scopeSeconds, 0.01f, 1.0f);
        scopeGain = ofClamp(scopeGain, 0.1f, 500.0f);
        scopeAlpha = ofClamp(scopeAlpha, 0.0f, 1.0f);
        spikeThreshold = ofClamp(spikeThreshold, 0.02f, 8.0f);
        detectLowpassHz = ofClamp(detectLowpassHz, 0.0f, 20000.0f);
        backgroundMult = ofClamp(backgroundMult, 0.0f, 20.0f);
        refractoryMs = ofClamp(refractoryMs, 0.2f, 200.0f);
        rateSmoothingMs = ofClamp(rateSmoothingMs, 1.0f, 5000.0f);
        mixDirect = ofClamp(mixDirect, 0.0f, 4.0f);
        mixSpike = ofClamp(mixSpike, 0.0f, 4.0f);
        mixRate = ofClamp(mixRate, 0.0f, 4.0f);
        outputGain = ofClamp(outputGain, 0.0f, 4.0f);
        audioLowpassHz = ofClamp(audioLowpassHz, 0.0f, 20000.0f);
        audioExpanderKnee = ofClamp(audioExpanderKnee, 0.1f, 20.0f);
        audioExpanderRatio = ofClamp(audioExpanderRatio, 1.0f, 12.0f);
        audioExpanderAttackMs = ofClamp(audioExpanderAttackMs, 0.05f, 200.0f);
        audioExpanderReleaseMs = ofClamp(audioExpanderReleaseMs, 1.0f, 2000.0f);
        audioResonanceHz = ofClamp(audioResonanceHz, 20.0f, 8000.0f);
        audioResonanceQ = ofClamp(audioResonanceQ, 0.5f, 60.0f);
        audioResonanceMix = ofClamp(audioResonanceMix, 0.0f, 1.0f);
        audioDuckDepth = ofClamp(audioDuckDepth, 0.0f, 1.0f);
        audioDuckReleaseMs = ofClamp(audioDuckReleaseMs, 5.0f, 5000.0f);
        spikeGrainMs = ofClamp(spikeGrainMs, 0.5f, 500.0f);
        spikeGrainRatio = ofClamp(spikeGrainRatio, 0.25f, 32.0f);
        rateVoiceMinHz = ofClamp(rateVoiceMinHz, 20.0f, 4000.0f);
        rateVoiceMaxHz = ofClamp(rateVoiceMaxHz, rateVoiceMinHz, 8000.0f);
        rateVoiceFullScaleHz = ofClamp(rateVoiceFullScaleHz, 1.0f, 5000.0f);
        rateEnvSeconds = ofClamp(rateEnvSeconds, 0.05f, 30.0f);
        if (bufferSize < 64) bufferSize = 64;
        if (sampleRate < 8000) sampleRate = 8000;
    }

private:
    template <typename T>
    static void num(const ofJson &j, const char *key, T &dst) {
        auto it = j.find(key);
        if (it != j.end() && !it->is_null() && !it->is_string()) dst = it->get<T>();
    }
    static void str(const ofJson &j, const char *key, std::string &dst) {
        auto it = j.find(key);
        if (it != j.end() && it->is_string()) dst = it->get<std::string>();
    }
};
