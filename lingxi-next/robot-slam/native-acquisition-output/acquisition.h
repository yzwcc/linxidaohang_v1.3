#pragma once
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
const char* lingxi_acquisition_error(void);
int lingxi_acquisition_open(void);
// metadata: frame ID, return count, trajectory sample count, archive byte count.
// Null bytes queries the pending slot; successful copy consumes it. 0 means empty.
int lingxi_acquisition_take(size_t capacity, unsigned char* bytes, uint64_t* metadata);
// callbacks, initialization skips, copies, takes, discarded on close.
int lingxi_acquisition_counts(uint64_t* destination);
int lingxi_acquisition_close(void);
#ifdef __cplusplus
}
#endif
