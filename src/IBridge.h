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

// Abstract interface shared by NeuronBridge and NetworkBridge.
// ofApp calls through this pointer; the two implementations live in NeuronBridge.h
// and NetworkBridge.h.
class IBridge : public ofThread {
public:
    virtual ~IBridge() {}

    virtual void start(const std::string& arg) = 0;
    virtual void stop() = 0;

    virtual void setBias(const std::string& name, float voltage) = 0;
    virtual void setMonitorNeuron(int n) = 0;
    virtual void sendProgram(int syn, int weight, bool exc) = 0;
    virtual void sendRoute(int syn, int neuron, bool exc) = 0;
    virtual void sendTrigger() = 0;
    virtual void sendPoisson(float hz) = 0;
    virtual void sendPoissonStop() = 0;
    virtual void sendSpikeMask(int mask) = 0;
    virtual void sendTrigRun(float hz, bool run) = 0;
    virtual void sendRecord(bool on) = 0;
    virtual void sendEcg(const std::string& cls, int neuron, int weight, bool run) = 0;
    virtual void sendSynfire(int laps) = 0;
    virtual void sendSynfireStop() = 0;
    virtual void sendReservoir() = 0;
    virtual void sendReservoirStop() = 0;
    virtual void sendStimNeuron(int id) = 0;
    virtual void sendRecurLevel(int per) = 0;
    virtual void sendCoincidence(int synA, int synB, int neuron, float dt_us) = 0;
    virtual void sendPulseMult(int mult) = 0;
    virtual std::vector<std::string> drainLog() = 0;

    virtual std::deque<SpikeEvent> getSpikes(float windowSec) = 0;
    virtual void getSpikeCounts(int out[16], float windowSec) = 0;
    virtual float getSpikeRate() const = 0;
    virtual float getSpikeLimit() const = 0;
    virtual bool isOverLimit() const = 0;
    virtual int getRecentDrops() const = 0;
    virtual int getRecentStalls() const = 0;
    virtual bool isConnected() const = 0;
};
