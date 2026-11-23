#include "../../fastlio-backlog/replay-probe.hpp"

#include <cassert>
#include <limits>

using lingxi_fastlio_replay::BacklogProbe;
using lingxi_fastlio_replay::FrameTimer;
using std::chrono::milliseconds;

int main() {
    BacklogProbe probe;
    const BacklogProbe::Clock::time_point start{};
    probe.wait_for_imu(10.0, start);
    probe.wait_for_imu(10.0, start + milliseconds(8));
    for (int i = 20; i >= 1; --i) probe.record_preprocess(i);
    probe.record_processing(120.0);

    const auto waiting = probe.snapshot(12, 12, 34, 10.0, 11.125, 1.0, 10.037, true, 10.1,
                                        start + milliseconds(230));
    assert(waiting.find("\"spanMs\":1125.000000,\"guardMs\":1000.000000") != std::string::npos);
    assert(waiting.find("\"imuMinusHeadEndMs\":-63.000000,\"headImuWaitChecks\":2") != std::string::npos);
    assert(waiting.find("\"sinceFirstHeadImuWaitMs\":230.000000") != std::string::npos);
    assert(waiting.find("\"recentLidarPreprocess\":{\"samples\":16,\"meanMs\":8.500000,\"maxMs\":16.000000}") != std::string::npos);
    assert(waiting.find("\"recentFrameProcessing\":{\"samples\":1,\"meanMs\":120.000000,\"maxMs\":120.000000}") != std::string::npos);

    const auto unknown = probe.snapshot(11, 12, 34, 10.0, 11.125, 1.0, 10.037, false, 10.1);
    assert(unknown.find("\"clouds\":11,\"stamps\":12") != std::string::npos);
    assert(unknown.find("\"headEndS\":null,\"imuMinusHeadEndMs\":null,\"headImuWaitChecks\":0") != std::string::npos);

    const auto invalid = probe.snapshot(12, 12, 34, 10.0, 11.125, 1.0, 10.037, true,
                                        std::numeric_limits<double>::infinity(), start + milliseconds(230));
    assert(invalid.find("\"headEndNonfinite\":true,\"headEndS\":null,\"imuMinusHeadEndMs\":null") != std::string::npos);

    probe.wait_for_imu(11.0, start + milliseconds(250));
    const auto next_head = probe.snapshot(11, 11, 44, 11.0, 12.125, 1.0, 11.01, true, 11.1,
                                          start + milliseconds(270));
    assert(next_head.find("\"headImuWaitChecks\":1,\"sinceFirstHeadImuWaitMs\":20.000000") != std::string::npos);
    probe.head_removed();
    // A new scan with a reused stamp must not inherit the previous wait.
    const auto ready = probe.snapshot(11, 11, 44, 11.0, 12.125, 1.0, 11.15, true, 11.1);
    assert(ready.find("\"imuMinusHeadEndMs\":50.000000,\"headImuWaitChecks\":0,\"sinceFirstHeadImuWaitMs\":null") != std::string::npos);

    { const FrameTimer timer(probe); }
    assert(probe.snapshot(12, 12, 34, 11.0, 12.125, 1.0, 11.15, false, 0.0)
           .find("\"recentFrameProcessing\":{\"samples\":2") != std::string::npos);
}
