contract C {
    modifier costs(uint _amount) { require(msg.value >= _amount); _; }
    function f() costs(1 trx) public view {}
}
// ----
// TypeError 4006: (101-113): This modifier uses "msg.value", "msg.tokenid", "msg.tokenvalue" or "callvalue()" and thus the function has to be payable or internal.
