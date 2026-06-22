{
    // Two recursive cycles sharing alpha: alpha <-> beta and alpha -> gamma -> beta -> alpha,
    // so {alpha, beta, gamma} is a single strongly-connected component and all three are recursive.
    function alpha() { beta() gamma() }
    function beta() { alpha() }
    function gamma() { beta() }
}
// ----
// <main>: non-recursive
// alpha: recursive
// beta: recursive
// gamma: non-recursive
