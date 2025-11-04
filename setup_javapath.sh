#!/bin/bash

helpFunction()
{
  echo "Usage: source setup_javapath.sh -s linux|mac"
  echo -e "\t-s linux|mac - Specify the system type to configure Java for (linux or mac)"
}

# Check if the script is being sourced
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo "This script must be sourced. Use 'source setup_javapath.sh -s linux|mac'"
  exit 1
fi

# Parse arguments
while [ $# -gt 0 ]; do
  case "$1" in
    -s)
      system_type="$2"
      shift 2
      ;;
    *)
      echo "[Error]: Unknown argument $1"
      helpFunction
      return 1
      ;;
  esac
done

if [ -z "$system_type" ]; then
  echo "[Missing Argument]: -s flag"
  helpFunction
  return 1
fi

# Configure Java 21
if [ "$system_type" = "linux" ]; then
  export JAVA_HOME=$PWD/src/open_apps/apps/onlineshop_app/java/jdk-21.0.1
elif [ "$system_type" = "mac" ]; then
  export JAVA_HOME=$PWD/src/open_apps/apps/onlineshop_app/java/jdk-21.0.1.jdk/Contents/Home
else
  echo "[Missing Argument]: the `-s` flag not recognized"
  helpFunction
  return 1
fi
export PATH=$JAVA_HOME/bin:$PATH