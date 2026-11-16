"""Disposable synthetic TLS+password MQTT broker for isolated integration tests."""
import asyncio,logging,os
from amqtt.broker import Broker
async def main():
    broker=Broker({'listeners':{'default':{'type':'tcp','bind':os.environ['TEST_BIND'],'ssl':True,'certfile':os.environ['TEST_CERT'],'keyfile':os.environ['TEST_KEY'],'max_connections':8}},'plugins':{'amqtt.plugins.authentication.FileAuthPlugin':{'password_file':os.environ['TEST_PASSWORD_FILE']}}})
    await broker.start();print('READY',flush=True)
    try:await asyncio.wait_for(asyncio.Event().wait(),timeout=180)
    finally:await broker.shutdown()
if __name__=='__main__':logging.basicConfig(level=logging.ERROR);asyncio.run(main())
