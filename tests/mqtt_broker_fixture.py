"""Anonymous loopback AMQTT fixture only; never a deployed device broker."""
import asyncio
import logging
import sys
from amqtt.broker import Broker


async def main(port):
    broker = Broker({'listeners': {'default': {'type': 'tcp', 'bind': f'127.0.0.1:{port}', 'max_connections': 10}},
                     'plugins': {'amqtt.plugins.authentication.AnonymousAuthPlugin': {'allow_anonymous': True}}})
    await broker.start()
    print('READY', flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await broker.shutdown()


if __name__ == '__main__':
    logging.basicConfig(level=logging.ERROR)
    asyncio.run(main(int(sys.argv[1])))
