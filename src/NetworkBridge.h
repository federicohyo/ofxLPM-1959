#pragma once

#include "IBridge.h"
#include <string>
#include <vector>
#include <deque>
#include <queue>
#include <mutex>
#include <atomic>

class NetworkBridge : public IBridge {
public:
    NetworkBridge();
    ~NetworkBridge() override;

    void start(const std::string& arg) override;
    void start(const std::string& host, int spikePort, int membranePort, int cmdPort);
    void stop() override;

    // Commands (main thread, thread-safe) — same API as NeuronBridge
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
    std::vector<std::string> drainMembrane();
    float getSpikeRate() const override { return spikeRate; }
    float getSpikeLimit() const override { return spikeLimit; }
    bool isOverLimit() const override { return recentDrops > 0 || recentStalls > 0; }
    int getRecentDrops() const override { return recentDrops; }
    int getRecentStalls() const override { return recentStalls; }
    bool isConnected() const override { return connected; }

protected:
    void threadedFunction() override;

private:
    void connectSockets();
    void disconnectSockets();

    std::string host;
    int spikePort = 9100;
    int membranePort = 9101;
    int cmdPort = 9102;

    int spikeFd = -1;
    int membraneFd = -1;
    int cmdFd = -1;

    std::string readBuffer;
    std::string memBuffer;

    std::deque<SpikeEvent> spikes;
    std::mutex spikeMutex;
    static const size_t MAX_SPIKES = 100000;

    std::queue<std::string> cmdQueue;
    std::mutex cmdMutex;

    std::deque<std::string> logQueue_;
    std::mutex logMutex_;

    std::vector<std::string> memSamples_;
    std::mutex memMutex_;

    std::atomic<float> spikeRate;
    std::atomic<float> spikeLimit;
    std::atomic<int> recentDrops;
    std::atomic<int> recentStalls;
    std::atomic<bool> connected;

    std::string memLineBuf;
};
