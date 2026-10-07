#!/usr/bin/env bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends cmake ninja-build pkg-config \
  libcjson-dev libwebsockets-dev libavahi-compat-libdnssd-dev libavahi-client-dev \
  avahi-daemon libnss-mdns dbus python3-venv git ca-certificates >/dev/null

mkdir -p /tmp/openeebus /results /run/dbus
tar -C /workspace/openeebus --exclude=./build --exclude=./.git -cf - . | tar -C /tmp/openeebus -xf -
cd /tmp/openeebus
export LD_LIBRARY_PATH=/usr/local/lib64:/usr/local/lib

if [ "${1:-integration}" = unit ]; then
  cmake -S tests -B /tmp/openeebus-tests -G Ninja -DCMAKE_BUILD_TYPE=Debug > /results/configure.log
  if ! cmake --build /tmp/openeebus-tests --parallel 3 > /results/build.log 2>&1; then
    tail -n 100 /results/build.log
    exit 1
  fi
  ctest --test-dir /tmp/openeebus-tests --output-on-failure 2>&1 | tee /results/ctest.log
  exit 0
fi

cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Debug -DOPTION_MDNS_USE_AVAHI_CLIENT=ON \
  '-DCMAKE_C_FLAGS=-DSHIP_NODE_DEBUG=1 -DEEBUS_SERVICE_DEBUG=1 -DMDNS_DEBUG=1' > /results/configure.log
if ! cmake --build build --parallel 3 > /results/build.log 2>&1; then
  tail -n 100 /results/build.log
  exit 1
fi

mkdir -p /results/node-logs
trap 'cp /tmp/tmp*.log /results/node-logs/ 2>/dev/null || true' EXIT
python3 -m venv /tmp/integration-venv
/tmp/integration-venv/bin/pip install -q pytest pytest-html
dbus-daemon --system --fork
avahi-daemon --daemonize --no-drop-root --no-chroot
export STRESS_TOTAL=3
/tmp/integration-venv/bin/pytest integration_tests/ --close-timing-iter=3 \
  --junitxml=/results/junit.xml --html=/results/report.html --self-contained-html \
  -x -v 2>&1 | tee /results/pytest.log