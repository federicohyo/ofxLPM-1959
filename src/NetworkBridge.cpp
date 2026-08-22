#include "NetworkBridge.h"
#include "ofLog.h"
#include "ofUtils.h"

#include <chrono>
#include <thread>
#include <unistd.h>
#include <fcntl.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <netdb.h>
#include <cstring>
#include <cstdio>
#include <cstdlib>
#include <cerrno>
#include <algorithm>

NetworkBridge::NetworkBridge()
    : spikeRate(0)
    , spikeLimit(0)
    , recentDrops(0)
    , recentStalls(0)
    , connected(false)
    , reconnected(false)
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
        reconnected = true;
        retryMs = RETRY_MIN_MS;
        ofLogNotice("NetworkBridge") << "connected to " << host
            << " (spike:" << spikePort << " mem:" << membranePort << " cmd:" << cmdPort << ")";
    } else {
        // Not an error: the plinth may simply not be plugged in yet. The thread
        // keeps dialling, so this is a normal state to be in, not a failure.
        disconnectSockets();
    }
}

// A link that has gone is not the same as a link that was never there: the
// buffers hold half a line, the command queue holds commands for a chip that is
// no longer listening, and the spike deque holds timestamps from a Timer0 that
// will restart at zero when the server reflashes the die on its way back up.
// All of it has to go, or it poisons the next connection.
void NetworkBridge::handleLinkLoss(const char* why) {
    ofLogNotice("NetworkBridge") << "link lost (" << why << ") -- retrying " << host;
    disconnectSockets();

    readBuffer.clear();
    memBuffer.clear();
    { std::lock_guard<std::mutex> lock(memMutex_);   memSamples_.clear(); }
    { std::lock_guard<std::mutex> lock(spikeMutex);  spikes.clear(); }
    { std::lock_guard<std::mutex> lock(cmdMutex);    std::queue<std::string>().swap(cmdQueue); }

    spikeRate = 0;
    recentDrops = 0;
    recentStalls = 0;
    retryMs = RETRY_MIN_MS;
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

    retryMs = RETRY_MIN_MS;
    connectSockets();
    // The thread runs whether or not that worked. It is the thing that dials,
    // so the app comes up fine with the plinth switched off and joins the die
    // whenever it appears.
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

// Pull everything the socket has into buf. Returns false once the peer is gone
// -- read() returning 0 is the only way TCP says so, and it has to be told apart
// from EAGAIN, which is just a non-blocking socket saying "nothing right now".
// The old single-read version could not distinguish the two, which is why a
// dead server used to leave the app sitting there quietly forever.
static bool drainFd(int fd, std::string& buf) {
    if (fd < 0) return false;
    char tmp[4096];
    for (;;) {
        const ssize_t n = read(fd, tmp, sizeof(tmp));
        if (n > 0) { buf.append(tmp, (size_t)n); continue; }
        if (n == 0) return false;                                  // peer closed
        if (errno == EINTR) continue;
        if (errno == EAGAIN || errno == EWOULDBLOCK) return true;  // simply idle
        return false;                                              // a real error
    }
}

static bool popLine(std::string& buf, std::string& line) {
    const size_t pos = buf.find('\n');
    if (pos == std::string::npos) return false;
    line = buf.substr(0, pos);
    if (!line.empty() && line.back() == '\r') line.pop_back();
    buf.erase(0, pos + 1);
    return true;
}

// Sleep, but in slices, so stop() does not have to wait out a whole backoff.
static void sleepUnlessStopping(const ofThread& self, int ms) {
    while (ms > 0 && self.isThreadRunning()) {
        const int slice = std::min(ms, 100);
        ofSleepMillis(slice);
        ms -= slice;
    }
}

void NetworkBridge::threadedFunction(){
    while(isThreadRunning()){
        // --- dial, if there is nothing to read from --------------------------
        if (!connected) {
            sleepUnlessStopping(*this, retryMs);
            if (!isThreadRunning()) break;
            connectSockets();
            if (!connected) {
                retryMs = std::min(retryMs * 2, (int)RETRY_MAX_MS);
                continue;
            }
            continue;   // read on the next pass, with clean buffers
        }

        // --- the die's own report --------------------------------------------
        if (!drainFd(spikeFd, readBuffer)) { handleLinkLoss("spike port closed"); continue; }

        std::string line;
        while (popLine(readBuffer, line)) {
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

        // --- the membrane, forwarded to the caller via drainMembrane ---------
        if (!drainFd(membraneFd, memBuffer)) { handleLinkLoss("membrane port closed"); continue; }

        std::string memLine;
        while (popLine(memBuffer, memLine)) {
            std::lock_guard<std::mutex> lock(memMutex_);
            memSamples_.push_back(memLine);
            if (memSamples_.size() > 10000) memSamples_.erase(memSamples_.begin(), memSamples_.begin() + 5000);
        }

        // --- commands out ----------------------------------------------------
        // MSG_NOSIGNAL, not write(): writing to a socket the far end has closed
        // raises SIGPIPE, whose default action is to kill the process. The
        // whole point here is to survive that moment, not to die in it.
        bool writeFailed = false;
        {
            std::lock_guard<std::mutex> lock(cmdMutex);
            while (!cmdQueue.empty()) {
                const std::string& cmd = cmdQueue.front();
                if (cmdFd >= 0) {
                    const ssize_t n = send(cmdFd, cmd.c_str(), cmd.size(), MSG_NOSIGNAL);
                    if (n < 0 && errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR) {
                        writeFailed = true;
                        break;
                    }
                }
                cmdQueue.pop();
            }
        }
        if (writeFailed) { handleLinkLoss("command port closed"); continue; }

        std::this_thread::sleep_for(std::chrono::microseconds(100));
    }
}
