#pragma once

#include "IBridge.h"
#include <deque>
#include <queue>
#include <string>
#include <vector>
#include <atomic>
#include <mutex>

class NeuronBridge : public IBridge {
public:
    NeuronBridge();
    ~NeuronBridge() override;

    void start(const std::string& bridgeScript) override;
    void stop() override;

    // Commands (main thread, thread-safe)
    void setBias(const std::string& name, float voltage) override;
    void setMonitorNeuron(int n) override;
    void sendProgram(int syn, int weight, bool exc) override;
    void sendRoute(int syn, int neuron, bool exc) override;
    void sendTrigger() override;
    void sendPoisson(float hz) override;
    void sendPoissonStop() override;
    void sendSpikeMask(int mask) override;
    void sendTrigRun(float hz, bool run) override;
    void sendRecord(bool on) override;
    void sendEcg(const std::string& cls, int neuron, int weight, bool run) override;
    void sendSynfire(int laps) override;
    void sendSynfireStop() override;
    void sendReservoir() override;
    void sendReservoirStop() override;
    void sendStimNeuron(int id) override;
    void sendRecurLevel(int per) override;
    void sendCoincidence(int synA, int synB, int neuron, float dt_us) override;
    void sendPulseMult(int mult) override;
    std::vector<std::string> drainLog() override;

    // Data access (main thread)
    std::deque<SpikeEvent> getSpikes(float windowSec) override;
    void getSpikeCounts(int out[16], float windowSec) override;
    float getSpikeRate() const override { return spikeRate; }
    float getSpikeLimit() const override { return spikeLimit; }
    bool isOverLimit() const override { return recentDrops > 0 || recentStalls > 0; }
    int getRecentDrops() const override { return recentDrops; }
    int getRecentStalls() const override { return recentStalls; }
    bool isConnected() const override { return connected; }

protected:
    void threadedFunction() override;

private:
    // Read a line from the bridge stdout (non-blocking). Returns true if got a line.
    bool readLine(std::string& line);

    // Pipe file descriptors
    int pipeToChild[2];
    int pipeFromChild[2];
    pid_t childPid;

    std::string readBuffer;

    std::deque<SpikeEvent> spikes;
    std::mutex spikeMutex;
    static const size_t MAX_SPIKES = 100000;

    std::queue<std::string> cmdQueue;
    std::mutex cmdMutex;

    std::deque<std::string> logQueue_;
    std::mutex logMutex_;

    std::atomic<float> spikeRate;
    std::atomic<float> spikeLimit;
    std::atomic<int> recentDrops;
    std::atomic<int> recentStalls;
    std::atomic<bool> connected;
};
