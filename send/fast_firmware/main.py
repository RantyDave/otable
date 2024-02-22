import machine
import asyncio

async def main():
    led = machine.Pin("LED", machine.Pin.OUT)
    while True:
        led.value(1)
        await asyncio.sleep_ms(100)
        led.value(0)
        await asyncio.sleep_ms(100)
