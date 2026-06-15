#!/usr/bin/env python3
import os
import sys
import hashlib
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend

# Add server directory to path to use virtualenv libraries if needed
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT_DIR, 'server'))

try:
    import crx3.creator
except ImportError:
    print("Error: 'crx3' package is not installed. Please run: pip install crx3")
    sys.exit(1)

def get_extension_id(private_key_path):
    """Calculate the Chrome Extension ID from the private key (DER public key hash)."""
    with open(private_key_path, 'rb') as key_file:
        private_key = serialization.load_pem_private_key(
            key_file.read(),
            password=None,
            backend=default_backend()
        )
    
    # Get the public key serialized in SubjectPublicKeyInfo (DER) format
    public_key = private_key.public_key()
    der_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    
    # Calculate SHA256 of DER public key bytes
    sha256_hash = hashlib.sha256(der_bytes).hexdigest()
    
    # First 32 characters of hash represent the extension ID
    half_hash = sha256_hash[:32]
    
    # Map hex characters (0-f) to (a-p)
    # 0 -> a, 1 -> b, ..., f -> p
    mapping = {hex(i)[2:]: chr(97 + i) for i in range(16)}
    extension_id = "".join(mapping[c] for c in half_hash)
    
    return extension_id

def main():
    extension_src_dir = os.path.join(ROOT_DIR, 'extension')
    extensions_dist_dir = os.path.join(ROOT_DIR, 'server', 'static', 'extensions')
    
    os.makedirs(extensions_dist_dir, exist_ok=True)
    key_path = os.path.join(extensions_dist_dir, 'key.pem')
    crx_path = os.path.join(extensions_dist_dir, 'youtube_monitor.crx')
    
    # 1. Generate key.pem if it doesn't exist
    if not os.path.exists(key_path):
        print(f"Generating new private key at {key_path}...")
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
            backend=default_backend()
        )
        pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        )
        with open(key_path, 'wb') as key_file:
            key_file.write(pem)
    else:
        print(f"Using existing private key at {key_path}")

    # 2. Calculate extension ID
    ext_id = get_extension_id(key_path)
    print(f"Calculated Chrome Extension ID: {ext_id}")

    # 3. Create CRX3 bundle
    print(f"Packaging extension from '{extension_src_dir}' into '{crx_path}'...")
    try:
        crx3.creator.create_crx_file(extension_src_dir, key_path, crx_path)
        print("Success! Extension packaged and signed successfully.")
        
        # Save extension ID to a file so server can load it dynamically if needed
        id_path = os.path.join(extensions_dist_dir, 'extension_id.txt')
        with open(id_path, 'w') as id_file:
            id_file.write(ext_id)
            
    except Exception as e:
        print(f"Error during packaging: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
