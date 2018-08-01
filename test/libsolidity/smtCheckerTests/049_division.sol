pragma experimental SMTChecker;
contract C {
    function f(int x, int y) public pure returns (int) {
        require(y != 0);
        return x / y;
    }
}
// ----
// Warning: (142-147): Overflow (resulting value larger than 0x7fffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff) might happen here.
