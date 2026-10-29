execute_process(COMMAND "${NM}" --dynamic --defined-only --format=just-symbols "${LIBRARY}"
  RESULT_VARIABLE status OUTPUT_VARIABLE symbols ERROR_VARIABLE error)
if(NOT status EQUAL 0)
  message(FATAL_ERROR "Native exported-symbol inspection failed: ${error}")
endif()
string(STRIP "${symbols}" symbols)
string(REPLACE "\n" ";" symbols "${symbols}")
list(SORT symbols)
set(expected lingxi_points_close lingxi_points_counts lingxi_points_error
  lingxi_points_open lingxi_points_take)
if(NOT "${symbols}" STREQUAL "${expected}")
  message(FATAL_ERROR "Only the five explicit point-output C functions may be exported")
endif()
