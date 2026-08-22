#include "NetworkBridge.h"
#include "ofLog.h"
#include "ofUtils.h"

#include <unistd.h>
#include <fcntl.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <netdb.h>
#include <cstring>
#include <cstdio>
#include <cstdlib>
#include <algorithm>

NetworkBridge::NetworkBridge()
    : spikeRate(0)
    , spikeLimit(0)
    , recentDrops(0)
    , recentStalls(0)
    , connected(false)
{
}

void NetworkBridge::start(const std::string& arg) {
    // IBridge virtual stub — not used; the 4-arg overload is the real one.
}

NetworkBridge::~NetworkBridge(){
    stop();
}

static int tcp_connect(const std::string& host, int port) {
    struct addrinfo hints, *res = nullptr;
    memset(&hints, 0, sizeof(hints));
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;

    char portStr[16];
    snprintf(portStr, sizeof(portStr), "%d", port);

    int err = getaddrinfo(host.c_str(), portStr, &hints, &res);
    if (err != 0 || !res) return -1;

    int fd = -1;
    for (struct addrinfo* rp = res; rp; rp = rp->ai_next) {
        fd = socket(rp->ai_family, rp->ai_socktype, rp->ai_protocol);
        if (fd < 0) continue;
        int flag = 1;
        setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &flag, sizeof(flag));
        if (connect(fd, rp->ai_addr, rp->ai_addrlen) == 0) break;
        close(fd);
        fd = -1;
    }
    freeaddrinfo(res);

    if (fd >= 0) {
        int flags = fcntl(fd, F_GETFL, 0);
        fcntl(fd, F_SETFL, flags | O_NONBLOCK);
    }
    return fd;
}

void NetworkBridge::connectSockets() {
    spikeFd = tcp_connect(host, spikePort);
    membraneFd = tcp_connect(host, membranePort);
    cmdFd = tcp_connect(host, cmdPort);

    if (spikeFd >= 0 && membraneFd >= 0 && cmdFd >= 0) {
        connected = true;
        ofLogNotice("NetworkBridge") << "connected to " << host
            << " (spike:" << spikePort << " mem:" << membranePort << " cmd:" << cmdPort << ")";
    } else {
        ofLogError("NetworkBridge") << "connection failed to " << host;
        disconnectSockets();
    }
}

void NetworkBridge::disconnectSockets() {
    if (spikeFd >= 0) { close(spikeFd); spikeFd = -1; }
    if (membraneFd >= 0) { close(membraneFd); membraneFd = -1; }
    if (cmdFd >= 0) { close(cmdFd); cmdFd = -1; }
    connected = false;
}

void NetworkBridge::start(const std::string& host_, int sPort, int mPort, int cPort) {
    host = host_;
    spikePort = sPort;
    membranePort = mPort;
    cmdPort = cPort;

    connectSockets();
    if (!connected) return;
    startThread();
}

void NetworkBridge::stop() {
    if (isThreadRunning()) {
        stopThread();
        waitForThread(true, 3000);
    }
    disconnectSockets();
    ofLogNotice("NetworkBridge") << "stopped";
}

// --- command helpers --------------------------------------------------------

void NetworkBridge::setBias(const std::string& name, float voltage) {
    char buf[128];
    snprintf(buf, sizeof(buf), "B %s %.4f\n", name.c_str(), voltage);
    std::lock_guard<std::mutex> lock(cmdMutex);
    cmdQueue.push(std::string(buf));
}

void NetworkBridge::setMonitorNeuron(int n) {
    char buf[32];
    snprintf(buf, sizeof(buf), "M %d\n", n);
    std::lock_guard<std::mutex> lock(cmdMutex);
    cmdQueue.push(std::string(buf));
}

void NetworkBridge::sendProgram(int syn, int weight, bool exc) {
    char buf[64];
    snprintf(buf, sizeof(buf), "P %d %d %s\n", syn, weight, exc ? "exc" : "nexc");
    std::lock_guard<std::mutex> lock(cmdMutex);
    cmdQueue.push(std::string(buf));
}

void NetworkBridge::sendRoute(int syn, int neuron, bool exc) {
    char buf[64];
    snprintf(buf, sizeof(buf), "S %d %d %s\n", syn, neuron, exc ? "exc" : "nexc");
    std::lock_guard<std::mutex> lock(cmdMutex);
    cmdQueue.push(std::string(buf));
}

void NetworkBridge::sendTrigger() {
    std::lock_guard<std::mutex> lock(cmdMutex);
    cmdQueue.push("T\n");
}

void NetworkBridge::sendPoisson(float hz) {
    char buf[48];
    snprintf(buf, sizeof(buf), "POISSON %.3f\n", hz);
    std::lock_guard<std::mutex> lock(cmdMutex);
    cmdQueue.push(std::string(buf));
}

void NetworkBridge::sendPoissonStop() {
    std::lock_guard<std::mutex> lock(cmdMutex);
    cmdQueue.push("POISSONSTOP\n");
}

void NetworkBridge::sendSpikeMask(int mask) {
    std::lock_guard<std::mutex> lock(cmdMutex);
    cmdQueue.push("MASK " + std::to_string(mask & 0xFFFF) + "\n");
}

void NetworkBridge::sendTrigRun(float hz, bool run) {
    char buf[48];
    if (run) snprintf(buf, sizeof(buf), "TRIGRUN %.3f\n", hz);
    else     snprintf(buf, sizeof(buf), "TRIGSTOP\n");
    std::lock_guard<std::mutex> lock(cmdMutex);
    cmdQueue.push(std::string(buf));
}

void NetworkBridge::sendRecord(bool on) {
    std::lock_guard<std::mutex> lock(cmdMutex);
    cmdQueue.push(on ? "RECORD\n" : "RECORDSTOP\n");
}

void NetworkBridge::sendEcg(const std::string& cls, int neuron, int weight, bool run) {
    char buf[64];
    if (run) snprintf(buf, sizeof(buf), "ECGRUN %s %d %d\n", cls.c_str(), neuron, weight);
    else     snprintf(buf, sizeof(buf), "ECGSTOP\n");
    std::lock_guard<std::mutex> lock(cmdMutex);
    cmdQueue.push(std::string(buf));
}

void NetworkBridge::sendSynfire(int laps) {
    char buf[48];
    snprintf(buf, sizeof(buf), "SYNFIRE %d\n", laps);
    std::lock_guard<std::mutex> lock(cmdMutex);
    cmdQueue.push(std::string(buf));
}

void NetworkBridge::sendSynfireStop() {
    std::lock_guard<std::mutex> lock(cmdMutex);
    cmdQueue.push("SYNFIRESTOP\n");
}

void NetworkBridge::sendReservoir() {
    std::lock_guard<std::mutex> lock(cmdMutex);
    cmdQueue.push("RESERVOIR\n");
}

void NetworkBridge::sendReservoirStop() {
    std::lock_guard<std::mutex> lock(cmdMutex);
    cmdQueue.push("RESERVOIRSTOP\n");
}

void NetworkBridge::sendStimNeuron(int id) {
    char buf[48];
    snprintf(buf, sizeof(buf), "STIMNEURON %d\n", id);
    std::lock_guard<std::mutex> lock(cmdMutex);
    cmdQueue.push(std::string(buf));
}

void NetworkBridge::sendRecurLevel(int per) {
    char buf[48];
    snprintf(buf, sizeof(buf), "RECURLEVEL %d\n", per);
    std::lock_guard<std::mutex> lock(cmdMutex);
    cmdQueue.push(std::string(buf));
}

void NetworkBridge::sendCoincidence(int synA, int synB, int neuron, float dt_us) {
    char buf[64];
    snprintf(buf, sizeof(buf), "COINC %d %d %d %.1f\n", synA, synB, neuron, dt_us);
    std::lock_guard<std::mutex> lock(cmdMutex);
    cmdQueue.push(std::string(buf));
}

void NetworkBridge::sendPulseMult(int mult) {
    char buf[32];
    const int m = std::max(1, std::min(63, mult));
    snprintf(buf, sizeof(buf), "PULSEMULT %d\n", m);
    std::lock_guard<std::mutex> lock(cmdMutex);
    cmdQueue.push(std::string(buf));
}

std::vector<std::string> NetworkBridge::drainMembrane() {
    std::lock_guard<std::mutex> lock(memMutex_);
    std::vector<std::string> out = std::move(memSamples_);
    memSamples_.clear();
    return out;
}

std::vector<std::string> NetworkBridge::drainLog() {
    std::lock_guard<std::mutex> lock(logMutex_);
    std::vector<std::string> out(logQueue_.begin(), logQueue_.end());
    logQueue_.clear();
    return out;
}

std::deque<SpikeEvent> NetworkBridge::getSpikes(float windowSec){
    std::lock_guard<std::mutex> lock(spikeMutex);
    if(spikes.empty()) return {};

    int64_t latestTs = spikes.back().timestamp_us;
    int64_t cutoff = latestTs - (int64_t)(windowSec * 1e6);

    std::deque<SpikeEvent> result;
    for(auto it = spikes.rbegin(); it != spikes.rend(); ++it){
        if(it->timestamp_us < cutoff) break;
        result.push_front(*it);
    }
    return result;
}

void NetworkBridge::getSpikeCounts(int out[16], float windowSec){
    std::lock_guard<std::mutex> lock(spikeMutex);
    memset(out, 0, 16 * sizeof(int));
    if(spikes.empty()) return;

    int64_t latestTs = spikes.back().timestamp_us;
    int64_t cutoff = latestTs - (int64_t)(windowSec * 1e6);

    for(auto it = spikes.rbegin(); it != spikes.rend(); ++it){
        if(it->timestamp_us < cutoff) break;
        if(it->neuron >= 0 && it->neuron < 16)
            out[it->neuron]++;
    }
}

// --- threaded function: read from TCP sockets, write commands ---------------

static bool readFd(int fd, std::string& buf, std::string& line) {
    char tmp[4096];
    ssize_t n = read(fd, tmp, sizeof(tmp));
    if (n > 0) {
        buf.append(tmp, n);
    } else if (n == 0) {
        return false; // disconnected
    }
    size_t pos = buf.find('\n');
    if (pos != std::string::npos) {
        line = buf.substr(0, pos);
        if (!line.empty() && line.back() == '\r') line.pop_back();
        buf.erase(0, pos + 1);
        return true;
    }
    return false;
}

void NetworkBridge::threadedFunction(){
    while(isThreadRunning()){
        if (!connected) {
            ofSleepMillis(100);
            continue;
        }

        // Read spike lines
        std::string line;
        while (readFd(spikeFd, readBuffer, line)) {
            if (line.empty()) continue;

            if (line == "READY") {
                // already connected
            }
            else if (line.size() >= 2 && line[0] == 'S' && line[1] == ' ') {
                SpikeEvent ev;
                long long ts;
                if (sscanf(line.c_str(), "S %d %lld", &ev.neuron, &ts) == 2) {
                    ev.timestamp_us = ts;
                    std::lock_guard<std::mutex> lock(spikeMutex);
                    spikes.push_back(ev);
                    while (spikes.size() > MAX_SPIKES) spikes.pop_front();
                }
            }
            else if (line.substr(0, 5) == "RATE ") {
                float rate = 0;
                sscanf(line.c_str(), "RATE %f", &rate);
                spikeRate = rate;
            }
            else if (line.substr(0, 6) == "DROPS ") {
                int d = 0;
                sscanf(line.c_str(), "DROPS %d", &d);
                recentDrops = d;
            }
            else if (line.substr(0, 7) == "STALLS ") {
                int st = 0;
                sscanf(line.c_str(), "STALLS %d", &st);
                recentStalls = st;
            }
            else if (line.substr(0, 6) == "LIMIT ") {
                float lim = 0;
                if (sscanf(line.c_str(), "LIMIT %f", &lim) == 1 && lim > 0) {
                    spikeLimit = lim;
                }
            }
            else if (line.substr(0, 4) == "BOK " ||
                     line.substr(0, 4) == "MOK " ||
                     line.substr(0, 4) == "POK " ||
                     line.substr(0, 4) == "SOK " ||
                     line.substr(0, 7) == "RECORDO" ||
                     line.substr(0, 10) == "RECORDSTOP" ||
                     line.substr(0, 7) == "RESLOOP") {
                std::lock_guard<std::mutex> lock(logMutex_);
                logQueue_.push_back(line);
                if (logQueue_.size() > 500) logQueue_.pop_front();
            }
            else if (line.substr(0, 5) == "ERROR") {
                ofLogError("NetworkBridge") << line;
                std::lock_guard<std::mutex> lock(logMutex_);
                logQueue_.push_back("ERR: " + line.substr(6));
                if (logQueue_.size() > 500) logQueue_.pop_front();
            }
        }

        // Read membrane lines (forwarded to caller via drainMembrane)
        std::string memLine;
        while (readFd(membraneFd, memBuffer, memLine)) {
            std::lock_guard<std::mutex> lock(memMutex_);
            memSamples_.push_back(memLine);
            if (memSamples_.size() > 10000) memSamples_.erase(memSamples_.begin(), memSamples_.begin() + 5000);
        }

        // Flush command queue to cmd socket
        {
            std::lock_guard<std::mutex> lock(cmdMutex);
            while (!cmdQueue.empty()) {
                const std::string& cmd = cmdQueue.front();
                if (cmdFd >= 0) {
                    (void)write(cmdFd, cmd.c_str(), cmd.size());
                }
                cmdQueue.pop();
            }
        }

        ofSleepMillis(1);
    }
}
