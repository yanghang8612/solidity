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
/**
 * Tests for gas estimation of TRON-specific instructions in libevmasm's GasMeter.
 */

#include <libevmasm/AssemblyItem.h>
#include <libevmasm/GasMeter.h>
#include <libevmasm/Instruction.h>
#include <libevmasm/KnownState.h>

#include <test/Common.h>

#include <boost/test/unit_test.hpp>

#include <memory>
#include <optional>

using namespace solidity::evmasm;
using namespace solidity::langutil;

namespace solidity::frontend::test
{

namespace
{
/// Feeds the four NATIVEVOTE arguments as constants (or CALLVALUE for an unknown
/// element count) and returns the estimate for the NATIVEVOTE item itself.
GasMeter::GasConsumption estimateVote(
	u256 const& _srOffset,
	u256 const& _srCount,
	u256 const& _tpOffset,
	std::optional<u256> const& _tpCount
)
{
	// KnownState keeps pointers into the fed items (feedItem's _copyItem defaults
	// to false), so they have to outlive the meter, as they do in real assembly.
	// NATIVEVOTE stack layout, top first: tpCount, tpOffset, srCount, srOffset.
	AssemblyItems items{
		_srOffset,
		_srCount,
		_tpOffset,
		_tpCount ? AssemblyItem(*_tpCount) : AssemblyItem(Instruction::CALLVALUE),
		Instruction::NATIVEVOTE
	};
	GasMeter meter(std::make_shared<KnownState>(), solidity::test::CommonOptions::get().evmVersion());
	GasMeter::GasConsumption gas;
	for (AssemblyItem const& item: items)
		gas = meter.estimateMax(item);
	return gas;
}
}

BOOST_AUTO_TEST_SUITE(EvmasmGasMeter)

BOOST_AUTO_TEST_CASE(nativevote_charges_memory_expansion_for_word_arrays)
{
	// java-tron (EnergyCost.getVoteWitnessCost2/3) charges per array
	// memNeeded(offset, 32 * count + 32) and expands once to the larger end.
	// Arrays at 128 and 256 with two elements each end at 224 and 352 bytes,
	// i.e. 7 and 11 words: 3 * 11 = 33 on top of the flat vote cost.
	GasMeter::GasConsumption gas = estimateVote(u256(128), u256(2), u256(256), u256(2));
	BOOST_REQUIRE(!gas.isInfinite);
	BOOST_CHECK_EQUAL(gas.value, u256(GasCosts::voteGas + 33));
}

BOOST_AUTO_TEST_CASE(nativevote_charges_length_slot_for_empty_arrays)
{
	// The TVM reads and charges the 32-byte length slot even when count == 0:
	// ends are 128+32 and 512+32 bytes, i.e. 5 and 17 words: 3 * 17 = 51.
	GasMeter::GasConsumption gas = estimateVote(u256(128), u256(0), u256(512), u256(0));
	BOOST_REQUIRE(!gas.isInfinite);
	BOOST_CHECK_EQUAL(gas.value, u256(GasCosts::voteGas + 51));
}

BOOST_AUTO_TEST_CASE(nativevote_with_unknown_element_count_is_unbounded)
{
	GasMeter::GasConsumption gas = estimateVote(u256(128), u256(2), u256(256), std::nullopt);
	BOOST_CHECK(gas.isInfinite);
}

BOOST_AUTO_TEST_SUITE_END()

} // end namespaces
