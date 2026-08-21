#include "NeuronBridge.h"
#include "ofLog.h"
#include "ofUtils.h"

#include <unistd.h>
#include <signal.h>
#include <sys/wait.h>
#include <fcntl.h>
#include <cstring>
#include <cstdio>
#include <cstdlib>
#include <algorithm>
#include <sstream>

NeuronBridge::NeuronBridge()
    : childPid(-1)
    , spikeRate(0)
    , spikeLimit(0)
    , recentDrops(0)
    , recentStalls(0)
    , connected(false)
{
    pipeToChild[0] = pipeToChild[1] = -1;
    pipeFromChild[0] = pipeFromChild[1] = -1;
}

NeuronBridge::~NeuronBridge(){
    stop();
}

void NeuronBridge::start(const std::string& bridgeScript){
    if(childPid > 0) return;

    if(pipe(pipeToChild) < 0 || pipe(pipeFromChild) < 0){
        ofLogError("NeuronBridge") << "pipe() failed: " << strerror(errno);
        return;
    }

    childPid = fork();
    if(childPid < 0){
        ofLogError("NeuronBridge") << "fork() failed: " << strerror(errno);
        return;
    }

    if(childPid == 0){
        // Child process
        close(pipeToChild[1]);
        close(pipeFromChild[0]);

        dup2(pipeToChild[0], STDIN_FILENO);
        dup2(pipeFromChild[1], STDOUT_FILENO);

        close(pipeToChild[0]);
        close(pipeFromChild[1]);

        // Find project root (parent of ofxCaravanViewer)
        std::string scriptDir = bridgeScript;
        size_t lastSlash = scriptDir.rfind('/');
        std::string dir = (lastSlash != std::string::npos) ? scriptDir.substr(0, lastSlash) : ".";
        chdir(dir.c_str());

        std::string scriptName = (lastSlash != std::string::npos) ? scriptDir.substr(lastSlash + 1) : bridgeScript;

        execlp("python3", "python3", "-u", scriptName.c_str(), (char*)NULL);
        // If execlp fails
        _exit(127);
    }

    // Parent process
    close(pipeToChild[0]);
    close(pipeFromChild[1]);

    // Set read pipe non-blocking
    int flags = fcntl(pipeFromChild[0], F_GETFL, 0);
    fcntl(pipeFromChild[0], F_SETFL, flags | O_NONBLOCK);

    ofLogNotice("NeuronBridge") << "Bridge process started, pid=" << childPid;

    startThread();
}

void NeuronBridge::stop(){
    if(childPid <= 0) return;

    // Send QUIT command
    if(pipeToChild[1] >= 0){
        const char* quit = "QUIT\n";
        (void)write(pipeToChild[1], quit, strlen(quit));
    }

    // Stop thread
    if(isThreadRunning()){
        stopThread();
        waitForThread(true, 3000);
    }

    // Close write pipe first so child gets EOF on stdin
    if(pipeToChild[1] >= 0) { close(pipeToChild[1]); pipeToChild[1] = -1; }

    // Wait for child, escalate: SIGTERM -> SIGKILL
    int status;
    pid_t ret = waitpid(childPid, &status, WNOHANG);
    if(ret == 0){
        kill(childPid, SIGTERM);
        usleep(500000);
        ret = waitpid(childPid, &status, WNOHANG);
    }
    if(ret == 0){
        ofLogWarning("NeuronBridge") << "Bridge not responding to SIGTERM, sending SIGKILL";
        kill(childPid, SIGKILL);
        waitpid(childPid, &status, 0);
    }

    // Close remaining pipe
    if(pipeFromChild[0] >= 0) { close(pipeFromChild[0]); pipeFromChild[0] = -1; }

    childPid = -1;
    connected = false;

    ofLogNotice("NeuronBridge") << "Bridge stopped";
}

void NeuronBridge::setBias(const std::string& name, float voltage){
    char buf[128];
    snprintf(buf, sizeof(buf), "B %s %.4f\n", name.c_str(), voltage);
    std::lock_guard<std::mutex> lock(cmdMutex);
    cmdQueue.push(std::string(buf));
}

void NeuronBridge::setMonitorNeuron(int n){
    char buf[32];
    snprintf(buf, sizeof(buf), "M %d\n", n);
    std::lock_guard<std::mutex> lock(cmdMutex);
    cmdQueue.push(std::string(buf));
}

void NeuronBridge::sendProgram(int syn, int weight, bool exc) {
    char buf[64];
    snprintf(buf, sizeof(buf), "P %d %d %s\n", syn, weight, exc ? "exc" : "nexc");
    std::lock_guard<std::mutex> lock(cmdMutex);
    cmdQueue.push(std::string(buf));
}

void NeuronBridge::sendRoute(int syn, int neuron, bool exc) {
    char buf[64];
    snprintf(buf, sizeof(buf), "S %d %d %s\n", syn, neuron, exc ? "exc" : "nexc");
    std::lock_guard<std::mutex> lock(cmdMutex);
    cmdQueue.push(std::string(buf));
}

void NeuronBridge::sendTrigger() {
    std::lock_guard<std::mutex> lock(cmdMutex);
    cmdQueue.push("T\n");
}

void NeuronBridge::sendPoisson(float hz) {
    char buf[48];
    snprintf(buf, sizeof(buf), "POISSON %.3f\n", hz);
    std::lock_guard<std::mutex> lock(cmdMutex);
    cmdQueue.push(std::string(buf));
}

void NeuronBridge::sendPoissonStop() {
    std::lock_guard<std::mutex> lock(cmdMutex);
    cmdQueue.push("POISSONSTOP\n");
}

void NeuronBridge::sendSpikeMask(int mask) {
    std::lock_guard<std::mutex> lock(cmdMutex);
    cmdQueue.push("MASK " + std::to_string(mask & 0xFFFF) + "\n");
}

void NeuronBridge::sendTrigRun(float hz, bool run) {
    char buf[48];
    if (run) snprintf(buf, sizeof(buf), "TRIGRUN %.3f\n", hz);
    else     snprintf(buf, sizeof(buf), "TRIGSTOP\n");
    std::lock_guard<std::mutex> lock(cmdMutex);
    cmdQueue.push(std::string(buf));
}

void NeuronBridge::sendRecord(bool on) {
    std::lock_guard<std::mutex> lock(cmdMutex);
    cmdQueue.push(on ? "RECORD\n" : "RECORDSTOP\n");
}

void NeuronBridge::sendEcg(const std::string& cls, int neuron, int weight, bool run) {
    char buf[64];
    if (run) snprintf(buf, sizeof(buf), "ECGRUN %s %d %d\n", cls.c_str(), neuron, weight);
    else     snprintf(buf, sizeof(buf), "ECGSTOP\n");
    std::lock_guard<std::mutex> lock(cmdMutex);
    cmdQueue.push(std::string(buf));
}

void NeuronBridge::sendSynfire(int laps) {
    char buf[48];
    snprintf(buf, sizeof(buf), "SYNFIRE %d\n", laps);
    std::lock_guard<std::mutex> lock(cmdMutex);
    cmdQueue.push(std::string(buf));
}

void NeuronBridge::sendSynfireStop() {
    std::lock_guard<std::mutex> lock(cmdMutex);
    cmdQueue.push("SYNFIRESTOP\n");
}

void NeuronBridge::sendReservoir() {
    std::lock_guard<std::mutex> lock(cmdMutex);
    cmdQueue.push("RESERVOIR\n");
}

void NeuronBridge::sendReservoirStop() {
    std::lock_guard<std::mutex> lock(cmdMutex);
    cmdQueue.push("RESERVOIRSTOP\n");
}

void NeuronBridge::sendStimNeuron(int id) {
    char buf[48];
    snprintf(buf, sizeof(buf), "STIMNEURON %d\n", id);
    std::lock_guard<std::mutex> lock(cmdMutex);
    cmdQueue.push(std::string(buf));
}

void NeuronBridge::sendRecurLevel(int per) {
    char buf[48];
    snprintf(buf, sizeof(buf), "RECURLEVEL %d\n", per);
    std::lock_guard<std::mutex> lock(cmdMutex);
    cmdQueue.push(std::string(buf));
}

void NeuronBridge::sendCoincidence(int synA, int synB, int neuron, float dt_us) {
    char buf[64];
    snprintf(buf, sizeof(buf), "COINC %d %d %d %.1f\n", synA, synB, neuron, dt_us);
    std::lock_guard<std::mutex> lock(cmdMutex);
    cmdQueue.push(std::string(buf));
}

void NeuronBridge::sendPulseMult(int mult) {
    char buf[32];
    const int m = std::max(1, std::min(63, mult));
    snprintf(buf, sizeof(buf), "PULSEMULT %d\n", m);
    std::lock_guard<std::mutex> lock(cmdMutex);
    cmdQueue.push(std::string(buf));
}

std::vector<std::string> NeuronBridge::drainLog() {
    std::lock_guard<std::mutex> lock(logMutex_);
    std::vector<std::string> out(logQueue_.begin(), logQueue_.end());
    logQueue_.clear();
    return out;
}

std::deque<SpikeEvent> NeuronBridge::getSpikes(float windowSec){
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

void NeuronBridge::getSpikeCounts(int out[16], float windowSec){
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

bool NeuronBridge::readLine(std::string& line){
    // Try to read more data
    char buf[4096];
    ssize_t n = read(pipeFromChild[0], buf, sizeof(buf));
    if(n > 0){
        readBuffer.append(buf, n);
    }

    // Check for complete line
    size_t pos = readBuffer.find('\n');
    if(pos != std::string::npos){
        line = readBuffer.substr(0, pos);
        // Strip \r
        if(!line.empty() && line.back() == '\r')
            line.pop_back();
        readBuffer.erase(0, pos + 1);
        return true;
    }
    return false;
}

void NeuronBridge::threadedFunction(){
    while(isThreadRunning()){
        // Read lines from bridge
        std::string line;
        while(readLine(line)){
            if(line.empty()) continue;

            if(line == "READY"){
                connected = true;
                ofLogNotice("NeuronBridge") << "Bridge ready";
            }
            else if(line.size() >= 2 && line[0] == 'S' && line[1] == ' '){
                // Spike event: S <neuron> <timestamp_us>
                SpikeEvent ev;
                long long ts;
                if(sscanf(line.c_str(), "S %d %lld", &ev.neuron, &ts) == 2){
                    ev.timestamp_us = ts;
                    std::lock_guard<std::mutex> lock(spikeMutex);
                    spikes.push_back(ev);
                    while(spikes.size() > MAX_SPIKES){
                        spikes.pop_front();
                    }
                }
            }
            else if(line.substr(0, 5) == "RATE "){
                float rate = 0;
                sscanf(line.c_str(), "RATE %f", &rate);
                spikeRate = rate;
            }
            else if(line.substr(0, 6) == "DROPS "){
                int d = 0;
                sscanf(line.c_str(), "DROPS %d", &d);
                recentDrops = d;
            }
            else if(line.substr(0, 7) == "STALLS "){
                int st = 0;
                sscanf(line.c_str(), "STALLS %d", &st);
                recentStalls = st;
            }
            else if(line.substr(0, 6) == "LIMIT "){
                float lim = 0;
                if(sscanf(line.c_str(), "LIMIT %f", &lim) == 1 && lim > 0){
                    spikeLimit = lim;
                    ofLogNotice("NeuronBridge") << "UART spike ceiling: " << lim << " spk/s";
                }
            }
            else if(line.substr(0, 4) == "BOK ") {
                std::lock_guard<std::mutex> lock(logMutex_);
                logQueue_.push_back("Bias OK: " + line.substr(4));
                if (logQueue_.size() > 500) logQueue_.pop_front();
            }
            else if(line.substr(0, 7) == "RECORDO" || line.substr(0, 10) == "RECORDSTOP") {
                std::lock_guard<std::mutex> lock(logMutex_);
                logQueue_.push_back(line);           // shows the file path / spike count
                if (logQueue_.size() > 500) logQueue_.pop_front();
            }
            else if(line.substr(0, 4) == "MOK ") {
                std::lock_guard<std::mutex> lock(logMutex_);
                logQueue_.push_back("Monitor → N" + line.substr(4));
                if (logQueue_.size() > 500) logQueue_.pop_front();
            }
            else if(line.substr(0, 4) == "POK ") {
                std::lock_guard<std::mutex> lock(logMutex_);
                logQueue_.push_back("Prog OK: " + line.substr(4));
                if (logQueue_.size() > 500) logQueue_.pop_front();
            }
            else if(line.substr(0, 4) == "SOK ") {
                std::lock_guard<std::mutex> lock(logMutex_);
                logQueue_.push_back("Route OK: " + line.substr(4));
                if (logQueue_.size() > 500) logQueue_.pop_front();
            }
            else if(line.substr(0, 4) == "TOK ") {
                // Trigger ACK — skip, too frequent
            }
            else if(line.substr(0, 5) == "ERROR") {
                ofLogError("NeuronBridge") << line;
                std::lock_guard<std::mutex> lock(logMutex_);
                logQueue_.push_back("ERR: " + line.substr(6));
                if (logQueue_.size() > 500) logQueue_.pop_front();
            }
            else if(line.substr(0, 7) == "RESLOOP") {
                std::lock_guard<std::mutex> lock(logMutex_);
                logQueue_.push_back("RESLOOP");   // ECG loop-start marker for the raster
                if (logQueue_.size() > 500) logQueue_.pop_front();
            }
        }

        // Flush command queue to bridge stdin
        {
            std::lock_guard<std::mutex> lock(cmdMutex);
            while(!cmdQueue.empty()){
                const std::string& cmd = cmdQueue.front();
                (void)write(pipeToChild[1], cmd.c_str(), cmd.size());
                cmdQueue.pop();
            }
        }

        ofSleepMillis(1);
    }
}
