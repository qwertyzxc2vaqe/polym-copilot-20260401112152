"""
USDC Approval Script for Polymarket Trading Bot

This script checks and sets up unlimited USDC approvals for all Polymarket
contracts required for trading. Run once before starting the trading bot.
"""

import sys
import time
import logging
from typing import List, Tuple, Optional

from web3 import Web3
from web3.exceptions import TransactionNotFound
from eth_account import Account

from config import get_config

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s'
)
logger = logging.getLogger(__name__)


# Contract Addresses (Polygon Mainnet)
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
USDC_DECIMALS = 6

# Polymarket Spender Contracts
SPENDER_CONTRACTS = {
    "Polymarket Exchange": "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
    "Neg Risk Exchange": "0xC5d563A36AE78145C45a50134d48A1215220f80a",
    "Neg Risk Adapter": "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296",
    "CTF (Conditional Tokens)": "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045",
}

# Free public Polygon RPC endpoints
DEFAULT_RPC_URLS = [
    "https://polygon-bor-rpc.publicnode.com",
    "https://polygon-rpc.com",
    "https://polygon.llamarpc.com",
]

# ERC20 ABI (only functions we need)
ERC20_ABI = [
    {
        "constant": True,
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "spender", "type": "address"}
        ],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function"
    },
    {
        "constant": False,
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount", "type": "uint256"}
        ],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function"
    },
]

# Unlimited approval amount (2^256 - 1)
UNLIMITED_APPROVAL = 2**256 - 1

# Minimum allowance threshold (in USDC, with decimals)
# If allowance is below this, we'll approve again
MIN_ALLOWANCE_THRESHOLD = 1_000_000 * 10**USDC_DECIMALS  # 1 million USDC


def connect_with_fallback(rpc_urls: List[str], max_retries: int = 3) -> Web3:
    """
    Connect to Polygon RPC with fallback support.
    Tries each RPC URL in order until one works.
    """
    for rpc_url in rpc_urls:
        for attempt in range(max_retries):
            try:
                logger.info(f"Connecting to RPC: {rpc_url} (attempt {attempt + 1}/{max_retries})")
                w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={'timeout': 30}))
                
                if w3.is_connected():
                    chain_id = w3.eth.chain_id
                    block = w3.eth.block_number
                    logger.info(f"[OK] Connected to Polygon (Chain ID: {chain_id}, Block: {block})")
                    return w3
                    
            except Exception as e:
                logger.warning(f"RPC connection failed: {e}")
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt  # Exponential backoff
                    logger.info(f"Retrying in {wait_time}s...")
                    time.sleep(wait_time)
    
    raise ConnectionError("Failed to connect to any Polygon RPC endpoint")


def validate_private_key(private_key: str) -> Tuple[str, str]:
    """
    Validate and normalize private key format.
    Returns (normalized_key, wallet_address).
    """
    # Remove 0x prefix if present
    if private_key.startswith("0x"):
        private_key = private_key[2:]
    
    # Validate length
    if len(private_key) != 64:
        raise ValueError("Private key must be 64 hex characters (32 bytes)")
    
    # Validate hex format
    try:
        int(private_key, 16)
    except ValueError:
        raise ValueError("Private key must be a valid hex string")
    
    # Derive address
    normalized_key = "0x" + private_key
    account = Account.from_key(normalized_key)
    
    return normalized_key, account.address


def check_allowance(w3: Web3, token_address: str, owner: str, spender: str) -> int:
    """Check current token allowance for a spender."""
    token = w3.eth.contract(
        address=Web3.to_checksum_address(token_address),
        abi=ERC20_ABI
    )
    return token.functions.allowance(
        Web3.to_checksum_address(owner),
        Web3.to_checksum_address(spender)
    ).call()


def check_balance(w3: Web3, token_address: str, owner: str) -> int:
    """Check token balance."""
    token = w3.eth.contract(
        address=Web3.to_checksum_address(token_address),
        abi=ERC20_ABI
    )
    return token.functions.balanceOf(Web3.to_checksum_address(owner)).call()


def execute_approval(
    w3: Web3,
    private_key: str,
    token_address: str,
    spender: str,
    amount: int = UNLIMITED_APPROVAL,
    max_retries: int = 3
) -> str:
    """
    Execute approval transaction with retry logic.
    Returns transaction hash.
    """
    account = Account.from_key(private_key)
    owner = account.address
    
    token = w3.eth.contract(
        address=Web3.to_checksum_address(token_address),
        abi=ERC20_ABI
    )
    
    for attempt in range(max_retries):
        try:
            # Get nonce
            nonce = w3.eth.get_transaction_count(owner, 'pending')
            
            # Estimate gas
            gas_estimate = token.functions.approve(
                Web3.to_checksum_address(spender),
                amount
            ).estimate_gas({'from': owner})
            
            # Get gas price (use EIP-1559 if available)
            try:
                base_fee = w3.eth.get_block('latest')['baseFeePerGas']
                priority_fee = w3.to_wei(30, 'gwei')
                max_fee = base_fee * 2 + priority_fee
                
                tx = token.functions.approve(
                    Web3.to_checksum_address(spender),
                    amount
                ).build_transaction({
                    'from': owner,
                    'nonce': nonce,
                    'gas': int(gas_estimate * 1.2),
                    'maxFeePerGas': max_fee,
                    'maxPriorityFeePerGas': priority_fee,
                    'chainId': w3.eth.chain_id,
                })
            except Exception:
                # Fallback to legacy gas pricing
                gas_price = w3.eth.gas_price
                tx = token.functions.approve(
                    Web3.to_checksum_address(spender),
                    amount
                ).build_transaction({
                    'from': owner,
                    'nonce': nonce,
                    'gas': int(gas_estimate * 1.2),
                    'gasPrice': int(gas_price * 1.1),
                    'chainId': w3.eth.chain_id,
                })
            
            # Sign and send
            signed_tx = w3.eth.account.sign_transaction(tx, private_key)
            tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            
            return tx_hash.hex()
            
        except Exception as e:
            logger.warning(f"Approval attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                logger.info(f"Retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                raise


def wait_for_confirmation(
    w3: Web3,
    tx_hash: str,
    timeout: int = 120,
    poll_interval: int = 3
) -> bool:
    """
    Wait for transaction confirmation.
    Returns True if successful, False if failed.
    """
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        try:
            receipt = w3.eth.get_transaction_receipt(tx_hash)
            if receipt is not None:
                if receipt['status'] == 1:
                    logger.info(f"[OK] Transaction confirmed in block {receipt['blockNumber']}")
                    return True
                else:
                    logger.error(f"[FAIL] Transaction failed (reverted)")
                    return False
        except TransactionNotFound:
            pass
        
        time.sleep(poll_interval)
    
    logger.warning(f"[TIMEOUT] Transaction confirmation timeout after {timeout}s")
    return False


def format_amount(amount: int, decimals: int = USDC_DECIMALS) -> str:
    """Format token amount with proper decimals."""
    if amount >= UNLIMITED_APPROVAL // 2:
        return "Unlimited"
    return f"{amount / 10**decimals:,.2f}"


def main():
    """Main approval workflow."""
    print("\n" + "="*60)
    print("[KEY] Polymarket USDC Approval Script")
    print("="*60 + "\n")
    
    try:
        # Load configuration
        config = get_config()
        
        # Check if in dry_run mode - skip approvals
        if config.trading.mode == "dry_run":
            logger.info("[NOTE] DRY_RUN mode detected - skipping actual approvals")
            logger.info("   Approvals are only needed for live trading.")
            logger.info("   Switch TRADING_MODE to 'live_test' when ready to trade.")
            print("\n" + "="*60)
            print("[OK] Dry run mode - no approvals needed!")
            print("="*60 + "\n")
            return 0
        
        # Get and validate private key
        private_key = config.blockchain.private_key
        private_key, wallet_address = validate_private_key(private_key)
        logger.info(f"Wallet address: {wallet_address}")
        
        # Build RPC URL list
        rpc_urls = config.get_rpc_urls()
        if not rpc_urls or rpc_urls[0] == "":
            rpc_urls = DEFAULT_RPC_URLS
        
        # Connect to Polygon
        w3 = connect_with_fallback(rpc_urls)
        
        # Check MATIC balance for gas
        matic_balance = w3.eth.get_balance(wallet_address)
        matic_formatted = w3.from_wei(matic_balance, 'ether')
        logger.info(f"MATIC balance: {matic_formatted:.4f} MATIC")
        
        # If no MATIC, cannot execute approvals - but allow bot to start in dry_run
        if matic_balance == 0:
            logger.warning("[WARN] Zero MATIC balance - cannot execute approval transactions")
            logger.info("   To approve contracts, you need MATIC for gas fees (~0.01 MATIC)")
            logger.info("   Bot can still run in dry_run mode without approvals.")
            print("\n" + "="*60)
            print("[WARN] No MATIC for gas - skipping approvals")
            print("   Add MATIC to wallet for live trading approvals")
            print("="*60 + "\n")
            return 0  # Return success to allow bot to start
        
        if matic_balance < w3.to_wei(0.01, 'ether'):
            logger.warning("[WARN] Low MATIC balance! You may not have enough for gas fees.")
        
        # Check USDC balance
        usdc_balance = check_balance(w3, USDC_ADDRESS, wallet_address)
        logger.info(f"USDC balance: {format_amount(usdc_balance)} USDC")
        
        print("\n" + "-"*60)
        print("📋 Checking Allowances for Polymarket Contracts")
        print("-"*60 + "\n")
        
        # Check and approve each spender
        approvals_needed = []
        
        for name, spender in SPENDER_CONTRACTS.items():
            allowance = check_allowance(w3, USDC_ADDRESS, wallet_address, spender)
            allowance_formatted = format_amount(allowance)
            
            if allowance >= MIN_ALLOWANCE_THRESHOLD:
                logger.info(f"[OK] {name}: {allowance_formatted} USDC (sufficient)")
            else:
                logger.info(f"[WARN] {name}: {allowance_formatted} USDC (needs approval)")
                approvals_needed.append((name, spender))
        
        if not approvals_needed:
            print("\n" + "="*60)
            print("[OK] All approvals are already set! No action needed.")
            print("="*60 + "\n")
            return 0
        
        print("\n" + "-"*60)
        print(f"[SYNC] Executing {len(approvals_needed)} Approval Transaction(s)")
        print("-"*60 + "\n")
        
        successful = 0
        failed = 0
        
        for name, spender in approvals_needed:
            logger.info(f"Approving USDC for {name}...")
            logger.info(f"   Spender: {spender}")
            
            try:
                tx_hash = execute_approval(
                    w3=w3,
                    private_key=private_key,
                    token_address=USDC_ADDRESS,
                    spender=spender
                )
                logger.info(f"   TX Hash: {tx_hash}")
                logger.info(f"   Polygon Scan: https://polygonscan.com/tx/{tx_hash}")
                
                if wait_for_confirmation(w3, tx_hash):
                    successful += 1
                    # Verify new allowance
                    new_allowance = check_allowance(w3, USDC_ADDRESS, wallet_address, spender)
                    logger.info(f"   New allowance: {format_amount(new_allowance)} USDC")
                else:
                    failed += 1
                    
            except Exception as e:
                logger.error(f"   [FAIL] Approval failed: {e}")
                failed += 1
            
            print()  # Blank line between approvals
        
        # Summary
        print("="*60)
        print("[STATS] Summary")
        print("="*60)
        print(f"   Successful: {successful}")
        print(f"   Failed: {failed}")
        
        if failed > 0:
            print("\n[WARN] Some approvals failed. Please check errors above and retry.")
            return 1
        else:
            print("\n[OK] All approvals completed successfully!")
            print("   You can now run the Polymarket trading bot.")
            return 0
            
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
