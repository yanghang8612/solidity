/*
	This file is part of solidity.

	solidity is free software: you can redistribute it and/or modify
	it under the terms of the GNU General Public License as published by
	the Free Software Foundation, either version 3 of the License, or
	(at your option) any later version.

	solidity is distributed in the hope that it will be useful,
	but WITHOUT ANY WARRANTY; without even the implied warranty of
	MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
	GNU General Public License for more details.

	You should have received a copy of the GNU General Public License
	along with solidity.  If not, see <http://www.gnu.org/licenses/>.
*/
// SPDX-License-Identifier: GPL-3.0

#include <test/libyul/CallGraphTest.h>

#include <test/libyul/Common.h>
#include <test/Common.h>

#include <libyul/optimiser/ASTWalker.h>
#include <libyul/optimiser/CallGraphGenerator.h>

#include <libyul/AST.h>
#include <libyul/Object.h>
#include <libyul/YulStack.h>

#include <liblangutil/Exceptions.h>

#include <sstream>

using namespace solidity;
using namespace solidity::langutil;
using namespace solidity::yul;
using namespace solidity::yul::test;
using namespace solidity::frontend;
using namespace solidity::frontend::test;

namespace
{

/// Collects function names in the order in which the functions are defined, descending into nested
/// functions, so that the call graph can be printed deterministically.
struct FunctionNameCollector: ASTWalker
{
	using ASTWalker::operator();
	void operator()(FunctionDefinition const& _function) override
	{
		names.emplace_back(_function.name);
		ASTWalker::operator()(_function);
	}

	std::vector<YulName> names;
};

}

std::unique_ptr<TestCase> CallGraphTest::create(Config const& _config)
{
	return std::make_unique<CallGraphTest>(_config.filename);
}

CallGraphTest::CallGraphTest(std::string const& _filename):
	TestCase(_filename)
{
	m_source = m_reader.source();
	m_expectation = m_reader.simpleExpectations();
}

TestCase::TestResult CallGraphTest::run(std::ostream& _stream, std::string const& _linePrefix, bool const _formatted)
{
	YulStack const yulStack = parseYul(m_source);
	solUnimplementedAssert(yulStack.parserResult()->subObjects.empty(), "Tests with subobjects not supported.");

	if (yulStack.hasErrors())
	{
		printYulErrors(yulStack, _stream, _linePrefix, _formatted);
		return TestResult::FatalError;
	}

	Block const& root = yulStack.parserResult()->code()->root();

	std::ostringstream out;
	try
	{
		CallGraph const callGraph = CallGraphGenerator::callGraph(root);
		std::set<FunctionHandle> const recursive = callGraph.recursiveFunctions();

		auto classification = [&](FunctionHandle const& _function) {
			return recursive.contains(_function) ? "recursive" : "non-recursive";
		};

		FunctionNameCollector collector;
		collector(root);

		// The outermost (non-function) context is denoted by the empty name in the call graph.
		out << "<main>: " << classification(YulName{}) << "\n";
		for (YulName const& name: collector.names)
			out << name.str() << ": " << classification(name) << "\n";
	}
	catch (langutil::InternalCompilerError const& _error)
	{
		out << "InternalCompilerError: " << (_error.comment() ? *_error.comment() : "") << "\n";
	}
	m_obtainedResult = out.str();

	return checkResult(_stream, _linePrefix, _formatted);
}
