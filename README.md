# The Solidity Contract-Oriented Programming Language
[![Join the chat at https://gitter.im/ethereum/solidity](https://badges.gitter.im/Join%20Chat.svg)](https://gitter.im/ethereum/solidity?utm_source=badge&utm_medium=badge&utm_campaign=pr-badge&utm_content=badge) [![Build Status](https://travis-ci.org/ethereum/solidity.svg?branch=develop)](https://travis-ci.org/ethereum/solidity)

## Useful links
To get started you can find an introduction to the language in the [Solidity documentation](https://solidity.readthedocs.org). In the documentation, you can find [code examples](https://solidity.readthedocs.io/en/latest/solidity-by-example.html) as well as [a reference](https://solidity.readthedocs.io/en/latest/solidity-in-depth.html) of the syntax and details on how to write smart contracts.

You can start using [Solidity in your browser](http://remix.ethereum.org) with no need to download or compile anything.

The changelog for this project can be found [here](https://github.com/ethereum/solidity/blob/develop/Changelog.md).

Solidity is still under development. So please do not hesitate and open an [issue in GitHub](https://github.com/ethereum/solidity/issues) if you encounter anything strange.

# Build and Install
Here is instructions about how to build and install the Solidity compiler.

## Prerequisites - macOS
For macOS, ensure that you have the latest version of Xcode installed. This contains the Clang C++ compiler, the Xcode IDE and other Apple development tools which are required for building C++ applications on OS X. If you are installing Xcode for the first time, or have just installed a new version then you will need to agree to the license before you can do command-line builds:
```
sudo xcodebuild -license accept
```

Our OS X builds require you to install the Homebrew package manager for installing external dependencies.

## Prerequisites - Windows

Here is the list of components that should be installed in Windows:
```
git
CMake
Visual Studio 2017 Build Tools or Visual Studio 2017

# visual studio 2017 Build Tools should include:
Visual Studio C++ core features
VC++ 2017 v141 toolset (x86,x64)
Windows Universal CRT SDK
Windows 8.1 SDK
C++/CLI support
```

## Clone the Repository
To clone the source code, execute the following command:
```
git clone --recursive https://github.com/tronprotocol/solidity.git
cd solidity
```
## build solc

### External Dependencies

We have a helper script which installs all required external dependencies on macOS, Windows and on numerous Linux distros.
```
./scripts/install_deps.sh
```
Or, on Windows (not official verified yet):
```
scripts\install_deps.bat
```
Or, on Centos (not official verified yet):
```
# install  z3
https://github.com/Z3Prover/z3
# install deps
scripts\install_deps.bat
```

Solidity project uses CMake to configure the build. You might want to install ccache to speed up repeated builds. CMake will pick it up automatically. Building Solidity is quite similar on Linux, macOS and other Unices:
```
mkdir build
cd build
cmake .. && make
```

## build solc-js (Build for javascript)
See the [Solidity documentation](https://solidity.readthedocs.io/en/latest/installing-solidity.html#building-from-source) for build instructions.
```
cd solidity

#Prepare to Build solcjson, assume your project dir is "/root/project/solidity"

# External Dependencies

on Ubuntu
```
docker run -it -v $(pwd):/root/project -w /root/project trzeci/emscripten:sdk-tag-1.35.4-64bit

```
apt-get update
apt-get install wget gcc libboost-all-dev

# install essetial
apt-get install -y build-essential

# if "install build-essential" failed, please install Emscripten from this url:
https://github.com/juj/emsdk/tree/llvm_root

# check if boost is installed
./scripts/travis-emscripten/install_deps.sh

# begin to compile
./scripts/travis-emscripten/build_emscripten.sh

# release soljson.js
bash tran.sh

```

### Windows
```
https://solidity.readthedocs.io/en/latest/installing-solidity.html#building-from-source
# install vs2017
# install cmake
scripts\install_deps.bat
部分文件在中文windows下警告被看作错误，改一下文件编码，删除不用的测试就行了
mkdir build
cd build
cmake -G "Visual Studio 15 2017 Win64" ..
cmake --build . --config RelWithDebInfo
```
