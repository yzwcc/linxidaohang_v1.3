cmake_minimum_required(VERSION 3.22)
project(lingxi_glim_acquisition_output LANGUAGES CXX)
find_package(glim REQUIRED)
add_library(lingxi_glim_acquisition_output SHARED acquisition.cpp)
target_compile_features(lingxi_glim_acquisition_output PRIVATE cxx_std_17)
target_link_libraries(lingxi_glim_acquisition_output PRIVATE glim::glim)
target_compile_options(lingxi_glim_acquisition_output PRIVATE -Wall -Wextra -Wpedantic)
target_link_options(lingxi_glim_acquisition_output PRIVATE
  "LINKER:--version-script=${CMAKE_CURRENT_SOURCE_DIR}/exports.map")
set_property(TARGET lingxi_glim_acquisition_output APPEND PROPERTY
  LINK_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/exports.map")
include(CTest)
if(BUILD_TESTING)
  add_executable(acquisition_contract acquisition_contract.cpp)
  target_compile_features(acquisition_contract PRIVATE cxx_std_17)
  target_link_libraries(acquisition_contract PRIVATE lingxi_glim_acquisition_output glim::glim)
  foreach(scenario IN ITEMS copy pending_close overflow missing_raw missing_trajectory incomplete_trajectory
      unordered_trajectory quaternion points times pose lidar_pose skipped_id wrong_thread wrong_api_thread capacity
      invalid_stamp invalid_frame duplicate_id return_overflow trajectory_overflow negative_time unordered_time
      nonfinite_point nonhomogeneous_point extrinsic raw_stamp null_frame null_metadata)
    add_test(NAME acquisition_${scenario} COMMAND acquisition_contract ${scenario})
  endforeach()
endif()
