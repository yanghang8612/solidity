contract C {
    function f() public {
        freeze(1, 1);
        unfreeze(1);
        freezeExpireTime(1);
    }
}
// ----
// DeclarationError 7576: (47-53): Undeclared identifier.
// DeclarationError 7576: (69-77): Undeclared identifier.
// DeclarationError 7576: (90-106): Undeclared identifier.
