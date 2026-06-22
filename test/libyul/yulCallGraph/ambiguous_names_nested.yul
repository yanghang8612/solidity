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
// InternalCompilerError: CallGraphGenerator requires a disambiguated AST: duplicate function name g.
