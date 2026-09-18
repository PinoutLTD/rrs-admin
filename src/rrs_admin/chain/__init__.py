"""ED25519 keys for Robonomics, on PyNaCl.

Copied from rrs-ha-integration (`custom_components/robonomics_report_service/chain`,
commit e011d6d): bip39.py, ss58.py, keys.py and the BIP39 wordlist, unchanged. The
integration, the connector and this tool should share one package eventually; until
then the copies are kept byte-identical, and the test vectors pin their behaviour.
"""

from .bip39 import MnemonicError, validate_mnemonic
from .keys import ROBONOMICS_SS58_FORMAT, Keypair
from .ss58 import SS58Error, ss58_decode

__all__ = [
    "ROBONOMICS_SS58_FORMAT",
    "Keypair",
    "MnemonicError",
    "SS58Error",
    "ss58_decode",
    "validate_mnemonic",
]
