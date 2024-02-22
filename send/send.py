"""Uploading a new firmware (directory) using OtaBle"""
# Going to need bleak, pycryptodome
# python3 -m pip install bleak pycryptodome
import asyncio
from asyncio.exceptions import CancelledError, TimeoutError
from bleak import BleakScanner, BleakClient, BleakError
from binascii import unhexlify
from Crypto.Cipher import AES
import argparse
import os
import tarfile
import zlib
import hashlib
import io


parser = argparse.ArgumentParser(description="Upload a new firmware using OtaBle")
parser.add_argument("directory", help="The firmware directory")
parser.add_argument("service", help="The service UUID")
parser.add_argument("control", help="The control UUID")
parser.add_argument("key", help="The key")

args = parser.parse_args()

class TargetDevice:
    def __init__(self, service_uuid):
        self.service_uuid = service_uuid

    def filter(self, device, advertising_data):
        return self.service_uuid in advertising_data.service_uuids


async def main():
    # prepare upload
    buffer = io.BytesIO()
    old_cwd = os.getcwd()
    os.chdir(args.directory)
    with tarfile.TarFile(fileobj=buffer, mode='x') as tf:
        tf.add('.')
    os.chdir(old_cwd)

    compressed = zlib.compress(buffer.getvalue())
    compressed = compressed + b'\x00' * (16 - len(compressed) % 16)  # padded to 128 bits
    
    hash = hashlib.sha1(compressed)

    cipher = AES.new(unhexlify(args.key), AES.MODE_ECB)
    ciphertext = cipher.encrypt(compressed)

    # find the device
    print(f"Looking for device exposing service {args.service}")
    target = TargetDevice(args.service)
    device = await BleakScanner.find_device_by_filter(target.filter)
    if device is None:
        print(f"Device exposing service uuid {args.service} not found")
        return

    # send
    print(f"Connecting to {device.name}")
    try:
        async with BleakClient(device, services=[args.service]) as client:
            print(f"Uploading {len(ciphertext)} bytes")
            await client.write_gatt_char(args.control, hash.digest())

            for i in range(0, len(ciphertext), 20):
                await client.write_gatt_char(args.control, ciphertext[i:i+20])
            await client.write_gatt_char(args.control, b'')


    except (CancelledError, TimeoutError, BleakError):
        print("Connection failed")
        return

asyncio.run(main())