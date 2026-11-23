#pragma once

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <iomanip>
#include <locale>
#include <sstream>
#include <string>

namespace lingxi_fastlio_replay {

// The pinned mapper dispatches these callbacks serially. Keep fixed-size
// timing windows and do no I/O on the normal callback path.
class RecentTimings {
public:
    void add(double ms) noexcept {
        samples_[next_] = ms;
        next_ = (next_ + 1) % samples_.size();
        count_ = std::min(count_ + 1, samples_.size());
    }

    void write(std::ostream &out) const {
        double sum = 0;
        double max = 0;
        for (std::size_t i = 0; i < count_; ++i) {
            sum += samples_[i];
            max = std::max(max, samples_[i]);
        }
        out << "{\"samples\":" << count_ << ",\"meanMs\":";
        if (count_) out << sum / count_; else out << "null";
        out << ",\"maxMs\":";
        if (count_) out << max; else out << "null";
        out << '}';
    }

private:
    std::array<double, 16> samples_{};
    std::size_t next_ = 0;
    std::size_t count_ = 0;
};

class BacklogProbe {
public:
    using Clock = std::chrono::steady_clock;

    void record_preprocess(double ms) noexcept { preprocess_.add(ms); }
    void record_processing(double ms) noexcept { processing_.add(ms); }

    void wait_for_imu(double head_s, Clock::time_point now = Clock::now()) noexcept {
        // Also reset on a changed head, so an old wait is not attributed to it.
        if (!wait_checks_ || head_s != waiting_head_s_) {
            wait_checks_ = 0;
            waiting_head_s_ = head_s;
            wait_start_ = now;
        }
        ++wait_checks_;
    }

    void head_removed() noexcept { wait_checks_ = 0; }

    std::string snapshot(std::size_t cloud_count, std::size_t stamp_count, std::size_t imu_count,
                         double head_s, double tail_s, double limit_s, double imu_watermark_s,
                         bool head_end_known, double head_end_s,
                         Clock::time_point now = Clock::now()) const {
        std::ostringstream out;
        out.imbue(std::locale::classic());
        out << std::fixed << std::setprecision(6);
        const bool current_wait = head_end_known && wait_checks_ && head_s == waiting_head_s_;
        const bool usable_head_end = head_end_known && std::isfinite(head_end_s);
        out << "{\"clouds\":" << cloud_count << ",\"stamps\":" << stamp_count
            << ",\"imus\":" << imu_count << ",\"headS\":" << head_s << ",\"tailS\":" << tail_s
            << ",\"spanMs\":" << (tail_s - head_s) * 1000 << ",\"guardMs\":" << limit_s * 1000
            << ",\"imuWatermarkS\":" << imu_watermark_s
            << ",\"headEndNonfinite\":" << (head_end_known && !usable_head_end ? "true" : "false")
            << ",\"headEndS\":";
        if (usable_head_end) out << head_end_s; else out << "null";
        out << ",\"imuMinusHeadEndMs\":";
        if (usable_head_end) out << (imu_watermark_s - head_end_s) * 1000; else out << "null";
        out << ",\"headImuWaitChecks\":" << (current_wait ? wait_checks_ : 0)
            << ",\"sinceFirstHeadImuWaitMs\":";
        if (current_wait) out << elapsed_ms(now - wait_start_); else out << "null";
        out << ",\"recentLidarPreprocess\":";
        preprocess_.write(out);
        out << ",\"recentFrameProcessing\":";
        processing_.write(out);
        out << '}';
        return out.str();
    }

    static double elapsed_ms(Clock::duration delta) noexcept {
        return std::chrono::duration<double, std::milli>(delta).count();
    }

private:
    std::size_t wait_checks_ = 0;
    double waiting_head_s_ = 0;
    Clock::time_point wait_start_{};
    RecentTimings preprocess_;
    RecentTimings processing_;
};

// Measure through the end of the callback, including publish and early returns
// after synchronization; the first scan (time anchor only) is not measured.
class FrameTimer {
public:
    explicit FrameTimer(BacklogProbe &probe) noexcept : probe_(probe) {}
    ~FrameTimer() { probe_.record_processing(BacklogProbe::elapsed_ms(BacklogProbe::Clock::now() - start_)); }
    FrameTimer(const FrameTimer &) = delete;
    FrameTimer &operator=(const FrameTimer &) = delete;

private:
    BacklogProbe &probe_;
    BacklogProbe::Clock::time_point start_ = BacklogProbe::Clock::now();
};

} // namespace lingxi_fastlio_replay
