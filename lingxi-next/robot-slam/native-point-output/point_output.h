#pragma once
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif
const char* lingxi_points_error(void);
int lingxi_points_open(int maximum_points);
/* metadata: native frame ID, algorithm stamp, native point count, copied count. */
int lingxi_points_take(int capacity, double* world_xyz, double* lidar_pose, double* metadata);
/* counts: callbacks, initialization skips, copies, takes, discarded on close. */
int lingxi_points_counts(uint64_t* destination);
int lingxi_points_close(void);
#ifdef __cplusplus
}
#endif
