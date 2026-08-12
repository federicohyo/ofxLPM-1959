#pragma once

#include "ofThread.h"
#include <deque>
#include <queue>
#include <string>
#include <vector>
#include <atomic>
#include <mutex>

struct SpikeEvent {
    int neuron;
    int64_t timestamp_us;
};

class NeuronBridge : public ofThread {
public:
    NeuronBridge();
    ~NeuronBridge();

    void start(const std::string& bridgeScript);
    void stop();

    // Commands (main thread, thread-safe)
    void setBias(const std::string& name, float voltage);
    void setMonitorNeuron(int n);
    void sendProgram(int syn, int weight, bool exc);
    void sendRoute(int syn, int neuron, bool exc);
    void sendTrigger();
    void sendPoisson(float hz);   // start on-chip Poisson generation at mean rate hz
    void sendPoissonStop();
    void sendSpikeMask(int mask);  // bit i set = chip streams neuron i
    void sendTrigRun(float hz, bool run);  // regular spike train, paced by the bridge
    void sendRecord(bool on);              // dump spikes to a txt file (id, ticks, us)
    void sendEcg(const std::string& cls, int neuron, int weight, bool run);  // loop one ECG beat
    void sendSynfire(int laps);   // start RISC-V synfire chain for N laps
    void sendSynfireStop();
    void sendReservoir();         // program W_rec + continuously loop one delta ECG trace
    void sendReservoirStop();
    void sendStimNeuron(int id);  // load neuron id's bias + stimulate only it with the ECG delta
    void sendRecurLevel(int per); // regenerate W_rec at `per` connections/neuron
    void sendCoincidence(int synA, int synB, int neuron, float dt_us);  // one coincidence trial
    std::vector<std::string> drainLog();

    // Data access (main thread)
    std::deque<SpikeEvent> getSpikes(float windowSec);
    void getSpikeCounts(int out[16], float windowSec);
    float getSpikeRate() const { return spikeRate; }
    // Aggregate spikes/s at which the UART saturates (baud/40). 0 = not reported.
    float getSpikeLimit() const { return spikeLimit; }
    // The chip's OWN reports for the last RATE window. Thresholding on spikeRate
    // cannot work: dropping is precisely what holds that rate below the ceiling,
    // so a saturated link looks like a slow array. Only the firmware knows.
    //   stalls = the flush waited on the UART FIFO -> link saturated, array is
    //            back-pressured. Fires BEFORE the ring ever overflows.
    //   drops  = the ring overflowed -> spikes were lost outright.
    bool isOverLimit() const { return recentDrops > 0 || recentStalls > 0; }
    int getRecentDrops() const { return recentDrops; }
    int getRecentStalls() const { return recentStalls; }
    bool isConnected() const { return connected; }

protected:
    void threadedFunction() override;

private:
    // Read a line from the bridge stdout (non-blocking). Returns true if got a line.
    bool readLine(std::string& line);

    // Pipe file descriptors
    int pipeToChild[2];    // parent writes -> child stdin
    int pipeFromChild[2];  // child stdout -> parent reads
    pid_t childPid;

    // Read buffer for non-blocking reads
    std::string readBuffer;

    // Spike data
    std::deque<SpikeEvent> spikes;
    std::mutex spikeMutex;
    static const size_t MAX_SPIKES = 100000;

    // Command queue
    std::queue<std::string> cmdQueue;
    std::mutex cmdMutex;

    // Log queue
    std::deque<std::string> logQueue_;
    std::mutex logMutex_;

    // State
    std::atomic<float> spikeRate;
    std::atomic<float> spikeLimit;
    std::atomic<int> recentDrops;
    std::atomic<int> recentStalls;
    std::atomic<bool> connected;
};
