#!/bin/bash
#
# Puts the OpenJDK 21 installed by setup.sh on PATH, for the map app's
# OpenTripPlanner route-planning server. The online shop no longer needs Java.

# Check if the script is being sourced
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo "This script must be sourced. Use 'source setup_javapath.sh'"
  exit 1
fi

JAVA_DIR="$PWD/src/open_apps/apps/map_app/java"

# Configure Java 21
ARCH="$(uname -m)"
# For Linux x64
if [ "$ARCH" = "x86_64" ]; then
  export JAVA_HOME=$JAVA_DIR/jdk-21.0.1
elif [ "$ARCH" = "arm64" ] || [ "$ARCH" = "aarch64" ]; then
  export JAVA_HOME=$JAVA_DIR/jdk-21.0.1.jdk/Contents/Home
else
  echo "[System Not Supported]: Currently only x64 and Mac ARM64 are supported."
  return 1
fi
export PATH=$JAVA_HOME/bin:$PATH
