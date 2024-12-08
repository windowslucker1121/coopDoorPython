#execute this to generate a new pair of VAPID keys and save them to .secrets.yaml

import base64
from ruamel.yaml import YAML
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from py_vapid import Vapid
import os
import logging

logger = logging.getLogger(__name__)

def generate_vapid_keys():
    # Generate a new pair of VAPID keys
    vapid = Vapid()
    vapid.generate_keys()

    # Extract the public key in raw bytes (uncompressed point)
    public_key_bytes = vapid.public_key.public_bytes(
        encoding=Encoding.X962,  # Use X962 for the uncompressed point
        format=PublicFormat.UncompressedPoint
    )

    # Extract the private key in raw bytes (as a private integer)
    private_key_number = vapid.private_key.private_numbers().private_value
    private_key_bytes = private_key_number.to_bytes((private_key_number.bit_length() + 7) // 8, byteorder="big")

    # Encode the keys in Base64 URL-safe format
    public_key_b64 = base64.urlsafe_b64encode(public_key_bytes).decode('utf-8').rstrip("=")
    private_key_b64 = base64.urlsafe_b64encode(private_key_bytes).decode('utf-8').rstrip("=")

    return public_key_b64, private_key_b64


def dump_keys_to_yaml(public_key, private_key, filename=".secrets.yaml"):
    # Create the dictionary structure for the YAML file
    secrets = {
        "secrets": {
            "vapid_public_key": public_key,
            "vapid_private_key": private_key
        }
    }

    # Create an instance of YAML
    yaml = YAML()
    
    # Write to the YAML file
    with open(filename, "w") as file:
        yaml.dump(secrets, file)

    logger.info(f"Keys have been successfully written to {filename}")


# Generate the keys and prompt for saving
public_key, private_key = generate_vapid_keys()

save_keys = input("Do you want to save the keys? THIS WILL ALSO DELETE YOUR NOTIFICATION SUBSCRIPTIONS (y/n): ").strip().lower()

if save_keys == "y":
    # Check if the old subscriptions file exists
    subscriptions_file = ".subscriptions.json"
    if os.path.exists(subscriptions_file):
        try:
            os.remove(subscriptions_file)
            # Delete them because the keys have changed
            logger.info(f"Deleted old subscriptions file: {subscriptions_file}")
        except Exception as e:
            logger.critical(f"Error deleting subscriptions file: {e}")
    else:
        logger.info(f"No existing subscriptions file found to delete.")

    # Save the new keys to .secrets.yaml
    dump_keys_to_yaml(public_key, private_key)
else:
    logger.critical("Keys were not saved, and subscriptions were not deleted.")
