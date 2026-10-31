#pragma once
#include <glim/odometry/estimation_frame.hpp>

namespace lingxi {
void retain_acquisition_trajectory(glim::EstimationFrame& frame);
// Called after trajectory smoothing, before non-keyframe points are discarded.
void capture_observation(const glim::EstimationFrame& frame);
}
