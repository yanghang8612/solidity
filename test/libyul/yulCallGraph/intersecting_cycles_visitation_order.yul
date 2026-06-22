{
    // Same topology as intersecting_cycles.yul (two recursive cycles sharing a node, so the whole
    // {hub, spoke, rim} set is a single strongly-connected component) but with different function names.
    function hub() { spoke() rim() }
    function spoke() { hub() }
    function rim() { spoke() }
}
// ----
// <main>: non-recursive
// hub: recursive
// spoke: recursive
// rim: recursive
