#!/bin/bash

# navigate to the directory where the script is located
cd src/open_apps/apps/onlineshop_app
# Configure Java 21
mkdir -p java;
cd java;
# Download and install Java 21 from https://jdk.java.net/archive/
ARCH="$(uname -m)"
# For Linux x64
if [ "$ARCH" = "x86_64" ]; then
  echo "Intel/AMD machine"
  wget https://download.java.net/java/GA/jdk21.0.1/415e3f918a1f4062a0074a2794853d0d/12/GPL/openjdk-21.0.1_linux-x64_bin.tar.gz
  tar -xzf openjdk-21.0.1_linux-x64_bin.tar.gz
elif [ "$ARCH" = "arm64" ] || [ "$ARCH" = "aarch64" ]; then
  echo "ARM64 machine"
# For Mac ARM64
  curl -O https://download.java.net/java/GA/jdk21.0.1/415e3f918a1f4062a0074a2794853d0d/12/GPL/openjdk-21.0.1_macos-aarch64_bin.tar.gz
  tar -xzf openjdk-21.0.1_macos-aarch64_bin.tar.gz
else
  echo "[System Not Supported]: Currently only x64 and Mac ARM64 are supported."
fi
cd ..

# Download dataset into `data` folder via `gdown` command
mkdir -p data;
cd data;
gdown https://drive.google.com/uc?id=1EgHdxQ_YxqIQlvvq5iKlCrkEKR6-j0Ib; # items_shuffle_1000 - product scraped info
gdown https://drive.google.com/uc?id=1IduG0xl544V_A_jv3tHXC0kyFi7PnyBu; # items_ins_v2_1000 - product attributes

# Only download these two large files if you want to fully replicate the original paper's setting with 12,087 tasks
# gdown https://drive.google.com/uc?id=1A2whVgOO0euk5O13n2iYDM0bQRkkRduB; # items_shuffle
# gdown https://drive.google.com/uc?id=1s2j6NgHljiZzQNL3veZaAiyW_qDEgBNi; # items_ins_v2

gdown https://drive.google.com/uc?id=14Kb5SPBk_jfdLZ_CDBNitW98QLDlKR5O # items_human_ins
cd ..

# Download spaCy large NLP model
python -m spacy download en_core_web_lg

# return to the original directory
cd ../../../../