{
    // Nested function definitions with a cycle a -> b -> c -> a.
    function a() {
        function b() {
            function c() { a() }
            c()
        }
        b()
    }
    a()
}
// ----
// <main>: non-recursive
// a: recursive
// b: recursive
// c: recursive
