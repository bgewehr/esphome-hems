cmake_minimum_required(VERSION 3.20)

project(esphome_hems_host_tests LANGUAGES CXX)

include(CTest)

add_executable(semp_decoder_test
  semp_decoder_test.cpp
  ../components/eebus_eg/semp_decoder.cpp
)

target_compile_features(semp_decoder_test PRIVATE cxx_std_17)
target_include_directories(semp_decoder_test PRIVATE
  ..
  ../openeebus
)

if(MSVC)
  target_compile_options(semp_decoder_test PRIVATE /W4)
else()
  target_compile_options(semp_decoder_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
endif()

add_test(NAME semp_decoder COMMAND semp_decoder_test)

add_executable(xemex_control_test xemex_control_test.cpp)
target_compile_features(xemex_control_test PRIVATE cxx_std_17)
target_include_directories(xemex_control_test PRIVATE ..)
if(MSVC)
  target_compile_options(xemex_control_test PRIVATE /W4)
else()
  target_compile_options(xemex_control_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
endif()
add_test(NAME xemex_control COMMAND xemex_control_test)

add_executable(solar_policy_test solar_policy_test.cpp)
target_compile_features(solar_policy_test PRIVATE cxx_std_17)
target_include_directories(solar_policy_test PRIVATE ..)
if(MSVC)
  target_compile_options(solar_policy_test PRIVATE /W4)
else()
  target_compile_options(solar_policy_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
endif()
add_test(NAME solar_policy COMMAND solar_policy_test)