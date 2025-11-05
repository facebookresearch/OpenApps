#!/bin/bash

# Check if the script is being sourced
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo "This script must be sourced. Use 'source setup_javapath.sh'"
  exit 1
fi

# Configure Java 21
ARCH="$(uname -m)"
# For Linux x64
if [ "$ARCH" = "x86_64" ]; then
  export JAVA_HOME=$PWD/src/open_apps/apps/onlineshop_app/java/jdk-21.0.1
elif [ "$ARCH" = "arm64" ] || [ "$ARCH" = "aarch64" ]; then
  export JAVA_HOME=$PWD/src/open_apps/apps/onlineshop_app/java/jdk-21.0.1.jdk/Contents/Home
else
  echo "[System Not Supported]: Currently only x64 and Mac ARM64 are supported."
  return 1
fi
export PATH=$JAVA_HOME/bin:$PATH