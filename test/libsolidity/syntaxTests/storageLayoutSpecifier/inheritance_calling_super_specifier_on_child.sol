contract A { function f() public virtual {} }
contract B is A { function f() public override virtual { super.f(); } }
//Compilation of C1 currently crashes.
//contract C1 is B layout at 2**256 - 2**42 {}
contract C2 is B {}
