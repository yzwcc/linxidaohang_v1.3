cmake_minimum_required(VERSION 3.22)
project(lingxi_glim_time_smoke LANGUAGES CXX)

find_package(glim REQUIRED)
if(NOT IS_ABSOLUTE "${GLIM_TRIAL_CONFIG}")
  message(FATAL_ERROR "GLIM_TRIAL_CONFIG must name an explicit synthetic configuration directory")
endif()
if(NOT EXISTS "${GLIM_TRIAL_CONFIG}/trial-manifest.json")
  message(FATAL_ERROR "The generated synthetic trial manifest is required")
endif()
file(READ "${GLIM_TRIAL_CONFIG}/trial-manifest.json" trial_manifest)
string(JSON input_classification GET "${trial_manifest}" inputClassification)
if(NOT input_classification STREQUAL "synthetic_fixture")
  message(FATAL_ERROR "This smoke test accepts only an explicitly synthetic fixture")
endif()

add_executable(lingxi_glim_time_smoke time_smoke.cpp)
target_compile_features(lingxi_glim_time_smoke PRIVATE cxx_std_17)
target_link_libraries(lingxi_glim_time_smoke PRIVATE glim::glim ${CMAKE_DL_LIBS})

enable_testing()
add_test(NAME synthetic_relative_nanoseconds
  COMMAND lingxi_glim_time_smoke "${GLIM_TRIAL_CONFIG}"
    "$<TARGET_FILE:glim::odometry_estimation_cpu>"
    "$<TARGET_FILE:glim::sub_mapping_passthrough>"
    "$<TARGET_FILE:glim::global_mapping_pose_graph>")
