import time
from datetime import datetime, timedelta
import asyncio
import json
import logging
from web3 import Web3
from agents import get_balance  # Adjusted imports
from web3.exceptions import ContractLogicError
import requests  # You might need this to fetch the current ETH price


# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Connect to Base Mainnet via WebSocket
web3 = Web3(Web3.LegacyWebSocketProvider("wss://base-rpc.publicnode.com"))
logging.info(f"Connected to Base Mainnet: {web3.is_connected()}")

# Load Uniswap Factory and Router ABIs
with open('uniswap_v2_factory_abi.json', 'r') as f:
    factory_abi = json.load(f)
with open('uniswap_v2_router_abi.json', 'r') as f:
    router_abi = json.load(f)

FACTORY_ADDRESS = web3.to_checksum_address("0x8909Dc15e40173Ff4699343b6eB8132c65e18eC6")
ROUTER_ADDRESS = web3.to_checksum_address("0x4752ba5dbc23f44d87826276bf6fd6b1c372ad24")

factory_contract = web3.eth.contract(address=FACTORY_ADDRESS, abi=factory_abi)
router_contract = web3.eth.contract(address=ROUTER_ADDRESS, abi=router_abi)


async def monitor_new_pairs(trade=False):
    logging.info("Monitoring new pairs...")
    latest_block = web3.eth.block_number

    while True:
        try:
            current_block = web3.eth.block_number
            if latest_block >= current_block:
                await asyncio.sleep(5)
                continue

            event_signature_hash = web3.keccak(text="PairCreated(address,address,address,uint256)")
            logs = web3.eth.get_logs({
                'fromBlock': latest_block + 1,
                'toBlock': current_block,
                'address': FACTORY_ADDRESS,
                'topics': [event_signature_hash]
            })

            latest_block = current_block

            for log in logs:
                event = factory_contract.events.PairCreated().process_log(log)
                token0 = event['args']['token0']
                token1 = event['args']['token1']
                logging.info(f"New pair detected: {token0} - {token1}")

                # Only call monitor_arbitrage_only when token0 and token1 are defined
                if not trade:
                    await monitor_arbitrage_only(token0, token1)

        except Exception as e:
            logging.error(f"Error monitoring new pairs: {e}")
            await asyncio.sleep(5)

        await asyncio.sleep(5)


# Function to get the current ETH price (you can use any reliable API)
def get_eth_price():
    try:
        response = requests.get('https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd')
        data = response.json()
        return data['ethereum']['usd']
    except Exception as e:
        logging.error(f"Failed to fetch ETH price: {e}")
        return None



async def monitor_arbitrage_only(token_in, token_out):
    logging.info(f"Monitoring arbitrage for pair without trading: {token_in} - {token_out}")
    
    initial_liquidity = None
    previous_liquidity = None
    MIN_LIQUIDITY_THRESHOLD = Web3.to_wei(1, 'ether')  # Minimum liquidity to keep monitoring
    LIQUIDITY_DROP_THRESHOLD = 0.1  # Stop monitoring if liquidity drops by more than 90%

    while True:
        try:
            # Fetch pair address and reserves
            pair_address = await get_pair_address(token_in, token_out)
            reserves = await get_reserves(pair_address)

            # Calculate the total liquidity as the sum of reserves
            current_liquidity = reserves['reserve_in'] + reserves['reserve_out']

            # Set initial liquidity if not already set
            if initial_liquidity is None:
                initial_liquidity = current_liquidity

            # Check if current liquidity is below the minimum threshold
            if current_liquidity < MIN_LIQUIDITY_THRESHOLD:
                logging.warning(f"Liquidity below minimum threshold for pair {token_in} - {token_out}. Stopping monitoring.")
                break

            # Check for a significant liquidity drop
            if previous_liquidity and current_liquidity < previous_liquidity * LIQUIDITY_DROP_THRESHOLD:
                logging.warning(f"Significant liquidity drop detected for pair {token_in} - {token_out}. Monitoring stopped.")
                break

            # Update previous liquidity for next comparison
            previous_liquidity = current_liquidity

            # Log the liquidity status
            if reserves['reserve_in'] > 0 and reserves['reserve_out'] > 0:
                logging.info(f"Pair {token_in} - {token_out} has liquidity: {reserves['reserve_in']} / {reserves['reserve_out']}.")
            else:
                logging.info(f"Pair {token_in} - {token_out} does not have sufficient liquidity.")

            # Wait before the next check
            await asyncio.sleep(10)

        except Exception as e:
            logging.error(f"Error monitoring pair {token_in} - {token_out}: {e}")
            await asyncio.sleep(10)



async def get_pair_address(token_in, token_out):
    return factory_contract.functions.getPair(token_in, token_out).call()

async def get_reserves(pair_address):
    try:
        pair_contract_abi = '[{"constant":true,"inputs":[],"name":"getReserves","outputs":[{"internalType":"uint112","name":"_reserve0","type":"uint112"},{"internalType":"uint112","name":"_reserve1","type":"uint112"},{"internalType":"uint32","name":"_blockTimestampLast","type":"uint32"}],"payable":false,"stateMutability":"view","type":"function"}]'
        pair_contract = web3.eth.contract(address=pair_address, abi=json.loads(pair_contract_abi))
        reserves = pair_contract.functions.getReserves().call()
        return {
            'reserve_in': reserves[0],
            'reserve_out': reserves[1]
        }
    except Exception as e:
        logging.error(f"Failed to get reserves for pair {pair_address}: {e}")
        return {
            'reserve_in': 0,
            'reserve_out': 0
        }


async def main():
    await monitor_new_pairs()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Program terminated by user")
