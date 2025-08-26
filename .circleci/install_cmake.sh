#!/bin/bash
# Usage: ./install_cmake.sh 3.28.3
# This script installs the specified CMake version on macOS
# Installation path: /usr/local/cmake-<version>
# A symlink will be created in /usr/local/bin for easy access

set -e

if [ -z "$1" ]; then
  echo "Usage: $0 <cmake-version>"
  exit 1
fi

VERSION=$1
INSTALL_DIR="/usr/local/cmake-$VERSION"

# Check if the version is already installed
if [ -d "$INSTALL_DIR" ]; then
  echo "CMake $VERSION is already installed in $INSTALL_DIR"
  exit 0
fi

# Download official CMake binary release
URL="https://github.com/Kitware/CMake/releases/download/v$VERSION/cmake-$VERSION-macos-universal.tar.gz"
TMPFILE="/tmp/cmake-$VERSION.tar.gz"

echo "Downloading CMake $VERSION..."
curl -L "$URL" -o "$TMPFILE"

echo "Extracting to $INSTALL_DIR..."
sudo mkdir -p "$INSTALL_DIR"
sudo tar --strip-components=1 -xzf "$TMPFILE" -C "$INSTALL_DIR"

# Create symlinks for system-wide access
sudo ln -sfn "$INSTALL_DIR/bin/cmake" /usr/local/bin/cmake
sudo ln -sfn "$INSTALL_DIR/bin/ccmake" /usr/local/bin/ccmake
sudo ln -sfn "$INSTALL_DIR/bin/cpack" /usr/local/bin/cpack
sudo ln -sfn "$INSTALL_DIR/bin/ctest" /usr/local/bin/ctest

echo "CMake $VERSION installation completed."
cmake --version
