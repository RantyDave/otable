"""Over the air updates via BLE for Micropython"""
# Needs tarfile, shutil installed
# mpremote mip install tarfile shutil
import asyncio
import bluetooth
import aioble
import json
import os
import io
import sys
import binascii
import cryptolib
import hashlib
import deflate
import tarfile
import shutil
import machine


class OtaBle:
    """
    On construction looks for a file called otable-config.json and another called otable-key

    otable-config.json looks a bit like this:
    {
        "name": "YourDeviceUpdate",
        "service_uuid": "guid-with-dashes-here",
        "control_uuid": "guid-with-dashes-here",
        "version_uuid": "guid-with-dashes-here"
    }

    Obviously you can put config into VCS but not the key. To put the key on the device I suggest something like:
    
    % echo "0123456789abcdef0123456789abcdef" > otable-key ; mpremote cp otable-key :otable-key ; rm otable-key

    The key is visible to anyone who can get to your device's USB port so ... you've been warned.

    Contents of the file 'version' in the root of the firmware directory will be set as the firmware chacteristic, if present.
    """
    def __init__(self):
        # Load the config
        try:
            with open("otable-config.json", "r") as f:
                config = json.load(f)
                self.name = config["name"]
                self.service_uuid = bluetooth.UUID(config["service_uuid"])
                self.control_uuid = bluetooth.UUID(config["control_uuid"])
                self.version_uuid = bluetooth.UUID(config["version_uuid"])
        except:
            print("otable: failed - otable-config.json not found or could not be parsed")
            raise

        # Load the key
        try:
            with open("otable-key", "r") as f:
                self.key = binascii.unhexlify(f.read()[:32])
        except:
            print("otable: failed - otable-key not found or could not be parsed")
            raise
        
        # OK, we're good
        self.service = aioble.Service(self.service_uuid)
        self.control = aioble.Characteristic(self.service, self.control_uuid, read=False, write=True, capture=True)
        self.version = aioble.Characteristic(self.service, self.version_uuid, read=True, write=False, capture=False)
        aioble.register_services(self.service)

    async def advertise(self):
        version = None
        try:
            with open("/firmware/version", "r") as f:
                version = f.read()[:20]
            self.version.write(version)
            print("otable: firmware version is", version)
        except OSError:
            print("otable: did not find version file, version characteristic will remain empty")                
        print("otable: running advertising loop")
        while True:
            async with await aioble.advertise(
                1000,
                name=self.name,
                services=[self.service_uuid],
            ) as connection:
                print("otable: service connected")
                workflow_task = asyncio.create_task(self.workflow())
                await connection.disconnected()
                workflow_task.cancel()
                print("otable: service disconnected")
                
    async def workflow(self):
        print("otable: workflow started")
        try:
            # we expect to be first given a 20 byte sha1 hash
            connection, data = await self.control.written()
            if len(data) != 20:
                print("otable: invalid hash length")
                return
            target_hash = data

            # next the blob is sent in 20 byte chunks
            received_data = bytes()
            while True:
                connection, data = await self.control.written()
                if data == b"":
                    break
                received_data += data
            print("otable: received data length", len(received_data))

            # decrypt
            cipher = cryptolib.aes(self.key, 1)  # ecb mode
            decrypted = cipher.decrypt(received_data)

            # find the hash of the decrypted data
            actual_hash = hashlib.sha1(decrypted).digest()
            if actual_hash != target_hash:
                print("otable: hash mismatch")
                return
            
            # decompress
            tar_expand(decrypted, "/new_firmware")

            # switcheroo
            print("otable: switching to new firmware")
            try:
                shutil.rmtree("firmware")
            except OSError:
                print("otable: did not find existing firmware to delete (is OK)")
            os.rename("new_firmware", "firmware")

            # reset
            print("otable: resetting")
            machine.soft_reset()
                
        except asyncio.CancelledError:
            pass
            
        print("otable: workflow exited")


def tar_expand(data, root):
    print("otable: expanding tar into ", root)
    data_stream = io.BytesIO(data)
    try:
        os.mkdir(root)
    except OSError:
        pass
    root = root + "/"
    with deflate.DeflateIO(data_stream, deflate.ZLIB) as df:
        with tarfile.TarFile(fileobj=df) as tf:
            for i in tf:
                name = i.name
                if name[-10:] == "@PaxHeader":  # skip these
                    continue
                while name[:2] == './':  # strip this large collection of dot slashes
                    name = name[2:]
                try:
                    if i.type == tarfile.DIRTYPE:
                        os.mkdir(root + name)
                        print("otable: created directory ", root + name)
                    else:
                        f = tf.extractfile(i)
                        with open(root + name, "wb") as of:
                            of.write(f.read())
                        print("otable: extracted file ", root + name)
                except OSError as e:
                    print("otable: ignored OSError when dealing with ", root + name, e)
                    pass

async def main():
    # Bring the OTA service up
    ota = OtaBle()
    ota_task = asyncio.create_task(ota.advertise())

    # bring the firmware up
    try:
        sys.path.append("/firmware")
        import firmware.main  # if this blocks you'll brick the device (but it can throw)
        fw_task = asyncio.create_task(firmware.main.main())  # must be an async def (and use asyncio.sleep)
    except ImportError:
        print("otable: no firmware found, still listening for uploads")

    # All good
    asyncio.get_event_loop().run_forever()

asyncio.run(main())