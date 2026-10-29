cmake_minimum_required(VERSION 3.22)
project(lingxi_glim_point_output LANGUAGES CXX)
find_package(glim REQUIRED)
add_library(lingxi_glim_point_output SHARED point_output.cpp)
target_compile_features(lingxi_glim_point_output PRIVATE cxx_std_17)
target_link_libraries(lingxi_glim_point_output PRIVATE glim::glim)
target_compile_options(lingxi_glim_point_output PRIVATE -Wall -Wextra -Wpedantic)
target_link_options(lingxi_glim_point_output PRIVATE
  "LINKER:--version-script=${CMAKE_CURRENT_SOURCE_DIR}/exports.map")
set_property(TARGET lingxi_glim_point_output APPEND PROPERTY
  LINK_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/exports.map")
include(CTest)
if(BUILD_TESTING)
  add_executable(point_output_contract point_output_contract.cpp)
  target_compile_features(point_output_contract PRIVATE cxx_std_17)
  target_link_libraries(point_output_contract PRIVATE lingxi_glim_point_output glim::glim)
  foreach(scenario IN ITEMS copy overflow invalid_frame wrong_thread wrong_api_thread
      invalid_stamp invalid_points invalid_pose missing_points skipped_id pending_close)
    add_test(NAME point_output_${scenario} COMMAND point_output_contract ${scenario})
  endforeach()
  add_test(NAME point_output_exported_abi COMMAND "${CMAKE_COMMAND}"
    "-DNM=${CMAKE_NM}" "-DLIBRARY=$<TARGET_FILE:lingxi_glim_point_output>"
    -P "${CMAKE_CURRENT_SOURCE_DIR}/verify_exports.cmake")
endif()
