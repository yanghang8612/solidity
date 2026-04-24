contract C {
    function freezeFor(address payable a) public {
        a.freeze(1, 1);
        a.unfreeze(1);
    }

    function freezeExpiry(address a) public view returns (uint256) {
        return a.freezeExpireTime(1);
    }
}
