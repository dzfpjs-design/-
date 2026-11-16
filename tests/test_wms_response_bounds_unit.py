import unittest
from unittest.mock import patch
from generic_agents import wms

class Stream:
    def __init__(self, encoding='', chunks=()):
        self.status_code=200;self.headers={'content-type':'image/png','content-encoding':encoding};self.chunks=chunks;self.read=False
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def raise_for_status(self):pass
    def iter_raw(self,chunk_size):
        assert chunk_size==65536
        self.read=True
        yield from self.chunks
    def iter_bytes(self):raise AssertionError('transparent decompression must not be used')

class Client:
    def __init__(self,response):self.response=response;self.sent=None
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def stream(self,*args,**kwargs):self.sent=kwargs;return self.response

class WMSBoundedResponseTest(unittest.TestCase):
    def run_fetch(self,response,limit=8):
        client=Client(response)
        with patch.object(wms,'authorize'),patch.object(wms,'endpoint',return_value='https://example.invalid/wms'),patch.object(wms,'headers',return_value={}),patch.object(wms.httpx,'Client',return_value=client):
            result=wms.fetch({'workspace_id':'w','endpoint':'x'}, {},limit,lambda:None,db=object())
        self.assertEqual(client.sent['headers']['Accept-Encoding'],'identity')
        return result
    def test_plain_body_uses_raw_stream(self):
        self.assertEqual(self.run_fetch(Stream(chunks=[b'ab',b'cd'])),(b'abcd','image/png'))
    def test_compressed_body_is_rejected_before_read(self):
        response=Stream('gzip',[b'not-read'])
        with self.assertRaisesRegex(ValueError,'压缩编码'):self.run_fetch(response)
        self.assertFalse(response.read)
    def test_limit_still_applies_to_raw_bytes(self):
        with self.assertRaisesRegex(ValueError,'大小或时间'):self.run_fetch(Stream(chunks=[b'12345',b'67890']))
