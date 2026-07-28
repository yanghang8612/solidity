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

#include <liblangutil/EVMVersion.h>
#include <libevmasm/Instruction.h>

#include <boost/test/unit_test.hpp>

namespace solidity::langutil::test
{

BOOST_AUTO_TEST_SUITE(EVMVersionTest)

BOOST_AUTO_TEST_CASE(tron_instructions_are_not_available_in_eof)
{
	for (unsigned opcode = 0xd0; opcode <= 0xdf; ++opcode)
	{
		auto const instruction = static_cast<evmasm::Instruction>(opcode);
		BOOST_TEST(EVMVersion::osaka().hasOpcode(instruction, std::nullopt));
		BOOST_TEST(!EVMVersion::osaka().hasOpcode(instruction, 1));
	}
}

BOOST_AUTO_TEST_SUITE_END()

}
