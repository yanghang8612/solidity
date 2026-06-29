contract A { uint public immutable a = 1; }
contract B is A { uint public immutable b = 2; }
// Using specifier, `--via-ir` produces incorrect output, while legacy produces correct one.
//contract C1 is B layout at 2**256 - 2**42 { uint public immutable c = 3; }
contract C2 is B { uint public immutable c = 3; }
contract F {
    //function withSpecifier() public returns(uint, uint, uint) {
    //  C1 c = new C1();
    //  return (c.a(), c.b(), c.c());
    //}
    function withoutSpecifier() public returns(uint, uint, uint) {
        C2 c = new C2();
        return (c.a(), c.b(), c.c());
    }
}
// ----
// withoutSpecifier() -> 1, 2, 3
// gas irOptimized: 55246
// gas irOptimized code: 45400
// gas legacy: 56451
// gas legacy code: 62800
