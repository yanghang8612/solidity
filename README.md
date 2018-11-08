# The Solidity Contract-Oriented Programming Language
[![Join the chat at https://gitter.im/ethereum/solidity](https://badges.gitter.im/Join%20Chat.svg)](https://gitter.im/ethereum/solidity?utm_source=badge&utm_medium=badge&utm_campaign=pr-badge&utm_content=badge) [![Build Status](https://travis-ci.org/ethereum/solidity.svg?branch=develop)](https://travis-ci.org/ethereum/solidity)

## Useful links
To get started you can find an introduction to the language in the [Solidity documentation](https://solidity.readthedocs.org). In the documentation, you can find [code examples](https://solidity.readthedocs.io/en/latest/solidity-by-example.html) as well as [a reference](https://solidity.readthedocs.io/en/latest/solidity-in-depth.html) of the syntax and details on how to write smart contracts.

You can start using [Solidity in your browser](http://remix.ethereum.org) with no need to download or compile anything.

The changelog for this project can be found [here](https://github.com/ethereum/solidity/blob/develop/Changelog.md).

Solidity is still under development. So please do not hesitate and open an [issue in GitHub](https://github.com/ethereum/solidity/issues) if you encounter anything strange.

## Building
See the [Solidity documentation](https://solidity.readthedocs.io/en/latest/installing-solidity.html#building-from-source) for build instructions.
```
Build for javascript

cd solidity

# 1. Prepare to Build solcjson, assume your project dir is "/root/project/solidity"
# a. build from Ubuntu [Optional]
docker run -it -v $(pwd):/root/project -w /root/project trzeci/emscripten:sdk-tag-1.35.4-64bit
apt-get update
apt-get install wget gcc boost
# b. build from macOS [Optional]

# 2.a install essetial
apt-get install -y build-essential
# 2.b if "2.a install build-essential" failed, please install Emscripten from this url:
https://github.com/juj/emsdk/tree/llvm_root

# 3. check if boost is installed 
./scripts/travis-emscripten/install_deps.sh

# 4. begin to compile
./scripts/travis-emscripten/build_emscripten.sh

# 5. release soljson.js
bash tran.sh

```

## Windows
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
