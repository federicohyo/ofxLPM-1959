#pragma once

#include "ofMain.h"

// The chip: one owner of the FTDI, spawned as a child process. Its SpikeEvent
// is the AER address stream, which is why the detector's own SpikeEvent has to
// be renamed before it comes in -- see HubelDetector.h.
// For remote operation (chip on another machine), set "remoteChip" in settings.json.
#include "IBridge.h"
#include "NeuronBridge.h"
#include "NetworkBridge.h"

// The installation's own signal path, shared verbatim via -I../ofxLPM/coro/src.
#include "HubelDetector.h"   // Detector.h + RingBuffer.h, SpikeEvent -> HubelSpikeEvent
#include "Settings.h"

#include <atomic>
#include <string>
#include <vector>

// One sample of the membrane, as the installation carries it.
struct MembraneFrame {
    float rawA = 0.0f;   // volts, as the ADC read them
    float vA = 0.0f;     // baseline-removed, what the spiral draws
    float bA = 0.0f;     // the baseline that was removed
    float dA = 0.0f;     // draw duck, 1 at a detected reset
};

// Struck by a measured reset and decaying. Nothing free-runs.
struct Voice {
    float grainPhase = 0.0f;
    float grainAmp = 0.0f;
    float grainFreq = 400.0f;
    float tickLp = 0.0f;
};

// Where the audible tick comes from. The membrane path is the physics; the AER
// path is the chip's own report of the same spike and survives the ADC board
// being unplugged, which for an unattended installation is worth having.
enum TickSource { TICK_MEMBRANE = 0, TICK_AER = 1, TICK_BOTH = 2 };

class ofApp : public ofBaseApp {
public:
    void setup();
    void update();
    void draw();
    void exit();
    void keyPressed(int key);
    void windowResized(int w, int h);
    void audioOut(ofSoundBuffer &out);

private:
    // --- chip bring-up ----------------------------------------------------
    void advanceBoot();
    void loadBiases(const std::string &path);
    void appendLog(const std::string &msg);

    // --- membrane in, over the board's serial link ------------------------
    bool openSerial();
    void readSerial();
    void feed(float volts);

    // --- film ------------------------------------------------------------
    bool loadPcm(const std::string &path);
    void syncVideo();

    // --- settings ---------------------------------------------------------
    void applySettings();
    void reloadSettingsIfChanged();

    // --- render -----------------------------------------------------------
    void computeLayout();
    void allocatePhosphor();
    void drawSpirals();
    void drawTrajectory(const std::vector<MembraneFrame> &f, uint64_t first, size_t n);
    void drawScopePanel();
    void drawVideoPanel();
    void drawCalibration();
    void drawStartupHud();
    float warpRadius(float v) const;

    // ======================================================================
    // 1959 -- the film, and the audio clock everything else is slaved to
    // ======================================================================
    ofVideoPlayer video;
    std::vector<int16_t> pcm;            // interleaved stereo at audioFs
    size_t pcmFrames = 0;
    std::atomic<uint64_t> playHead{0};   // frames consumed by the audio thread
    uint64_t outFrame = 0;               // audio thread only
    float lastNudgeAt = 0.0f;
    std::atomic<bool> paused{false};

    MembraneDetector filmDet;                 // the cat's spikes, off the tape
    SpscRing<HubelSpikeEvent> filmSpikes;     // audio thread -> update(), one TRIG each
    std::atomic<int> filmDetected{0};
    std::atomic<int> chipFired{0};
    float lastTrigAt = 0.0f;
    float lastRouteAt = 0.0f;
    float heartbeatAt = 0.0f;

    // ======================================================================
    // 2026 -- the die: commands out over FTDI, membrane back over the ADC
    // ======================================================================
    NeuronBridge bridge;
    NetworkBridge* netBridge = nullptr;  // non-null when using remote chip
    IBridge* brg() { return netBridge ? (IBridge*)netBridge : &bridge; }

    enum BootStage { BOOT_WAIT, BOOT_BIASES, BOOT_MONITOR, BOOT_PROG,
                     BOOT_ROUTE, BOOT_RUN, BOOT_DONE };
    BootStage boot = BOOT_WAIT;
    float bootStageAt = 0.0f;
    std::atomic<bool> stimulating{false};

    ofSerial serial;
    bool serialOpen = false;
    float lastOpenAttempt = 0.0f;
    std::string lineBuf;
    std::vector<unsigned char> rx;
    uint64_t sigFrame = 0;        // samples received; the board's rate is exact
    float lastSampleAt = 0.0f;    // host time, only to notice the link went quiet
    float sigFs = 1000.0f;        // the board's sample rate, NOT the audio rate
    float measuredFs = 0.0f;      // what actually arrived, to catch a rate mismatch
    uint64_t rateWindowFirst = 0;
    float rateWindowAt = 0.0f;
    bool rateReported = false;

    MembraneDetector memDet;                  // the silicon neuron's own resets
    SpscRing<HubelSpikeEvent> memSpikes;      // update() -> audio thread
    SpscRing<HubelSpikeEvent> aerSpikes;      // update() -> audio thread
    int64_t lastAerTs = 0;
    uint64_t aerSpikeCount = 0;              // cumulative total (update())
    int aerNewThisFrame = 0;                 // new spikes this frame (update())
    uint64_t aerNewThisWindow = 0;           // accumulated over heal interval
    std::atomic<float> chipRateHz{0.0f};

    SpscRing<MembraneFrame> vizRing;          // update() -> draw()
    float drawDuck = 0.0f;

    // ======================================================================
    // sound: 1959 in one ear, 2026 in the other
    // ======================================================================
    ofSoundStream soundStream;
    bool streamOpen = false;
    float audioFs = 48000.0f;
    Voice voice;
    std::atomic<float> aFilmGain{1.0f}, aTickGain{0.7f};
    int audioMode = 2;  // 0=left only, 1=right only, 2=both
    std::atomic<float> aGrainDecay{0.0f}, aGrainRatio{4.0f};
    std::atomic<int> aTickSource{TICK_MEMBRANE};

    // ======================================================================
    // render
    // ======================================================================
    ofFbo phosphor;
    bool phosphorReady = false;
    int phosphorAttempts = 0;
    float decayPending = 0.0f;
    float lastFrameTime = 0.0f;
    double spiralPeriod = 2.0;

    ofRectangle leftRect, rightRect;

    std::vector<MembraneFrame> drain;
    glm::vec2 lastVert;
    bool havePrevVert = false;
    double prevPhase = -1.0;

    std::vector<MembraneFrame> scope;
    size_t scopePos = 0;

    // ======================================================================
    // config
    // ======================================================================
    Settings cfg;
    std::string settingsPath;
    int64_t settingsMtime = 0;
    float settingsCheckedAt = 0.0f;

    // the die, and the one route the film drives
    bool chipEnabled = true;
    std::string bridgePath = "../tools/neuron_bridge.py";
    std::string remoteChip;              // empty = local, otherwise "host:spikePort:memPort:cmdPort"
    std::string biasFile = "bias_n14_lpm_v1.biases";
    int chipNeuron = 14;
    int chipSynapse = 0;    // routed and fired
    int chipProgSyn = 14;   // also weight-programmed; -1 to skip
    int chipWeight = 15;
    bool chipExc = true;
    float trigMinIntervalMs = 50.0f;
    int trigMaxPerFrame = 3;

    // the board
    std::string devicePath = "/dev/ttyACM0";
    int deviceBaud = 115200;
    float deviceRateHz = 1000.0f;
    float followerFallMs = 20.0f;
    float voltsFullScale = 1.2f;

    // the film
    std::string videoPath = "video.mp4";
    std::string audioPath = "audio.s16";
    float videoDim = 1.0f;
    float filmGain = 1.0f;
    float tickGain = 2.0f;   // on top of outputGain * mixSpike
    float filmThreshold = 0.22f;
    float filmFollowerFallMs = 50.0f;
    float filmRefractoryMs = 20.0f;
    float filmLowpassHz = 1800.0f;

    // the window
    float splitFraction = 0.5f;

    // ======================================================================
    // self-healing
    // ======================================================================
    float pulseExtV = 1.38f;        // vepulseextp, loaded from bias file, nudged by recovery
    float excBias = 0.336f;         // JExcWn0, loaded from bias file, nudged by recovery
    int healStage = 0;              // 0=idle, 1=widening, 2=narrowing, 3=re-route
    float healCheckAt = 0.0f;       // next time we evaluate the ratio
    uint64_t lastFilmCount = 0;    // filmDetected at last check
    uint64_t healLastTrigCount = 0; // chipFired at last check
    uint64_t healLastSpikeCount = 0;// aerSpikeCount at last check
    float healRatio = 1.0f;         // last measured ratio
    int healActions = 0;            // total recovery actions taken
    int healBiasNudges = 0;         // bias nudges specifically
    std::string healStatus = "idle";

    static const int MAX_LOG = 200;
    std::deque<std::string> logLines;
    bool showCalibration = false;
    float stimStartedAt = -1.0f;   // when the film first reached the die
    float hudHoldSeconds = 25.0f;  // HUD visible this long after that, then fades
    std::string keySequence;
    float keySequenceAt = 0.0f;
};
