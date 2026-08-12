#pragma once

#include <algorithm>
#include <atomic>
#include <cstdint>
#include <vector>

// Single-producer / single-consumer ring buffer.
//
// The producer is the audio callback: it never blocks, never locks and never
// allocates after setup(). The consumer is the render thread.
//
// Positions are absolute frame counts rather than wrapped indices, so the
// consumer always knows which input frame a sample came from — that is what
// lets the drawing place a sample on the spiral by its true acquisition time
// instead of by when a frame happened to pick it up. If the consumer falls
// behind by more than the capacity the oldest frames are dropped and the
// consumer re-synchronises; the absolute index stays correct across the gap.
template <typename T>
class SpscRing {
public:
    // capacity must be a power of two
    void setup(size_t capacity) {
        cap = capacity;
        mask = capacity - 1;
        buf.assign(cap, T());
        wr.store(0, std::memory_order_relaxed);
        rd = 0;
    }

    // --- producer side ---
    void push(const T &v) {
        const uint64_t w = wr.load(std::memory_order_relaxed);
        buf[w & mask] = v;
        wr.store(w + 1, std::memory_order_release);
    }

    // --- consumer side ---
    uint64_t written() const { return wr.load(std::memory_order_acquire); }
    uint64_t consumed() const { return rd; }

    size_t available() const {
        const uint64_t w = wr.load(std::memory_order_acquire);
        const uint64_t n = w - rd;
        return (size_t)std::min<uint64_t>(n, cap);
    }

    // Pop up to maxN frames into out. firstIndex receives the absolute index
    // of out[0]. Returns the number of frames written to out.
    size_t pop(T *out, size_t maxN, uint64_t &firstIndex) {
        const uint64_t w = wr.load(std::memory_order_acquire);
        if (w - rd > cap) rd = w - cap;  // fell behind: skip to oldest valid
        const size_t n = (size_t)std::min<uint64_t>(w - rd, maxN);
        firstIndex = rd;
        for (size_t i = 0; i < n; ++i) out[i] = buf[(rd + i) & mask];
        rd += n;
        return n;
    }

    // Pop a single frame; false if empty.
    bool popOne(T &out) {
        uint64_t idx;
        return pop(&out, 1, idx) == 1;
    }

    // Peek at the next frame without consuming it.
    bool peek(T &out) const {
        const uint64_t w = wr.load(std::memory_order_acquire);
        if (w == rd) return false;
        out = buf[rd & mask];
        return true;
    }

    void skipOne() {
        const uint64_t w = wr.load(std::memory_order_acquire);
        if (w != rd) ++rd;
    }

    void clear() { rd = wr.load(std::memory_order_acquire); }

private:
    std::vector<T> buf;
    size_t cap = 0;
    size_t mask = 0;
    std::atomic<uint64_t> wr{0};
    uint64_t rd = 0;
};
