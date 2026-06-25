// SPDX-License-Identifier: GPL-3.0
pragma solidity >= 0.0.0;

// Regression test: calling getchainparameter used to abort the legacy pipeline
// with an ICE ("Non-padded and in-place encoding can only be combined.").
// Producing bytecode here (no metadata) means it compiled successfully.

contract C {
    function f() public view returns (uint) {
        return getchainparameter(0);
    }
}
