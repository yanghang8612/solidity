contract A { function f() public virtual {} }
// Compilation of B1 currently crashes.
//contract B1 is A layout at 2**256 - 2**42 { function f() public override virtual { super.f(); } }
contract B2 is A { function f() public override virtual { super.f(); } }
