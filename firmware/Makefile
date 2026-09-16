CC ?= gcc
CFLAGS ?= -std=c99 -Wall -Wextra -pedantic -O2 -Iinclude
LDFLAGS ?= -lm

BUILD_DIR = build
BIN_DIR = bin

ADAPT_SRC = src/adaptive_sonar_engine.c
CHIRP_SRC = src/chirp.c
TEST_FW_SRC = src/test_firmware.c

ADAPT_HDR = include/adaptive_sonar_engine.h
CHIRP_HDR = include/chirp.h include/afe.h

ADAPT_TEST_BIN = $(BIN_DIR)/test_adaptation_engine
CHIRP_OBJ = $(BUILD_DIR)/chirp.o
ADAPT_OBJ = $(BUILD_DIR)/adaptive_sonar_engine.o
FW_TEST_BIN = $(BIN_DIR)/test_firmware
SHARED_LIB = $(BUILD_DIR)/libadaptive_sonar.so
ROOT_SHARED_LIB = ../build/libadaptive_sonar.so

.PHONY: all test clean dirs

all: dirs $(ADAPT_TEST_BIN) $(CHIRP_OBJ) $(FW_TEST_BIN) $(SHARED_LIB) $(ROOT_SHARED_LIB)

dirs:
	@mkdir -p $(BUILD_DIR) $(BIN_DIR)

$(ADAPT_TEST_BIN): $(ADAPT_SRC) $(ADAPT_HDR) | dirs
	$(CC) $(CFLAGS) -DBUILD_TEST_C $(ADAPT_SRC) -o $@ $(LDFLAGS)

$(CHIRP_OBJ): $(CHIRP_SRC) $(CHIRP_HDR) | dirs
	$(CC) $(CFLAGS) -c $(CHIRP_SRC) -o $@

$(ADAPT_OBJ): $(ADAPT_SRC) $(ADAPT_HDR) | dirs
	$(CC) $(CFLAGS) -fPIC -c $(ADAPT_SRC) -o $@

$(FW_TEST_BIN): $(TEST_FW_SRC) $(ADAPT_OBJ) $(CHIRP_OBJ) $(ADAPT_HDR) $(CHIRP_HDR) | dirs
	$(CC) $(CFLAGS) $(TEST_FW_SRC) $(ADAPT_OBJ) $(CHIRP_OBJ) -o $@ $(LDFLAGS)

$(SHARED_LIB): $(ADAPT_SRC) $(ADAPT_HDR) | dirs
	$(CC) $(CFLAGS) -fPIC -shared $(ADAPT_SRC) -o $@ $(LDFLAGS)

$(ROOT_SHARED_LIB): $(SHARED_LIB)
	@mkdir -p ../build
	cp -f $(SHARED_LIB) $(ROOT_SHARED_LIB)

test: all
	@echo "=== Running Adaptation Engine Standalone Suite ==="
	./$(ADAPT_TEST_BIN)
	@echo "\n=== Running Firmware Integration Suite ==="
	./$(FW_TEST_BIN)

clean:
	rm -rf $(BUILD_DIR) $(BIN_DIR) ../build

