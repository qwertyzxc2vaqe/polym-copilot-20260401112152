#!/usr/bin/env python3
"""
Derive Polymarket CLOB API credentials from wallet private key.
This script uses py-clob-client to generate API key, secret, and passphrase.
"""

import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv

# Load existing .env
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

def derive_credentials():
    """Derive CLOB API credentials from private key."""
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds
    except ImportError:
        print("[FAIL] py-clob-client not installed. Run: pip install py-clob-client")
        return None
    
    private_key = os.getenv("PRIVATE_KEY", "")
    if not private_key:
        print("[FAIL] PRIVATE_KEY not set in .env")
        return None
    
    # Remove 0x prefix if present
    if private_key.startswith("0x"):
        private_key = private_key[2:]
    
    if len(private_key) != 64:
        print(f"[FAIL] PRIVATE_KEY must be 64 hex chars, got {len(private_key)}")
        return None
    
    print("[KEY] Deriving CLOB API credentials from wallet...")
    print(f"   Chain ID: 137 (Polygon Mainnet)")
    
    try:
        # Initialize client with just the private key (Level 1 auth)
        client = ClobClient(
            host="https://clob.polymarket.com",
            key=private_key,
            chain_id=137,
            signature_type=0  # EOA (Externally Owned Account)
        )
        
        print("   Connected to CLOB API")
        
        # Derive API credentials
        print("   Deriving API credentials...")
        creds = client.derive_api_key()
        
        if creds:
            print("\n[OK] Successfully derived CLOB credentials!")
            print(f"   API Key: {creds.api_key[:20]}...")
            print(f"   Secret: {creds.api_secret[:20]}...")
            print(f"   Passphrase: {creds.api_passphrase}")
            return creds
        else:
            print("   Derive failed, trying create...")
            creds = client.create_api_key()
            if creds:
                print("\n[OK] Successfully created CLOB credentials!")
                return creds
            
    except Exception as e:
        print(f"[FAIL] Error deriving credentials: {e}")
        
        # Try the alternative method
        try:
            print("\n   Trying create_or_derive_api_creds()...")
            creds = client.create_or_derive_api_creds()
            if creds:
                print("\n[OK] Successfully obtained CLOB credentials!")
                return creds
        except Exception as e2:
            print(f"[FAIL] Alternative method also failed: {e2}")
    
    return None


def update_env_file(creds):
    """Update .env file with derived credentials."""
    env_path = Path(__file__).parent.parent / ".env"
    
    with open(env_path, 'r') as f:
        content = f.read()
    
    # Update credentials
    if 'CLOB_API_KEY=' in content:
        lines = content.split('\n')
        new_lines = []
        for line in lines:
            if line.startswith('CLOB_API_KEY='):
                new_lines.append(f'CLOB_API_KEY={creds.api_key}')
            elif line.startswith('CLOB_SECRET='):
                new_lines.append(f'CLOB_SECRET={creds.api_secret}')
            elif line.startswith('CLOB_PASSPHRASE='):
                new_lines.append(f'CLOB_PASSPHRASE={creds.api_passphrase}')
            else:
                new_lines.append(line)
        content = '\n'.join(new_lines)
    
    with open(env_path, 'w') as f:
        f.write(content)
    
    print(f"\n[OK] Updated {env_path}")


def main():
    print("=" * 60)
    print("  POLYMARKET CLOB CREDENTIAL DERIVATION")
    print("=" * 60)
    print()
    
    creds = derive_credentials()
    
    if creds:
        print("\n" + "=" * 60)
        print("  CREDENTIALS (save these securely)")
        print("=" * 60)
        print(f"CLOB_API_KEY={creds.api_key}")
        print(f"CLOB_SECRET={creds.api_secret}")
        print(f"CLOB_PASSPHRASE={creds.api_passphrase}")
        print("=" * 60)
        
        # Update .env file
        update_env_file(creds)
        return 0
    else:
        print("\n[FAIL] Failed to derive credentials")
        print("   Make sure your PRIVATE_KEY in .env is valid")
        print("   and you have an active Polymarket account")
        return 1


if __name__ == "__main__":
    sys.exit(main())
