from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
import base64

private_key = ec.generate_private_key(ec.SECP256R1())
public_key = private_key.public_key()

private_key_pem = private_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.TraditionalOpenSSL,
    encryption_algorithm=serialization.NoEncryption()
)

public_key_pem = public_key.public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo
)

def encode_vapid_key(public_key_pem):
    cleaned_key = public_key_pem.decode().replace(
        "-----BEGIN PUBLIC KEY-----", ""
    ).replace(
        "-----END PUBLIC KEY-----", ""
    ).replace("\n", "")
    # Decode the base64 string and re-encode as URL-safe
    return base64.urlsafe_b64encode(base64.b64decode(cleaned_key)).decode('utf-8').rstrip('=')

vapid_public_key = encode_vapid_key(public_key_pem)

if __name__ == "__main__":
    print("Private Key:\n", private_key_pem.decode())
    print("Public Key:\n", public_key_pem.decode())
    print("URL-safe VAPID Public Key:\n", vapid_public_key)

    save = input("Do you want to save the keys to a file? (y/n): ")
    if save.lower() != "y":
        exit()
        
    with open(".secrets.yaml", "w") as f:
        f.write("secrets:\n")
        f.write("  private_key: |\n")
        for line in private_key_pem.decode().splitlines():
            f.write(f"    {line}\n")
        f.write("  public_key: |\n")
        for line in public_key_pem.decode().splitlines():
            f.write(f"    {line}\n")
        f.write("  vapid_public_key: |\n")
        f.write(f"    {vapid_public_key}\n")
