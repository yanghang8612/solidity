contract A { constructor(uint x) {} }
contract B { constructor(uint x) {} }
// Compilation of C1 crashes with "invalid IR generated" with `--via-ir`
//contract C1 is A(2), B(3) layout at 2**256 - 2**42 { constructor(uint x) {} }
contract C2 is A(2), B(3) { constructor(uint x) {} }
contract F {
    //function withSpecifier() public {
    //  new C1(4);
    //}
    function withoutSpecifier() public {
        new C2(4);
    }
}
// ----
// withoutSpecifier() ->
