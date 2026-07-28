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
#include <tuple>
#include <utility>
#include <vector>

using namespace solidity::evmasm;
using namespace solidity::langutil;

namespace solidity::frontend::test
{

namespace
{
GasMeter::GasConsumption estimateInstruction(
	Instruction _instruction,
	AssemblyItems _arguments,
	bool _includeExternalCosts = true,
	EVMVersion _evmVersion = EVMVersion{}
)
{
	_arguments.emplace_back(_instruction);
	GasMeter meter(std::make_shared<KnownState>(), _evmVersion);
	GasMeter::GasConsumption gas;
	for (AssemblyItem const& item: _arguments)
		gas = meter.estimateMax(item, _includeExternalCosts);
	return gas;
}

AssemblyItems zeroArguments(size_t _count)
{
	return AssemblyItems(_count, AssemblyItem{u256(0)});
}

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

BOOST_AUTO_TEST_CASE(tvm_fixed_instruction_prices_do_not_depend_on_evm_version)
{
	std::vector<std::tuple<Instruction, size_t, unsigned>> const fixedCosts{
		{Instruction::SLOAD, 1, GasCosts::tvmSloadGas},
		{Instruction::BALANCE, 1, GasCosts::tvmBalanceGas},
		{Instruction::TOKENBALANCE, 2, GasCosts::tvmBalanceGas},
		{Instruction::ISCONTRACT, 1, GasCosts::tvmBalanceGas},
		{Instruction::EXTCODESIZE, 1, GasCosts::tvmExtCodeSizeGas},
		{Instruction::EXTCODECOPY, 4, GasCosts::tvmExtCodeCopyGas},
		{Instruction::EXTCODEHASH, 1, GasCosts::tvmExtCodeHashGas},
		{Instruction::NATIVEFREEZE, 3, GasCosts::freezeV1Gas + GasCosts::callNewAccountGas},
		{Instruction::NATIVEUNFREEZE, 2, GasCosts::freezeV1Gas},
		{Instruction::NATIVEFREEZEEXPIRETIME, 2, GasCosts::expireTimeGas},
		{Instruction::NATIVEWITHDRAWREWARD, 0, GasCosts::withdrawGas},
		{Instruction::NATIVEFREEZEBALANCEV2, 2, GasCosts::freezeV2Gas},
		{Instruction::NATIVEUNFREEZEBALANCEV2, 2, GasCosts::freezeV2Gas},
		{Instruction::NATIVECANCELALLUNFREEZEV2, 0, GasCosts::freezeV2Gas},
		{Instruction::NATIVEWITHDRAWEXPIREUNFREEZE, 0, GasCosts::freezeV2Gas},
		{Instruction::NATIVEDELEGATERESOURCE, 3, GasCosts::freezeV2Gas},
		{Instruction::NATIVEUNDELEGATERESOURCE, 3, GasCosts::freezeV2Gas}
	};

	for (EVMVersion const& evmVersion: EVMVersion::allVersions())
		for (auto const& [instruction, argumentCount, expected]: fixedCosts)
		{
			GasMeter::GasConsumption gas = estimateInstruction(
				instruction,
				zeroArguments(argumentCount),
				true,
				evmVersion
			);
			BOOST_REQUIRE(!gas.isInfinite);
			BOOST_CHECK_EQUAL(gas.value, u256(expected));
		}
}

BOOST_AUTO_TEST_CASE(tvm_sstore_uses_java_tron_set_and_reset_prices)
{
	GasMeter::GasConsumption set = estimateInstruction(Instruction::SSTORE, {u256(1), u256(0)});
	BOOST_REQUIRE(!set.isInfinite);
	BOOST_CHECK_EQUAL(set.value, u256(GasCosts::tvmSstoreSetGas));

	GasMeter::GasConsumption reset = estimateInstruction(Instruction::SSTORE, {u256(0), u256(0)});
	BOOST_REQUIRE(!reset.isInfinite);
	BOOST_CHECK_EQUAL(reset.value, u256(GasCosts::tvmSstoreResetGas));
}

BOOST_AUTO_TEST_CASE(tvm_call_family_uses_fixed_base_and_conditional_transfer_prices)
{
	for (Instruction instruction: {Instruction::DELEGATECALL, Instruction::STATICCALL})
	{
		GasMeter::GasConsumption gas = estimateInstruction(instruction, zeroArguments(6), false);
		BOOST_REQUIRE(!gas.isInfinite);
		BOOST_CHECK_EQUAL(gas.value, u256(GasCosts::tvmCallGas));
	}

	GasMeter::GasConsumption zeroValueCall = estimateInstruction(
		Instruction::CALL,
		{u256(0), u256(0), u256(0), u256(0), u256(0), u256(2), u256(0)},
		false
	);
	BOOST_REQUIRE(!zeroValueCall.isInfinite);
	BOOST_CHECK_EQUAL(zeroValueCall.value, u256(GasCosts::tvmCallGas));

	GasMeter::GasConsumption valueCall = estimateInstruction(
		Instruction::CALL,
		{u256(0), u256(0), u256(0), u256(0), u256(1), u256(2), u256(0)},
		false
	);
	BOOST_REQUIRE(!valueCall.isInfinite);
	BOOST_CHECK_EQUAL(
		valueCall.value,
		u256(GasCosts::tvmCallGas + GasCosts::callValueTransferGas + GasCosts::callNewAccountGas)
	);

	GasMeter::GasConsumption valueCallCode = estimateInstruction(
		Instruction::CALLCODE,
		{u256(0), u256(0), u256(0), u256(0), u256(1), u256(2), u256(0)},
		false
	);
	BOOST_REQUIRE(!valueCallCode.isInfinite);
	BOOST_CHECK_EQUAL(valueCallCode.value, u256(GasCosts::tvmCallGas + GasCosts::callValueTransferGas));
}

BOOST_AUTO_TEST_CASE(tvm_calltoken_uses_fixed_base_and_conditional_transfer_prices)
{
	GasMeter::GasConsumption zeroValue = estimateInstruction(
		Instruction::CALLTOKEN,
		{u256(0), u256(0), u256(0), u256(0), u256(1), u256(0), u256(2), u256(0)},
		false
	);
	BOOST_REQUIRE(!zeroValue.isInfinite);
	BOOST_CHECK_EQUAL(zeroValue.value, u256(GasCosts::tvmCallGas));

	GasMeter::GasConsumption nonZeroValue = estimateInstruction(
		Instruction::CALLTOKEN,
		{u256(0), u256(0), u256(0), u256(0), u256(1), u256(1), u256(2), u256(0)},
		false
	);
	BOOST_REQUIRE(!nonZeroValue.isInfinite);
	BOOST_CHECK_EQUAL(
		nonZeroValue.value,
		u256(GasCosts::tvmCallGas + GasCosts::callValueTransferGas + GasCosts::callNewAccountGas)
	);
}

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
