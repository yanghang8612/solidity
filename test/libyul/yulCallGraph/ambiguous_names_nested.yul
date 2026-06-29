{
    {
        function f() {
            function g() {}
        }
    }
    {
        function g() {}
    }
}
// ----
// YulAssertion: CallGraphGenerator requires a disambiguated AST: duplicate function name g.
