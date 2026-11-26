# Host-side unit tests for the firmware's portable logic.
CXX ?= g++
CXXFLAGS ?= -std=c++17 -O2 -Wall -Wextra -Werror

test_firmware: test_firmware.cpp ../NixieCounter/FracDivider.h ../NixieCounter/CommandParser.h
	$(CXX) $(CXXFLAGS) -o $@ test_firmware.cpp

.PHONY: test clean
test: test_firmware
	./test_firmware

clean:
	rm -f test_firmware
