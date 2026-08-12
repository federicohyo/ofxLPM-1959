#pragma once
// Shim: include Detector.h and RingBuffer.h, renaming SpikeEvent to HubelSpikeEvent
// so it does not clash with NeuronBridge.h's SpikeEvent.
#define SpikeEvent HubelSpikeEvent
#include "Detector.h"
#include "RingBuffer.h"
#undef SpikeEvent
