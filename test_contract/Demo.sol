contract Demo{
    uint public val = 0;
    function test() public returns(uint){
        val = val + 1;
    }
}