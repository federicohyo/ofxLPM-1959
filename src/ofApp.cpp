// 1959 -- one window, two halves, sixty-seven years apart.
//
// RIGHT: the Hubel & Wiesel film. A cat's visual cortex in 1959, a bar of light
// crossing a receptive field, and the clicks a single cortical neuron made on
// tape. The soundtrack is decoded ahead of time to raw PCM (prepare.sh) because
// the analysis needs the samples themselves and a video player only hands its
// audio to the sound card. The PCM is the master clock; the video is slaved to
// it, not the other way round.
//
// LEFT: a silicon neuron answering the same recording, now. Every click the
// detector finds on the tape becomes one spike delivered to neuron 14 of a
// fabricated SkyWater 130 nm die through its excitatory synapse 0. The die's
// membrane comes back on a separate path -- the analog monitor pin, sampled by
// a Pixhawk at 1 kHz and streamed over USB CDC -- and is drawn as the
// installation's spiral. Nothing on that side is generated: unplug the board
// and the picture stops moving; unplug the chip and it stops answering.
//
// SOUND: the film in the left ear, the die in the right. Neither is a
// simulation of the other, and they do not agree, which is the piece.
//
// Three devices, three separate paths, and only one of them is shared:
//   FTDI  -> neuron_bridge.py -> UART commands + AER spike stream   (the chip)
//   ACM   -> "seconds,volts" at 1 kHz                               (the membrane)
//   sound -> the film out, and the ticks the measured resets strike
//
// The one FTDI rule still holds: kill any other neuron_bridge.py, GUI or bench
// script before starting this. The app does not flash the chip -- run the
// bring-up first, and make sure CARAVAN_CLK_MHZ matches both the flashed image
// and the clock the biases were tuned at.

#include "ofApp.h"

#include "ofJson.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <system_error>

namespace {
constexpr size_t kVizCapacity = 1 << 14;   // ~16 s at 1 kHz
constexpr size_t kSpikeCapacity = 1 << 10;
constexpr size_t kScopeLen = 8192;         // ~8 s at 1 kHz
constexpr float kTwoPi = 6.28318530717958648f;

float onePoleK(float tauSeconds, float fs) {
    if (tauSeconds <= 0.0f) return 1.0f;
    return 1.0f - std::exp(-1.0f / (tauSeconds * fs));
}

int64_t fileMtime(const std::string &path) {
    std::error_code ec;
    auto t = std::filesystem::last_write_time(path, ec);
    if (ec) return 0;
    return (int64_t)t.time_since_epoch().count();
}
}  // namespace

// ---------------------------------------------------------------------------
// setup
// ---------------------------------------------------------------------------

void ofApp::setup() {
    settingsPath = ofToDataPath("settings.json", true);
    cfg.load(settingsPath);
    settingsMtime = fileMtime(settingsPath);

    ofSetVerticalSync(true);
    ofSetFrameRate(60);
    ofBackground(0);
    ofSetWindowTitle("1959");
    ofSetEscapeQuitsApp(false);

    audioFs = (float)cfg.sampleRate;

    vizRing.setup(kVizCapacity);
    memSpikes.setup(kSpikeCapacity);
    aerSpikes.setup(kSpikeCapacity);
    filmSpikes.setup(kSpikeCapacity);
    drain.assign(kVizCapacity, MembraneFrame());
    scope.assign(kScopeLen, MembraneFrame());
    rx.assign(8192, 0);
    lineBuf.reserve(128);

    applySettings();

    if (cfg.fullscreen) ofSetFullscreen(true);
    ofHideCursor();
    computeLayout();

    spiralPeriod = cfg.spiralPeriod;
    lastFrameTime = ofGetElapsedTimef();
    settingsCheckedAt = lastFrameTime;
    lastSampleAt = lastFrameTime;
    rateWindowAt = lastFrameTime;

    // --- the film ---------------------------------------------------------
    const std::string vpath = ofToDataPath(videoPath, true);
    if (video.load(vpath)) {
        video.setVolume(0.0f);   // the soundtrack comes from the PCM, once
        video.setLoopState(OF_LOOP_NORMAL);
        video.play();
        appendLog("film " + ofToString((int)video.getWidth()) + "x"
                  + ofToString((int)video.getHeight()) + ", "
                  + ofToString(video.getDuration(), 1) + " s");
    } else {
        appendLog("could not load " + vpath + " -- see prepare.sh");
    }
    if (!loadPcm(ofToDataPath(audioPath, true)))
        appendLog("could not load " + audioPath + " -- run ./prepare.sh");

    // --- the membrane path ------------------------------------------------
    openSerial();

    // --- sound ------------------------------------------------------------
    ofSoundStreamSettings s;
    s.numInputChannels = 0;
    s.numOutputChannels = 2;
    s.sampleRate = cfg.sampleRate;
    s.bufferSize = cfg.bufferSize;
    s.numBuffers = 4;
    s.setOutListener(this);
    streamOpen = soundStream.setup(s);
    if (!streamOpen) appendLog("sound stream would not open");

    // --- the chip ---------------------------------------------------------
    // Everything after this is staged in advanceBoot(), which waits for the
    // bridge to say READY before it touches a DAC.
    bootStageAt = ofGetElapsedTimef();
    if (chipEnabled) {
        if (!remoteChip.empty()) {
            // Remote chip: parse "host:spikePort:memPort:cmdPort"
            // Split right-to-left so each substr(0, pos) yields the remaining prefix.
            std::string h = remoteChip;
            int sPort = 9100, mPort = 9101, cPort = 9102;
            size_t c3 = h.rfind(':');
            if (c3 != std::string::npos) {
                cPort = std::stoi(h.substr(c3 + 1));
                h = h.substr(0, c3);
                size_t c2 = h.rfind(':');
                if (c2 != std::string::npos) {
                    mPort = std::stoi(h.substr(c2 + 1));
                    h = h.substr(0, c2);
                    size_t c1 = h.rfind(':');
                    if (c1 != std::string::npos) {
                        sPort = std::stoi(h.substr(c1 + 1));
                        h = h.substr(0, c1);
                    }
                }
            }
            netBridge = new NetworkBridge();
            netBridge->start(h, sPort, mPort, cPort);
            appendLog("remote bridge: " + h + ":" + ofToString(sPort)
                      + ":" + ofToString(mPort) + ":" + ofToString(cPort));
        } else {
            // Local chip: spawn neuron_bridge.py as child process
            const std::string bp =
                ofFilePath::join(ofFilePath::getCurrentExeDir(), bridgePath);
            brg()->start(bp);
            netBridge = nullptr;
            appendLog("bridge starting: " + bp);
        }
    } else {
        boot = BOOT_DONE;
        // The film detector still runs, so the click count in the calibration
        // overlay is a real reading of the tape; there is simply nowhere to
        // deliver it.
        stimulating.store(true);
        appendLog("chipEnabled false -- picture and sound only, the die is untouched");
    }
}

void ofApp::applySettings() {
    ofJson j;
    try { j = ofLoadJson(settingsPath); } catch (...) { j = ofJson(); }
    if (!j.is_null()) {
        auto num = [&](const char *k, auto &dst) {
            auto it = j.find(k);
            if (it != j.end() && !it->is_null() && !it->is_string())
                dst = it->get<std::decay_t<decltype(dst)>>();
        };
        auto str = [&](const char *k, std::string &dst) {
            auto it = j.find(k);
            if (it != j.end() && it->is_string()) dst = it->get<std::string>();
        };
        str("devicePath", devicePath);
        num("deviceBaud", deviceBaud);
        num("deviceRateHz", deviceRateHz);
        num("followerFallMs", followerFallMs);
        num("voltsFullScale", voltsFullScale);

        str("videoPath", videoPath);
        str("audioPath", audioPath);
        num("videoDim", videoDim);
        num("filmGain", filmGain);
        num("tickGain", tickGain);
        num("filmThreshold", filmThreshold);
        num("filmFollowerFallMs", filmFollowerFallMs);
        num("filmRefractoryMs", filmRefractoryMs);
        num("filmLowpassHz", filmLowpassHz);

        num("chipEnabled", chipEnabled);
        str("bridgePath", bridgePath);
        str("remoteChip", remoteChip);
        str("biasFile", biasFile);
        num("chipNeuron", chipNeuron);
        num("chipSynapse", chipSynapse);
        num("chipProgSyn", chipProgSyn);
        num("chipWeight", chipWeight);
        num("trigMinIntervalMs", trigMinIntervalMs);
        num("trigMaxPerFrame", trigMaxPerFrame);

        num("splitFraction", splitFraction);

        auto it = j.find("tickSource");
        if (it != j.end() && it->is_string()) {
            const std::string v = it->get<std::string>();
            aTickSource.store(v == "aer" ? TICK_AER
                              : v == "both" ? TICK_BOTH : TICK_MEMBRANE);
        }
    }
    sigFs = deviceRateHz > 1.0f ? deviceRateHz : 1000.0f;

    // The membrane detector runs at the BOARD's rate, in volts. followerFallMs
    // must be longer than the reset crash and shorter than the recovery; at
    // 1 kHz the crash is one sample and the recovery is ~20.
    memDet.setup(sigFs);
    memDet.setFollowerFallMs(followerFallMs);
    memDet.setThreshold(cfg.spikeThreshold);
    memDet.setRefractoryMs(cfg.refractoryMs);
    memDet.setRateSmoothingMs(cfg.rateSmoothingMs);
    // Nyquist here is sigFs/2, so a 3 kHz detection lowpass is meaningless; it
    // is only applied when it is actually below the band.
    memDet.setInputLowpassHz(cfg.detectLowpassHz < sigFs * 0.45f ? cfg.detectLowpassHz
                                                                 : 0.0f);
    memDet.setBackgroundMult(cfg.backgroundMult);

    // The film detector runs at the AUDIO rate, on a clean recording, so the
    // level envelope is off and the threshold is absolute -- these are the
    // values the tuner (ofxLPM/hubel-wiesel-audio-filter) settled on.
    filmDet.setup(audioFs);
    filmDet.setLevelNorm(false);
    filmDet.setFollowerFallMs(filmFollowerFallMs);
    filmDet.setThreshold(filmThreshold);
    filmDet.setRefractoryMs(filmRefractoryMs);
    filmDet.setRateSmoothingMs(250.0f);
    filmDet.setInputLowpassHz(filmLowpassHz);
    filmDet.setBackgroundMult(0.0f);

    aFilmGain.store(filmGain);
    // The die is quieter than the tape: one short grain per spike against a
    // continuous soundtrack. tickGain is the trim that puts them in the same
    // room, and settings.json is re-read within a second of being saved, so it
    // can be set by ear with the piece running.
    aTickGain.store(cfg.outputGain * cfg.mixSpike * tickGain);
    aGrainRatio.store(cfg.spikeGrainRatio);
    aGrainDecay.store(std::exp(-1.0f / (cfg.spikeGrainMs * 1e-3f * audioFs)));
}

void ofApp::reloadSettingsIfChanged() {
    const int64_t mtime = fileMtime(settingsPath);
    if (mtime == 0 || mtime == settingsMtime) return;
    settingsMtime = mtime;
    const std::string prevDevice = devicePath;
    cfg.load(settingsPath);
    applySettings();
    if (devicePath != prevDevice) openSerial();
    appendLog("settings reloaded");
}

void ofApp::appendLog(const std::string &msg) {
    ofLogNotice("1959") << msg;
    logLines.push_back("[" + ofGetTimestampString("%H:%M:%S") + "] " + msg);
    while ((int)logLines.size() > MAX_LOG) logLines.pop_front();
}

// ---------------------------------------------------------------------------
// bring-up -- staged, and never before the bridge says READY
// ---------------------------------------------------------------------------

void ofApp::loadBiases(const std::string &path) {
    std::ifstream f(path);
    if (!f.is_open()) { appendLog("no bias file at " + path); return; }
    ofJson j;
    try { f >> j; } catch (...) { appendLog("bias JSON parse error: " + path); return; }
    int count = 0;
    // Per-channel B commands through the bridge -- the only correct path. The
    // single-VREF route halves the seven NMOS biases and the array free-runs.
    for (auto &[name, val] : j.items()) {
        try {
            const float v = val.get<float>();
            brg()->setBias(name, v);
            ++count;
            if (name == "JExcWn0") excBias = v;
            else if (name == "vepulseextp") pulseExtV = v;
        } catch (...) {}
    }
    appendLog("pushed " + ofToString(count) + " biases from "
              + ofFilePath::getFileName(path));
}

void ofApp::advanceBoot() {
    const float now = ofGetElapsedTimef();
    const float held = now - bootStageAt;

    switch (boot) {
    case BOOT_WAIT:
        // The bridge owns the FTDI, engages the DLL and programs the DACs. It
        // says READY when the chip is answering; do not touch a bias before it.
        if (brg()->isConnected()) {
            appendLog("bridge ready");
            boot = BOOT_BIASES; bootStageAt = now;
        } else if (held > 45.0f) {
            appendLog("bridge never said READY -- is another process holding the FTDI?");
            boot = BOOT_BIASES; bootStageAt = now;   // try anyway; it is an installation
        }
        break;

    case BOOT_BIASES:
        loadBiases(ofToDataPath(biasFile, true));
        boot = BOOT_MONITOR; bootStageAt = now;
        break;

    case BOOT_MONITOR:
        // 24 bit-banged DAC channels take a moment; let them land first.
        if (held < 3.0f) break;
        brg()->setMonitorNeuron(chipNeuron);
        appendLog("monitor -> N" + ofToString(chipNeuron));
        boot = BOOT_PROG; bootStageAt = now;
        break;

    case BOOT_PROG:
        if (held < 0.4f) break;
        // PROG before ROUTE, always: programming a weight re-runs spikesetup
        // on the chip and clobbers whatever route was staged.
        //
        // Two synapses are programmed, not one. chipProgSyn is the bench
        // sequence as it is written down (S14); chipSynapse is the one that is
        // actually routed and fired (S0). Weighting only the first would fire
        // an unweighted synapse and the neuron would sit there silent, which is
        // exactly the failure this is here to avoid -- and programming a
        // synapse that is never routed costs nothing.
        if (chipProgSyn >= 0 && chipProgSyn != chipSynapse) {
            brg()->sendProgram(chipProgSyn, chipWeight, chipExc);
            appendLog("prog S" + ofToString(chipProgSyn) + " W" + ofToString(chipWeight)
                      + (chipExc ? " exc" : " nexc"));
        }
        brg()->sendProgram(chipSynapse, chipWeight, chipExc);
        appendLog("prog S" + ofToString(chipSynapse) + " W" + ofToString(chipWeight)
                  + (chipExc ? " exc" : " nexc"));
        boot = BOOT_ROUTE; bootStageAt = now;
        break;

    case BOOT_ROUTE:
        if (held < 0.4f) break;
        brg()->sendRoute(chipSynapse, chipNeuron, chipExc);
        lastRouteAt = now;
        appendLog("route S" + ofToString(chipSynapse) + " -> N" + ofToString(chipNeuron)
                  + (chipExc ? " exc" : " nexc"));
        boot = BOOT_RUN; bootStageAt = now;
        break;

    case BOOT_RUN:
        if (held < 0.6f) break;
        // From here the film drives the die. The detector has been running on
        // the soundtrack since the first buffer, so drop what it found while
        // the chip was still being set up rather than firing it all at once.
        filmSpikes.clear();
        stimulating.store(true);
        stimStartedAt = now;
        appendLog("stimulation running: 1959 -> N" + ofToString(chipNeuron));
        boot = BOOT_DONE; bootStageAt = now;
        break;

    case BOOT_DONE:
        break;
    }
}

// ---------------------------------------------------------------------------
// the film -- decoded ahead of time, played and analysed by the same process
// ---------------------------------------------------------------------------

bool ofApp::loadPcm(const std::string &path) {
    std::ifstream in(path, std::ios::binary | std::ios::ate);
    if (!in) return false;
    const std::streamsize bytes = in.tellg();
    in.seekg(0);
    pcm.resize((size_t)bytes / sizeof(int16_t));
    in.read(reinterpret_cast<char *>(pcm.data()), bytes);
    pcmFrames = pcm.size() / 2;
    appendLog("tape " + ofToString(pcmFrames) + " frames = "
              + ofToString(pcmFrames / audioFs, 0) + " s");
    return pcmFrames > 0;
}

void ofApp::syncVideo() {
    video.update();
    if (!video.isLoaded() || paused.load()) return;

    // Gentle nudge only: seek when the drift exceeds a quarter second, at most
    // once a second. The PCM is the clock, the picture follows it.
    const float now = ofGetElapsedTimef();
    if (now - lastNudgeAt < 1.0f) return;
    const float dur = video.getDuration();
    if (dur <= 0.1f) return;
    const float audioT = (float)playHead.load(std::memory_order_acquire) / audioFs;
    const float videoT = video.getPosition() * dur;
    if (std::fabs(videoT - audioT) > 0.25f) {
        video.setPosition(ofClamp(audioT / dur, 0.0f, 0.999f));
        lastNudgeAt = now;
    }
}

// ---------------------------------------------------------------------------
// the membrane -- "seconds,volts" over USB CDC at the board's exact rate
// ---------------------------------------------------------------------------

bool ofApp::openSerial() {
    serial.close();
    serialOpen = serial.setup(devicePath, deviceBaud);
    lastOpenAttempt = ofGetElapsedTimef();
    // Restart the rate measurement here, not in setup(): loading the film's
    // 127 MB soundtrack sits between the two, and counting that as time the
    // board was silent reads as a board streaming at a third of its rate.
    rateWindowFirst = sigFrame;
    rateWindowAt = lastOpenAttempt;
    lastSampleAt = lastOpenAttempt;
    rateReported = false;
    if (serialOpen) appendLog("membrane from " + devicePath + " at "
                              + ofToString(sigFs, 0) + " Hz");
    else            appendLog("cannot open " + devicePath + "; retrying");
    return serialOpen;
}

void ofApp::readSerial() {
    if (!serialOpen) return;
    int avail = serial.available();
    while (avail > 0) {
        const int want = std::min(avail, (int)rx.size());
        const int got = serial.readBytes(rx.data(), want);
        if (got <= 0) break;
        for (int i = 0; i < got; ++i) {
            const char c = (char)rx[i];
            if (c == '\n' || c == '\r') {
                if (lineBuf.size() > 2) {
                    // The value is what matters; the index comes from the
                    // count, because the board's rate is exact and its
                    // timestamp is a display convention that wraps.
                    const size_t comma = lineBuf.find(',');
                    if (comma != std::string::npos && comma + 1 < lineBuf.size()) {
                        const char *p = lineBuf.c_str() + comma + 1;
                        char *end = nullptr;
                        const double v = std::strtod(p, &end);
                        if (end != p) feed((float)v);
                    }
                }
                lineBuf.clear();
            } else if (lineBuf.size() < 120) {
                lineBuf.push_back(c);
            }
        }
        avail = serial.available();
    }
}

void ofApp::feed(float volts) {
    const float ddDepth = cfg.drawDuckDepth;
    const float ddRel = onePoleK(cfg.drawDuckReleaseMs * 1e-3f, sigFs);

    HubelSpikeEvent ev;
    drawDuck -= drawDuck * ddRel;
    if (memDet.process(volts, sigFrame, ev)) {
        memSpikes.push(ev);
        drawDuck = 1.0f;
    }

    MembraneFrame f;
    f.rawA = volts;
    f.vA = cfg.drawBandLimited ? memDet.filtered() : memDet.voltage();
    f.bA = memDet.baseline();
    f.dA = drawDuck;
    f.vA *= (1.0f - ddDepth) + ddDepth * drawDuck;
    vizRing.push(f);

    ++sigFrame;
    lastSampleAt = ofGetElapsedTimef();
}

// ---------------------------------------------------------------------------
// sound -- 1959 left, 2026 right
// ---------------------------------------------------------------------------

void ofApp::audioOut(ofSoundBuffer &out) {
    const size_t n = out.getNumFrames();
    const size_t ch = out.getNumChannels();
    if (ch == 0) return;

    const float fGain = aFilmGain.load();
    const float tGain = aTickGain.load();
    const float grainDecay = aGrainDecay.load();
    const float ratio = aGrainRatio.load();
    const int src = aTickSource.load();
    const bool run = stimulating.load();
    const bool hold = paused.load();

    // A tick is struck by a measured reset and decays. Both rings are drained
    // regardless of which one is audible, so switching source cannot dump a
    // backlog into the room.
    auto strike = [&](SpscRing<HubelSpikeEvent> &ring, bool audible) {
        HubelSpikeEvent e;
        while (ring.peek(e)) {
            ring.skipOne();
            if (!audible) continue;
            voice.grainAmp = std::min(e.excursion, 4.0f);
            voice.grainPhase = 0.0f;
            voice.tickLp = 0.0f;
            voice.grainFreq = ofClamp(220.0f * ratio, 20.0f, audioFs * 0.45f);
        }
    };
    strike(memSpikes, src == TICK_MEMBRANE || src == TICK_BOTH);
    strike(aerSpikes, src == TICK_AER || src == TICK_BOTH);

    auto grain = [&]() {
        if (voice.grainAmp <= 1e-5f) { voice.grainAmp = 0.0f; return 0.0f; }
        voice.grainPhase += 1.0f / audioFs;
        const float att = std::min(voice.grainPhase * 800.0f, 1.0f);
        const float rel = std::exp(-voice.grainPhase * 60.0f);
        const float tone = std::sin(voice.grainPhase * 300.0f * kTwoPi);
        voice.tickLp += (tone - voice.tickLp) * 0.3f;
        const float g = voice.tickLp * att * rel * voice.grainAmp;
        voice.grainAmp *= grainDecay;
        return g;
    };

    if (pcmFrames == 0 || hold) {
        for (size_t i = 0; i < n; ++i) {
            const float t = ofClamp(tGain * grain(), -1.0f, 1.0f);
            out[i * ch] = 0.0f;
            if (ch >= 2) out[i * ch + 1] = t;
        }
        outFrame += n;
        return;
    }

    uint64_t head = playHead.load(std::memory_order_relaxed);
    HubelSpikeEvent ev;

    for (size_t i = 0; i < n; ++i) {
        if (head >= pcmFrames) head = 0;

        const float a = pcm[head * 2] * (1.0f / 32768.0f);
        const float b = pcm[head * 2 + 1] * (1.0f / 32768.0f);

        // The cat's spikes are found on the tape's own left channel, which is
        // where the electrode is, and are queued for update() to deliver.
        if (filmDet.process(a, outFrame + i, ev) && run) {
            filmSpikes.push(ev);
            filmDetected.fetch_add(1);
        }

        const float film = fGain * 0.5f * (a + b);
        const float tick = tGain * grain();

        const int am = audioMode;
        out[i * ch] = ofClamp(am == 1 ? 0.0f : film, -1.0f, 1.0f);
        if (ch >= 2) out[i * ch + 1] = ofClamp(am == 0 ? 0.0f : tick, -1.0f, 1.0f);
        ++head;
    }

    outFrame += n;
    playHead.store(head, std::memory_order_release);
}

// ---------------------------------------------------------------------------
// update
// ---------------------------------------------------------------------------

void ofApp::update() {
    const float now = ofGetElapsedTimef();

    if (now - settingsCheckedAt > 1.0f) {
        settingsCheckedAt = now;
        reloadSettingsIfChanged();
    }

    // A link that has come back is a chip that has been brought back: the
    // service on the plinth reflashes the die every time it starts, so it is
    // listening with no weights, no route, and a Timer0 counting from zero
    // again. Stage the whole bring-up a second time rather than talking to a
    // chip that is no longer set up. (The first connection also raises this,
    // which is why it only fires once we are past BOOT_WAIT.)
    if (netBridge && netBridge->consumeReconnect() && boot != BOOT_WAIT) {
        appendLog("link came back -- bringing the die up again");
        stimulating.store(false);
        filmSpikes.clear();

        // The AER accounting is all relative to timestamps and counts from the
        // old session. Left alone, the high-water mark would sit in the future
        // of the restarted Timer0, every new spike would look old, and the
        // sentinel would read a ratio of zero and haul the bias up chasing it.
        lastAerTs = 0;
        aerNewThisFrame = 0;
        aerNewThisWindow = 0;
        healLastTrigCount = chipFired.load();
        healLastSpikeCount = aerSpikeCount;
        lastFilmCount = filmDetected.load();
        healRatio = 1.0f;
        heartbeatAt = now;

        boot = BOOT_WAIT;
        bootStageAt = now;
    }

    advanceBoot();
    // When using a remote chip, membrane comes from the network instead of serial
    if (netBridge) {
        auto memLines = netBridge->drainMembrane();
        for (const auto& ml : memLines) {
            const size_t comma = ml.find(',');
            if (comma != std::string::npos && comma + 1 < ml.size()) {
                const char *p = ml.c_str() + comma + 1;
                char *end = nullptr;
                const double v = std::strtod(p, &end);
                if (end != p) feed((float)v);
            }
        }
        if (!memLines.empty()) lastSampleAt = ofGetElapsedTimef();
    } else {
        readSerial();
    }
    syncVideo();

    // The board's rate is a load-bearing constant: it sets the spiral timebase
    // and the detector's follower. Measure what actually arrives and say so, so
    // a board flashed with a different image is visible instead of silent.
    if (now - rateWindowAt > 5.0f) {
        if (sigFrame > rateWindowFirst) {
            measuredFs = (float)(sigFrame - rateWindowFirst) / (now - rateWindowAt);
            const bool off = !netBridge && std::fabs(measuredFs - sigFs) > 0.15f * sigFs;
            if (off)
                appendLog("board is streaming " + ofToString(measuredFs, 0)
                          + " Hz but deviceRateHz is " + ofToString(sigFs, 0));
            else if (!rateReported)
                appendLog("board confirmed at " + ofToString(measuredFs, 0) + " Hz");
            rateReported = !off;
        }
        rateWindowFirst = sigFrame;
        rateWindowAt = now;
    }

    // The link going quiet is survivable: the picture keeps decaying, no state
    // is reset, and the port is retried. (Not used in remote-chip mode.)
    if (!netBridge) {
        if (!serialOpen && now - lastOpenAttempt > 2.0f) openSerial();
        else if (serialOpen && now - lastSampleAt > 3.0f && now - lastOpenAttempt > 3.0f) {
            appendLog("no samples for 3 s -- reopening " + devicePath);
            openSerial();
            lastSampleAt = now;
        }
    }

    // Deliver the cat's spikes to the die, one TRIG each. The rate limit is the
    // whole reason this is here and not in the audio thread: the UART cannot
    // take a burst, and a burst is exactly what a click train is.
    if (chipEnabled && stimulating.load()) {
        HubelSpikeEvent ev;
        int fired = 0;
        const float minGap = trigMinIntervalMs * 1e-3f;
        // The route is staged, not re-staged: this app is the only thing talking
        // to the chip, and a route survives until something re-runs spikesetup.
        // Re-sending it with every trigger doubled the command traffic and
        // buried the log. It is refreshed slowly instead, as cheap insurance.
        if (now - lastRouteAt > 10.0f) {
            brg()->sendRoute(chipSynapse, chipNeuron, chipExc);
            lastRouteAt = now;
        }
        while (fired < trigMaxPerFrame && filmSpikes.peek(ev)) {
            if (now - lastTrigAt < minGap) break;
            filmSpikes.skipOne();
            brg()->sendTrigger();
            lastTrigAt = now;
            ++fired;
            chipFired.fetch_add(1);
        }
    }

    // The chip's own report of the spikes it produced. Only the monitored
    // neuron is of interest here; the rest of the array is not being driven.
    // New spikes are detected by tracking the latest timestamp seen; this gives
    // a clean per-frame delta that the self-healing can compare to TRIGs without
    // deque-aging artefacts.
    if (chipEnabled) {
        auto spikes = brg()->getSpikes(2.0f);
        int inWindow = 0;
        int64_t maxTs = lastAerTs;
        bool gotNew = false;
        for (const auto &s : spikes) {
            if (s.neuron != chipNeuron) continue;
            ++inWindow;
            if (s.timestamp_us > lastAerTs) {
                ++aerNewThisFrame;
                if (s.timestamp_us > maxTs) maxTs = s.timestamp_us;
                gotNew = true;
            }
        }
        if (gotNew) lastAerTs = maxTs;
        // One tick per frame if new AER spikes arrived (enough for audio).
        if (aerNewThisFrame > 0) {
            HubelSpikeEvent e;
            e.frame = 0;
            e.excursion = 1.0f;
            e.rateHz = 0.0;
            aerSpikes.push(e);
        }
        chipRateHz.store(inWindow / 2.0f);
        aerSpikeCount += aerNewThisFrame;
        aerNewThisWindow += aerNewThisFrame;
        aerNewThisFrame = 0;
    }

    for (auto &msg : brg()->drainLog()) appendLog(msg);

    // ------------------------------------------------------------------
    // 15-second heartbeat: log counts + self-healing in one window so the
    // numbers in the log are exactly what the heal acted on.
    // ------------------------------------------------------------------
    // Not while the link is down: with no spikes coming back the ratio reads
    // zero, and the sentinel would spend the outage walking JExcWn0 away from a
    // bias that was never the problem.
    if (chipEnabled && stimulating.load() && boot == BOOT_DONE
        && brg()->isConnected() && now - heartbeatAt > 15.0f) {
        heartbeatAt = now;

        const uint64_t dClicks = filmDetected.load() - lastFilmCount;
        const uint64_t dTrigs = chipFired.load() - healLastTrigCount;
        const uint64_t dSpikes = aerNewThisWindow;
        lastFilmCount = filmDetected.load();
        healLastTrigCount = chipFired.load();
        aerNewThisWindow = 0;
        const float ratio = dTrigs > 0 ? (float)dSpikes / (float)dTrigs : -1.0f;
        healRatio = (dTrigs >= 5) ? ratio : 1.0f;

        appendLog("clicks " + ofToString(dClicks)
                  + "  triggers " + ofToString(dTrigs)
                  + "  N" + ofToString(chipNeuron) + " " + ofToString(chipRateHz.load(), 1)
                  + " spk/s  aerN14 " + ofToString(dSpikes)
                  + "  ratio " + ofToString(healRatio, 2)
                  + "  bias " + ofToString(excBias, 4)
                  + "  membrane " + ofToString(memDet.rate(), 1) + " Hz");

        if (dTrigs >= 5) {
            if (healRatio < 0.85f) {
                const float err = 1.0f - healRatio;
                const float step = std::max(0.001f, std::min(0.005f, err * 0.003f));
                const float newV = excBias + step;
                excBias = newV;
                brg()->setBias("JExcWn0", newV);
                healStage = 1;
                ++healActions;
                ++healBiasNudges;
                healStatus = "raising JExcWn0 to " + ofToString(newV, 4) + "V";
                appendLog("heal: ratio " + ofToString(healRatio, 2)
                          + "  step " + ofToString(step, 3) + "V  " + healStatus);
            } else if (healRatio > 1.15f) {
                const float err = healRatio - 1.0f;
                const float step = std::max(0.001f, std::min(0.005f, err * 0.003f));
                const float newV = excBias - step;
                excBias = newV;
                brg()->setBias("JExcWn0", newV);
                healStage = 2;
                ++healActions;
                ++healBiasNudges;
                healStatus = "lowering JExcWn0 to " + ofToString(newV, 4) + "V";
                appendLog("heal: ratio " + ofToString(healRatio, 2)
                          + "  step " + ofToString(step, 3) + "V  " + healStatus);
            } else if (healRatio < 0.01f && dTrigs > 20) {
                brg()->sendProgram(chipSynapse, chipWeight, chipExc);
                brg()->sendRoute(chipSynapse, chipNeuron, chipExc);
                healStage = 3;
                ++healActions;
                healStatus = "re-routing S" + ofToString(chipSynapse)
                             + " -> N" + ofToString(chipNeuron);
                appendLog("heal: dead! " + healStatus);
            } else {
                healStage = 0;
                healStatus = "ok (" + ofToString(healRatio, 2) + ")";
            }
        }
    }

    spiralPeriod = cfg.spiralPeriod;
}

// ---------------------------------------------------------------------------
// render
// ---------------------------------------------------------------------------

void ofApp::computeLayout() {
    const float W = ofGetWidth(), H = ofGetHeight();
    const float split = ofClamp(splitFraction, 0.15f, 0.85f);
    leftRect.set(0, 0, std::floor(W * split), H);
    rightRect.set(leftRect.getRight(), 0, W - leftRect.width, H);
}

float ofApp::warpRadius(float v) const {
    const float m = std::pow(std::fabs(v), cfg.radiusGamma);
    return v < 0.0f ? -m : m;
}

void ofApp::allocatePhosphor() {
    ofFboSettings s;
    s.width = std::max(16, (int)leftRect.width);
    s.height = std::max(16, (int)leftRect.height);
    s.useDepth = false;
    s.useStencil = false;
    s.numSamples = 0;
    const int formats[] = {GL_RGBA16F, GL_RGBA32F, GL_RGBA};
    for (int fmt : formats) {
        s.internalformat = fmt;
        phosphor.allocate(s);
        if (phosphor.isAllocated()) break;
    }
    phosphorReady = phosphor.isAllocated();
    if (phosphorReady) {
        phosphor.begin();
        ofClear(0, 0, 0, 255);
        phosphor.end();
    }
}

void ofApp::windowResized(int w, int h) {
    computeLayout();
    phosphorReady = false;
    phosphorAttempts = 0;
    havePrevVert = false;
}

void ofApp::draw() {
    const float now = ofGetElapsedTimef();
    const float dt = ofClamp(now - lastFrameTime, 0.0f, 1.0f);
    lastFrameTime = now;

    computeLayout();
    ofClear(0, 0, 0, 255);

    // --- right: 1959 ------------------------------------------------------
    drawVideoPanel();

    // --- left: 2026 -------------------------------------------------------
    if (!phosphorReady) {
        if (phosphorAttempts < 8) { ++phosphorAttempts; allocatePhosphor(); }
        if (!phosphorReady) return;
    }

    phosphor.begin();
    // Old trajectory decays exponentially and the buffer is never cleared; the
    // fractional alpha is carried between frames so a slow fade is not lost to
    // integer truncation.
    decayPending = 1.0f - (1.0f - decayPending) * std::exp(-dt / cfg.decaySeconds);
    const int alpha = (int)std::floor(decayPending * 255.0f);
    if (alpha >= 1) {
        decayPending -= (float)alpha / 255.0f;
        ofEnableBlendMode(OF_BLENDMODE_ALPHA);
        ofSetColor(0, 0, 0, alpha);
        ofDrawRectangle(0, 0, phosphor.getWidth(), phosphor.getHeight());
    }
    drawSpirals();
    phosphor.end();

    ofEnableBlendMode(OF_BLENDMODE_ADD);
    ofSetColor(255);
    phosphor.draw(leftRect.x, leftRect.y, leftRect.width, leftRect.height);
    ofEnableBlendMode(OF_BLENDMODE_ALPHA);

    if (cfg.scopePanel) drawScopePanel();
    drawStartupHud();
    if (showCalibration) drawCalibration();
}

void ofApp::drawVideoPanel() {
    if (!video.isLoaded()) return;
    const float vw = video.getWidth(), vh = video.getHeight();
    if (vw < 1.0f || vh < 1.0f) return;
    // Contain, not cover: the film is the evidence, so none of it is cropped.
    const float s = std::min(rightRect.width / vw, rightRect.height / vh);
    const float dw = vw * s, dh = vh * s;
    ofEnableBlendMode(OF_BLENDMODE_ALPHA);
    ofSetColor(255, 255, 255, (int)(ofClamp(videoDim, 0.0f, 1.0f) * 255.0f));
    video.draw(rightRect.x + (rightRect.width - dw) * 0.5f,
               rightRect.y + (rightRect.height - dh) * 0.5f, dw, dh);
    ofSetColor(255);
}

void ofApp::drawSpirals() {
    uint64_t first = 0;
    const size_t n = vizRing.pop(drain.data(), drain.size(), first);
    if (n == 0) return;
    for (size_t i = 0; i < n; ++i) {
        scope[scopePos] = drain[i];
        scopePos = (scopePos + 1) % scope.size();
    }
    ofEnableBlendMode(OF_BLENDMODE_ADD);
    ofSetLineWidth(cfg.traceWidth);
    drawTrajectory(drain, first, n);
    ofSetLineWidth(1.0f);
}

void ofApp::drawTrajectory(const std::vector<MembraneFrame> &f, uint64_t first,
                           size_t n) {
    const float w = phosphor.getWidth();
    const float h = phosphor.getHeight();
    const glm::vec2 c(w * 0.5f, h * 0.5f);
    const float rmax = 0.5f * std::min(w, h);
    const float base = cfg.radiusBase * rmax;
    const float gain = cfg.radiusGain * rmax;
    const float rmin = std::max(1.0f, cfg.radiusMin * rmax);

    ofFloatColor col = ofFloatColor::fromHsb(cfg.hueA, cfg.saturation, cfg.brightness);
    const float aBase = cfg.traceAlphaQuiet;
    const float aSpan = std::max(0.0f, cfg.traceAlpha - cfg.traceAlphaQuiet);

    const double T = spiralPeriod > 1e-3 ? spiralPeriod : 1e-3;

    ofMesh mesh;
    mesh.setMode(OF_PRIMITIVE_LINE_STRIP);
    if (havePrevVert) {
        mesh.addVertex(glm::vec3(lastVert, 0.0f));
        col.a = aBase;
        mesh.addColor(col);
    }

    for (size_t i = 0; i < n; ++i) {
        const double t = (double)(first + i) / (double)sigFs;
        const double phase = std::fmod(t, T) / T;
        float r = base + gain * warpRadius(f[i].vA);
        if (r < rmin) r = rmin;
        if (r > rmax * 1.15f) r = rmax * 1.15f;

        const float th = (float)(phase * 6.283185307179586);
        const glm::vec2 pt(c.x + r * std::cos(th), c.y + r * std::sin(th));

        if (phase < prevPhase) {
            if (mesh.getNumVertices() > 1) mesh.draw();
            mesh.clear();
            mesh.setMode(OF_PRIMITIVE_LINE_STRIP);
        }
        prevPhase = phase;

        mesh.addVertex(glm::vec3(pt, 0.0f));
        col.a = aBase + aSpan * ofClamp(f[i].dA, 0.0f, 1.0f);
        mesh.addColor(col);
        lastVert = pt;
    }
    if (mesh.getNumVertices() > 1) mesh.draw();
    havePrevVert = true;
}

void ofApp::drawScopePanel() {
    const float w = leftRect.width;
    const float ph = leftRect.height * cfg.scopeHeight;
    const float py = leftRect.getBottom() - ph;
    const float mid = py + ph * 0.5f;
    const float half = ph * 0.5f;

    const size_t cap = scope.size();
    size_t n = (size_t)(cfg.scopeSeconds * sigFs);
    if (n > cap) n = cap;
    if (n < 2) return;
    const size_t start = (scopePos + cap - n) % cap;

    // DC-coupled, so the strip shows absolute volts about their own mean --
    // which is what makes it look like the oscilloscope.
    double mean = 0.0;
    for (size_t i = 0; i < n; ++i) mean += scope[(start + i) % cap].rawA;
    mean /= (double)n;

    const float g = cfg.scopeGain * half;
    const float sign = cfg.scopeInvert ? -1.0f : 1.0f;

    ofEnableBlendMode(OF_BLENDMODE_ALPHA);
    ofSetColor(120, 120, 130, 40);
    ofDrawLine(leftRect.x, mid, leftRect.x + w, mid);

    ofMesh m;
    m.setMode(OF_PRIMITIVE_LINE_STRIP);
    const int cols = (int)w;
    for (int c = 0; c < cols; ++c) {
        const size_t i0 = (size_t)((double)c * n / cols);
        const float v = (float)(scope[(start + i0) % cap].rawA - mean);
        m.addVertex(glm::vec3(leftRect.x + (float)c,
                              ofClamp(mid - sign * v * g, py, py + ph), 0));
    }
    ofFloatColor col = ofFloatColor::fromHsb(cfg.hueA, cfg.saturation, cfg.brightness);
    col.a = cfg.scopeAlpha;
    ofSetColor(col);
    m.draw();
}

// Visible confirmation, for the person setting the piece up rather than for the
// visitor: what the die is being told, in order, and then the two counts that
// say it is actually happening. It fades out once the room is running.
void ofApp::drawStartupHud() {
    if (showCalibration) return;   // the full overlay says all of this already
    float a = 1.0f;
    if (stimStartedAt >= 0.0f) {
        const float since = ofGetElapsedTimef() - stimStartedAt;
        if (since > hudHoldSeconds + 4.0f) return;
        if (since > hudHoldSeconds) a = 1.0f - (since - hudHoldSeconds) / 4.0f;
    }

    const char *stage[] = {"waiting for the bridge", "loading biases",
                           "monitor -> N", "programming synapse",
                           "staging route", "starting", "running"};
    char l1[192], l2[192];
    snprintf(l1, sizeof(l1), "%s%s", stage[(int)boot],
             boot == BOOT_MONITOR ? ofToString(chipNeuron).c_str() : "");
    snprintf(l2, sizeof(l2), "prog S%d/S%d W%d exc   route S%d -> N%d   "
             "clicks %d   spikes sent %d",
             chipProgSyn, chipSynapse, chipWeight, chipSynapse, chipNeuron,
             filmDetected.load(), chipFired.load());

    ofEnableBlendMode(OF_BLENDMODE_ALPHA);
    ofSetColor(0, 0, 0, (int)(150 * a));
    ofDrawRectangle(0, 0, ofGetWidth(), 52);
    ofSetColor(200, 210, 220, (int)(230 * a));
    ofDrawBitmapString(l1, 16, 20);
    ofSetColor(150, 160, 170, (int)(210 * a));
    ofDrawBitmapString(l2, 16, 38);
}

void ofApp::drawCalibration() {
    ofEnableBlendMode(OF_BLENDMODE_ALPHA);
    ofSetColor(0, 0, 0, 190);
    ofDrawRectangle(0, 0, ofGetWidth(), 128);
    ofSetColor(190);
    char buf[400];

    const char *stage[] = {"waiting for bridge", "biases", "monitor", "prog",
                           "route", "starting", "running"};
    snprintf(buf, sizeof(buf), "chip: %s  bridge %s  N%d <- S%d W%d %s   %.1f spk/s",
             stage[(int)boot], brg()->isConnected() ? "ready" : "DOWN",
             chipNeuron, chipSynapse, chipWeight, chipExc ? "exc" : "nexc",
             chipRateHz.load());
    ofDrawBitmapString(buf, 16, 22);

    snprintf(buf, sizeof(buf),
             "membrane: %s %s  %.0f Hz (measured %.0f)  samples %llu  "
             "rate %.2f Hz  thr %.2f  follower %.0f ms",
             devicePath.c_str(), serialOpen ? "open" : "CLOSED", sigFs, measuredFs,
             (unsigned long long)sigFrame, memDet.rate(), cfg.spikeThreshold,
             followerFallMs);
    ofDrawBitmapString(buf, 16, 42);

    const float t = (float)playHead.load() / audioFs;
    snprintf(buf, sizeof(buf),
             "film: %.1f / %.1f s  thr %.2f  detected %d  fired %d  %s",
             t, pcmFrames / audioFs, filmThreshold, filmDetected.load(),
             chipFired.load(), paused.load() ? "PAUSED" : "");
    ofDrawBitmapString(buf, 16, 62);

    const char *hue = healStage == 0 ? "ok" : healStage == 1 ? "WIDEN" : healStage == 2 ? "NARROW" : "REROUTE";
    snprintf(buf, sizeof(buf),
             "heal: pe=%.3fV  ratio=%.2f  %s  actions=%d  nudges=%d  %s",
             pulseExtV, healRatio, hue, healActions, healBiasNudges, healStatus.c_str());
    ofDrawBitmapString(buf, 16, 82);

    snprintf(buf, sizeof(buf), "T %.2f s   scopeGain %.1f   fps %.1f   "
             "[arrows] thr/gain  [ ] period  [space] pause  type 'quit'",
             spiralPeriod, cfg.scopeGain, ofGetFrameRate());
    ofDrawBitmapString(buf, 16, 102);

    int line = 0;
    for (auto it = logLines.rbegin(); it != logLines.rend() && line < 2; ++it, ++line)
        ofDrawBitmapString(*it, 16, 122 + line * 14);
}

// ---------------------------------------------------------------------------

void ofApp::exit() {
    stimulating.store(false);
    if (streamOpen) soundStream.close();
    serial.close();
    video.close();
    brg()->stop();
    if (netBridge) { delete netBridge; netBridge = nullptr; }
}

void ofApp::keyPressed(int key) {
    if (key == OF_KEY_UP || key == OF_KEY_DOWN) {
        cfg.scopeGain = ofClamp(cfg.scopeGain * (key == OF_KEY_UP ? 1.3f : 1.0f / 1.3f),
                                0.1f, 500.0f);
        appendLog("scopeGain " + ofToString(cfg.scopeGain, 2));
        return;
    }
    if (key == OF_KEY_LEFT || key == OF_KEY_RIGHT) {
        cfg.spikeThreshold =
            ofClamp(cfg.spikeThreshold * (key == OF_KEY_RIGHT ? 1.15f : 1.0f / 1.15f),
                    0.02f, 8.0f);
        memDet.setThreshold(cfg.spikeThreshold);
        appendLog("membrane threshold " + ofToString(cfg.spikeThreshold, 3)
                  + "  rate " + ofToString(memDet.rate(), 2) + " Hz");
        return;
    }
    if (key == ',' || key == '.') {
        filmThreshold = ofClamp(filmThreshold * (key == '.' ? 1.15f : 1.0f / 1.15f),
                                0.01f, 8.0f);
        filmDet.setThreshold(filmThreshold);
        appendLog("film threshold " + ofToString(filmThreshold, 3)
                  + "  rate " + ofToString(filmDet.rate(), 2) + " Hz");
        return;
    }
    if (key == '[' || key == ']') {
        const float step = cfg.spiralPeriod >= 1.0f ? 0.5f : 0.1f;
        cfg.spiralPeriod = ofClamp(cfg.spiralPeriod + (key == ']' ? step : -step),
                                   0.1f, 60.0f);
        appendLog("spiralPeriod " + ofToString(cfg.spiralPeriod, 2) + " s");
        return;
    }
    if (key == '-' || key == '=' || key == '+') {
        tickGain = ofClamp(tickGain * (key == '-' ? 1.0f / 1.25f : 1.25f), 0.05f, 20.0f);
        aTickGain.store(cfg.outputGain * cfg.mixSpike * tickGain);
        appendLog("tickGain " + ofToString(tickGain, 2) + "  (chip channel "
                  + ofToString(cfg.outputGain * cfg.mixSpike * tickGain, 2) + ")");
        return;
    }
    if (key == 'v' || key == 'V') { cfg.scopeInvert = !cfg.scopeInvert; return; }
    if (key == ' ') {
        const bool p = !paused.load();
        paused.store(p);
        video.setPaused(p);
        appendLog(p ? "paused" : "running");
        return;
    }
    if (key == 'r' || key == 'R') {
        audioMode = 1;
        appendLog("audio: right only (chip)");
        return;
    }
    if (key == 'l' || key == 'L') {
        audioMode = 0;
        appendLog("audio: left only (film)");
        return;
    }
    if (key == 'b' || key == 'B') {
        audioMode = 2;
        appendLog("audio: both");
        return;
    }

    const float now = ofGetElapsedTimef();
    if (now - keySequenceAt > 2.0f) keySequence.clear();
    keySequenceAt = now;
    if (key >= 'A' && key <= 'Z') key = key - 'A' + 'a';
    if (key < 'a' || key > 'z') { keySequence.clear(); return; }
    keySequence.push_back((char)key);
    if (keySequence.size() > 8) keySequence.erase(0, keySequence.size() - 8);
    auto ends = [&](const char *w) {
        const size_t l = strlen(w);
        return keySequence.size() >= l &&
               keySequence.compare(keySequence.size() - l, l, w) == 0;
    };
    if (ends("cal")) { showCalibration = !showCalibration; keySequence.clear(); }
    else if (ends("quit")) ofExit();
}
