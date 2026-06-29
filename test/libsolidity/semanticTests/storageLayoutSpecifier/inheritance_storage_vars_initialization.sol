contract A { uint public x = 1; }
contract B is A { uint public y = x * 10; }
// Using specifier, `--via-ir` and legacy produce diffrent output (incorrect in both cases).
//contract C1 is B layout at 2**256 - 2**40 { uint public z = y + 5; }
contract C2 is B { uint public z = y + 5; }
contract F {
    //function withSpecifier() public returns (uint, uint, uint) {
    //    C1 c = new C1();
    //    return (c.x(), c.y(), c.z());
    //}
    function withoutSpecifier() public returns (uint, uint, uint) {
        C2 c = new C2();
        return (c.x(), c.y(), c.z());
    }
}
// ----
// withoutSpecifier() -> 1, 10, 15
// gas irOptimized: 121728
// gas irOptimized code: 27600
// gas legacy: 123599
// gas legacy code: 40400
// gas legacyOptimized: 121916
// gas legacyOptimized code: 20600
// gas ssaCFGOptimized: 121687
// gas ssaCFGOptimized code: 26200
