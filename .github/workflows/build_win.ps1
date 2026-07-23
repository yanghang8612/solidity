$ErrorActionPreference = "Stop"

cd "$PSScriptRoot\..\.."

New-Item prerelease.txt -type file
Write-Host "Building release version."

mkdir build
cd build
$boost_dir=(Resolve-Path $PSScriptRoot\..\..\deps\boost\lib\cmake\Boost-*)
..\deps\cmake\bin\cmake -G "Visual Studio 17 2022" -DBoost_DIR="$boost_dir\" -DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded -DUSE_Z3=OFF -DTESTS=OFF ..
if ( -not $? ) { throw "CMake configure failed." }
..\deps\cmake\bin\cmake --build . -j 10 --target solc --config Release
if ( -not $? ) { throw "solc build failed." }
